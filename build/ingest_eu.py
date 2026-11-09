#!/usr/bin/env python3
"""Stage 1 (eu source) — ingest Europarl + DGT RO into `source_counts`.

M3 of docs/wrodfreq-spec.md §13: "a strong 'does this word exist in formal
use at all' signal" (spec §6, `eu` row, which names both Europarl *and* DGT
as one source). Both are OPUS monolingual Romanian exports, resolved
dynamically via the OPUS API like ingest_subs.py:

  - Europarl v8: European Parliament proceedings (spoken, then transcribed
    and translated) — 10.8M OPUS-tokens, 7,530 documents.
  - DGT v2021: EU legal/legislative translation memory (written original
    register) — 92.6M OPUS-tokens, 27,782 documents.

Both bureaucratic-formal, but distinct genres (spoken-transcribed vs. written
legal), folded into one `source_id='eu'` per the spec's panel table. Both are
tiny compared to wiki/web/news/subs (under 160 MB compressed combined) — this
should finish in minutes, not hours.

Generalizes ingest_subs.py's shape (one big file, no per-file boundaries,
resume = HTTP Range on the download + decompress-and-fast-forward on
processing) over a short, fixed list of two named parts instead of one file.
Each part's resolved URL/version is pinned into the checkpoint on first run,
same reasoning as ingest_subs.py: a later --resume must not silently pick up
a newer OPUS version mid-job.

`documents` counts source lines/paragraphs from each corpus, not the document
counts OPUS's own metadata reports (7,530 / 27,782) — same simplification as
ingest_subs.py, same spec §14 "documents is an independence claim" caveat:
this source's `documents` column means something narrower than wiki/web/
news's, where one row is one real document.

No DEX filter, no length filter — same as every other ingester, via
wrodfreq.tokenizer directly (spec §3).

Usage:
    python build/ingest_eu.py              # full run (minutes)
    python build/ingest_eu.py --test       # first 20k lines of each part, new experiment db
    python build/ingest_eu.py --limit N    # stop after N lines per part *in this session*
    python build/ingest_eu.py --resume     # continue the generation stored in the db

Restart loop (stop automatically on success):
    while true; do
        python -u build/ingest_eu.py --resume
        [ $? -eq 0 ] && break
        echo "[$(date)] restarting in 15s..." && sleep 15
    done

Output: data/wrodfreq.db, source_id='eu'. Progress: `ingest_state` in the same db,
committed with the counts (ADR-004). Both parts' URLs/versions are resolved once, at
the fresh start, and pinned as the generation's input manifest.
Raw downloads cached at: data/raw/europarl_ro.txt.gz, data/raw/dgt_ro.txt.gz
(gitignored, shared freely between --test and real runs — just cached source
bytes, not progress).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import json
import os
import signal
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_state import Batch, IngestRefused, SourceSpec, begin, target_db
from wrodfreq.db import connect
from wrodfreq.tokenizer import tokenize

SOURCE_ID     = "eu"
DISPLAY_NAME  = "Europarl + DGT RO"
URL           = "https://opus.nlpl.eu/Europarl + https://opus.nlpl.eu/DGT"
LICENCE       = ("Europarl: freely available for research (Koehn 2005). DGT-TM: EU "
                  "Commission Decision 2011/833/EU reuse policy — reproduction "
                  "authorised provided the source is acknowledged.")
REGISTER      = "formal"
PERIOD        = "contemporary"

OPUS_API = "https://opus.nlpl.eu/opusapi"

PARTS = {
    "europarl": {"opus_corpus": "Europarl", "raw_file": Path("data/raw/europarl_ro.txt.gz")},
    "dgt":      {"opus_corpus": "DGT",      "raw_file": Path("data/raw/dgt_ro.txt.gz")},
}

DOWNLOAD_CHUNK = 1 << 20        # 1 MiB
COMMIT_EVERY   = 100_000        # lines between commits
CURSOR_VERSION = 1

SPEC = SourceSpec(
    source_id=SOURCE_ID, display_name=DISPLAY_NAME, url=URL, licence=LICENCE,
    register=REGISTER, period=PERIOD, period_note=None,
    cursor_version=CURSOR_VERSION,
    policy={"policy_version": 1, "parts": sorted(PARTS), "unit": "line",
            "decode": "utf-8/replace"},
)

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    if _shutdown:
        # os._exit(), not raise SystemExit — see ingest_news.py's
        # activity-history entries (2026-09-06): a raised exception on the
        # main thread can sit forever if the interpreter is blocked in a
        # non-daemon background thread, and CPython won't tear down the
        # process while one is alive. os._exit() terminates at the OS level
        # immediately, unconditionally.
        print(f"\n[{datetime.now()}] second signal {sig} — forcing immediate exit "
              f"(the uncommitted batch is lost and will be re-read)", flush=True)
        os._exit(1)
    print(f"\n[{datetime.now()}] signal {sig} — flushing and exiting after current batch "
          f"(press again to force quit immediately, e.g. if stuck mid-download)", flush=True)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGHUP, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ---------------------------------------------------------------------------
# OPUS API / download
# ---------------------------------------------------------------------------

def resolve_download_url(opus_corpus: str) -> dict:
    """Look up the current Romanian monolingual export for one OPUS corpus.

    Returns {'url': ..., 'version': ...} for the raw (untokenized) .txt.gz
    variant — OPUS also publishes a '.tok.gz' pre-tokenized variant we don't
    want, since it uses OPUS's own tokenization conventions, not ours.
    """
    api_url = (f"{OPUS_API}?corpus={opus_corpus}&source=ro"
               f"&preprocessing=mono&version=latest")
    with urllib.request.urlopen(api_url, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    candidates = [c for c in data.get("corpora", []) if c["url"].endswith(".txt.gz")]
    if not candidates:
        raise RuntimeError(f"OPUS API returned no raw mono .txt.gz entry for "
                            f"{opus_corpus}: {data}")
    best = candidates[0]
    return {"url": best["url"], "version": best["version"]}


def download_resumable(url: str, dest: Path) -> None:
    """Download `url` to `dest`, resuming via HTTP Range if a partial file exists."""
    dest.parent.mkdir(parents=True, exist_ok=True)

    head_req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(head_req, timeout=30) as resp:
        total = int(resp.headers.get("Content-Length", 0))

    existing = dest.stat().st_size if dest.exists() else 0
    if total and existing >= total:
        print(f"    already downloaded ({existing:,} bytes)", flush=True)
        return

    mode = "ab" if existing else "wb"
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    if existing:
        print(f"    resuming download at {existing:,}/{total:,} bytes", flush=True)
    else:
        print(f"    downloading {total:,} bytes...", flush=True)

    req = urllib.request.Request(url, headers=headers)
    start = time.time()
    with urllib.request.urlopen(req, timeout=60) as resp:
        if existing and resp.status != 206:
            print("    server did not honor Range request, restarting download", flush=True)
            existing = 0
            mode = "wb"
        with open(dest, mode) as f:
            since_log = 0
            while True:
                if _shutdown:
                    print(f"\n[{datetime.now()}] signal received — download paused at "
                          f"{existing:,}/{total:,} bytes, resumable", flush=True)
                    return
                chunk = resp.read(DOWNLOAD_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                existing += len(chunk)
                since_log += len(chunk)
                if since_log >= 20 * (1 << 20):  # every 20 MiB — these files are small
                    elapsed = time.time() - start
                    rate = existing / elapsed / (1 << 20) if elapsed > 0 else 0
                    pct = 100 * existing / total if total else 0
                    print(f"    {existing:,}/{total:,} bytes ({pct:.1f}%) | "
                          f"{rate:.1f} MiB/s", flush=True)
                    since_log = 0

    final_size = dest.stat().st_size
    if total and final_size != total:
        raise RuntimeError(f"download incomplete: {final_size:,}/{total:,} bytes "
                            f"— rerun to resume")
    print(f"    download complete: {final_size:,} bytes", flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def empty_cursor() -> dict:
    return {"parts": {key: {"lines_done": 0, "tokens_done": 0, "completed": False,
                            "raw_sha256": None} for key in PARTS}}


def period_note(manifest: dict) -> str:
    versions = "; ".join(f"{PARTS[k]['opus_corpus']} {manifest[k]['version']}" for k in PARTS)
    return (f"Combines OPUS monolingual Romanian exports ({versions}) — "
            f"Europarl is spoken-then-transcribed parliamentary proceedings, "
            f"DGT is written EU legal/legislative translation memory; both "
            f"bureaucratic-formal, folded into one source_id per spec's panel "
            f"table. `documents` counts source lines/paragraphs, not the "
            f"document counts OPUS's own metadata reports.")


# ---------------------------------------------------------------------------
# Per-part processing
# ---------------------------------------------------------------------------

def process_part(key: str, batch: Batch, cursor: dict, url: str,
                 line_limit: int | None) -> str:
    """Download (if needed) and ingest one part.

    Returns 'done' (part exhausted), 'limit', 'signal' or 'refused'. The caller
    commits the final batch.
    """
    part = cursor["parts"][key]
    raw_file = PARTS[key]["raw_file"]
    print(f"  [{key}] {url}", flush=True)
    download_resumable(url, raw_file)
    if _shutdown:
        return "signal"
    raw_sha = sha256_file(raw_file)
    if part["raw_sha256"] not in (None, raw_sha):
        print(f"refused: {raw_file} changed since this generation began; restore it or "
              f"rebuild into an empty staged database", file=sys.stderr)
        return "refused"
    part["raw_sha256"] = raw_sha

    print(f"  [{key}] processing (skipping first {part['lines_done']:,} already counted)...",
          flush=True)
    session_lines = 0
    with gzip.open(raw_file, "rt", encoding="utf-8", errors="replace") as f:
        if part["lines_done"]:
            for _ in itertools.islice(f, part["lines_done"]):
                pass
        for line in f:
            if _shutdown:
                return "signal"
            tokens = tokenize(line)
            batch.add_document(tokens)
            part["lines_done"] += 1
            part["tokens_done"] += len(tokens)
            session_lines += 1
            if line_limit is not None and session_lines >= line_limit:
                return "limit"
            if batch.ndocs >= COMMIT_EVERY:
                batch.commit(cursor)
                print(f"    [{key}] {part['lines_done']:,} lines | "
                      f"{part['tokens_done']:,} tokens", flush=True)
    part["completed"] = True
    print(f"  [{key}] done: {part['lines_done']:,} lines, {part['tokens_done']:,} tokens",
          flush=True)
    return "done"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Europarl + DGT RO into source_counts")
    parser.add_argument("--test", action="store_true",
                         help="First 20k lines of each part, into a new experiment database")
    parser.add_argument("--limit", type=int,
                         help="Stop after N lines per part in this session")
    parser.add_argument("--resume", action="store_true",
                         help="Continue the generation stored in the database")
    parser.add_argument("--db", type=Path, default=None,
                         help="Target database (default data/wrodfreq.db)")
    args = parser.parse_args()

    try:
        db_path = target_db(SOURCE_ID, args.db, args.test, args.resume)
    except IngestRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    line_limit = 20_000 if args.test else args.limit

    def manifest() -> dict:
        print("Resolving download URLs via the OPUS API...", flush=True)
        return {key: resolve_download_url(part["opus_corpus"]) for key, part in PARTS.items()}

    conn = connect(db_path)
    try:
        session = begin(conn, SPEC, resume=args.resume, initial_cursor=empty_cursor(),
                        input_manifest=manifest, verify_manifest_on_resume=False)
    except IngestRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        conn.close()
        return 2
    if session.finished:
        conn.close()
        return 0

    pinned = session.input_manifest
    cursor = json.loads(json.dumps(session.cursor))
    batch = Batch(session)
    for key in PARTS:
        if cursor["parts"][key]["completed"]:
            print(f"  [{key}] already complete, skipping", flush=True)
            continue
        outcome = process_part(key, batch, cursor, pinned[key]["url"], line_limit)
        if outcome == "refused":
            conn.close()
            return 2
        if outcome != "done":
            batch.commit(cursor)
            conn.close()
            return 1 if outcome == "signal" else 0
        batch.commit(cursor)

    batch.commit(cursor, complete=True, period_note=period_note(pinned),
                 provenance={"raw_files": {k: {"name": PARTS[k]["raw_file"].name,
                                               "sha256": cursor["parts"][k]["raw_sha256"]}
                                           for k in PARTS}})
    total_tokens = sum(p["tokens_done"] for p in cursor["parts"].values())
    total_lines = sum(p["lines_done"] for p in cursor["parts"].values())
    print(f"\nDone: {total_lines:,} lines, {total_tokens:,} tokens across {len(PARTS)} parts",
          flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
