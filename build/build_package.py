#!/usr/bin/env python3
"""Stage 5 — compile one exact build into a generation directory (spec §7.6, §10.2; ADR-006).

`msgpack` + `xz` (stdlib `lzma`), per spec: "no SQLite dependency at runtime."

Input: the database and a build-input manifest (`build/build_manifest.py`). The
manifest fixes the build date and the build id; this stage never reads the clock,
so the same manifest gives byte-identical files on any day.

Output: `<out-root>/<build_id>/` holding

  - `ro_surface.msgpack.xz` — the core table (words, zipf, n_reliable,
    n_attesting, spread). Parallel arrays, zipf/spread quantized to centizipf
    ints (measured: 97.7 MB packed vs 193.4 MB as floats).
  - `ro_by_source.msgpack.xz` — the per-source breakdown for by_source() only,
    positional against the surface word order (re-shipping the words would take
    the file from 9.5 MB to 27.6 MB).
  - `ro_lemma.msgpack.xz` — the DEX-paradigm layer, only if the manifest includes
    it. (Shipping it assumes DEX Online's permission — docs/BACKLOG.md.)
  - `ro_surface.tsv.gz` — the same rows for readers (build/export_tsv.py).
  - `inputs.json` — the manifest, and `result.json` — written **last**, with the
    SHA-256 of every file above. A directory without `result.json` is unfinished
    and no consumer selects it.

Data format 2 (new in 0.2.1): every payload header carries the same `build_id`,
source panel, `zipf_scale` and `words_sha256` (the identity of the ordered word
list), so a by-source or lemma file from another build is rejected even when its
row count happens to match. Format 1 files are refused with a rebuild message.

The generation is written into a hidden temporary directory and renamed into place
only when complete. If the final directory already exists, the stage rebuilds into
a temporary one and requires identical bytes — a free determinism check.

Rows with more than MAX_HYPHENS hyphens are left out of the files (they stay in
the database). Measured 2026-10-05: the 23,453 such rows are URL slugs, titles,
chemical names; 9 are in DEX, none above Zipf 3.2.

Usage:
    python build/build_package.py --db PATH --manifest inputs.json --out-root data/builds
    python build/build_package.py --install data/builds/<build_id> [--package-dir DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

import msgpack

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_manifest import (DATA_FORMAT_VERSION, MAX_HYPHENS, ZIPF_SCALE, code_identity,
                            load_manifest)
from ingest_state import canonical, read_states
from provenance import read_stages, stage_problems
from wrodfreq.db import connect_readonly, eligible_sources

DEFAULT_PACKAGE_DIR = ROOT / "wrodfreq" / "data"
SURFACE_FILENAME = "ro_surface.msgpack.xz"
BY_SOURCE_FILENAME = "ro_by_source.msgpack.xz"
LEMMA_FILENAME = "ro_lemma.msgpack.xz"
TSV_FILENAME = "ro_surface.tsv.gz"
INPUTS_FILENAME = "inputs.json"
RESULT_FILENAME = "result.json"
BUILD_INFO_FILENAME = "build.json"
PACKAGE_FILES = (SURFACE_FILENAME, BY_SOURCE_FILENAME, LEMMA_FILENAME)
FORMAT_VERSION = DATA_FORMAT_VERSION


class PackageRefused(RuntimeError):
    pass


def _quantize(zipf: float) -> int:
    return round(zipf * ZIPF_SCALE)


def words_sha256(words: list[str]) -> str:
    return hashlib.sha256("\n".join(words).encode("utf-8")).hexdigest()


def _header(manifest: dict) -> dict:
    return {"format_version": FORMAT_VERSION, "build_id": manifest["build_id"],
            "sources": list(manifest["panel"]), "zipf_scale": ZIPF_SCALE}


def check_db_matches(conn: sqlite3.Connection, manifest: dict) -> None:
    """The database's current state is the one the manifest describes (cheap checks)."""
    panel = eligible_sources(conn)
    if panel != manifest["panel"]:
        raise PackageRefused(f"eligible panel {panel} != manifest panel {manifest['panel']}")
    problems = stage_problems(conn, panel, manifest["lemma"])
    if problems:
        raise PackageRefused("stale derived stages:\n  " + "\n  ".join(problems))
    states = read_states(conn)
    totals = dict(conn.execute("SELECT source_id, total_tokens FROM sources"))
    for src in manifest["sources"]:
        sid = src["source_id"]
        st = states.get(sid, {})
        if (st.get("generation"), st.get("seq"), totals.get(sid)) != (
                src["generation"], src["seq"], src["total_tokens"]):
            raise PackageRefused(f"{sid}: database generation/seq/total differ from the manifest")
    stages = read_stages(conn)
    for name, rid in manifest["stages"].items():
        if stages.get(name, {}).get("run_id") != rid:
            raise PackageRefused(f"stage {name} differs from the manifest")


def build_payloads(conn: sqlite3.Connection, manifest: dict) -> tuple[dict, dict]:
    """(surface_payload, by_source_payload) for the manifest's build. No clock reads."""
    sources = list(manifest["panel"])
    rows = conn.execute(
        "SELECT word, zipf, n_reliable, n_attesting, n_sources, spread "
        "FROM merged WHERE length(word) - length(replace(word, '-', '')) <= ? "
        "ORDER BY word",
        (MAX_HYPHENS,),
    ).fetchall()
    n_sources_values = {r[4] for r in rows}
    if n_sources_values - {len(sources)}:
        # n_sources is stored once in the header; a per-word value would be lost.
        raise PackageRefused(f"n_sources in merged is {n_sources_values}, panel has "
                             f"{len(sources)} — merged is from another panel")

    words = [r[0] for r in rows]
    surface = {
        **_header(manifest),
        "built": manifest["build_date"],
        "package_version": manifest["package_version"],
        "n_sources": len(sources),
        "source_tokens": [s["total_tokens"] for s in manifest["sources"]],
        "lemma": bool(manifest["lemma"]),
        "word_count": len(words),
        "words_sha256": words_sha256(words),
        "words": words,
        "zipf": [_quantize(r[1]) for r in rows],
        "n_reliable": [r[2] for r in rows],
        "n_attesting": [r[3] for r in rows],
        "spread": [_quantize(r[5]) for r in rows],
    }
    del rows

    # Stream source_zipf in (word, source_id) order alongside the sorted words.
    index = {s: i for i, s in enumerate(sources)}
    by_source = []
    placeholders = ",".join("?" * len(sources))
    cur = conn.execute(
        f"SELECT word, source_id, zipf FROM source_zipf WHERE reliable = 1 "
        f"AND source_id IN ({placeholders}) ORDER BY word, source_id", sources)
    pending = next(cur, None)
    for w in words:
        row = [None] * len(sources)
        while pending is not None and pending[0] < w:
            pending = next(cur, None)
        while pending is not None and pending[0] == w:
            row[index[pending[1]]] = _quantize(pending[2])
            pending = next(cur, None)
        by_source.append(row)
    by_source_payload = {
        **_header(manifest),
        "word_count": len(words),
        "words_sha256": surface["words_sha256"],
        "by_source": by_source,
    }
    return surface, by_source_payload


def build_lemma_payload(conn: sqlite3.Connection, manifest: dict) -> dict | None:
    """The DEX-paradigm layer (spec §9), or None if the manifest leaves it out."""
    if not manifest["lemma"]:
        return None
    rows = conn.execute(
        "SELECT lemma, zipf, n_forms, zipf_headword, family_ratio "
        "FROM lemma_zipf ORDER BY lemma").fetchall()
    if not rows:
        raise PackageRefused("the manifest includes the lemma layer but lemma_zipf is empty")
    return {
        **_header(manifest),
        "lemma_count": len(rows),
        "lemmas": [r[0] for r in rows],
        "zipf": [_quantize(r[1]) for r in rows],
        "n_forms": [r[2] for r in rows],
        # -1 = the citation form alone was never seen reliably
        "zipf_headword": [-1 if r[3] is None else _quantize(r[3]) for r in rows],
        "family_ratio": [-1 if r[4] is None else _quantize(r[4]) for r in rows],
    }


def _fsync_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def encode(payload: dict) -> bytes:
    packed = msgpack.packb(payload, use_bin_type=True)
    return lzma.compress(packed, format=lzma.FORMAT_XZ, preset=9)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def write_generation(conn: sqlite3.Connection, manifest: dict, out_root: Path,
                     log=print) -> Path:
    """Write a complete generation directory; returns its path."""
    from export_tsv import export_tsv

    check_db_matches(conn, manifest)
    bid = manifest["build_id"]
    out_root.mkdir(parents=True, exist_ok=True)
    for stale in out_root.glob(f".{bid}.partial-*"):
        shutil.rmtree(stale, ignore_errors=True)
    tmp = out_root / f".{bid}.partial-{uuid.uuid4().hex[:8]}"
    tmp.mkdir()
    try:
        log("building payloads...")
        surface, by_source = build_payloads(conn, manifest)
        lemma = build_lemma_payload(conn, manifest)
        log(f"  {surface['word_count']:,} words, {len(manifest['panel'])} sources"
            f"{', ' + format(lemma['lemma_count'], ',') + ' lemmas' if lemma else ''}")
        for name, payload in ((SURFACE_FILENAME, surface), (BY_SOURCE_FILENAME, by_source),
                              (LEMMA_FILENAME, lemma)):
            if payload is None:
                continue
            log(f"  compressing {name}...")
            _fsync_write(tmp / name, encode(payload))
        word_count, words_hash = surface["word_count"], surface["words_sha256"]
        lemma_count = lemma["lemma_count"] if lemma else 0
        del surface, by_source, lemma
        log(f"  writing {TSV_FILENAME}...")
        tsv_rows = export_tsv(conn, tmp / TSV_FILENAME)
        if tsv_rows != word_count:
            raise PackageRefused(f"TSV has {tsv_rows:,} rows, package {word_count:,}")
        inputs_bytes = (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False)
                        + "\n").encode("utf-8")
        _fsync_write(tmp / INPUTS_FILENAME, inputs_bytes)
        files = {p.name: {"sha256": sha256_file(p), "size": p.stat().st_size}
                 for p in sorted(tmp.iterdir()) if not p.name.endswith(".tmp")}
        result = {
            "kind": "wrodfreq-build-result",
            "build_id": bid,
            "package_version": manifest["package_version"],
            "data_format_version": FORMAT_VERSION,
            "build_date": manifest["build_date"],
            "synthetic": manifest["synthetic"],
            "lemma": manifest["lemma"],
            "panel": manifest["panel"],
            "word_count": word_count,
            "words_sha256": words_hash,
            "lemma_count": lemma_count,
            "inputs_sha256": hashlib.sha256(inputs_bytes).hexdigest(),
            "files": files,
        }
        _fsync_write(tmp / RESULT_FILENAME,
                     (json.dumps(result, indent=2, sort_keys=True) + "\n").encode())
        _fsync_dir(tmp)
        final = out_root / bid
        if final.exists():
            old = final / RESULT_FILENAME
            if old.exists() and old.read_bytes() == (tmp / RESULT_FILENAME).read_bytes():
                log(f"generation {bid} already exists with identical bytes — kept")
                shutil.rmtree(tmp)
                return final
            raise PackageRefused(f"{final} exists with different content — a build is not "
                                 f"deterministic, or its inputs were edited")
        os.rename(tmp, final)
        _fsync_dir(out_root)
        return final
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def verify_generation(gen_dir: Path) -> dict:
    """result.json of a complete generation whose files match it; raises otherwise."""
    result_path = Path(gen_dir) / RESULT_FILENAME
    if not result_path.exists():
        raise PackageRefused(f"{gen_dir} is not a completed generation (no {RESULT_FILENAME})")
    result = json.loads(result_path.read_text())
    if result.get("kind") != "wrodfreq-build-result":
        raise PackageRefused(f"{result_path} is not a build result")
    for name, ident in result["files"].items():
        path = Path(gen_dir) / name
        if not path.exists() or sha256_file(path) != ident["sha256"]:
            raise PackageRefused(f"{path} is missing or does not match {RESULT_FILENAME}")
    manifest = load_manifest(Path(gen_dir) / INPUTS_FILENAME)
    if manifest["build_id"] != result["build_id"]:
        raise PackageRefused("inputs.json and result.json name different builds")
    return result


def install(gen_dir: Path, package_dir: Path) -> dict:
    """Copy a verified generation's data files into the package directory, together.

    Files from an older generation (e.g. a lemma file the new build omits) are
    removed, and `build.json` records which build the directory now holds.
    """
    result = verify_generation(gen_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    wanted = [n for n in PACKAGE_FILES if n in result["files"]]
    for name in PACKAGE_FILES:
        if name not in wanted:
            (package_dir / name).unlink(missing_ok=True)
    for name in wanted:
        tmp = package_dir / (name + ".tmp")
        shutil.copyfile(Path(gen_dir) / name, tmp)
        tmp.replace(package_dir / name)
    info = {k: result[k] for k in ("build_id", "package_version", "data_format_version",
                                   "build_date", "synthetic", "lemma", "panel",
                                   "word_count", "words_sha256", "lemma_count")}
    info["files"] = {n: result["files"][n] for n in wanted}
    (package_dir / BUILD_INFO_FILENAME).write_text(json.dumps(info, indent=2, sort_keys=True)
                                                   + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 5: compile one build's data files")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--manifest", type=Path, help="Build-input manifest (build_manifest.py)")
    parser.add_argument("--out-root", type=Path, help="Directory that holds generations")
    parser.add_argument("--install", type=Path, metavar="GENERATION",
                        help="Copy a completed generation into the package data directory")
    parser.add_argument("--package-dir", type=Path, default=DEFAULT_PACKAGE_DIR)
    parser.add_argument("--allow-code-drift", action="store_true",
                        help="Build although the code differs from the manifest (tests only)")
    args = parser.parse_args()

    if args.install:
        try:
            result = install(args.install, args.package_dir)
        except PackageRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print(f"installed build {result['build_id']} into {args.package_dir}")
        return 0

    if not (args.db and args.manifest and args.out_root):
        parser.error("--db, --manifest and --out-root are required to build")
    try:
        manifest = load_manifest(args.manifest)
        conn = connect_readonly(args.db)
    except (ValueError, FileNotFoundError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if (code_identity()["files_sha256"] != manifest["code"]["files_sha256"]
            and not args.allow_code_drift):
        print("refused: the pipeline code differs from the code named in the manifest; "
              "write a new manifest", file=sys.stderr)
        return 2
    try:
        final = write_generation(conn, manifest, args.out_root)
    except PackageRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    result = json.loads((final / RESULT_FILENAME).read_text())
    for name, ident in result["files"].items():
        print(f"  {name:28s} {ident['size']:>12,} bytes  {ident['sha256'][:16]}")
    print(f"generation complete: {final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
