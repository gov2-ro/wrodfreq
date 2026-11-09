#!/usr/bin/env python3
"""Release preflight: check one prepared candidate bundle (ADR-006, brief B7 Phase 5).

Checks the **actual distribution** that would be released, never a stand-in:

  - with `--expected-sha256`, the tarball's digest before anything is extracted;
    extraction refuses absolute paths, `..` and links;
  - `candidate.json` lists every file with its SHA-256, and they match;
  - the generation is complete and matches its result.json;
  - the wheel and the sdist both contain the generation's data files, byte for
    byte, plus a build.json naming the same build; versions agree;
  - release mode: a strict, release-eligible, non-synthetic validation report that
    binds this build id, these artifact hashes, this code and this version;
    synthetic candidates are rejected;
  - `--install-test`: the wheel installs into a clean venv and answers surface,
    by-source, top-list and lemma lookups from outside the checkout, with
    PYTHONPATH unset.

A hash-bound report says what the local real-data validation found; this script
does not rerun the corpus checks (the runner has no database). That local run
stays a documented release step.

Modes:
    --mode release        (default) a real candidate, for the release gate
    --mode ci-synthetic   CI only: the candidate must be synthetic; no report needed

Usage:
    python build/check_release.py --bundle DIR_OR_TARBALL [--expected-sha256 HEX]
        [--mode release|ci-synthetic] [--install-test] [--tag vX.Y.Z]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

DATA_FILES = ("ro_surface.msgpack.xz", "ro_by_source.msgpack.xz", "ro_lemma.msgpack.xz")


class Fail(Exception):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def safe_extract(tarball: Path, dest: Path) -> Path:
    with tarfile.open(tarball, "r:*") as tar:
        members = tar.getmembers()
        for m in members:
            target = (dest / m.name).resolve()
            if (m.name.startswith("/") or ".." in Path(m.name).parts or m.issym() or m.islnk()
                    or not str(target).startswith(str(dest.resolve()))):
                raise Fail(f"unsafe member in bundle: {m.name}")
        tar.extractall(dest, members=members)
    tops = {Path(m.name).parts[0] for m in members}
    if len(tops) != 1:
        raise Fail("the bundle tarball must hold exactly one top-level directory")
    return dest / tops.pop()


def check_bundle(bundle: Path, mode: str, tag: str | None, repo_version: str | None) -> dict:
    cand_path = bundle / "candidate.json"
    if not cand_path.exists():
        raise Fail("candidate.json missing — not a bundle from build/make_distribution.py")
    cand = json.loads(cand_path.read_text())
    if cand.get("kind") != "wrodfreq-release-candidate":
        raise Fail("candidate.json is not a release-candidate record")
    on_disk = {str(p.relative_to(bundle)) for p in bundle.rglob("*")
               if p.is_file() and p.name != "candidate.json"}
    if on_disk != set(cand["files"]):
        raise Fail(f"bundle files differ from candidate.json: "
                   f"{sorted(on_disk ^ set(cand['files']))[:5]}")
    for rel, digest in cand["files"].items():
        if sha256_file(bundle / rel) != digest:
            raise Fail(f"{rel} does not match candidate.json")

    import build_package
    gen = bundle / "generation"
    try:
        result = build_package.verify_generation(gen)
    except (build_package.PackageRefused, ValueError) as exc:
        raise Fail(f"generation: {exc}")
    if (result["build_id"], result["package_version"], result["synthetic"]) != (
            cand["build_id"], cand["package_version"], cand["synthetic"]):
        raise Fail("candidate.json and the generation disagree on build/version/synthetic")
    version = result["package_version"]
    if repo_version is not None and repo_version != version:
        raise Fail(f"the checkout is version {repo_version}, the candidate {version}")
    if tag and tag != f"v{version}":
        raise Fail(f"tag {tag} does not name version {version}")

    if mode == "release":
        if result["synthetic"]:
            raise Fail("synthetic candidate — a synthetic build can never be released")
        rep_path = bundle / "validation.json"
        if not rep_path.exists():
            raise Fail("validation.json missing — run build/validate.py --strict locally")
        rep = json.loads(rep_path.read_text())
        problems = []
        if rep.get("kind") != "wrodfreq-validation-report":
            problems.append("not a validation report")
        if not rep.get("strict"):
            problems.append("not a strict report")
        if not rep.get("release_eligible") or rep.get("synthetic"):
            problems.append("not release-eligible")
        if any(s != "pass" for s in (rep.get("statuses") or {"none": "missing"}).values()):
            problems.append(f"checks not all passed: {rep.get('statuses')}")
        if rep.get("build_id") != result["build_id"]:
            problems.append("binds another build id")
        if rep.get("artifacts") != result["files"]:
            problems.append("binds other artifact hashes")
        if rep.get("inputs_sha256") != result["inputs_sha256"]:
            problems.append("binds other inputs")
        if rep.get("package_version") != version:
            problems.append("binds another version")
        manifest = json.loads((gen / "inputs.json").read_text())
        if rep.get("code") != manifest["code"]:
            problems.append("binds another code revision")
        if not rep.get("inputs_unchanged"):
            problems.append("the validation changed its inputs")
        if problems:
            raise Fail("validation report: " + "; ".join(problems))
    elif not result["synthetic"]:
        raise Fail("--mode ci-synthetic accepts only synthetic candidates")

    dist = bundle / "dist"
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise Fail(f"expected one wheel and one sdist, found {len(wheels)} and {len(sdists)}")
    expected = {n: result["files"][n]["sha256"] for n in DATA_FILES if n in result["files"]}
    if "ro_surface.msgpack.xz" not in expected or "ro_by_source.msgpack.xz" not in expected:
        raise Fail("the generation lacks a required data file")

    def check_archive(names_to_bytes, label: str, prefix: str) -> None:
        for name, digest in expected.items():
            data = names_to_bytes(prefix + "wrodfreq/data/" + name)
            if data is None:
                raise Fail(f"{label} lacks wrodfreq/data/{name}")
            if sha256_bytes(data) != digest:
                raise Fail(f"{label}: wrodfreq/data/{name} differs from the generation")
        for name in DATA_FILES:
            if name not in expected and names_to_bytes(prefix + "wrodfreq/data/" + name):
                raise Fail(f"{label} ships {name}, which this build does not include")
        info = names_to_bytes(prefix + "wrodfreq/data/build.json")
        if info is None or json.loads(info)["build_id"] != result["build_id"]:
            raise Fail(f"{label}: build.json missing or names another build")

    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())
        check_archive(lambda n: zf.read(n) if n in names else None, wheels[0].name, "")
        meta = [n for n in names if n.endswith(".dist-info/METADATA")]
        if not meta or f"\nVersion: {version}\n" not in zf.read(meta[0]).decode():
            raise Fail("wheel METADATA does not carry the candidate version")
    with tarfile.open(sdists[0]) as tf:
        members = {m.name: m for m in tf.getmembers()}
        top = sdists[0].name.removesuffix(".tar.gz") + "/"
        check_archive(lambda n: tf.extractfile(members[n]).read() if n in members else None,
                      sdists[0].name, top)
    return {"build_id": result["build_id"], "version": version, "lemma": result["lemma"],
            "synthetic": result["synthetic"], "wheel": wheels[0], "sdist": sdists[0]}


INSTALL_PROBE = r"""
import json, os, sys
import wrodfreq
from pathlib import Path
here = Path(wrodfreq.__file__).resolve()
repo = Path(sys.argv[1]).resolve()
assert repo not in here.parents, f"imported from the checkout: {here}"
info = wrodfreq.build_info()
assert info["build_id"] == sys.argv[2], info
z = wrodfreq.zipf_frequency("de")
assert z > 0, z
assert wrodfreq.word_frequency("de") > 0
top = wrodfreq.top_n_list(5)
assert len(top) == 5 and "de" in top, top
assert wrodfreq.by_source("de"), "by_source empty"
assert wrodfreq.frequency_detail("cuvântinexistentxyz") is None
lemma = wrodfreq.lemma_detail(sys.argv[4])
if sys.argv[3] == "1":
    assert lemma is not None, "lemma layer missing"
else:
    assert lemma is None and wrodfreq.lemma_frequency("de") == z
print(json.dumps({"zipf_de": z, "top5": top, "build_id": info["build_id"],
                  "lemma": lemma is not None, "module": str(here)}))
"""


def install_test(wheel: Path, build_id: str, lemma: bool, probe_lemma: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="wrodfreq-install-") as tmp:
        tmp = Path(tmp)
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        subprocess.run([sys.executable, "-m", "venv", str(tmp / "venv")], check=True)
        py = tmp / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(py), "-m", "pip", "install", "-q", str(wheel)], check=True,
                       env=env, cwd=tmp)
        out = subprocess.run([str(py), "-I", "-c", INSTALL_PROBE, str(ROOT), build_id,
                              "1" if lemma else "0", probe_lemma],
                             capture_output=True, text=True, env=env, cwd=tmp)
        if out.returncode != 0:
            raise Fail(f"installed-wheel probe failed:\n{out.stderr[-2000:]}")
        return json.loads(out.stdout.strip().splitlines()[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description="Release preflight for one candidate bundle")
    parser.add_argument("--bundle", type=Path, required=True, help="Bundle directory or .tar.gz")
    parser.add_argument("--expected-sha256", help="Required for a tarball: its SHA-256")
    parser.add_argument("--mode", choices=["release", "ci-synthetic"], default="release")
    parser.add_argument("--install-test", action="store_true")
    parser.add_argument("--probe-lemma", default="casă", help="A lemma the build should know")
    parser.add_argument("--tag", help="Git tag being released (vX.Y.Z)")
    args = parser.parse_args()

    text = (ROOT / "pyproject.toml").read_text()
    repo_version = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)
    try:
        with tempfile.TemporaryDirectory(prefix="wrodfreq-bundle-") as tmp:
            bundle = args.bundle
            if bundle.is_file():
                if not args.expected_sha256:
                    raise Fail("a bundle tarball needs --expected-sha256")
                digest = sha256_file(bundle)
                if digest != args.expected_sha256.lower():
                    raise Fail(f"bundle sha256 {digest} != expected {args.expected_sha256}")
                bundle = safe_extract(bundle, Path(tmp))
            elif args.expected_sha256:
                raise Fail("--expected-sha256 applies to a tarball, not a directory")
            found = check_bundle(bundle, args.mode, args.tag, repo_version)
            print(f"bundle ok: build {found['build_id']}, version {found['version']}, "
                  f"lemma {'included' if found['lemma'] else 'omitted'}, "
                  f"{'SYNTHETIC' if found['synthetic'] else 'real'}")
            if args.install_test:
                probe = install_test(found["wheel"], found["build_id"], found["lemma"],
                                     args.probe_lemma)
                print(f"installed wheel ok: {probe}")
    except Fail as exc:
        print(f"RELEASE CHECK FAILED: {exc}", file=sys.stderr)
        return 1
    print("RELEASE CHECK PASSED" if args.mode == "release" else "CI SYNTHETIC CHECK PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
