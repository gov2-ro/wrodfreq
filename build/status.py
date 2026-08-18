#!/usr/bin/env python3
"""Point-in-time status of the wROdfreq build (spec §4: oțios's status.py,
"keep the shape"). Read-only, safe to run anytime — including against a db a
live ingester is actively writing to, and from a different shell/SSH session
than the one running the ingest.

`sources.total_docs`/`total_tokens` are as fresh as the ingester's last
flush (every COMMIT_EVERY docs — a few seconds of lag at most). A source
flips to `status = 'completed'` only once its full corpus is exhausted.

Usage:
    python build/status.py [--db PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect

CHECKPOINT_DIR = Path("data/checkpoints")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    if not args.db.exists():
        print(f"{args.db} does not exist yet — no ingester has run")
        return 1

    conn = connect(args.db)
    rows = conn.execute(
        "SELECT source_id, display_name, status, total_docs, total_tokens, ingested_at "
        "FROM sources ORDER BY source_id"
    ).fetchall()
    if not rows:
        print("no sources ingested yet")
        return 0

    for source_id, display_name, status, total_docs, total_tokens, ingested_at in rows:
        print(f"{source_id:8s} {display_name:20s} {status:12s} "
              f"{total_docs:>12,} docs  {total_tokens:>14,} tokens  "
              f"(as of {ingested_at})")
        cp_path = CHECKPOINT_DIR / f"{source_id}_checkpoint.json"
        if status == "in_progress" and cp_path.exists():
            cp = json.loads(cp_path.read_text())
            print(f"           checkpoint: {json.dumps(cp)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
