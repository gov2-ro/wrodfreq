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
COMPRESS_EVERY = 250_000  # records buffered to the .ndjson before compacting

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
    tmp.write_text(json.dumps(cp, indent=2))
    tmp.replace(CHECKPOINT)


def compress(ndjson_path: Path, zst_path: Path) -> None:
    """Compact the append-log into the .zst ingest_social.py reads."""
    import zstandard
    if not ndjson_path.exists():
        return
    data = ndjson_path.read_bytes()
    if not data:
        ndjson_path.unlink()
        return
    zst_path.write_bytes(zstandard.ZstdCompressor(level=10).compress(data))
    ndjson_path.unlink()


# ---------------------------------------------------------------------------
# Fetch one subreddit
# ---------------------------------------------------------------------------

def fetch(subreddit: str, kind: str, cp: dict, max_records: int | None) -> int:
    """Page back through one subreddit's history. Returns records written."""
    key = f"{subreddit}/{kind}"
    state = cp.setdefault(key, {"before": None, "count": 0, "done": False})
    if state["done"]:
        print(f"  [{key}] already complete ({state['count']:,} records)", flush=True)
        return 0

    fields = COMMENT_FIELDS if kind == "comments" else SUBMISSION_FIELDS
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ndjson_path = RAW_DIR / f"{subreddit}_{kind}.ndjson"
    zst_path    = RAW_DIR / f"{subreddit}_{kind}.zst"

    if state["count"] == 0 and ndjson_path.exists():
        ndjson_path.unlink()          # stale partial from a discarded run

    written = 0
    start = time.time()
    window: deque[tuple[float, int]] = deque()   # (timestamp, cumulative count)
    fh = ndjson_path.open("a", encoding="utf-8")
    try:
        while not _shutdown:
            if max_records is not None and state["count"] >= max_records:
                print(f"  [{key}] reached --max-records {max_records:,}", flush=True)
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
        fh.close()
        save_checkpoint(cp)

    if state["done"] or (max_records is not None and state["count"] >= max_records):
        compress(ndjson_path, zst_path)
        print(f"  [{key}] wrote {zst_path.name}", flush=True)
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
        if _shutdown:
            break

    save_checkpoint(cp)
    done = all(cp.get(f"{s}/{k}", {}).get("done") for s in subs for k in kinds)
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
