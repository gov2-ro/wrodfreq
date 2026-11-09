#!/usr/bin/env python3
"""Build a tiny, complete, synthetic generation through the real pipeline (CI, tests).

Six synthetic sources are written through the shared ingest protocol
(`ingest_state`), then stages 2-5 run exactly as on real data: compute_zipf,
merge, the lemma layer (from a small built-in paradigm map), the manifest, and
build_package. The manifest is marked `synthetic: true`, so no release gate can
accept the result as a real build.

Usage:
    python build/make_synthetic.py --out-dir DIR [--no-lemma] [--build-date YYYY-MM-DD]

Writes `synthetic.db`, a DEX-format `dex.db`, `inputs.json` and `builds/<build_id>/`
into DIR. Prints the path of the completed generation directory on the last line.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_lemma_layer
import build_package
import compute_zipf
import merge
from build_manifest import make_manifest
from ingest_state import SourceSpec, begin
from wrodfreq.db import connect, eligible_sources

SOURCES = {          # source_id -> (total scale, register)
    "eu": (2_000_000, "formal"),
    "news": (30_000_000, "news"),
    "social": (8_000_000, "social"),
    "subs": (25_000_000, "conversational"),
    "web": (300_000_000, "web"),
    "wiki": (3_000_000, "encyclopedic"),
}
FUNCTION_WORDS = ["de", "și", "la", "un", "cu", "în", "a", "o", "nu", "se", "că", "pe"]
CONTENT = ["casă", "casa", "casei", "case", "apă", "mașină", "carte", "drum", "oraș",
           "copil", "copii", "timp", "lucru", "frumos", "bine", "hidratare", "automobil",
           "locuință", "publicație", "traseu", "metropolă", "durată", "obiect", "mare",
           "mări", "mării", "mar", "mere", "câine", "pisică", "cluj-napoca", "site-ul",
           "bla-bla-bla-bla"]
FORM_LEMMA = {"casă": ["casă"], "casa": ["casă"], "casei": ["casă"], "case": ["casă", "caz"],
              "copil": ["copil"], "copii": ["copil"], "mare": ["mare", "mar"],
              "mări": ["mare"], "mării": ["mare"], "mere": ["măr", "mere"],
              "mar": ["mar"], "carte": ["carte"]}


def _counts(source: str, scale: int) -> dict[str, int]:
    """Zipf-shaped counts with a per-source tilt; deterministic."""
    out = {}
    seed = sum(map(ord, source))
    for rank, word in enumerate(FUNCTION_WORDS + CONTENT, start=1):
        tilt = 1.0 + ((seed * 7 + rank * 13) % 11 - 5) / 25.0
        n = int(scale * 0.06 / rank ** 1.15 * tilt)
        if n > 0:
            out[word] = n
    out["rarisim"] = 2 + seed % 3                      # below the floor everywhere
    rest = scale - sum(out.values())
    out["restul"] = rest
    return out


def build_synthetic_db(db_path: Path) -> None:
    conn = connect(db_path)
    for sid, (scale, register) in SOURCES.items():
        spec = SourceSpec(sid, f"Synthetic {sid}", None, "synthetic", register,
                          "contemporary", "synthetic fixture", 1, {"synthetic": True})
        session = begin(conn, spec, resume=False, initial_cursor={"done": False},
                        input_manifest={"synthetic": sid})
        counts = _counts(sid, scale)
        docs = {w: max(1, n // 50) for w, n in counts.items()}
        session.commit_batch(counts, docs, {"done": True}, sum(counts.values()),
                             scale // 500, complete=True)
    conn.close()


def write_dex_map(path: Path) -> None:
    """A DEX-format paradigm map (the tables the real loaders read)."""
    import sqlite3
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE form_lemma (form TEXT, lemma TEXT);
        CREATE TABLE inflected (form TEXT);
        CREATE TABLE lexeme (lemma TEXT, frequency REAL);
    """)
    conn.executemany("INSERT INTO form_lemma VALUES (?, ?)",
                     [(f, lm) for f, ls in FORM_LEMMA.items() for lm in ls])
    conn.executemany("INSERT INTO inflected VALUES (?)", [(f,) for f in FORM_LEMMA])
    lemmas = sorted({lm for ls in FORM_LEMMA.values() for lm in ls})
    conn.executemany("INSERT INTO lexeme VALUES (?, ?)",
                     [(lm, 0.99 if lm in FORM_LEMMA else 0.5) for lm in lemmas])
    conn.commit()
    conn.close()


def build_stages(db_path: Path, dex_path: Path, lemma: bool) -> None:
    conn = connect(db_path)
    compute_zipf.compute_everything(conn)
    sources = eligible_sources(conn)
    merge.run_merge(conn, sources, merge.load_dex_forms(dex_path))
    if lemma:
        build_lemma_layer.run(conn, sources, build_lemma_layer.load_form_lemma(dex_path))
    conn.close()


def make_generation(out_dir: Path, lemma: bool = True, build_date: str = "2000-01-01",
                    log=print) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "synthetic.db"
    if db_path.exists():
        raise FileExistsError(f"{db_path} exists — use an empty directory")
    build_synthetic_db(db_path)
    write_dex_map(out_dir / "dex.db")
    build_stages(db_path, out_dir / "dex.db", lemma)
    conn = connect(db_path)
    manifest = make_manifest(conn, build_date, lemma=lemma, synthetic=True, progress=False)
    (out_dir / "inputs.json").write_text(json.dumps(manifest, indent=2, sort_keys=True,
                                                    ensure_ascii=False) + "\n")
    gen = build_package.write_generation(conn, manifest, out_dir / "builds", log=log)
    conn.close()
    return gen


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a synthetic generation (CI, tests)")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--no-lemma", action="store_true")
    parser.add_argument("--build-date", default="2000-01-01")
    args = parser.parse_args()
    gen = make_generation(args.out_dir, lemma=not args.no_lemma, build_date=args.build_date)
    print(gen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
