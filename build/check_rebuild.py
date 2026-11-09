#!/usr/bin/env python3
"""Independent rebuild verification (brief B7 Phase 5; replaces old validate check 6).

Copies only the **raw** state of a candidate database — `sources` (without the
derived floor), `source_counts`, `ingest_state`, `db_meta` — into a disposable
database under `--work-dir`, reruns stages 2-5 there from scratch with the same
build-input manifest, and compares:

  - the rebuilt manifest with the original (same build id: same code, same raw
    counts, same stage runs);
  - every emitted file, byte for byte (surface, by-source, lemma, TSV,
    inputs.json, result.json);
  - the logical content of every derived table (not SQLite file bytes — page
    layout differs legitimately).

Then it validates the rebuilt candidate (build/validate.py's checks, read-only).
The inspected database is attached read-only and never written.

Usage:
    python build/check_rebuild.py --db CANDIDATE.db --generation GEN_DIR --work-dir SCRATCH
        [--dex-db PATH] [--wordfreq-python PATH] [--report OUT.json] [--keep]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_lemma_layer
import build_package
import compute_zipf
import merge
from build_manifest import load_manifest, make_manifest
from provenance import derived_hashes
from validate import DEFAULT_DEX_DB, WORDFREQ_PYTHON, run_validation
from wrodfreq.db import connect, connect_readonly, eligible_sources

RAW_TABLES = ("db_meta", "ingest_state", "source_counts")


def copy_raw(candidate: Path, target: Path) -> None:
    """Raw tables only, through a read-only attachment of the candidate."""
    conn = connect(target)
    conn.execute("DELETE FROM db_meta")          # take the candidate's identity
    conn.commit()
    conn.execute(f"ATTACH DATABASE 'file:{candidate.resolve()}?mode=ro' AS cand")
    conn.execute("BEGIN")
    for table in RAW_TABLES:
        conn.execute(f"INSERT INTO main.{table} SELECT * FROM cand.{table}")
    conn.execute("INSERT INTO main.sources SELECT * FROM cand.sources")
    conn.execute("UPDATE main.sources SET zipf_floor = NULL")
    conn.execute("COMMIT")
    conn.execute("DETACH DATABASE cand")
    conn.close()


def rebuild(work_db: Path, manifest: dict, dex_db: Path, log=print) -> dict:
    """Stages 2-4 from raw counts, then the manifest for the rebuilt state."""
    conn = connect(work_db)
    log("stage 2 (compute_zipf)...")
    compute_zipf.compute_everything(conn)
    sources = eligible_sources(conn)
    dex_forms = merge.load_dex_forms(dex_db) if manifest["is_dex_map"] else set()
    if merge.dex_forms_identity(dex_forms) != manifest["is_dex_map"]:
        raise SystemExit("refused: the DEX map at --dex-db is not the one the build used")
    log("stage 3 (merge)...")
    merge.run_merge(conn, sources, dex_forms)
    if manifest["lemma"]:
        log("stage 4 (lemma layer)...")
        build_lemma_layer.run(conn, sources, build_lemma_layer.load_form_lemma(dex_db))
    log("rebuilt manifest...")
    rebuilt = make_manifest(conn, manifest["build_date"], manifest["lemma"],
                            manifest["synthetic"], progress=False)
    conn.close()
    return rebuilt


def compare_generations(a: Path, b: Path) -> dict:
    names = sorted({p.name for p in a.iterdir()} | {p.name for p in b.iterdir()})
    out = {}
    for name in names:
        pa, pb = a / name, b / name
        out[name] = (pa.exists() and pb.exists() and pa.read_bytes() == pb.read_bytes())
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent rebuild verification")
    parser.add_argument("--db", type=Path, required=True, help="Candidate database (read-only)")
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--dex-db", type=Path, default=DEFAULT_DEX_DB)
    parser.add_argument("--wordfreq-python", type=Path, default=WORDFREQ_PYTHON)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--strict", action="store_true",
                        help="Validate the rebuilt candidate strictly")
    parser.add_argument("--keep", action="store_true", help="Keep the rebuilt database")
    args = parser.parse_args()

    started = time.time()
    if not args.db.is_file():
        print(f"database not found: {args.db}", file=sys.stderr)
        return 2
    original = build_package.verify_generation(args.generation)
    manifest = load_manifest(args.generation / build_package.INPUTS_FILENAME)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    work_db = args.work_dir / "rebuild.db"
    if work_db.exists():
        print(f"refused: {work_db} exists — use an empty work directory", file=sys.stderr)
        return 2

    print(f"copying raw tables of {args.db} (read-only) into {work_db}...", flush=True)
    copy_raw(args.db, work_db)
    rebuilt_manifest = rebuild(work_db, manifest, args.dex_db)
    same_manifest = rebuilt_manifest == manifest
    print(f"rebuilt build_id {rebuilt_manifest['build_id']} "
          f"({'same' if same_manifest else 'DIFFERENT'})", flush=True)

    conn = connect_readonly(work_db)
    gen = build_package.write_generation(conn, rebuilt_manifest, args.work_dir / "builds")
    rebuilt_hashes = derived_hashes(conn)
    conn.close()
    cand = connect_readonly(args.db)
    print("hashing the candidate's derived tables...", flush=True)
    candidate_hashes = derived_hashes(cand)
    cand.close()
    files = compare_generations(args.generation, gen)
    tables = {t: candidate_hashes.get(t) == rebuilt_hashes.get(t)
              for t in sorted(set(candidate_hashes) | set(rebuilt_hashes))}
    print("validating the rebuilt candidate...", flush=True)
    validation = run_validation(work_db, gen, strict=args.strict, dex_db=args.dex_db,
                                wordfreq_python=args.wordfreq_python)
    ok = (same_manifest and all(files.values()) and all(tables.values())
          and not any(s == "fail" for s in validation["statuses"].values()))
    report = {
        "kind": "wrodfreq-rebuild-report",
        "build_id": original["build_id"],
        "rebuilt_build_id": rebuilt_manifest["build_id"],
        "same_manifest": same_manifest,
        "files_identical": files,
        "derived_tables_identical": tables,
        "candidate_derived_hashes": candidate_hashes,
        "rebuilt_derived_hashes": rebuilt_hashes,
        "rebuilt_validation_statuses": validation["statuses"],
        "ok": ok,
        "informational": {"elapsed_s": round(time.time() - started, 1)},
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    for name, same in files.items():
        print(f"  {name:28s} {'identical' if same else 'DIFFERS'}")
    for name, same in tables.items():
        print(f"  table {name:22s} {'identical' if same else 'DIFFERS'}")
    print(f"rebuilt candidate validation: {validation['statuses']}")
    if not args.keep:
        work_db.unlink(missing_ok=True)
        shutil.rmtree(args.work_dir / "builds", ignore_errors=True)
    print("INDEPENDENT REBUILD " + ("MATCHES" if ok else "DOES NOT MATCH"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
