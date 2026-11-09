#!/usr/bin/env python3
"""Stage 1 (news source) — ingest CC-News RO into `source_counts`.

M3 of docs/wrodfreq-spec.md §13: the register CulturaX under-represents (spec
§6, `news` row). There is no ready-made Romanian-only CC-News mirror, so this
reads `stanford-oval/ccnews` — a cleaned, deduplicated, language-tagged parquet
mirror of the full CommonCrawl News crawl (2016-06 to 2024-06, 600M articles,
100+ languages, one shard-set per crawl year, no per-language split) — and
keeps only rows where `language == 'ro'`. See:
https://huggingface.co/datasets/stanford-oval/ccnews

Consequence for cost: every shard is scanned in full (parquet columnar read
fetches only the `language` and `plain_text` columns, not the other eleven,
but those two still dominate file size). Expect a low match rate — this is a
multilingual crawl, not a Romanian one — so unlike ingest_wiki.py/ingest_web.py
"documents processed" and "rows scanned" now diverge:

  - `current_file_rows_scanned` / resume position — every row read, any language.
  - `current_file_docs_matched`, `*_tokens_done` — Romanian rows only; these
    are what land in `source_counts` and `sources.total_docs/total_tokens`.
    (Writing rows_scanned into total_docs would silently claim CC-News's full
    multilingual row count as this source's Romanian document count — the
    same honest-denominator mistake §3.2 exists to prevent, just on the docs
    side instead of the token side.)

Shares ingest_web.py's per-file checkpointing / row-group resume machinery
(spec §4) since `stanford-oval/ccnews` is also HfFileSystem + pyarrow parquet,
one shard per (roughly) 1M rows. No DEX filter, no length filter — same as
every other ingester, via wrodfreq.tokenizer directly (spec §3).

Usage:
    python build/ingest_news.py              # full run (hours to a day+)
    python build/ingest_news.py --test       # first 200k scanned rows, into a new experiment db
    python build/ingest_news.py --limit N    # stop at absolute scanned row N (not matched
                                              # docs — match rate is unknown up front)
    python build/ingest_news.py --resume     # continue the generation stored in the db

Restart loop (stop automatically on success):
    while true; do
        python -u build/ingest_news.py --resume
        [ $? -eq 0 ] && break
        echo "[$(date)] restarting in 15s..." && sleep 15
    done

Output: data/wrodfreq.db, source_id='news'. Progress: `ingest_state` in the same db,
committed with the counts (ADR-004).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_state import Batch, IngestRefused, SourceSpec, begin, target_db
from wrodfreq.db import connect
from wrodfreq.tokenizer import tokenize

SOURCE_ID     = "news"
DISPLAY_NAME  = "CC-News RO"
URL           = "https://huggingface.co/datasets/stanford-oval/ccnews"
LICENCE       = "CommonCrawl Terms of Use (research use) — see dataset card"
REGISTER      = "news"
PERIOD        = "contemporary"
PERIOD_NOTE   = "CommonCrawl News crawl dates 2016-06 to 2024-06 (crawl date, not publish date)"

HF_PARQUET_DIR = "datasets/stanford-oval/ccnews"
TEXT_COLUMN    = "plain_text"
LANG_COLUMN    = "language"
TARGET_LANG    = "ro"

COMMIT_EVERY  = 50_000  # rows *scanned* between commits (match rate is low and uneven)
CURSOR_VERSION = 1

SPEC = SourceSpec(
    source_id=SOURCE_ID, display_name=DISPLAY_NAME, url=URL, licence=LICENCE,
    register=REGISTER, period=PERIOD, period_note=PERIOD_NOTE,
    cursor_version=CURSOR_VERSION,
    policy={"policy_version": 1, "repo": HF_PARQUET_DIR, "text_column": TEXT_COLUMN,
            "lang_column": LANG_COLUMN, "target_lang": TARGET_LANG, "unit": "row"},
)

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    if _shutdown:
        # Second signal: force out even if stuck inside a blocking network
        # read (a single shard's row-group fetch can take 20+ minutes here,
        # unlike ingest_web.py's smaller groups) or a retry-storm sleep loop
        # in huggingface_hub's HTTP backoff. `raise SystemExit` alone isn't
        # enough here — huggingface_hub's retry machinery runs background
        # threads, and CPython won't tear down the process while a
        # non-daemon thread is alive, so a raised exception on the main
        # thread can sit there forever waiting for threads that never stop.
        # os._exit() skips all of that: it terminates the process at the OS
        # level immediately, like SIGKILL from inside the process itself.
        print(f"\n[{datetime.now()}] second signal {sig} — forcing immediate exit "
              f"(progress since the last checkpoint is lost)", flush=True)
        os._exit(1)
    print(f"\n[{datetime.now()}] signal {sig} — flushing and exiting after current batch "
          f"(press again to force quit immediately, e.g. if stuck mid-fetch)", flush=True)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGHUP, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def empty_cursor() -> dict:
    return {
        "completed_files": [],
        "current_file": None,
        "current_file_rows_scanned": 0,
        "current_file_docs_matched": 0,
        "current_file_tokens_done": 0,
        "rows_scanned_in_completed_files": 0,
        "docs_in_completed_files": 0,
        "tokens_in_completed_files": 0,
    }


# ---------------------------------------------------------------------------
# Parquet reading — tests replace these two functions
# ---------------------------------------------------------------------------

def list_parquet_files() -> list[str]:
    from huggingface_hub import HfFileSystem
    fs = HfFileSystem()
    paths = fs.ls(HF_PARQUET_DIR, detail=False)
    return sorted(p for p in paths if p.endswith(".parquet"))


def iter_rows(hf_path: str, start_row: int):
    """Yield (language, text) for each row of one remote parquet file from `start_row`.

    Only the two needed columns are fetched. A network error propagates.
    """
    import pyarrow.parquet as pq
    from huggingface_hub import HfFileSystem

    fname = hf_path.rsplit("/", 1)[-1]
    with HfFileSystem().open(hf_path, "rb") as fh:
        pf = pq.ParquetFile(fh)
        num_groups = pf.metadata.num_row_groups
        rows_before = 0
        print(f"  [{fname}] {pf.metadata.num_rows:,} rows, {num_groups} groups, "
              f"starting at row {start_row:,}", flush=True)
        for g in range(num_groups):
            rg_rows = pf.metadata.row_group(g).num_rows
            if rows_before + rg_rows <= start_row:
                rows_before += rg_rows
                continue
            cols = pf.read_row_group(g, columns=[LANG_COLUMN, TEXT_COLUMN])
            langs = cols.column(LANG_COLUMN).to_pylist()
            texts = cols.column(TEXT_COLUMN).to_pylist()
            skip = max(0, start_row - rows_before)
            rows_before += rg_rows
            yield from zip(langs[skip:], texts[skip:])


def input_manifest() -> dict:
    files = list_parquet_files()
    return {"repo": HF_PARQUET_DIR, "files": [f.rsplit("/", 1)[-1] for f in files]}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest CC-News RO into source_counts")
    parser.add_argument("--test", action="store_true",
                         help="Scan the first 200k rows, into a new experiment database")
    parser.add_argument("--limit", type=int,
                         help="Stop at absolute scanned row N (not matched docs)")
    parser.add_argument("--resume", action="store_true",
                         help="Continue the generation stored in the database")
    parser.add_argument("--db", type=Path, default=None,
                         help="Target database (default data/wrodfreq.db)")
    args = parser.parse_args()

    try:
        db_path = target_db(SOURCE_ID, args.db, args.test, args.resume)
    except IngestRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    scan_limit = 200_000 if args.test else args.limit

    conn = connect(db_path)
    try:
        session = begin(conn, SPEC, resume=args.resume, initial_cursor=empty_cursor(),
                        input_manifest=input_manifest)
    except IngestRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        conn.close()
        return 2
    if session.finished:
        conn.close()
        return 0

    cursor = dict(session.cursor)
    all_files = [f"{HF_PARQUET_DIR}/{name}" for name in session.input_manifest["files"]]
    done = set(cursor["completed_files"])
    print(f"  {len(all_files)} shards, {len(all_files) - len(done)} remaining", flush=True)

    batch = Batch(session)
    global_start = time.time()
    since_commit = 0

    for hf_path in all_files:
        fname = hf_path.rsplit("/", 1)[-1]
        if fname in done:
            continue
        if cursor["current_file"] != fname:
            cursor.update(current_file=fname, current_file_rows_scanned=0,
                          current_file_docs_matched=0, current_file_tokens_done=0)

        rows = iter_rows(hf_path, cursor["current_file_rows_scanned"])
        stop = None
        while True:
            if _shutdown:
                stop = "signal"
                break
            if scan_limit is not None and (cursor["rows_scanned_in_completed_files"]
                                           + cursor["current_file_rows_scanned"]) >= scan_limit:
                stop = "limit"
                break
            try:
                lang, text = next(rows)
            except StopIteration:
                break
            except Exception as exc:          # network: commit what was read, then retry
                print(f"  [{fname}] read error: {exc!r} — committing and exiting; "
                      f"restart with --resume", flush=True)
                stop = "error"
                break
            cursor["current_file_rows_scanned"] += 1
            since_commit += 1
            if lang == TARGET_LANG:
                tokens = tokenize(text or "")
                batch.add_document(tokens)
                cursor["current_file_docs_matched"] += 1
                cursor["current_file_tokens_done"] += len(tokens)
            if since_commit >= COMMIT_EVERY:
                batch.commit(cursor)
                since_commit = 0
                scanned = (cursor["rows_scanned_in_completed_files"]
                           + cursor["current_file_rows_scanned"])
                matched = cursor["docs_in_completed_files"] + cursor["current_file_docs_matched"]
                elapsed = time.time() - global_start
                print(f"  [{fname}] {cursor['current_file_rows_scanned']:,} scanned | "
                      f"{matched:,} ro docs ({100 * matched / max(scanned, 1):.2f}%) | "
                      f"{scanned / elapsed if elapsed > 0 else 0:.0f} rows/s", flush=True)

        if stop is not None:
            batch.commit(cursor)
            conn.close()
            if stop == "limit":
                print(f"\nScan limit of {scan_limit:,} rows reached.")
                return 0
            return 1

        cursor["completed_files"].append(fname)
        cursor["rows_scanned_in_completed_files"] += cursor["current_file_rows_scanned"]
        cursor["docs_in_completed_files"] += cursor["current_file_docs_matched"]
        cursor["tokens_in_completed_files"] += cursor["current_file_tokens_done"]
        cursor.update(current_file=None, current_file_rows_scanned=0,
                      current_file_docs_matched=0, current_file_tokens_done=0)
        done.add(fname)
        batch.commit(cursor)
        since_commit = 0
        print(f"  [{fname}] complete — {len(done)}/{len(all_files)} files, "
              f"{cursor['docs_in_completed_files']:,} ro docs, "
              f"{(time.time() - global_start) / 3600:.1f}h elapsed\n", flush=True)

    batch.commit(cursor, complete=True)
    print(f"\nDone: {cursor['rows_scanned_in_completed_files']:,} rows scanned, "
          f"{cursor['docs_in_completed_files']:,} ro docs, "
          f"{cursor['tokens_in_completed_files']:,} tokens", flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
