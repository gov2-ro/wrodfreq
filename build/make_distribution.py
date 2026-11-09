#!/usr/bin/env python3
"""Assemble a release-candidate bundle from one completed generation (ADR-006).

Builds the distribution from a clean staging tree — the package sources plus the
generation's data files installed together (`build_package.install`) — never from
whatever blobs happen to sit in the working checkout. The sdist is built first and
the wheel is built **from that sdist** (`python -m build`'s default), so a wheel
can only contain what the sdist carries.

Bundle layout:

    <out>/candidate.json          kind, version, build id, SHA-256 of every file below
    <out>/dist/*.tar.gz, *.whl    the sdist and the wheel
    <out>/generation/             the generation's files, result.json, inputs.json
    <out>/validation.json         the strict real-data validation report (release)
    <out>/rebuild.json            the independent-rebuild report, if given

A real candidate needs a strict, release-eligible validation report for the same
build. `--allow-synthetic` makes a synthetic bundle for CI; check_release.py
rejects it as a release.

Usage:
    python build/make_distribution.py --generation GEN --validation REPORT --out DIR
        [--rebuild-report R] [--tar]
    python build/make_distribution.py --generation GEN --out DIR --allow-synthetic
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_package
from build_manifest import package_version

SOURCE_FILES = ["pyproject.toml", "README.md", "LICENSE.txt"]


def sha256_file(path: Path) -> str:
    return build_package.sha256_file(path)


def stage_source(gen: Path, staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "wrodfreq").mkdir(parents=True)
    for name in SOURCE_FILES:
        shutil.copy(ROOT / name, staging / name)
    for py in sorted((ROOT / "wrodfreq").glob("*.py")):
        shutil.copy(py, staging / "wrodfreq" / py.name)
    build_package.install(gen, staging / "wrodfreq" / "data")


def build_dists(staging: Path, dist: Path) -> None:
    staging, dist = staging.resolve(), dist.resolve()     # the build runs in another cwd
    dist.mkdir(parents=True, exist_ok=True)
    import importlib.metadata
    try:
        importlib.metadata.version("build")     # the PyPA frontend, not ./build/
        cmd = [sys.executable, "-m", "build", "--outdir", str(dist), str(staging)]
    except importlib.metadata.PackageNotFoundError:
        cmd = ["uv", "build", "--out-dir", str(dist), str(staging)]
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=staging.parent)


def deterministic_tar(src: Path, out: Path) -> str:
    """A tar.gz whose bytes depend only on the files: sorted, zero mtimes and owners."""
    tmp = out.with_suffix(".tmp")
    with open(tmp, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                                               mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for path in sorted(p for p in src.rglob("*")):
                info = tar.gettarinfo(str(path), arcname=str(path.relative_to(src.parent)))
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                if path.is_file():
                    with path.open("rb") as fh:
                        tar.addfile(info, fh)
                else:
                    tar.addfile(info)
    tmp.replace(out)
    return sha256_file(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble a release-candidate bundle")
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--validation", type=Path, help="Strict validation report (JSON)")
    parser.add_argument("--rebuild-report", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tar", action="store_true", help="Also write <out>.tar.gz")
    parser.add_argument("--allow-synthetic", action="store_true",
                        help="CI only: bundle a synthetic generation without a report")
    args = parser.parse_args()

    result = build_package.verify_generation(args.generation)
    if result["package_version"] != package_version():
        print(f"refused: generation is for {result['package_version']}, pyproject says "
              f"{package_version()}", file=sys.stderr)
        return 2
    if result["synthetic"] and not args.allow_synthetic:
        print("refused: synthetic generation (pass --allow-synthetic for CI)", file=sys.stderr)
        return 2
    if not result["synthetic"]:
        if args.validation is None:
            print("refused: a real candidate needs --validation", file=sys.stderr)
            return 2
        report = json.loads(args.validation.read_text())
        if not (report.get("release_eligible") and report.get("build_id") == result["build_id"]
                and report.get("artifacts") == result["files"]):
            print("refused: the validation report is not a release-eligible report of this "
                  "exact generation", file=sys.stderr)
            return 2
    if args.out.exists() and any(args.out.iterdir()):
        print(f"refused: {args.out} is not empty", file=sys.stderr)
        return 2

    staging = args.out.parent / f".{args.out.name}.staging"
    stage_source(args.generation, staging)
    build_dists(staging, args.out / "dist")
    shutil.rmtree(staging)
    shutil.copytree(args.generation, args.out / "generation")
    if args.validation:
        shutil.copy(args.validation, args.out / "validation.json")
    if args.rebuild_report:
        shutil.copy(args.rebuild_report, args.out / "rebuild.json")
    files = {str(p.relative_to(args.out)): sha256_file(p)
             for p in sorted(args.out.rglob("*")) if p.is_file()}
    candidate = {"kind": "wrodfreq-release-candidate", "package_version": result["package_version"],
                 "build_id": result["build_id"], "synthetic": result["synthetic"],
                 "files": files}
    (args.out / "candidate.json").write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n")
    print(f"bundle: {args.out}")
    if args.tar:
        tar = args.out.with_name(args.out.name + ".tar.gz")
        digest = deterministic_tar(args.out, tar)
        print(f"tarball: {tar}\nsha256:  {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
