#!/usr/bin/env python3
"""Point-in-time status of the social fetch (companion to `status.py`, which
covers the db pipeline and knows nothing about acquisition).

Read-only and safe to run against a live crawl, from any shell. It reads the
checkpoint and the raw files; it never writes, and never imports the running
fetcher.

Usage:
    python build/social_status.py
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT       = Path(__file__).resolve().parent.parent
CHECKPOINT = ROOT / "data/checkpoints/social_fetch.json"
LOG        = ROOT / "data/checkpoints/social_fetch.log"
RAW        = ROOT / "data/raw/social"

# Probed comments/day at 2026-09-22, in the fetcher's own panel order.
RATES = {"Romania": 2729, "CasualRO": 1366, "programare": 889, "Bucuresti": 828,
         "AskRomania": 709, "moldova": 133, "cluj": 124, "Iasi": 94, "Sibiu": 59,
         "Timisoara": 53, "Oradea": 49, "Craiova": 47, "Constanta": 33,
         "Brasov": 24, "romani": 12}

# Records per unit of daily rate, measured from the subreddits that finished.
# Smaller subs are younger, so a larger share of their traffic is recent and
# this ratio falls -- scaling off r/Romania's 4,416 overestimates by ~3x.
RECORDS_PER_RATE = 1400
POSTS_SHARE      = 0.045        # posts as a fraction of a sub's comments


def human(n: float) -> str:
    return f"{n:,.0f}"


def main() -> int:
    cp = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else {}

    pids = subprocess.run(["pgrep", "-f", "build/fetch_social.py"],
                          capture_output=True, text=True).stdout.split()
    if pids:
        elapsed = subprocess.run(["ps", "-o", "etime=", "-p", pids[0]],
                                 capture_output=True, text=True).stdout.strip()
        print(f"RUNNING  pid {pids[0]}, up {elapsed}")
    else:
        print("NOT RUNNING — resume with: nohup build/run_social_fetch.sh > /dev/null 2>&1 &")

    done_subs, total = [], 0
    print(f"\n{'subreddit/kind':<26} {'records':>12}  {'oldest reached':<14} state")
    for key, st in cp.items():
        total += st["count"]
        when = (datetime.fromtimestamp(st["before"], timezone.utc).strftime("%Y-%m-%d")
                if st.get("before") else "—")
        state = "complete" if st.get("done") else "in progress"
        print(f"{key:<26} {human(st['count']):>12}  {when:<14} {state}")
        if st.get("done") and key.endswith("/comments"):
            done_subs.append(key.split("/")[0])

    print(f"\n{human(total)} records on disk")

    # Remaining estimate, from the measured ratio rather than a rate probe.
    started = {k.split("/")[0] for k in cp}
    remaining = 0
    for sub, rate in RATES.items():
        if sub in done_subs:
            continue
        projected = rate * RECORDS_PER_RATE * (1 + POSTS_SHARE)
        got = sum(v["count"] for k, v in cp.items() if k.startswith(sub + "/"))
        remaining += max(projected - got, 0)
    pending = [s for s in RATES if s not in done_subs]
    print(f"{len(done_subs)}/{len(RATES)} subreddits complete; "
          f"{len(pending)} to go: {', '.join(pending)}")

    # Throughput from the log's recent-window rates, which already exclude
    # the lifetime-average distortion that made a healthy job look stalled.
    rates = []
    if LOG.exists():
        for line in LOG.read_text(errors="replace").splitlines()[-400:]:
            if line.rstrip().endswith("/h"):
                try:
                    rates.append(float(line.rsplit("|", 1)[1].strip().rstrip("/h").replace(",", "")))
                except (ValueError, IndexError):
                    pass
    if rates and remaining:
        recent = sum(rates[-20:]) / len(rates[-20:])
        # Sustained rate is well below the instantaneous one: backoffs, 422
        # retries and per-subreddit compaction all stall the counter.
        for label, rate in (("at the recent window rate", recent),
                            ("at the sustained average", recent * 0.75)):
            print(f"~{human(remaining)} records left — {human(rate)}/h {label} "
                  f"→ {remaining/rate:.1f}h "
                  f"(~{datetime.fromtimestamp(time.time()+remaining/rate*3600):%a %H:%M})")

    if RAW.exists():
        zst = sum(f.stat().st_size for f in RAW.glob("*.zst"))
        nd  = sum(f.stat().st_size for f in RAW.glob("*.ndjson"))
        print(f"\ndisk: {zst/2**30:.2f} GiB compacted + {nd/2**30:.2f} GiB pending compaction")
    print(subprocess.run(["df", "-h", "/"], capture_output=True, text=True).stdout.splitlines()[-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
