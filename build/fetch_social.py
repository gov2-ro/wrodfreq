#!/usr/bin/env python3
"""Stage 0 (social source) — fetch Romanian subreddit history over HTTP.

Acquisition only. It writes `data/raw/social/*.zst` in the zstandard-ndjson
shape `ingest_social.py` reads, and does no counting, tokenizing or filtering
of its own — the split matters because the tokenizer has already changed twice
(elision 2026-09-17, doubled hyphens 2026-09-18) and each change would
otherwise have meant re-downloading the corpus instead of re-running a local
stage.

## Why HTTP and not a torrent

There is no per-subreddit torrent. Search results advertise one and Watchful1's
own PushshiftDumps README links one, but every such hash 404s; Academic
Torrents' `database.xml` lists 34 Reddit entries and none is per-subreddit.
The real archive torrent is 464 whole-month files totalling 2.84 TiB, so
pulling r/Romania out of it costs ~50 GiB per month of data. Verified
2026-09-22 — see docs/BACKLOG.md.

Arctic Shift (https://arctic-shift.photon-reddit.com) serves the same archive
per subreddit over plain HTTP with no auth. Measured ~335k comments/hour.

## Only four fields are kept, deliberately

A raw Arctic Shift comment carries ~90 fields (`all_awardings`,
`author_flair_background_color`, …) and runs 3-5 KB. Stored whole, this panel
would be tens of GB of JSON to recover a few hundred MB of Romanian. This
keeps `author`, `body`, `subreddit`, `created_utc` for comments — plus `title`
and `selftext` for submissions — which is exactly what `ingest_social.py`'s
`record_text()` reads, and nothing else. The trade is explicit: re-deriving
anything from score, flair or thread structure later means re-fetching. Text
frequency is what this project counts, so that is an acceptable loss and a
deliberate one rather than an oversight.

## Politeness

This is a volunteer-run service. One request at a time, a fixed delay between
pages, `X-RateLimit-*` honoured when present, exponential backoff on failure,
and a descriptive User-Agent with a contact address. Interrupting is safe and
cheap: progress is checkpointed per subreddit after every page, so a restart
resumes at the page boundary rather than re-walking history.

## Usage

    python build/fetch_social.py --list               # show the panel, fetch nothing
    python build/fetch_social.py --probe              # current activity per subreddit
    python build/fetch_social.py --subreddit Romania --max-records 50000
    python build/fetch_social.py                      # full history, all subreddits
    python build/fetch_social.py --resume             # continue where it stopped

Restart loop (stops automatically on success):
    while true; do
        python -u build/fetch_social.py --resume
        [ $? -eq 0 ] && break
        echo "[$(date)] restarting in 60s..." && sleep 60
    done

Then: python build/ingest_social.py --calibrate   (re-tune before the real run)
      python build/ingest_social.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

API_BASE   = "https://arctic-shift.photon-reddit.com/api"
RAW_DIR    = Path("data/raw/social")
CHECKPOINT = Path("data/checkpoints/social_fetch.json")

PAGE_LIMIT     = 100      # server caps here; larger values return nothing
PAGE_DELAY     = 0.25     # seconds between pages, on top of any rate-limit wait
MAX_BACKOFF    = 300.0
MAX_ATTEMPTS   = 8        # then hand back to the restart loop, see get_page()

USER_AGENT = ("wrodfreq/0.1 (Romanian word-frequency corpus; "
              "https://github.com/gov2-ro/wrodfreq; pax@mioritics.ro)")

# Measured 2026-09-22 with --probe; the dead ones are recorded so nobody
# re-probes them hopefully. Rates are comments/day at that date and are only a
# rough guide to how long each will take.
SUBREDDITS = [
    "Romania",       # ~2,729/day
    "CasualRO",      # ~1,366
    "programare",    # ~889
    "Bucuresti",     # ~828
    "AskRomania",    # ~709
    "moldova",       # ~133 — Moldovan Romanian; heavily code-switched, the
                     #        language filter earns its keep here
    "cluj",          # ~124
    "Iasi",          # ~94
    "Sibiu",         # ~59
    "Timisoara",     # ~53
    "Oradea",        # ~49
    "Craiova",       # ~47
    "Constanta",     # ~33
    "Brasov",        # ~24
    "romani",        # ~12 — marginal, included because it is cheap
]

# Probed and empty or long dead at 2026-09-22: RoGaming, RomaniaMuiePSD,
# RomaniaTravel, RepublicaMoldova, RoStocks, financiarRO, RomaniaCorporate,
# universitate, RomaniaGaming, antiromania (last activity 2022), baniRO,
# transilvania, ITjobsRomania, StiriDinRomania, RomanianFood.

COMMENT_FIELDS    = ("author", "body", "subreddit", "created_utc")
SUBMISSION_FIELDS = ("author", "title", "selftext", "subreddit", "created_utc")

class FetchStalled(RuntimeError):
    """A page failed past MAX_ATTEMPTS. Ends this pass without losing the
    checkpoint, so the restart loop resumes at the same page."""


_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    if _shutdown:
        print(f"\n[{datetime.now()}] second signal {sig} — exiting now "
              f"(the current page is lost, everything checkpointed is safe)", flush=True)
        os._exit(1)
    print(f"\n[{datetime.now()}] signal {sig} — finishing this page then stopping "
          f"(press again to force)", flush=True)
    _shutdown = True


for _s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    signal.signal(_s, _handle_signal)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def get_page(kind: str, subreddit: str, before: int | None) -> list[dict]:
    """One page of `kind` ('comments'|'posts'), newest first, older than `before`.

    Retries with exponential backoff. Raises only when the shutdown flag is set
    mid-backoff, so an operator's Ctrl+C is never swallowed by a retry loop.
    """
    endpoint = "comments" if kind == "comments" else "posts"
    url = f"{API_BASE}/{endpoint}/search?subreddit={subreddit}&limit={PAGE_LIMIT}&sort=desc"
    if before is not None:
        url += f"&before={before}"

    backoff = 2.0
    attempt = 0
    while True:
        attempt += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read())
                remaining = resp.headers.get("X-RateLimit-Remaining")
                reset = resp.headers.get("X-RateLimit-Reset")
                if remaining is not None:
                    try:
                        if int(float(remaining)) <= 1 and reset:
                            wait = min(float(reset), 60.0)
                            print(f"    rate limit reached, waiting {wait:.0f}s", flush=True)
                            time.sleep(wait)
                    except ValueError:
                        pass
                return payload.get("data", [])
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                json.JSONDecodeError, OSError) as exc:
            if _shutdown:
                raise KeyboardInterrupt from exc
            if attempt >= MAX_ATTEMPTS:
                # Give up on this page rather than spin forever. Observed
                # 2026-09-22: Arctic Shift returns a spurious HTTP 422 now and
                # then, which recovers on retry — but a *permanent* 4xx would
                # otherwise loop at MAX_BACKOFF for ever, looking alive while
                # making no progress. Raising here ends this subreddit's pass
                # with its checkpoint intact; run_social_fetch.sh restarts and
                # resumes from exactly this page, so nothing is skipped
                # silently.
                raise FetchStalled(
                    f"{subreddit}/{kind}: {attempt} consecutive failures, "
                    f"last was {exc!r}") from exc
            print(f"    request failed ({exc}); retry {attempt}/{MAX_ATTEMPTS} "
                  f"in {backoff:.0f}s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)


def slim(rec: dict, fields: tuple[str, ...]) -> dict:
    return {f: rec.get(f) for f in fields}


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        try:
            return json.loads(CHECKPOINT.read_text())
        except (json.JSONDecodeError, ValueError):
            print("warning: corrupted fetch checkpoint, starting fresh", flush=True)
    return {}


def save_checkpoint(cp: dict) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT.with_suffix(".tmp")
    with tmp.open("w") as fh:
        fh.write(json.dumps(cp, indent=2))
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(CHECKPOINT)


# ---------------------------------------------------------------------------
# Errors, fault injection
# ---------------------------------------------------------------------------

class Unrecoverable(RuntimeError):
    """Local material is missing or damaged; continuing would lose or invent records.

    Never handled by truncating: the operator must look, restore, or refetch.
    """


def _no_fault(point: str) -> None:
    return None


# Tests replace this to simulate process death between finalization steps.
FAULT_HOOK = _no_fault


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Append-log recovery (B4)
# ---------------------------------------------------------------------------

def rebuild_state_from_file(ndjson_path: Path, key: str) -> tuple[int, float | None, int]:
    """Derive (count, oldest created_utc, byte length) from the append-log itself.

    A missing or incoherent byte count is *unknown*, not zero. The file is the one
    thing that cannot disagree with itself, so read the answer off it.

    - A bad **final** line (no newline, or not a JSON object) is a torn write: it is
      excluded and the returned length stops before it, so the caller trims it.
    - A bad line **followed by valid records** means the file is damaged. That
      raises `Unrecoverable` — truncating there would silently discard every later
      record.
    - Records without a numeric `created_utc` are counted and reported. If no
      record has one, `before` cannot be recovered and `Unrecoverable` is raised.
    """
    count = 0
    oldest: float | None = None
    good_bytes = 0
    bad_at: int | None = None          # offset of a bad line not yet known to be final
    no_ts = 0
    with ndjson_path.open("rb") as fh:
        offset = 0
        for raw in fh:
            start = offset
            offset += len(raw)
            rec = None
            if raw.endswith(b"\n"):
                try:
                    rec = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    rec = None
            if not isinstance(rec, dict):
                if bad_at is None:
                    bad_at = start
                continue
            if bad_at is not None:
                raise Unrecoverable(
                    f"{key}: {ndjson_path.name} has a damaged line at byte {bad_at:,} "
                    f"followed by valid records — refusing to truncate them; inspect or "
                    f"restore the file")
            ts = rec.get("created_utc")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                oldest = ts if oldest is None else min(oldest, ts)
            else:
                no_ts += 1
            count += 1
            good_bytes = offset
    if no_ts:
        print(f"  [{key}] warning: {no_ts:,} record(s) without a numeric created_utc",
              flush=True)
    if count and oldest is None:
        raise Unrecoverable(f"{key}: no record in {ndjson_path.name} has a numeric "
                            f"created_utc, so the resume point cannot be recovered")
    print(f"  [{key}] recovered from file: {count:,} records, "
          f"{good_bytes:,} bytes", flush=True)
    return count, oldest, good_bytes


def _count_lines(path: Path, limit: int | None = None) -> tuple[int, bool]:
    """(newlines in the first `limit` bytes, whether that prefix ends on a newline)."""
    n = 0
    last = b"\n"
    remaining = limit
    with path.open("rb") as fh:
        while remaining is None or remaining > 0:
            block = fh.read(1 << 22 if remaining is None else min(1 << 22, remaining))
            if not block:
                break
            n += block.count(b"\n")
            last = block[-1:]
            if remaining is not None:
                remaining -= len(block)
    return n, last == b"\n"


def recover_append_log(ndjson_path: Path, state: dict, key: str, cp: dict) -> None:
    """Make the append-log and the checkpoint agree exactly, or refuse.

    Coherent checkpoint: `bytes` is an int, at most the file size, on a line
    boundary, and the first `bytes` hold exactly `count` lines. Then anything after
    `bytes` was written after the last checkpoint and is trimmed (resuming from the
    unchanged `before` refetches it). Anything else — `bytes` missing, zero with a
    non-zero count, beyond the end of the file, mid-line, or a line count that
    disagrees — is incoherent, and the state is rebuilt from the file instead.
    """
    if not ndjson_path.exists():
        if state["count"]:
            raise Unrecoverable(
                f"{key}: the checkpoint claims {state['count']:,} records but "
                f"{ndjson_path.name} is missing and the crawl is not finished — restore "
                f"the append log, or delete this key from the checkpoint to refetch")
        state["bytes"] = 0
        return
    size = ndjson_path.stat().st_size
    if state["count"] == 0:
        if size:
            print(f"  [{key}] discarding {size:,} bytes written before any checkpoint",
                  flush=True)
            with ndjson_path.open("r+b") as fh:
                fh.truncate(0)
        state["bytes"] = 0
        return

    nbytes = state.get("bytes")
    reason = None
    if not isinstance(nbytes, int) or isinstance(nbytes, bool):
        reason = "no byte count"
    elif nbytes <= 0:
        reason = f"byte count {nbytes} with {state['count']:,} records"
    elif nbytes > size:
        reason = f"byte count {nbytes:,} beyond the end of the file ({size:,})"
    else:
        lines, on_boundary = _count_lines(ndjson_path, nbytes)
        if not on_boundary:
            reason = f"byte count {nbytes:,} lands mid-line"
        elif lines != state["count"]:
            reason = (f"the first {nbytes:,} bytes hold {lines:,} records, "
                      f"not {state['count']:,}")
    if reason is None:
        if size > nbytes:
            print(f"  [{key}] truncating {size - nbytes:,} unaccounted bytes written after "
                  f"the last checkpoint (would otherwise duplicate)", flush=True)
            with ndjson_path.open("r+b") as fh:
                fh.truncate(nbytes)
        return

    print(f"  [{key}] incoherent checkpoint ({reason}) — rebuilding from the file", flush=True)
    count, oldest, good_bytes = rebuild_state_from_file(ndjson_path, key)
    if count != state["count"]:
        print(f"  [{key}] checkpoint said {state['count']:,} records, the file holds "
              f"{count:,} ({count - state['count']:+,}: the unaccounted window)", flush=True)
    if good_bytes < size:
        with ndjson_path.open("r+b") as fh:
            fh.truncate(good_bytes)
    state["count"] = count
    if oldest is not None:
        state["before"] = oldest
    state["bytes"] = good_bytes
    save_checkpoint(cp)


# ---------------------------------------------------------------------------
# Finalization (brief B7 Phase 2)
# ---------------------------------------------------------------------------

def _scan_zst(zst_path: Path) -> tuple[int, str]:
    """(records, sha256 of the decompressed content), streamed."""
    import zstandard
    h = hashlib.sha256()
    n = 0
    with zst_path.open("rb") as fh:
        reader = zstandard.ZstdDecompressor(max_window_size=2 ** 31).stream_reader(fh)
        for block in iter(lambda: reader.read(1 << 22), b""):
            h.update(block)
            n += block.count(b"\n")
    return n, h.hexdigest()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def compress_verified(ndjson_path: Path, zst_path: Path) -> dict:
    """Stream the append-log into `<zst>.tmp`, verify it, then replace `zst_path`.

    Never reads the whole log into memory. Returns the archive identity.
    """
    import zstandard
    tmp = zst_path.with_name(zst_path.name + ".tmp")
    h = hashlib.sha256()
    records = 0
    try:
        with ndjson_path.open("rb") as src, tmp.open("wb") as dst:
            with zstandard.ZstdCompressor(level=10).stream_writer(dst, closefd=False) as w:
                for block in iter(lambda: src.read(1 << 22), b""):
                    h.update(block)
                    records += block.count(b"\n")
                    w.write(block)
            dst.flush()
            os.fsync(dst.fileno())
        FAULT_HOOK("compressed")
        got_records, got_sha = _scan_zst(tmp)
        if (got_records, got_sha) != (records, h.hexdigest()):
            raise Unrecoverable(f"{tmp.name} does not decompress to {ndjson_path.name}")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, zst_path)
    _fsync_dir(zst_path.parent)
    return {"records": records, "content_sha256": h.hexdigest(),
            "sha256": _file_sha256(zst_path), "size": zst_path.stat().st_size}


def finalize(key: str, state: dict, cp: dict, ndjson_path: Path, zst_path: Path) -> None:
    """Turn an exhausted crawl into a verified `.zst`, recoverably.

    Steps: (1) the append log is durable and matches the checkpoint; (2) history
    exhaustion is `state["done"]`, recorded separately; (3) stream-compress to a
    temporary file; (4) verify record count and content, then replace atomically;
    (5) record the archive identity in `state["finalized"]` — only then is the
    append log removed. A restart at any point repeats or completes the remaining
    steps. A valid archive with no log (the archives made before this existed) is
    verified and kept.
    """
    tmp = zst_path.with_name(zst_path.name + ".tmp")
    tmp.unlink(missing_ok=True)                       # a crash during step 3
    fin = state.get("finalized")
    if fin and zst_path.exists() and _file_sha256(zst_path) == fin["sha256"]:
        if ndjson_path.exists():
            ndjson_path.unlink()                      # a crash between steps 5 and removal
        print(f"  [{key}] already complete ({state['count']:,} records, verified archive)",
              flush=True)
        return
    if ndjson_path.exists():
        recover_append_log(ndjson_path, state, key, cp)
        identity = compress_verified(ndjson_path, zst_path)
        if identity["records"] != state["count"]:
            raise Unrecoverable(f"{key}: archive holds {identity['records']:,} records, "
                                f"checkpoint {state['count']:,}")
        FAULT_HOOK("replaced")
    elif zst_path.exists():
        records, content = _scan_zst(zst_path)
        if records != state["count"]:
            raise Unrecoverable(
                f"{key}: {zst_path.name} holds {records:,} records but the checkpoint "
                f"claims {state['count']:,}, and there is no append log to rebuild from")
        identity = {"records": records, "content_sha256": content,
                    "sha256": _file_sha256(zst_path), "size": zst_path.stat().st_size}
    else:
        raise Unrecoverable(f"{key}: history is exhausted but neither {ndjson_path.name} "
                            f"nor {zst_path.name} exists — the records must be refetched")
    state["finalized"] = identity
    state["bytes"] = 0
    save_checkpoint(cp)
    FAULT_HOOK("recorded")
    ndjson_path.unlink(missing_ok=True)
    print(f"  [{key}] wrote {zst_path.name} ({identity['records']:,} records, verified)",
          flush=True)


def _restore_log_from_archive(key: str, state: dict, ndjson_path: Path, zst_path: Path) -> None:
    """An unfinished crawl compacted by an older version (`--max-records`).

    Decompress the archive back into the append log, if it holds exactly the
    checkpointed records, so the crawl continues without losing its prefix.
    """
    import zstandard
    records, _ = _scan_zst(zst_path)
    if records != state["count"]:
        raise Unrecoverable(
            f"{key}: unfinished crawl has an archive of {records:,} records but the "
            f"checkpoint claims {state['count']:,} — cannot tell which prefix is real")
    tmp = ndjson_path.with_name(ndjson_path.name + ".restore")
    with zst_path.open("rb") as src, tmp.open("wb") as dst:
        zstandard.ZstdDecompressor(max_window_size=2 ** 31).copy_stream(src, dst)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(tmp, ndjson_path)
    state["bytes"] = ndjson_path.stat().st_size
    print(f"  [{key}] restored {records:,} records from {zst_path.name} into the append log",
          flush=True)


# ---------------------------------------------------------------------------
# Fetch one subreddit
# ---------------------------------------------------------------------------

def fetch(subreddit: str, kind: str, cp: dict, max_records: int | None) -> int:
    """Page back through one subreddit's history. Returns records written.

    An unfinished crawl keeps its append log; it is compressed only once history is
    exhausted (`finalize`). `--max-records` stops early and leaves the crawl
    resumable with its whole prefix.
    """
    key = f"{subreddit}/{kind}"
    state = cp.setdefault(key, {"before": None, "count": 0, "done": False, "bytes": 0})
    fields = COMMENT_FIELDS if kind == "comments" else SUBMISSION_FIELDS
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ndjson_path = RAW_DIR / f"{subreddit}_{kind}.ndjson"
    zst_path    = RAW_DIR / f"{subreddit}_{kind}.zst"

    if state["done"]:
        finalize(key, state, cp, ndjson_path, zst_path)
        return 0

    if zst_path.exists():
        if ndjson_path.exists():
            raise Unrecoverable(f"{key}: unfinished crawl has both {ndjson_path.name} and "
                                f"{zst_path.name}; refusing to guess which one is current")
        _restore_log_from_archive(key, state, ndjson_path, zst_path)
        zst_path.unlink()
        save_checkpoint(cp)
    recover_append_log(ndjson_path, state, key, cp)

    written = 0
    start = time.time()
    window: deque[tuple[float, int]] = deque()   # (timestamp, cumulative count)
    fh = ndjson_path.open("a", encoding="utf-8")
    try:
        while not _shutdown:
            if max_records is not None and state["count"] >= max_records:
                print(f"  [{key}] reached --max-records {max_records:,} — crawl left "
                      f"resumable, not compressed", flush=True)
                break
            try:
                batch = get_page(kind, subreddit, state["before"])
            except KeyboardInterrupt:
                break
            except FetchStalled as exc:
                print(f"  [{key}] STALLED: {exc}", flush=True)
                print(f"  [{key}] checkpoint intact at {state['count']:,} records; "
                      f"the restart loop will resume here", flush=True)
                raise
            if not batch:
                state["done"] = True
                print(f"  [{key}] history exhausted at {state['count']:,} records", flush=True)
                break

            stamps = [r["created_utc"] for r in batch if r.get("created_utc")]
            if not stamps:
                state["done"] = True
                break

            for rec in batch:
                fh.write(json.dumps(slim(rec, fields), ensure_ascii=False) + "\n")
            written += len(batch)
            state["count"] += len(batch)

            oldest = min(stamps)
            if state["before"] is not None and oldest >= state["before"]:
                # No progress: the server returned nothing older. Step back one
                # second rather than spin on the same page forever.
                oldest = state["before"] - 1
            state["before"] = oldest

            if state["count"] % 5_000 < PAGE_LIMIT:
                fh.flush()
                os.fsync(fh.fileno())
                state["bytes"] = ndjson_path.stat().st_size
                save_checkpoint(cp)
                # A recent-window rate, not a cumulative average: a couple of
                # 300s network backoffs drag the lifetime average down by an
                # order of magnitude and make a perfectly healthy job look
                # stalled (observed 2026-09-22 — 21,872/h reported while the
                # job was actually sustaining 450,000/h).
                now = time.time()
                window.append((now, state["count"]))
                while len(window) > 1 and now - window[0][0] > 120:
                    window.popleft()
                if len(window) > 1:
                    dt = now - window[0][0]
                    dn = state["count"] - window[0][1]
                    rate = dn / dt * 3600 if dt > 0 else 0.0
                else:
                    rate = written / max(now - start, 1e-9) * 3600
                print(f"    [{key}] {state['count']:,} records | "
                      f"back to {datetime.fromtimestamp(oldest, timezone.utc):%Y-%m-%d} | "
                      f"{rate:,.0f}/h", flush=True)
            time.sleep(PAGE_DELAY)
    finally:
        # Step 1 of finalization, and every stop: the log is durable and the
        # checkpoint's byte count is its exact size.
        fh.flush()
        os.fsync(fh.fileno())
        fh.close()
        state["bytes"] = ndjson_path.stat().st_size if ndjson_path.exists() else 0
        save_checkpoint(cp)

    if state["done"] and not _shutdown:
        finalize(key, state, cp, ndjson_path, zst_path)
    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def probe() -> int:
    print(f"{'subreddit':<18} {'~comments/day':>14}  last activity")
    for sub in SUBREDDITS:
        try:
            batch = get_page("comments", sub, None)
        except KeyboardInterrupt:
            return 1
        if not batch:
            print(f"{sub:<18} {'(no data)':>14}")
            continue
        ts = [r["created_utc"] for r in batch if r.get("created_utc")]
        span_h = (max(ts) - min(ts)) / 3600 if len(ts) > 1 else 0
        rate = f"{len(ts)/span_h*24:,.0f}" if span_h > 0 else "very high"
        print(f"{sub:<18} {rate:>14}  "
              f"{datetime.fromtimestamp(max(ts), timezone.utc):%Y-%m-%d}")
        time.sleep(PAGE_DELAY)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subreddit", action="append",
                        help="Fetch only this subreddit (repeatable)")
    parser.add_argument("--kind", choices=["comments", "posts", "both"], default="both")
    parser.add_argument("--max-records", type=int,
                        help="Stop each subreddit/kind after N records")
    parser.add_argument("--resume", action="store_true",
                        help="Continue from the checkpoint (default is also resume-safe)")
    parser.add_argument("--list", action="store_true", help="Print the panel and exit")
    parser.add_argument("--probe", action="store_true",
                        help="Report current activity per subreddit and exit")
    args = parser.parse_args()

    if args.list:
        print("\n".join(SUBREDDITS))
        return 0
    if args.probe:
        return probe()

    subs = args.subreddit or SUBREDDITS
    kinds = ["comments", "posts"] if args.kind == "both" else [args.kind]
    cp = load_checkpoint()

    print(f"Fetching {len(subs)} subreddit(s) x {len(kinds)} kind(s) from Arctic Shift",
          flush=True)
    total = 0
    t0 = time.time()
    stalled = []
    for sub in subs:
        for kind in kinds:
            if _shutdown:
                break
            try:
                total += fetch(sub, kind, cp, args.max_records)
            except FetchStalled as exc:
                # One wedged subreddit must not block the other fourteen.
                stalled.append(str(exc))
            except Unrecoverable as exc:
                print(f"  UNRECOVERABLE: {exc}", flush=True)
                stalled.append(f"UNRECOVERABLE {exc}")
        if _shutdown:
            break

    save_checkpoint(cp)
    done = all(cp.get(f"{s}/{k}", {}).get("done") and cp.get(f"{s}/{k}", {}).get("finalized")
               for s in subs for k in kinds)
    grand = sum(v["count"] for v in cp.values())
    print(f"\n{total:,} records this run, {grand:,} total on disk, "
          f"{(time.time()-t0)/60:.1f}m", flush=True)
    if stalled:
        print(f"\n{len(stalled)} subreddit(s) stalled this pass:", flush=True)
        for m in stalled:
            print(f"  {m}", flush=True)
    if done and not stalled:
        print("All subreddits exhausted. Next: python build/ingest_social.py --calibrate")
        return 0
    print("Not finished — re-run with --resume to continue.", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
