#!/usr/bin/env python3
"""Stage 1 (subs source) — ingest OpenSubtitles RO into `source_counts`.

M3 of docs/wrodfreq-spec.md §13: "closest open thing to spoken Romanian" (spec
§6, `subs` row) — and per §6.2's own warning, the *safe* choice specifically:
oțios's own `subtitle_ro` corpus turned out to be ~1/6th folk-music broadcast
TV, not real dialogue. OpenSubtitles (film/TV subtitle text) is the corpus §6.2
explicitly calls "a different and safer animal."

Source: OPUS's OpenSubtitles Romanian monolingual export, resolved dynamically
via the OPUS API (https://opus.nlpl.eu/opusapi?corpus=OpenSubtitles&source=ro
&preprocessing=mono&version=latest) rather than a hardcoded object-storage URL,
since OPUS bumps corpus versions over time (v1 seen through v2024 at time of
writing) and the API always resolves "latest" to the current one. The resolved
URL/version is pinned into the checkpoint on first run so a later --resume
can't silently switch versions mid-job if OPUS publishes a new one meanwhile.
No licence file ships with this export; OPUS's own standard disclaimer for
OpenSubtitles is that copyright remains with the original subtitle authors/
uploaders and it is collected for research use only (Lison & Tiedemann 2016).

Structural difference from every other ingester here: this is *one* big gzip
file (~3.5 GB compressed at v2024), not many independently-resumable shards.
So resume works in two stages:
  1. The raw gzip download resumes via a plain HTTP Range request appended to
     a local cache file (data/raw/opensubtitles_ro.txt.gz, gitignored) — cheap
     and safe since the remote object is immutable per pinned version.
  2. Once the local file is fully downloaded, *processing* resumes by
     re-opening it from byte 0 (gzip has no random seek) and fast-forwarding
     — skipping, not tokenizing — to the last checkpointed line number. Wastes
     CPU on resume, not bandwidth; decompressing 3.5 GB locally is well under
     a minute.

The export is one subtitle line per line with no per-file (movie) boundaries
preserved — OPUS does not publish a companion `.ids` file for this release.
`documents` in source_counts therefore counts *lines*, not the 427,889
distinct subtitle files OPUS's own metadata reports for this corpus. That
undercounts independence relative to a true per-movie count (many consecutive
lines share one film/uploader) — flagged here per spec §14's "documents is an
independence claim": this source's `documents` column means something
narrower than wiki/web/news's, where one row is one real document.

No DEX filter, no length filter — same as every other ingester, via
wrodfreq.tokenizer directly (spec §3).

Usage:
    python build/ingest_subs.py              # full run (download, then ingest)
    python build/ingest_subs.py --test       # first 200k lines only, no resume
    python build/ingest_subs.py --limit N    # stop after N total lines processed
    python build/ingest_subs.py --resume     # pick up from checkpoint

Restart loop (stop automatically on success):
    while true; do
        python -u build/ingest_subs.py --resume
        [ $? -eq 0 ] && break
        echo "[$(date)] restarting in 15s..." && sleep 15
    done

Output: data/wrodfreq.db, source_id='subs'. Checkpoint: data/checkpoints/subs_checkpoint.json
Raw download cached at: data/raw/opensubtitles_ro.txt.gz (gitignored, shared
freely between --test and real runs — it's just cached source bytes, not
progress; only the *processing* checkpoint is test/real-sensitive, see below).
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

SOURCE_ID     = "subs"
DISPLAY_NAME  = "OpenSubtitles RO"
URL           = "https://opus.nlpl.eu/OpenSubtitles"
LICENCE       = ("No formal licence — per OPUS's standard disclaimer, copyright "
                  "remains with the original subtitle authors/uploaders; collected "
                  "for research use only (Lison & Tiedemann 2016)")
REGISTER      = "conversational"
PERIOD        = "contemporary"

OPUS_API = "https://opus.nlpl.eu/opusapi"

RAW_FILE      = Path("data/raw/opensubtitles_ro.txt.gz")
CHECKPOINT    = Path("data/checkpoints/subs_checkpoint.json")
DOWNLOAD_CHUNK = 1 << 20        # 1 MiB
COMMIT_EVERY   = 500_000        # lines between DB commits

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    if _shutdown:
        # See build/ingest_news.py's activity-history entries (2026-09-06) for
        # why this is os._exit() and not `raise SystemExit`: a raised
        # exception on the main thread can sit forever if the interpreter is
        # blocked in a non-daemon background thread (observed there with
        # huggingface_hub's retry machinery; urllib is simpler but a stuck
        # DNS/TLS call is not impossible), and CPython won't tear down the
        # process while any such thread is alive. os._exit() terminates at
        # the OS level immediately, unconditionally.
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

def resolve_download_url() -> dict:
    """Look up the current OpenSubtitles RO monolingual export via the OPUS API.

    Returns {'url': ..., 'version': ...} for the raw (untokenized) .txt.gz
    variant — OPUS also publishes a '.tok.gz' pre-tokenized variant we don't
    want, since it uses OPUS's own tokenization conventions, not ours.
    """
    api_url = (f"{OPUS_API}?corpus=OpenSubtitles&source=ro"
               f"&preprocessing=mono&version=latest")
    with urllib.request.urlopen(api_url, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    candidates = [c for c in data.get("corpora", []) if c["url"].endswith(".txt.gz")]
    if not candidates:
        raise RuntimeError(f"OPUS API returned no raw mono .txt.gz entry: {data}")
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
        print(f"  download already complete ({existing:,} bytes)", flush=True)
        return

    mode = "ab" if existing else "wb"
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    if existing:
        print(f"  resuming download at {existing:,}/{total:,} bytes", flush=True)
    else:
        print(f"  downloading {total:,} bytes...", flush=True)

    req = urllib.request.Request(url, headers=headers)
    start = time.time()
    with urllib.request.urlopen(req, timeout=60) as resp:
        if existing and resp.status != 206:
            # Server ignored the Range request (some proxies do) — restart clean.
            print("  server did not honor Range request, restarting download", flush=True)
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
                if since_log >= 100 * (1 << 20):  # every 100 MiB
                    elapsed = time.time() - start
                    rate = existing / elapsed / (1 << 20) if elapsed > 0 else 0
                    pct = 100 * existing / total if total else 0
                    print(f"  {existing:,}/{total:,} bytes ({pct:.1f}%) | "
                          f"{rate:.1f} MiB/s", flush=True)
                    since_log = 0

    final_size = dest.stat().st_size
    if total and final_size != total:
        raise RuntimeError(f"download incomplete: {final_size:,}/{total:,} bytes "
                            f"— rerun to resume")
    print(f"  download complete: {final_size:,} bytes", flush=True)


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

def _empty_checkpoint() -> dict:
    return {
        "download_url": None,
        "version": None,
        "lines_done": 0,
        "tokens_done": 0,
    }


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        try:
            cp = _empty_checkpoint()
            cp.update(json.loads(CHECKPOINT.read_text()))
            return cp
        except (json.JSONDecodeError, ValueError):
            print("warning: corrupted checkpoint, starting fresh")
    return _empty_checkpoint()


def save_checkpoint(cp: dict) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT.with_suffix(".tmp")
    tmp.write_text(json.dumps(cp, indent=2))
    tmp.replace(CHECKPOINT)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    global CHECKPOINT
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                         help="Process first 200k lines only, no resume")
    parser.add_argument("--limit", type=int,
                         help="Stop after N total lines processed")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    if args.test:
        # --test already means "no resume"; keep it from writing progress
        # into the checkpoint a real --resume would trust, same fix as
        # ingest_news.py's --test/--db footgun (activity-history 2026-09-06).
        CHECKPOINT = CHECKPOINT.with_name(CHECKPOINT.stem + ".test.json")

    line_limit = 200_000 if args.test else args.limit

    cp = load_checkpoint() if args.resume else _empty_checkpoint()

    if cp["download_url"] is None:
        print("Resolving OpenSubtitles RO download URL via OPUS API...", flush=True)
        resolved = resolve_download_url()
        cp["download_url"] = resolved["url"]
        cp["version"] = resolved["version"]
        save_checkpoint(cp)
    print(f"  {cp['download_url']} (version {cp['version']})", flush=True)

    print("Downloading (resumable)...", flush=True)
    download_resumable(cp["download_url"], RAW_FILE)
    if _shutdown:
        return 1

    period_note = (f"OPUS OpenSubtitles {cp['version']} Romanian monolingual export; "
                    f"movie release dates not preserved in this text-only export — "
                    f"treat as an undated snapshot of film/TV dialogue. `documents` "
                    f"counts subtitle lines, not the underlying films (no per-file "
                    f"boundary data in this release).")

    conn = connect(args.db)
    ensure_source_row(conn, period_note)

    print(f"Processing lines (skipping first {cp['lines_done']:,} already counted)...",
          flush=True)

    word_counts: dict = defaultdict(int)
    doc_counts: dict = defaultdict(int)
    since_flush = 0
    start = time.time()
    session_lines = 0
    exhausted = False

    with gzip.open(RAW_FILE, "rt", encoding="utf-8", errors="replace") as f:
        # Fast-forward past already-counted lines — no random seek into a
        # gzip stream, so this means decompressing from byte 0 every resume.
        if cp["lines_done"]:
            for _ in itertools.islice(f, cp["lines_done"]):
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

            cp["lines_done"] += 1
            cp["tokens_done"] += len(tokens)
            session_lines += 1
            since_flush += 1

            if line_limit is not None and session_lines >= line_limit:
                break

            if since_flush >= COMMIT_EVERY:
                flush(conn, word_counts, doc_counts, cp["tokens_done"], cp["lines_done"],
                      "in_progress")
                save_checkpoint(cp)
                since_flush = 0
                elapsed = time.time() - start
                rate = session_lines / elapsed if elapsed > 0 else 0
                print(f"  {cp['lines_done']:,} lines | {cp['tokens_done']:,} tokens | "
                      f"{rate:.0f} lines/s", flush=True)

        else:
            # Loop completed without break: the file is fully exhausted.
            exhausted = True

    completed = exhausted and not _shutdown
    status = "completed" if completed else "in_progress"
    flush(conn, word_counts, doc_counts, cp["tokens_done"], cp["lines_done"], status)
    save_checkpoint(cp)

    elapsed = time.time() - start
    print(f"\n{'Done' if completed else 'Stopped'}: {cp['lines_done']:,} lines, "
          f"{cp['tokens_done']:,} tokens in {elapsed:.0f}s", flush=True)

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
    return 0 if (completed or not _shutdown) else 1


if __name__ == "__main__":
    sys.exit(main())
