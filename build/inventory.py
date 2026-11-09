#!/usr/bin/env python3
"""Read-only inventory of the build inputs and outputs (brief B7, Phase 0 and 6).

Records the code revision, the interpreter and dependency versions, the size and
SHA-256 of every data file that matters, and the source metadata of a database.
The database is opened with `mode=ro`; nothing is written except the report.

Use it before a repair, to freeze the baseline, and after, to prove that the
preserved files did not change.

Usage:
    python build/inventory.py --out data/audit/<dir>/inventory.json
    python build/inventory.py --db PATH --out FILE [--no-hash-db] [--extra PATH ...]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "wrodfreq.db"
DEFAULT_DEX = Path.home() / "devbox/otios/data/processed/inflected_forms.db"
PACKAGES = ["msgpack", "zstandard", "pytest", "datasets", "huggingface_hub", "pyarrow"]


def sha256_file(path: Path, chunk: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def file_entry(path: Path, do_hash: bool = True) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False}
    st = path.stat()
    entry = {"path": str(path), "exists": True, "size": st.st_size,
             "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()}
    if do_hash:
        entry["sha256"] = sha256_file(path)
    return entry


def git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
    except OSError:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def versions() -> dict:
    found = {}
    for name in PACKAGES:
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            found[name] = None
    return found


def db_metadata(db: Path) -> dict:
    """Source rows and per-source raw sums, through a read-only connection."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sources)")]
        sources = [dict(zip(cols, row)) for row in conn.execute(
            "SELECT * FROM sources ORDER BY source_id")]
        sums = {sid: {"sum_occurrences": occ, "rows": n} for sid, occ, n in conn.execute(
            "SELECT source_id, SUM(occurrences), COUNT(*) FROM source_counts GROUP BY source_id")}
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                  for t in tables if t != "source_counts"}
        counts["source_counts"] = sum(v["rows"] for v in sums.values())
        return {"sources": sources, "source_counts": sums, "table_rows": counts}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--dex-db", type=Path, default=DEFAULT_DEX)
    parser.add_argument("--out", type=Path, required=True, help="JSON report path")
    parser.add_argument("--no-hash-db", action="store_true", help="Skip hashing the database")
    parser.add_argument("--extra", type=Path, action="append", default=[],
                        help="Another file or directory to hash (repeatable)")
    args = parser.parse_args()

    groups = {
        "package_data": sorted((ROOT / "wrodfreq" / "data").glob("*")),
        "release": sorted((ROOT / "release").glob("*")),
        "checkpoints": sorted(p for p in (ROOT / "data" / "checkpoints").glob("*") if p.is_file()),
        "raw_social": sorted((ROOT / "data" / "raw" / "social").glob("*")),
        "dex_map": [args.dex_db],
    }
    for extra in args.extra:
        groups.setdefault("extra", []).extend(
            sorted(p for p in extra.rglob("*") if p.is_file()) if extra.is_dir() else [extra])

    report = {
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "git_head": git("rev-parse", "HEAD"),
        "git_status": git("status", "--porcelain"),
        "python": sys.version,
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "packages": versions(),
        "disk_free_bytes": shutil.disk_usage(ROOT).free,
        "database": file_entry(args.db, not args.no_hash_db),
        "files": {g: [file_entry(p) for p in paths if p.is_file() or not p.exists()]
                  for g, paths in groups.items()},
    }
    if args.db.exists():
        report["database"]["metadata"] = db_metadata(args.db)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    tmp.replace(args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
