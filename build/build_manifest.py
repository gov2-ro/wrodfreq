#!/usr/bin/env python3
"""Write the canonical build-input manifest that names one exact build (ADR-006).

The manifest is the explicit input of stage 5 (`build_package.py`). It fixes:

  - the package and data-format versions, the code revision, and a content hash of
    every pipeline and package source file (so an uncommitted candidate is never
    attributed to HEAD alone), the tokenizer hash;
  - the build date, chosen once here — payload generation never reads the clock;
  - the eligible source panel: totals, generations, sequences, input/policy hashes,
    known upstream snapshots (unknown legacy provenance stays `unknown`), and the
    logical hash of each source's raw counts;
  - the derived-stage run ids, merge constants, quantization and filter choices;
  - the DEX map identity when the lemma layer is included, and whether it is;
  - whether the build is synthetic.

`build_id` is the SHA-256 of the canonical JSON of everything else. Nothing that
depends on a file path, the SQLite page layout, or a wall-clock time enters it.

Usage:
    python build/build_manifest.py --db PATH --build-date YYYY-MM-DD --out inputs.json
        [--no-lemma] [--synthetic]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_state import canonical, read_states, sha256_text, tokenizer_hash
from provenance import logical_count_hashes, read_stages, stage_problems
from wrodfreq.db import connect_readonly, eligible_sources
from wrodfreq.zipf import MIN_OCC_PER_SOURCE, MIN_SOURCES_TO_TRIM

MANIFEST_VERSION = 1
DATA_FORMAT_VERSION = 2
ZIPF_SCALE = 100
MAX_HYPHENS = 2
CODE_GLOBS = ["build/*.py", "wrodfreq/*.py", "pyproject.toml"]


def package_version() -> str:
    text = (ROOT / "pyproject.toml").read_text()
    return re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)


def code_identity() -> dict:
    files = {}
    for pattern in CODE_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            files[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()

    def git(*args):
        try:
            out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                                 timeout=30)
        except OSError:
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    dirty = git("status", "--porcelain", "--", "build", "wrodfreq", "pyproject.toml")
    return {"revision": git("rev-parse", "HEAD"), "dirty": bool(dirty),
            "files": files, "files_sha256": sha256_text(canonical(files))}


def build_id_of(manifest: dict) -> str:
    body = {k: v for k, v in manifest.items() if k != "build_id"}
    return sha256_text(canonical(body))[:32]


def make_manifest(conn, build_date: str, lemma: bool, synthetic: bool,
                  dex_identity: dict | None = None, progress: bool = True) -> dict:
    """The input manifest for the database's current, consistent state.

    Raises ValueError if a derived stage is stale or the panel is not sealed.
    """
    date.fromisoformat(build_date)
    panel = eligible_sources(conn)
    if not panel:
        raise ValueError("no eligible (contemporary + completed) source")
    problems = stage_problems(conn, panel, lemma)
    if problems:
        raise ValueError("the derived stages do not describe the current counts:\n  "
                         + "\n  ".join(problems))
    states = read_states(conn)
    stages = read_stages(conn)
    if progress:
        print("hashing raw counts (one pass over source_counts)...", flush=True)
    counts = logical_count_hashes(conn, panel)
    meta = {r[0]: r[1:] for r in conn.execute(
        "SELECT source_id, display_name, register, period, status, total_tokens, total_docs "
        "FROM sources")}
    sources = []
    for sid in panel:
        display, register, period, status, total_tokens, total_docs = meta[sid]
        st = states[sid]
        if counts[sid]["sum_occurrences"] != total_tokens:
            raise ValueError(f"{sid}: SUM(occurrences) != total_tokens")
        sources.append({
            "source_id": sid, "display_name": display, "register": register,
            "period": period, "status": status, "total_tokens": total_tokens,
            "total_docs": total_docs, "generation": st["generation"], "seq": st["seq"],
            "state": st["state"], "policy_hash": st["policy_hash"],
            "input_hash": st["input_hash"], "upstream": st["upstream"],
            "counts": counts[sid],
        })
    lemma_stage = stages.get("lemma")
    if lemma and dex_identity is not None and lemma_stage["inputs"].get("dex_map") != dex_identity:
        raise ValueError("the lemma stage was built from a different DEX map")
    manifest = {
        "kind": "wrodfreq-build-inputs",
        "manifest_version": MANIFEST_VERSION,
        "package_version": package_version(),
        "data_format_version": DATA_FORMAT_VERSION,
        "build_date": build_date,
        "synthetic": synthetic,
        "code": code_identity(),
        "tokenizer_sha256": tokenizer_hash(),
        "constants": {"MIN_OCC_PER_SOURCE": MIN_OCC_PER_SOURCE,
                      "MIN_SOURCES_TO_TRIM": MIN_SOURCES_TO_TRIM,
                      "ZIPF_SCALE": ZIPF_SCALE, "MAX_HYPHENS": MAX_HYPHENS,
                      "merge": "unweighted mean; trim max+min at >= MIN_SOURCES_TO_TRIM",
                      "family_ratio": "matched panel (ADR-005)"},
        "panel": panel,
        "sources": sources,
        "stages": {name: rec["run_id"] for name, rec in sorted(stages.items())
                   if name == "merge" or (name == "lemma" and lemma)
                   or name in {f"zipf:{s}" for s in panel}},
        "lemma": lemma,
        "dex_map": lemma_stage["inputs"].get("dex_map") if lemma else None,
        "is_dex_map": stages["merge"]["inputs"].get("dex"),
        "provenance_limits": [
            f"{s['source_id']}: sealed legacy counts — the raw inputs of the original "
            f"ingest were not recorded; the counts' logical hash is verified, a fresh "
            f"raw-corpus reconstruction is not" for s in sources
            if s["state"] == "sealed_legacy"],
    }
    manifest["build_id"] = build_id_of(manifest)
    return manifest


def load_manifest(path: Path) -> dict:
    manifest = json.loads(Path(path).read_text())
    if manifest.get("kind") != "wrodfreq-build-inputs":
        raise ValueError(f"{path} is not a wROdfreq build-input manifest")
    if build_id_of(manifest) != manifest.get("build_id"):
        raise ValueError(f"{path}: build_id does not match its content (edited by hand?)")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Write the build-input manifest")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--build-date", required=True, help="YYYY-MM-DD, fixed for this build")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-lemma", action="store_true",
                        help="Leave the lemma layer out of the build")
    parser.add_argument("--synthetic", action="store_true",
                        help="Mark the build as synthetic (fixtures, CI)")
    args = parser.parse_args()
    try:
        conn = connect_readonly(args.db)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        has_lemma = conn.execute("SELECT COUNT(*) FROM lemma_zipf").fetchone()[0] > 0
        manifest = make_manifest(conn, args.build_date, has_lemma and not args.no_lemma,
                                 args.synthetic)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    tmp.replace(args.out)
    print(f"build_id {manifest['build_id']} — {len(manifest['panel'])} sources, "
          f"lemma {'included' if manifest['lemma'] else 'omitted'}; wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
