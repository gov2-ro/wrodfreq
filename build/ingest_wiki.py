#!/usr/bin/env python3
"""Stage 1 (wiki source) — ingest Romanian Wikipedia into `source_counts`.

M1 of docs/wrodfreq-spec.md §13: proves the honest-denominator tokenizer works
before any multi-day job (CulturaX, M2) runs. Wikipedia RO is ~80M tokens —
small enough to load non-streaming via the `wikimedia/wikipedia` HF dataset
(a preprocessed mirror of the Wikimedia dumps oțios's archive/download_wikipedia_ro.py
already used successfully). Loading it non-streaming gives an indexable, Arrow
-backed Dataset, so resume is "start at row N again" — no need for oțios's
`HfFileSystem` + parquet-row-group workaround, which exists specifically to
dodge the `datasets` streaming `.skip(N)` cycling bug on much larger corpora.

Every token counted, no DEX filter (spec §3.1); no length filter on tokens
(spec §3.2) — see wrodfreq/tokenizer.py.

Usage:
    python build/ingest_wiki.py               # full run
    python build/ingest_wiki.py --test        # first 1000 articles, into a new experiment db
    python build/ingest_wiki.py --limit N     # stop at absolute article N (across sessions)
    python build/ingest_wiki.py --resume      # continue the generation stored in the db

Restart loop (stop automatically on success):
    while true; do
        python -u build/ingest_wiki.py --resume
        [ $? -eq 0 ] && break
        echo "restarting in 15s..." && sleep 15
    done

Output: data/wrodfreq.db, source_id='wiki'. Progress: the `ingest_state` table in the
same database, committed with the counts (ADR-004, build/ingest_state.py). A fresh run
refuses a database that already holds wiki counts.
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

SOURCE_ID     = "wiki"
DISPLAY_NAME  = "Wikipedia RO"
URL           = "https://huggingface.co/datasets/wikimedia/wikipedia"
LICENCE       = "CC BY-SA 4.0"
REGISTER      = "encyclopedic"
PERIOD        = "contemporary"
PERIOD_NOTE   = "2023-11-01 Wikimedia dump snapshot"

HF_DATASET    = "wikimedia/wikipedia"
HF_CONFIG     = "20231101.ro"

COMMIT_EVERY  = 2000  # documents between commits
CURSOR_VERSION = 1

SPEC = SourceSpec(
    source_id=SOURCE_ID, display_name=DISPLAY_NAME, url=URL, licence=LICENCE,
    register=REGISTER, period=PERIOD, period_note=PERIOD_NOTE,
    cursor_version=CURSOR_VERSION,
    policy={"policy_version": 1, "dataset": HF_DATASET, "config": HF_CONFIG,
            "text_field": "text", "unit": "article"},
)

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    print(f"\n[{datetime.now()}] signal {sig} — committing and exiting after this document",
          flush=True)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGHUP, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def empty_cursor() -> dict:
    return {"next_row": 0, "docs_done": 0, "tokens_done": 0}


def open_dataset():
    """(indexable rows with a 'text' field, input manifest). Tests replace this."""
    from datasets import load_dataset
    ds = load_dataset(HF_DATASET, HF_CONFIG, split="train")
    manifest = {"dataset": HF_DATASET, "config": HF_CONFIG, "num_rows": len(ds),
                "fingerprint": getattr(ds, "_fingerprint", None)}
    return ds, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Wikipedia RO into source_counts")
    parser.add_argument("--test", action="store_true",
                         help="First 1000 articles, into a new experiment database")
    parser.add_argument("--limit", type=int,
                         help="Stop at absolute article N (counted across sessions)")
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
    loaded: dict = {}

    def manifest() -> dict:
        print(f"Loading {HF_DATASET} ({HF_CONFIG}) ...", flush=True)
        loaded["ds"], m = open_dataset()
        return m

    try:
        session = begin(conn, SPEC, resume=args.resume, initial_cursor=empty_cursor(),
                        input_manifest=manifest)
    except IngestRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        conn.close()
        return 2
    if session.finished:
        conn.close()
        return 0
    ds = loaded["ds"]
    total_rows = len(ds)
    cursor = dict(session.cursor)
    end_row = total_rows if limit is None else min(total_rows, limit)
    print(f"  {total_rows:,} articles; starting at row {cursor['next_row']:,}", flush=True)

    batch = Batch(session)
    start = time.time()
    session_docs = 0
    for i in range(cursor["next_row"], end_row):
        if _shutdown:
            break
        tokens = tokenize(ds[i]["text"] or "")
        batch.add_document(tokens)
        cursor["next_row"] = i + 1
        cursor["docs_done"] += 1
        cursor["tokens_done"] += len(tokens)
        session_docs += 1
        if batch.ndocs >= COMMIT_EVERY:
            batch.commit(cursor)
            elapsed = time.time() - start
            print(f"  {cursor['next_row']:,}/{total_rows:,} rows | "
                  f"{cursor['tokens_done']:,} tokens | "
                  f"{session_docs / elapsed if elapsed > 0 else 0:.0f} docs/s", flush=True)

    completed = cursor["next_row"] >= total_rows
    if batch.ndocs or completed:
        batch.commit(cursor, complete=completed)

    print(f"\n{'Done' if completed else 'Stopped'}: {cursor['docs_done']:,} docs, "
          f"{cursor['tokens_done']:,} tokens in {time.time() - start:.0f}s", flush=True)
    conn.close()
    return 0 if (completed or not _shutdown) else 1


if __name__ == "__main__":
    sys.exit(main())
