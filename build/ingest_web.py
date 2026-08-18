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

Checkpoint schema (data/checkpoints/web_checkpoint.json):
  completed_files           — filenames fully processed
  current_file              — filename in progress (or null)
  current_file_rows_done    — rows of current_file counted so far
  current_file_tokens_done  — tokens of current_file counted so far
  docs_in_completed_files   — total docs across all completed files
  tokens_in_completed_files — total tokens across all completed files

On SIGKILL the last COMMIT_EVERY rows are lost (re-processed on next run). On
SIGTERM/SIGHUP the current batch is flushed cleanly before exit.

Usage:
    python build/ingest_web.py              # full run (hours to days)
    python build/ingest_web.py --test       # first 1000 docs, no resume
    python build/ingest_web.py --limit N    # stop after N total docs
    python build/ingest_web.py --resume     # pick up from checkpoint

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

SOURCE_ID     = "web"
DISPLAY_NAME  = "CulturaX RO"
URL           = "https://huggingface.co/datasets/uonlp/CulturaX"
LICENCE       = "mixed — mC4 (ODC-BY) + OSCAR (CC0) provenance, see dataset card"
REGISTER      = "web"
PERIOD        = "contemporary"
PERIOD_NOTE   = None

HF_PARQUET_DIR = "datasets/uonlp/CulturaX/ro"

CHECKPOINT    = Path("data/checkpoints/web_checkpoint.json")
COMMIT_EVERY  = 5_000  # documents between DB commits

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
        "current_file_rows_done": 0,
        "current_file_tokens_done": 0,
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
    limit: int | None,
    global_start: float,
) -> tuple[int, int, bool]:
    """Process one remote parquet file starting at start_row.

    Updates cp['current_file_rows_done'] / cp['current_file_tokens_done'] to
    absolute values (this file, across all sessions) at each COMMIT_EVERY
    boundary and at function exit. Returns (session_docs, session_tokens,
    shutdown_requested).
    """
    import pyarrow.parquet as pq
    from huggingface_hub import HfFileSystem

    global _shutdown
    fname = hf_path.rsplit("/", 1)[-1]
    fs = HfFileSystem()

    word_counts: dict = defaultdict(int)
    doc_counts: dict = defaultdict(int)
    session_docs = 0
    session_tokens = 0
    tokens_base = cp["current_file_tokens_done"]

    def checkpoint_progress(status: str) -> None:
        cp["current_file_rows_done"] = start_row + session_docs
        cp["current_file_tokens_done"] = tokens_base + session_tokens
        total_tokens = cp["tokens_in_completed_files"] + cp["current_file_tokens_done"]
        total_docs = cp["docs_in_completed_files"] + cp["current_file_rows_done"]
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
                return session_docs, session_tokens, True

            try:
                texts = pf.read_row_group(g).column("text").to_pylist()
            except Exception as exc:
                checkpoint_progress("in_progress")
                print(f"  [{fname}] network error at group {g}: {exc}", flush=True)
                print(f"  checkpoint saved at {start_row + session_docs:,} rows "
                      f"— restart with --resume", flush=True)
                return session_docs, session_tokens, True
            if g == start_group and skip_in_group:
                texts = texts[skip_in_group:]

            for text in texts:
                if _shutdown:
                    checkpoint_progress("in_progress")
                    return session_docs, session_tokens, True

                total_so_far = cp["docs_in_completed_files"] + start_row + session_docs
                if limit is not None and total_so_far >= limit:
                    checkpoint_progress("in_progress")
                    return session_docs, session_tokens, False

                tokens = tokenize(text or "")
                session_tokens += len(tokens)

                doc_words: set = set()
                for tok in tokens:
                    word_counts[tok] += 1
                    doc_words.add(tok)
                for w in doc_words:
                    doc_counts[w] += 1
                session_docs += 1

                if session_docs % COMMIT_EVERY == 0:
                    checkpoint_progress("in_progress")
                    elapsed = time.time() - global_start
                    total_done = cp["docs_in_completed_files"] + cp["current_file_rows_done"]
                    rate = total_done / elapsed if elapsed > 0 else 0
                    print(f"  [{fname}] {cp['current_file_rows_done']:,}/{total_rows:,}"
                          f" | {total_done:,} total docs | {rate:.0f} docs/s", flush=True)

    checkpoint_progress("in_progress")
    return session_docs, session_tokens, False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                         help="Process first 1000 documents only, no resume")
    parser.add_argument("--limit", type=int, help="Stop after N total documents")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    limit = 1000 if args.test else args.limit

    conn = connect(args.db)
    ensure_source_row(conn)

    cp = load_checkpoint() if args.resume else _empty_checkpoint()
    completed_set = set(cp["completed_files"])
    total_done = cp["docs_in_completed_files"] + cp["current_file_rows_done"]
    if args.resume and total_done:
        print(f"Resuming: {len(completed_set)} files done, {total_done:,} docs so far")

    print("Listing CulturaX RO parquet files...")
    all_files = list_parquet_files()
    remaining = sum(1 for f in all_files if f.rsplit("/", 1)[-1] not in completed_set)
    print(f"  {len(all_files)} total shards, {remaining} remaining\n")

    global_start = time.time()

    for hf_path in all_files:
        fname = hf_path.rsplit("/", 1)[-1]
        if fname in completed_set:
            continue

        if cp["current_file"] == fname:
            start_row = cp["current_file_rows_done"]
        else:
            start_row = 0
            cp["current_file"] = fname
            cp["current_file_rows_done"] = 0
            cp["current_file_tokens_done"] = 0
            save_checkpoint(cp)

        _docs, _tokens, shutdown = process_file(hf_path, conn, cp, start_row, limit, global_start)

        if shutdown:
            conn.close()
            return 1

        if limit is not None:
            total = cp["docs_in_completed_files"] + cp["current_file_rows_done"]
            if total >= limit:
                print(f"\nLimit of {limit:,} docs reached.")
                conn.close()
                return 0

        cp["completed_files"].append(fname)
        cp["docs_in_completed_files"] += cp["current_file_rows_done"]
        cp["tokens_in_completed_files"] += cp["current_file_tokens_done"]
        cp["current_file"] = None
        cp["current_file_rows_done"] = 0
        cp["current_file_tokens_done"] = 0
        completed_set.add(fname)
        save_checkpoint(cp)

        elapsed = time.time() - global_start
        print(f"  [{fname}] complete — {len(completed_set)}/{len(all_files)} files, "
              f"{cp['docs_in_completed_files']:,} docs, {elapsed / 3600:.1f}h elapsed\n",
              flush=True)

    # All files done
    elapsed = time.time() - global_start
    flush(conn, defaultdict(int), defaultdict(int),
          cp["tokens_in_completed_files"], cp["docs_in_completed_files"], "completed")

    unique_words = conn.execute(
        "SELECT COUNT(*) FROM source_counts WHERE source_id = ?", (SOURCE_ID,)
    ).fetchone()[0]
    print(f"\nDone: {cp['docs_in_completed_files']:,} docs, "
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
