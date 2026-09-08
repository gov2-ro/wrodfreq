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
    python build/ingest_eu.py --test       # first 20k lines of each part only, no resume
    python build/ingest_eu.py --limit N    # stop after N lines processed *per part*
    python build/ingest_eu.py --resume     # pick up from checkpoint

Restart loop (stop automatically on success):
    while true; do
        python -u build/ingest_eu.py --resume
        [ $? -eq 0 ] && break
        echo "[$(date)] restarting in 15s..." && sleep 15
    done

Output: data/wrodfreq.db, source_id='eu'. Checkpoint: data/checkpoints/eu_checkpoint.json
Raw downloads cached at: data/raw/europarl_ro.txt.gz, data/raw/dgt_ro.txt.gz
(gitignored, shared freely between --test and real runs — just cached source
bytes, not progress; only the *processing* checkpoint is test/real-sensitive).
"""

from __future__ import annotations

import argparse
import gzip
import itertools
import json
import os
import signal
import sqlite3
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect
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

CHECKPOINT    = Path("data/checkpoints/eu_checkpoint.json")
DOWNLOAD_CHUNK = 1 << 20        # 1 MiB
COMMIT_EVERY   = 100_000        # lines between DB commits

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
              f"(progress since the last checkpoint is lost)", flush=True)
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


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def ensure_source_row(conn: sqlite3.Connection, period_note: str) -> None:
    conn.execute(
        """
        INSERT INTO sources (source_id, display_name, url, licence, register,
                              period, period_note, total_tokens, total_docs, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'in_progress')
        ON CONFLICT(source_id) DO UPDATE SET period_note = excluded.period_note
        """,
        (SOURCE_ID, DISPLAY_NAME, URL, LICENCE, REGISTER, PERIOD, period_note),
    )
    conn.commit()


def flush(conn: sqlite3.Connection, word_counts: dict, doc_counts: dict,
          total_tokens: int, total_docs: int, status: str) -> None:
    if word_counts:
        conn.executemany(
            """
            INSERT INTO source_counts (word, source_id, occurrences, documents)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(word, source_id) DO UPDATE SET
                occurrences = occurrences + excluded.occurrences,
                documents   = documents   + excluded.documents
            """,
            [(w, SOURCE_ID, c, doc_counts[w]) for w, c in word_counts.items()],
        )
        word_counts.clear()
        doc_counts.clear()
    conn.execute(
        """
        UPDATE sources SET total_tokens = ?, total_docs = ?, status = ?,
                            ingested_at = ?
        WHERE source_id = ?
        """,
        (total_tokens, total_docs, status,
         datetime.now(timezone.utc).isoformat(), SOURCE_ID),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def _empty_part_checkpoint() -> dict:
    return {"download_url": None, "version": None, "lines_done": 0,
            "tokens_done": 0, "completed": False}


def _empty_checkpoint() -> dict:
    return {"parts": {key: _empty_part_checkpoint() for key in PARTS}}


def load_checkpoint() -> dict:
    cp = _empty_checkpoint()
    if CHECKPOINT.exists():
        try:
            loaded = json.loads(CHECKPOINT.read_text())
            for key in PARTS:
                if key in loaded.get("parts", {}):
                    cp["parts"][key].update(loaded["parts"][key])
        except (json.JSONDecodeError, ValueError):
            print("warning: corrupted checkpoint, starting fresh")
    return cp


def save_checkpoint(cp: dict) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT.with_suffix(".tmp")
    tmp.write_text(json.dumps(cp, indent=2))
    tmp.replace(CHECKPOINT)


# ---------------------------------------------------------------------------
# Per-part processing
# ---------------------------------------------------------------------------

def process_part(key: str, part_cp: dict, raw_file: Path, conn: sqlite3.Connection,
                  cp: dict, line_limit: int | None) -> bool:
    """Download (if needed) and ingest one part. Returns True if fully exhausted."""
    if part_cp["download_url"] is None:
        print(f"  Resolving {key} download URL via OPUS API...", flush=True)
        resolved = resolve_download_url(PARTS[key]["opus_corpus"])
        part_cp["download_url"] = resolved["url"]
        part_cp["version"] = resolved["version"]
        save_checkpoint(cp)
    print(f"  [{key}] {part_cp['download_url']} (version {part_cp['version']})", flush=True)

    download_resumable(part_cp["download_url"], raw_file)
    if _shutdown:
        return False

    print(f"  [{key}] processing (skipping first {part_cp['lines_done']:,} "
          f"already counted)...", flush=True)

    word_counts: dict = defaultdict(int)
    doc_counts: dict = defaultdict(int)
    since_flush = 0
    session_lines = 0
    exhausted = False

    with gzip.open(raw_file, "rt", encoding="utf-8", errors="replace") as f:
        if part_cp["lines_done"]:
            for _ in itertools.islice(f, part_cp["lines_done"]):
                pass

        for line in f:
            if _shutdown:
                break

            tokens = tokenize(line)
            doc_words: set = set()
            for tok in tokens:
                word_counts[tok] += 1
                doc_words.add(tok)
            for w in doc_words:
                doc_counts[w] += 1

            part_cp["lines_done"] += 1
            part_cp["tokens_done"] += len(tokens)
            session_lines += 1
            since_flush += 1

            if line_limit is not None and session_lines >= line_limit:
                break

            if since_flush >= COMMIT_EVERY:
                _flush_total(conn, cp, word_counts, doc_counts, "in_progress")
                save_checkpoint(cp)
                since_flush = 0
                print(f"    [{key}] {part_cp['lines_done']:,} lines | "
                      f"{part_cp['tokens_done']:,} tokens", flush=True)
        else:
            exhausted = True

    part_cp["completed"] = exhausted and not _shutdown
    _flush_total(conn, cp, word_counts, doc_counts,
                 "in_progress")  # overall status decided by caller
    save_checkpoint(cp)
    print(f"  [{key}] {'done' if part_cp['completed'] else 'stopped'}: "
          f"{part_cp['lines_done']:,} lines, {part_cp['tokens_done']:,} tokens",
          flush=True)
    return part_cp["completed"]


def _flush_total(conn: sqlite3.Connection, cp: dict, word_counts: dict,
                  doc_counts: dict, status: str) -> None:
    total_tokens = sum(p["tokens_done"] for p in cp["parts"].values())
    total_lines = sum(p["lines_done"] for p in cp["parts"].values())
    flush(conn, word_counts, doc_counts, total_tokens, total_lines, status)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    global CHECKPOINT
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                         help="Process first 20k lines of each part only, no resume")
    parser.add_argument("--limit", type=int,
                         help="Stop after N lines processed per part")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    if args.test:
        # Same --test/--db footgun fix as ingest_news.py/ingest_subs.py: keep
        # a disposable smoke run from writing progress into the checkpoint a
        # real --resume would trust.
        CHECKPOINT = CHECKPOINT.with_name(CHECKPOINT.stem + ".test.json")

    line_limit = 20_000 if args.test else args.limit

    cp = load_checkpoint() if args.resume else _empty_checkpoint()
    conn = connect(args.db)
    # Row must exist before any part's flush() UPDATEs it — an UPDATE against
    # a missing row is a silent no-op in SQLite, which would lose every
    # in-progress total until (if ever) this function ran again at the end.
    ensure_source_row(conn, "in progress — resolving parts")

    for key, part in PARTS.items():
        part_cp = cp["parts"][key]
        if part_cp["completed"]:
            print(f"  [{key}] already complete, skipping", flush=True)
            continue
        process_part(key, part_cp, part["raw_file"], conn, cp, line_limit)
        if _shutdown:
            conn.close()
            return 1

    all_done = all(p["completed"] for p in cp["parts"].values())
    version_notes = "; ".join(
        f"{PARTS[k]['opus_corpus']} {p['version']}" for k, p in cp["parts"].items() if p["version"]
    )
    period_note = (f"Combines OPUS monolingual Romanian exports ({version_notes}) — "
                    f"Europarl is spoken-then-transcribed parliamentary proceedings, "
                    f"DGT is written EU legal/legislative translation memory; both "
                    f"bureaucratic-formal, folded into one source_id per spec's panel "
                    f"table. `documents` counts source lines/paragraphs, not the "
                    f"document counts OPUS's own metadata reports.")
    ensure_source_row(conn, period_note)

    status = "completed" if all_done else "in_progress"
    _flush_total(conn, cp, defaultdict(int), defaultdict(int), status)

    total_tokens = sum(p["tokens_done"] for p in cp["parts"].values())
    total_lines = sum(p["lines_done"] for p in cp["parts"].values())
    print(f"\n{'Done' if all_done else 'Stopped'}: {total_lines:,} lines, "
          f"{total_tokens:,} tokens across {len(PARTS)} parts", flush=True)

    unique_words = conn.execute(
        "SELECT COUNT(*) FROM source_counts WHERE source_id = ?", (SOURCE_ID,)
    ).fetchone()[0]
    print(f"Unique words: {unique_words:,}")

    rows = conn.execute(
        """
        SELECT word, occurrences, documents FROM source_counts
        WHERE source_id = ? ORDER BY occurrences DESC LIMIT 20
        """,
        (SOURCE_ID,),
    ).fetchall()
    if rows:
        print("\nTop 20 by occurrence:")
        for word, occurrences, documents in rows:
            print(f"  {word:20s} {occurrences:>12,}  ({documents:,} docs)")

    conn.close()
    # 1 only for a real signal-interrupted stop (needs --resume); hitting
    # --test/--limit intentionally is a normal exit, same convention as
    # ingest_subs.py — otherwise the restart-loop's `[ $? -eq 0 ] && break`
    # would treat a deliberate --limit run as needing an automatic retry.
    return 0 if not _shutdown else 1


if __name__ == "__main__":
    sys.exit(main())
