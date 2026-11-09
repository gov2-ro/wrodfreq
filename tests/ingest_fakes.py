"""Offline readers for the six real ingesters (brief B7, Phase 1 tests).

Each adapter loads the real `build/ingest_<source>.py` module and replaces only its
reading functions (Hugging Face, OPUS, local files) with fixture data, so the real
batching, cursor and transaction code runs. `tests/ingest_driver.py` uses the same
adapters in a subprocess for the kill tests.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
for p in (str(ROOT), str(BUILD)):
    if p not in sys.path:
        sys.path.insert(0, p)

SOURCES = ["wiki", "web", "news", "subs", "eu", "social"]

DOCS = [
    "Ana are mere și pere, dar nu are prune.",
    "Nu știu dacă merge, dar cred că este bine să încercăm acum.",
    "De la cap la coadă, cu un singur gând.",
    "Într-o zi am mers la munte cu prietenii mei.",
    "Mașina era veche, însă mergea foarte bine pe drum.",
    "Casa de pe deal are un acoperiș roșu și o grădină mare.",
    "Copiii s-au jucat toată ziua în parc, apoi au mers acasă.",
]


def load(source: str):
    """A fresh copy of build/ingest_<source>.py (module state reset)."""
    spec = importlib.util.spec_from_file_location(f"_ingest_{source}_fake",
                                                  BUILD / f"ingest_{source}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _social_records() -> list[str]:
    recs = []
    for i, text in enumerate(DOCS):
        recs.append(json.dumps({"author": f"user{i % 3}", "body": text, "subreddit": "Romania",
                                "created_utc": 1_700_000_000 - i}, ensure_ascii=False))
    recs.insert(2, "{not json")                                       # malformed
    recs.insert(4, json.dumps({"author": "AutoModerator", "body": "Nu sunt bot"}))
    recs.insert(5, json.dumps({"author": "x", "body": "I think this is a really good idea"}))
    recs.insert(6, "")                                                # blank line
    return recs


def make_fixtures(fixture_dir: Path) -> None:
    """Write the local input files the adapters read."""
    fixture_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(fixture_dir / "subs.txt.gz", "wt", encoding="utf-8") as f:
        f.write("\n".join(DOCS) + "\n")
    with gzip.open(fixture_dir / "europarl.txt.gz", "wt", encoding="utf-8") as f:
        f.write("\n".join(DOCS[:3]) + "\n")
    with gzip.open(fixture_dir / "dgt.txt.gz", "wt", encoding="utf-8") as f:
        f.write("\n".join(DOCS[3:]) + "\n")
    import zstandard
    raw = fixture_dir / "social"
    raw.mkdir(exist_ok=True)
    recs = _social_records()
    for name, part in (("Romania_comments.zst", recs[:7]), ("cluj_posts.zst", recs[7:])):
        data = ("\n".join(part) + "\n").encode("utf-8")
        (raw / name).write_bytes(zstandard.ZstdCompressor().compress(data))


def install(source: str, mod, fixture_dir: Path) -> list[str]:
    """Replace the module's readers with fixtures; returns extra CLI arguments."""
    mod.COMMIT_EVERY = 2
    if source == "wiki":
        rows = [{"text": t} for t in DOCS]
        mod.open_dataset = lambda: (rows, {"dataset": "fake", "num_rows": len(rows)})
        return []
    if source in ("web", "news"):
        shards = {"a.parquet": DOCS[:4], "b.parquet": DOCS[4:]}
        mod.list_parquet_files = lambda: [f"{mod.HF_PARQUET_DIR}/{n}" for n in sorted(shards)]
        if source == "web":
            mod.iter_texts = lambda path, start: iter(shards[path.rsplit("/", 1)[-1]][start:])
        else:
            def rows(path, start):
                name = path.rsplit("/", 1)[-1]
                out = []
                for t in shards[name]:
                    out += [("ro", t), ("en", "the cat is on the table")]
                return iter(out[start:])
            mod.iter_rows = rows
        return []
    if source == "subs":
        mod.RAW_FILE = fixture_dir / "subs.txt.gz"
        mod.resolve_download_url = lambda: {"url": "fake://subs", "version": "v1"}
        mod.download_resumable = lambda url, dest: None
        return []
    if source == "eu":
        mod.PARTS["europarl"]["raw_file"] = fixture_dir / "europarl.txt.gz"
        mod.PARTS["dgt"]["raw_file"] = fixture_dir / "dgt.txt.gz"
        mod.resolve_download_url = lambda corpus: {"url": f"fake://{corpus}", "version": "v1"}
        mod.download_resumable = lambda url, dest: None
        return []
    if source == "social":
        return ["--raw-dir", str(fixture_dir / "social"),
                "--fetch-checkpoint", str(fixture_dir / "no-fetch-checkpoint.json")]
    raise ValueError(source)


def run(source: str, fixture_dir: Path, args: list[str]) -> int:
    mod = load(source)
    extra = install(source, mod, fixture_dir)
    saved = sys.argv
    sys.argv = [f"ingest_{source}.py", *extra, *args]
    try:
        return mod.main()
    finally:
        sys.argv = saved
