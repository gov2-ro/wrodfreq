#!/usr/bin/env python3
"""Seal completed legacy source counts under `ingest_state` (ADR-004, brief B7 Phase 1).

Before ADR-004 the ingest cursor lived in JSON files beside the database. Those
files cannot be trusted against the counts: the token migrations moved the
database totals after the files were last written (eu: 84,415,957 in the file,
84,722,606 in the database), and a `--test` run once added its counts to the real
social counts through them. Matching totals alone would not prove that a cursor
belongs to particular counts.

So this step never reads a cursor from a JSON file. For each named source it
requires a completed status and exact conservation (`SUM(occurrences) ==
total_tokens`), then records a `sealed_legacy` state that keeps the **database**
totals, the logical hash of the counts, and the hashes of the old JSON files as
evidence. The raw inputs of the original ingest were not recorded; the state says
so (`unknown`) instead of inventing a fingerprint.

A sealed source resumes as a no-op. Changing its inputs needs a new generation in
an empty staged database. An unfinished or non-conserving source is refused.

Usage:
    python build/adopt_legacy.py --db PATH --source eu --source news ...
    python build/adopt_legacy.py --db PATH --all-completed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_state import STATE_VERSION, canonical, now, sha256_text
from provenance import logical_count_hashes
from wrodfreq.db import connect, db_id

DEFAULT_CHECKPOINT_DIR = ROOT / "data" / "checkpoints"


def legacy_evidence(checkpoint_dir: Path, source_id: str) -> list[dict]:
    """Hashes (and readable totals) of the old JSON files for a source — evidence only."""
    out = []
    for path in sorted(checkpoint_dir.glob(f"{source_id}_checkpoint*.json")):
        entry = {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                 "size": path.stat().st_size}
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            entry["readable"] = False
        else:
            for key in ("tokens_done", "tokens_in_completed_files"):
                if isinstance(data.get(key), int):
                    entry[key] = data[key]
            if isinstance(data.get("parts"), dict):
                entry["parts_tokens_done"] = {
                    k: v.get("tokens_done") for k, v in data["parts"].items() if isinstance(v, dict)}
        out.append(entry)
    return out


def adopt(conn, sources: list[str], checkpoint_dir: Path) -> dict[str, str]:
    """Seal each source; returns {source: outcome}. Refusals do not stop the others."""
    this_db = db_id(conn)
    outcomes: dict[str, str] = {}
    rows = {r[0]: r[1:] for r in conn.execute(
        "SELECT source_id, status, total_tokens, total_docs, period_note FROM sources")}
    existing = {r[0] for r in conn.execute("SELECT source_id FROM ingest_state")}
    todo = []
    for sid in sources:
        if sid not in rows:
            outcomes[sid] = "refused: no such source"
        elif sid in existing:
            outcomes[sid] = "refused: already has ingest_state"
        elif rows[sid][0] != "completed":
            outcomes[sid] = (f"refused: status is {rows[sid][0]} — an unfinished legacy ingest "
                             f"cannot be resumed safely; rebuild it into an empty staged db")
        else:
            todo.append(sid)
    if not todo:
        return outcomes

    print(f"hashing counts of {', '.join(todo)} (one pass over source_counts)...", flush=True)
    hashes = logical_count_hashes(conn, todo)
    for sid in todo:
        status, total_tokens, total_docs, period_note = rows[sid]
        h = hashes[sid]
        if h["sum_occurrences"] != total_tokens:
            outcomes[sid] = (f"refused: SUM(occurrences) {h['sum_occurrences']:,} != "
                             f"total_tokens {total_tokens:,} — rebuild this source")
            continue
        provenance = {
            "adopted_at": now(),
            "adopted_by": "build/adopt_legacy.py",
            "logical_counts": h,
            "total_tokens": total_tokens,
            "total_docs": total_docs,
            "legacy_checkpoints": legacy_evidence(checkpoint_dir, sid),
            "note": ("Database totals are authoritative. Legacy JSON files are evidence "
                     "only; their totals predate the token migrations."),
        }
        manifest = {"status": "unknown",
                    "note": "the raw inputs of the original ingest were not recorded"}
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO ingest_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, STATE_VERSION, "legacy-" + h["sha256"][:24], 0, "sealed_legacy", 0,
                 canonical({"legacy": True}), "unknown-legacy", canonical(manifest),
                 sha256_text(canonical(manifest)),
                 canonical({"status": "unknown", "period_note": period_note}),
                 this_db, canonical(provenance), now(), now()))
            conn.execute("DELETE FROM stage_state")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        outcomes[sid] = (f"sealed: {h['rows']:,} rows, {total_tokens:,} tokens, "
                         f"counts sha256 {h['sha256'][:16]}")
    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description="Seal completed legacy source counts")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--source", action="append", default=[], help="Source to seal")
    parser.add_argument("--all-completed", action="store_true",
                        help="Seal every completed source that has no ingest_state")
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR,
                        help="Where the legacy JSON files are (hashed as evidence)")
    args = parser.parse_args()
    if not args.db.is_file():
        print(f"database not found: {args.db}", file=sys.stderr)
        return 2
    conn = connect(args.db)
    conn.isolation_level = None
    sources = list(args.source)
    if args.all_completed:
        have = {r[0] for r in conn.execute("SELECT source_id FROM ingest_state")}
        sources += [r[0] for r in conn.execute(
            "SELECT source_id FROM sources WHERE status = 'completed' ORDER BY source_id")
            if r[0] not in have and r[0] not in sources]
    if not sources:
        print("nothing to adopt")
        return 0
    outcomes = adopt(conn, sources, args.checkpoint_dir)
    conn.close()
    for sid, outcome in outcomes.items():
        print(f"  {sid}: {outcome}")
    return 0 if all(o.startswith("sealed") for o in outcomes.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
