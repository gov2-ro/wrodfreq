#!/usr/bin/env python3
"""Stage 1 (web source) — ingest CulturaX RO into `source_counts`.

The backbone job, M2 of docs/wrodfreq-spec.md §13 — ~40B raw tokens across 64
parquet shards. This is the multi-day job; it survives repeated SIGKILL
restarts via per-parquet-file checkpointing with row-group-level resume,
lifted wholesale from oțios's `process_culturax.py` (spec §4), including the
HfFileSystem + pyarrow row-group reader that avoids the `datasets` streaming
`.skip(N)` cycling bug (triggers when N exceeds the dataset size — irrelevant
for Wikipedia's single small dataset in ingest_wiki.py, unavoidable here).

Two departures from process_culturax.py, both spec-mandated (§3):
  - No DEX word filter. Every token is counted (§3.1) — open vocabulary.
  - No `len(t) > 2` filter. wrodfreq.tokenizer already excludes it (§3.2).

Cursor (the `ingest_state.cursor` JSON, committed with the counts — ADR-004):
  completed_files           — filenames fully processed
  current_file              — filename in progress (or null)
  current_file_rows_done    — rows of current_file counted so far
  current_file_tokens_done  — tokens of current_file counted so far
  docs_in_completed_files   — total docs across all completed files
  tokens_in_completed_files — total tokens across all completed files

On SIGKILL the uncommitted batch (at most COMMIT_EVERY rows) is lost and re-read on
the next run; it was never applied, so nothing is counted twice. On
SIGTERM/SIGHUP the current batch is committed before exit.

Usage:
    python build/ingest_web.py              # full run (hours to days)
    python build/ingest_web.py --test       # first 1000 docs, into a new experiment db
    python build/ingest_web.py --limit N    # stop at absolute document N (across sessions)
    python build/ingest_web.py --resume     # continue the generation stored in the db

Restart loop (stop automatically on success) — this is a multi-hour-plus job,
run it under this loop, not as a single foreground command:
    while true; do
        python -u build/ingest_web.py --resume
        [ $? -eq 0 ] && break
        echo "[$(date)] restarting in 15s..." && sleep 15
    done

Output: data/wrodfreq.db, source_id='web'.
"""

from __future__ import annotations

import argparse
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

SOURCE_ID     = "web"
DISPLAY_NAME  = "CulturaX RO"
URL           = "https://huggingface.co/datasets/uonlp/CulturaX"
LICENCE       = "mixed — mC4 (ODC-BY) + OSCAR (CC0) provenance, see dataset card"
REGISTER      = "web"
PERIOD        = "contemporary"
PERIOD_NOTE   = None

HF_PARQUET_DIR = "datasets/uonlp/CulturaX/ro"

COMMIT_EVERY  = 5_000  # documents between commits
CURSOR_VERSION = 1

SPEC = SourceSpec(
    source_id=SOURCE_ID, display_name=DISPLAY_NAME, url=URL, licence=LICENCE,
    register=REGISTER, period=PERIOD, period_note=PERIOD_NOTE,
    cursor_version=CURSOR_VERSION,
    policy={"policy_version": 1, "repo": HF_PARQUET_DIR, "text_column": "text",
            "unit": "row"},
)

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    print(f"\n[{datetime.now()}] signal {sig} — committing and exiting after current batch",
          flush=True)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGHUP, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def empty_cursor() -> dict:
    return {
        "completed_files": [],
        "current_file": None,
        "current_file_rows_done": 0,
        "current_file_tokens_done": 0,
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


def iter_texts(hf_path: str, start_row: int):
    """Yield the `text` of each row of one remote parquet file from `start_row` on.

    Reads row groups with pyarrow over HfFileSystem (not `datasets` streaming, whose
    `.skip(N)` cycles — see the module docstring). A network error propagates.
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
            texts = pf.read_row_group(g).column("text").to_pylist()
            skip = max(0, start_row - rows_before)
            rows_before += rg_rows
            yield from texts[skip:]


def input_manifest() -> dict:
    files = list_parquet_files()
    return {"repo": HF_PARQUET_DIR, "files": [f.rsplit("/", 1)[-1] for f in files]}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest CulturaX RO into source_counts")
    parser.add_argument("--test", action="store_true",
                         help="First 1000 documents, into a new experiment database")
    parser.add_argument("--limit", type=int,
                         help="Stop at absolute document N (counted across sessions)")
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
    limit = 1000 if args.test else args.limit

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

    for hf_path in all_files:
        fname = hf_path.rsplit("/", 1)[-1]
        if fname in done:
            continue
        if cursor["current_file"] != fname:
            cursor.update(current_file=fname, current_file_rows_done=0,
                          current_file_tokens_done=0)

        texts = iter_texts(hf_path, cursor["current_file_rows_done"])
        stop = None
        while True:
            if _shutdown:
                stop = "signal"
                break
            if limit is not None and (cursor["docs_in_completed_files"]
                                      + cursor["current_file_rows_done"]) >= limit:
                stop = "limit"
                break
            try:
                text = next(texts)
            except StopIteration:
                break
            except Exception as exc:          # network: commit what was read, then retry
                print(f"  [{fname}] read error: {exc!r} — committing and exiting; "
                      f"restart with --resume", flush=True)
                stop = "error"
                break
            tokens = tokenize(text or "")
            batch.add_document(tokens)
            cursor["current_file_rows_done"] += 1
            cursor["current_file_tokens_done"] += len(tokens)
            if batch.ndocs >= COMMIT_EVERY:
                batch.commit(cursor)
                total = cursor["docs_in_completed_files"] + cursor["current_file_rows_done"]
                elapsed = time.time() - global_start
                print(f"  [{fname}] {cursor['current_file_rows_done']:,} rows | "
                      f"{total:,} total docs | "
                      f"{total / elapsed if elapsed > 0 else 0:.0f} docs/s", flush=True)

        if stop is not None:
            batch.commit(cursor)
            conn.close()
            if stop == "limit":
                print(f"\nLimit of {limit:,} docs reached.")
                return 0
            return 1

        cursor["completed_files"].append(fname)
        cursor["docs_in_completed_files"] += cursor["current_file_rows_done"]
        cursor["tokens_in_completed_files"] += cursor["current_file_tokens_done"]
        cursor.update(current_file=None, current_file_rows_done=0, current_file_tokens_done=0)
        done.add(fname)
        batch.commit(cursor)
        print(f"  [{fname}] complete — {len(done)}/{len(all_files)} files, "
              f"{cursor['docs_in_completed_files']:,} docs, "
              f"{(time.time() - global_start) / 3600:.1f}h elapsed\n", flush=True)

    batch.commit(cursor, complete=True)
    print(f"\nDone: {cursor['docs_in_completed_files']:,} docs, "
          f"{cursor['tokens_in_completed_files']:,} tokens", flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
