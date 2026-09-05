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
    python build/ingest_news.py --test       # first 200k scanned rows of shard 0, no resume
    python build/ingest_news.py --limit N    # stop after N scanned rows (not matched docs —
                                              # match rate is unknown up front)
    python build/ingest_news.py --resume     # pick up from checkpoint

Restart loop (stop automatically on success):
    while true; do
        python -u build/ingest_news.py --resume
        [ $? -eq 0 ] && break
        echo "[$(date)] restarting in 15s..." && sleep 15
    done

Output: data/wrodfreq.db, source_id='news'. Checkpoint: data/checkpoints/news_checkpoint.json
"""

from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect
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

CHECKPOINT    = Path("data/checkpoints/news_checkpoint.json")
COMMIT_EVERY  = 50_000  # rows *scanned* between DB commits (match rate is low and uneven)

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    print(f"\n[{datetime.now()}] signal {sig} — flushing and exiting after current batch",
          flush=True)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGHUP, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def ensure_source_row(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO sources (source_id, display_name, url, licence, register,
                              period, period_note, total_tokens, total_docs, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'in_progress')
        ON CONFLICT(source_id) DO NOTHING
        """,
        (SOURCE_ID, DISPLAY_NAME, URL, LICENCE, REGISTER, PERIOD, PERIOD_NOTE),
    )
    conn.commit()


def flush(conn: sqlite3.Connection, word_counts: dict, doc_counts: dict,
          total_tokens: int, total_docs: int, status: str) -> None:
    if word_counts:
        conn.executemany(
            """
            INSERT INTO source_counts (word, source_id, occurrences, documents)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(word, source_id) DO UPDATE SET
                occurrences = occurrences + excluded.occurrences,
                documents   = documents   + excluded.documents
            """,
            [(w, SOURCE_ID, c, doc_counts[w]) for w, c in word_counts.items()],
        )
        word_counts.clear()
        doc_counts.clear()
    conn.execute(
        """
        UPDATE sources SET total_tokens = ?, total_docs = ?, status = ?,
                            ingested_at = ?
        WHERE source_id = ?
        """,
        (total_tokens, total_docs, status,
         datetime.now(timezone.utc).isoformat(), SOURCE_ID),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def _empty_checkpoint() -> dict:
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


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        try:
            cp = _empty_checkpoint()
            cp.update(json.loads(CHECKPOINT.read_text()))
            return cp
        except (json.JSONDecodeError, ValueError):
            print("warning: corrupted checkpoint, starting fresh")
    return _empty_checkpoint()


def save_checkpoint(cp: dict) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT.with_suffix(".tmp")
    tmp.write_text(json.dumps(cp, indent=2))
    tmp.replace(CHECKPOINT)


# ---------------------------------------------------------------------------
# Parquet processing
# ---------------------------------------------------------------------------

def list_parquet_files() -> list[str]:
    from huggingface_hub import HfFileSystem
    fs = HfFileSystem()
    paths = fs.ls(HF_PARQUET_DIR, detail=False)
    return sorted(p for p in paths if p.endswith(".parquet"))


def process_file(
    hf_path: str,
    conn: sqlite3.Connection,
    cp: dict,
    start_row: int,
    scan_limit: int | None,
    global_start: float,
) -> tuple[int, int, bool]:
    """Process one remote parquet file starting at start_row (scan position).

    `scan_limit` bounds total *rows scanned* across the whole run (not
    matched Romanian docs — the match rate is unknown up front, so bounding
    by matches could scan an entire multi-GB shard on a --test run and never
    stop). Updates cp['current_file_rows_scanned'] / '..._docs_matched' /
    '..._tokens_done' at each COMMIT_EVERY boundary and at function exit.
    Returns (session_rows_scanned, session_docs_matched, shutdown_requested).
    """
    import pyarrow.parquet as pq
    from huggingface_hub import HfFileSystem

    global _shutdown
    fname = hf_path.rsplit("/", 1)[-1]
    fs = HfFileSystem()

    word_counts: dict = defaultdict(int)
    doc_counts: dict = defaultdict(int)
    session_rows = 0
    session_docs = 0
    session_tokens = 0
    tokens_base = cp["current_file_tokens_done"]
    docs_base = cp["current_file_docs_matched"]

    def checkpoint_progress(status: str) -> None:
        cp["current_file_rows_scanned"] = start_row + session_rows
        cp["current_file_docs_matched"] = docs_base + session_docs
        cp["current_file_tokens_done"] = tokens_base + session_tokens
        total_tokens = cp["tokens_in_completed_files"] + cp["current_file_tokens_done"]
        total_docs = cp["docs_in_completed_files"] + cp["current_file_docs_matched"]
        flush(conn, word_counts, doc_counts, total_tokens, total_docs, status)
        save_checkpoint(cp)

    with fs.open(hf_path, "rb") as fh:
        pf = pq.ParquetFile(fh)
        num_groups = pf.metadata.num_row_groups
        total_rows = pf.metadata.num_rows

        start_group = num_groups
        skip_in_group = 0
        rows_before = 0
        for g in range(num_groups):
            rg_rows = pf.metadata.row_group(g).num_rows
            if rows_before + rg_rows <= start_row:
                rows_before += rg_rows
            else:
                start_group = g
                skip_in_group = start_row - rows_before
                break

        if start_row == 0:
            print(f"  [{fname}] {total_rows:,} rows, {num_groups} groups", flush=True)
        elif start_group < num_groups:
            print(f"  [{fname}] resuming row {start_row:,}/{total_rows:,} "
                  f"(group {start_group}/{num_groups})", flush=True)

        for g in range(start_group, num_groups):
            if _shutdown:
                checkpoint_progress("in_progress")
                return session_rows, session_docs, True

            try:
                cols = pf.read_row_group(g, columns=[LANG_COLUMN, TEXT_COLUMN])
                langs = cols.column(LANG_COLUMN).to_pylist()
                texts = cols.column(TEXT_COLUMN).to_pylist()
            except Exception as exc:
                checkpoint_progress("in_progress")
                print(f"  [{fname}] network error at group {g}: {exc}", flush=True)
                print(f"  checkpoint saved at {start_row + session_rows:,} rows scanned "
                      f"— restart with --resume", flush=True)
                return session_rows, session_docs, True
            if g == start_group and skip_in_group:
                langs = langs[skip_in_group:]
                texts = texts[skip_in_group:]

            for lang, text in zip(langs, texts):
                if _shutdown:
                    checkpoint_progress("in_progress")
                    return session_rows, session_docs, True

                total_scanned_so_far = (
                    cp["rows_scanned_in_completed_files"] + start_row + session_rows
                )
                if scan_limit is not None and total_scanned_so_far >= scan_limit:
                    checkpoint_progress("in_progress")
                    return session_rows, session_docs, False

                session_rows += 1

                if lang == TARGET_LANG:
                    tokens = tokenize(text or "")
                    session_tokens += len(tokens)

                    doc_words: set = set()
                    for tok in tokens:
                        word_counts[tok] += 1
                        doc_words.add(tok)
                    for w in doc_words:
                        doc_counts[w] += 1
                    session_docs += 1

                if session_rows % COMMIT_EVERY == 0:
                    checkpoint_progress("in_progress")
                    elapsed = time.time() - global_start
                    total_scanned = (
                        cp["rows_scanned_in_completed_files"] + cp["current_file_rows_scanned"]
                    )
                    total_matched = cp["docs_in_completed_files"] + cp["current_file_docs_matched"]
                    rate = total_scanned / elapsed if elapsed > 0 else 0
                    match_pct = 100 * total_matched / total_scanned if total_scanned else 0
                    print(f"  [{fname}] {cp['current_file_rows_scanned']:,}/{total_rows:,} scanned"
                          f" | {total_matched:,} ro docs ({match_pct:.2f}%) | "
                          f"{rate:.0f} rows/s", flush=True)

    checkpoint_progress("in_progress")
    return session_rows, session_docs, False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    global CHECKPOINT
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                         help="Scan first 200k rows of shard 0 only, no resume")
    parser.add_argument("--limit", type=int,
                         help="Stop after N total rows scanned (not matched docs)")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    if args.test:
        # --test already means "no resume"; also keep it from writing progress
        # into the real checkpoint, which a later --resume would trust even
        # though this run's counts went into --db, not the production DB.
        CHECKPOINT = CHECKPOINT.with_name(CHECKPOINT.stem + ".test.json")

    scan_limit = 200_000 if args.test else args.limit

    conn = connect(args.db)
    ensure_source_row(conn)

    cp = load_checkpoint() if args.resume else _empty_checkpoint()
    completed_set = set(cp["completed_files"])
    total_scanned = cp["rows_scanned_in_completed_files"] + cp["current_file_rows_scanned"]
    if args.resume and total_scanned:
        print(f"Resuming: {len(completed_set)} files done, {total_scanned:,} rows scanned, "
              f"{cp['docs_in_completed_files']:,} ro docs so far")

    print("Listing CC-News parquet files...")
    all_files = list_parquet_files()
    remaining = sum(1 for f in all_files if f.rsplit("/", 1)[-1] not in completed_set)
    print(f"  {len(all_files)} total shards, {remaining} remaining\n")

    global_start = time.time()

    for hf_path in all_files:
        fname = hf_path.rsplit("/", 1)[-1]
        if fname in completed_set:
            continue

        if cp["current_file"] == fname:
            start_row = cp["current_file_rows_scanned"]
        else:
            start_row = 0
            cp["current_file"] = fname
            cp["current_file_rows_scanned"] = 0
            cp["current_file_docs_matched"] = 0
            cp["current_file_tokens_done"] = 0
            save_checkpoint(cp)

        _rows, _docs, shutdown = process_file(
            hf_path, conn, cp, start_row, scan_limit, global_start
        )

        if shutdown:
            conn.close()
            return 1

        if scan_limit is not None:
            total = cp["rows_scanned_in_completed_files"] + cp["current_file_rows_scanned"]
            if total >= scan_limit:
                print(f"\nScan limit of {scan_limit:,} rows reached.")
                conn.close()
                return 0

        cp["completed_files"].append(fname)
        cp["rows_scanned_in_completed_files"] += cp["current_file_rows_scanned"]
        cp["docs_in_completed_files"] += cp["current_file_docs_matched"]
        cp["tokens_in_completed_files"] += cp["current_file_tokens_done"]
        cp["current_file"] = None
        cp["current_file_rows_scanned"] = 0
        cp["current_file_docs_matched"] = 0
        cp["current_file_tokens_done"] = 0
        completed_set.add(fname)
        save_checkpoint(cp)

        elapsed = time.time() - global_start
        print(f"  [{fname}] complete — {len(completed_set)}/{len(all_files)} files, "
              f"{cp['docs_in_completed_files']:,} ro docs, {elapsed / 3600:.1f}h elapsed\n",
              flush=True)

    # All files done
    elapsed = time.time() - global_start
    flush(conn, defaultdict(int), defaultdict(int),
          cp["tokens_in_completed_files"], cp["docs_in_completed_files"], "completed")

    unique_words = conn.execute(
        "SELECT COUNT(*) FROM source_counts WHERE source_id = ?", (SOURCE_ID,)
    ).fetchone()[0]
    print(f"\nDone: {cp['rows_scanned_in_completed_files']:,} rows scanned, "
          f"{cp['docs_in_completed_files']:,} ro docs, "
          f"{cp['tokens_in_completed_files']:,} tokens in "
          f"{elapsed:.0f}s ({elapsed / 3600:.1f}h)")
    print(f"Unique words: {unique_words:,}")

    rows = conn.execute(
        """
        SELECT word, occurrences, documents FROM source_counts
        WHERE source_id = ? ORDER BY occurrences DESC LIMIT 20
        """,
        (SOURCE_ID,),
    ).fetchall()
    if rows:
        print("\nTop 20 by occurrence:")
        for word, occurrences, documents in rows:
            print(f"  {word:20s} {occurrences:>12,}  ({documents:,} docs)")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
