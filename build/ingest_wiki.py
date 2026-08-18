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
    python build/ingest_wiki.py --test        # first 1000 articles, no resume
    python build/ingest_wiki.py --limit N     # stop after N total articles
    python build/ingest_wiki.py --resume      # pick up from checkpoint

Restart loop (stop automatically on success):
    while true; do
        python -u build/ingest_wiki.py --resume
        [ $? -eq 0 ] && break
        echo "restarting in 15s..." && sleep 15
    done

Output: data/wrodfreq.db, source_id='wiki'. Checkpoint: data/checkpoints/wiki_checkpoint.json
"""

from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect
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

CHECKPOINT    = Path("data/checkpoints/wiki_checkpoint.json")
COMMIT_EVERY  = 2000  # documents between flushes

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    print(f"\n[{datetime.now()}] signal {sig} — flushing and exiting after this document",
          flush=True)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGHUP, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _empty_checkpoint() -> dict:
    return {"next_row": 0, "docs_done": 0, "tokens_done": 0}


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


def flush(conn: sqlite3.Connection, occ: Counter, docs: Counter,
          cp: dict, status: str) -> None:
    if occ:
        conn.executemany(
            """
            INSERT INTO source_counts (word, source_id, occurrences, documents)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(word, source_id) DO UPDATE SET
                occurrences = occurrences + excluded.occurrences,
                documents   = documents   + excluded.documents
            """,
            [(w, SOURCE_ID, c, docs[w]) for w, c in occ.items()],
        )
        occ.clear()
        docs.clear()
    conn.execute(
        """
        UPDATE sources SET total_tokens = ?, total_docs = ?, status = ?,
                            ingested_at = ?
        WHERE source_id = ?
        """,
        (cp["tokens_done"], cp["docs_done"], status,
         datetime.now(timezone.utc).isoformat(), SOURCE_ID),
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                         help="Process first 1000 articles only, no resume")
    parser.add_argument("--limit", type=int,
                         help="Stop after N total articles")
    parser.add_argument("--resume", action="store_true",
                         help="Resume from checkpoint")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    limit = 1000 if args.test else args.limit

    print(f"Loading {HF_DATASET} ({HF_CONFIG}) ...", flush=True)
    from datasets import load_dataset
    ds = load_dataset(HF_DATASET, HF_CONFIG, split="train")
    total_rows = len(ds)
    print(f"  {total_rows:,} articles", flush=True)

    conn = connect(args.db)
    ensure_source_row(conn)

    cp = load_checkpoint() if args.resume else _empty_checkpoint()
    end_row = total_rows if limit is None else min(total_rows, limit)
    if cp["next_row"] >= end_row:
        print(f"Nothing to do: checkpoint at row {cp['next_row']:,}, target {end_row:,}")
        return 0
    if args.resume and cp["next_row"]:
        print(f"Resuming at row {cp['next_row']:,}/{total_rows:,}", flush=True)

    occ: Counter = Counter()
    docs: Counter = Counter()
    start = time.time()
    since_flush = 0
    session_docs = 0

    for i in range(cp["next_row"], end_row):
        if _shutdown:
            break

        text = ds[i]["text"] or ""
        tokens = tokenize(text)
        occ.update(tokens)
        for w in set(tokens):
            docs[w] += 1

        cp["next_row"] = i + 1
        cp["docs_done"] += 1
        cp["tokens_done"] += len(tokens)
        since_flush += 1
        session_docs += 1

        if since_flush >= COMMIT_EVERY:
            flush(conn, occ, docs, cp, "in_progress")
            save_checkpoint(cp)
            since_flush = 0
            elapsed = time.time() - start
            rate = session_docs / elapsed if elapsed > 0 else 0
            print(f"  {cp['next_row']:,}/{total_rows:,} rows | "
                  f"{cp['tokens_done']:,} tokens | {rate:.0f} docs/s | "
                  f"{elapsed:.0f}s elapsed", flush=True)

    completed = cp["next_row"] >= total_rows
    status = "completed" if completed else "in_progress"
    flush(conn, occ, docs, cp, status)
    save_checkpoint(cp)

    elapsed = time.time() - start
    print(f"\n{'Done' if completed else 'Stopped'}: {cp['docs_done']:,} docs, "
          f"{cp['tokens_done']:,} tokens in {elapsed:.0f}s", flush=True)

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
            print(f"  {word:20s} {occurrences:>10,}  ({documents:,} docs)")

    conn.close()
    return 0 if (completed or not _shutdown) else 1


if __name__ == "__main__":
    sys.exit(main())
