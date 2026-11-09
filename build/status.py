#!/usr/bin/env python3
"""Point-in-time status of the wROdfreq build (spec §4: oțios's status.py,
"keep the shape"). Read-only, safe to run anytime — including against a db a
live ingester is actively writing to, and from a different shell/SSH session
than the one running the ingest.

`sources.total_docs`/`total_tokens` and the `ingest_state` cursor are committed
together in each batch (ADR-004), so they always agree. A source flips to
`status = 'completed'` only once its full corpus is exhausted. The database is
opened read-only.

Usage:
    python build/status.py [--db PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect_readonly


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only status of the build database")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    if not args.db.exists():
        print(f"{args.db} does not exist yet — no ingester has run")
        return 1

    conn = connect_readonly(args.db)
    rows = conn.execute(
        "SELECT source_id, display_name, status, total_docs, total_tokens, ingested_at "
        "FROM sources ORDER BY source_id"
    ).fetchall()
    if not rows:
        print("no sources ingested yet")
        return 0
    try:
        states = {r[0]: r[1:] for r in conn.execute(
            "SELECT source_id, state, generation, seq, cursor FROM ingest_state")}
    except Exception:
        states = {}

    for source_id, display_name, status, total_docs, total_tokens, ingested_at in rows:
        print(f"{source_id:8s} {display_name:20s} {status:12s} "
              f"{total_docs:>12,} docs  {total_tokens:>14,} tokens  "
              f"(as of {ingested_at})")
        st = states.get(source_id)
        if st is None:
            print("           ingest_state: none (legacy counts — see build/adopt_legacy.py)")
            continue
        state, generation, seq, cursor = st
        print(f"           ingest_state: {state}, generation {generation[:12]}, seq {seq}")
        if state == "in_progress":
            text = cursor if len(cursor) < 300 else cursor[:300] + "…"
            print(f"           cursor: {text}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
