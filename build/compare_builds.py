#!/usr/bin/env python3
"""Compare a candidate database with a baseline, read-only (brief B7 Phase 6 step 9).

Reports, as JSON and a printed summary:

  - per source: total tokens, raw rows, SUM(occurrences), the logical count hash and
    whether it is unchanged;
  - `merged`: rows, shipped rows (<= MAX_HYPHENS hyphens), words added and removed,
    Zipf deltas (quantiles, largest movers), `n_reliable` transitions;
  - per source: Zipf delta quantiles of `source_zipf` (reliable in both);
  - `lemma_zipf`: rows, Zipf and `family_ratio` deltas, ratios below 1 in each.

Both databases are opened with `mode=ro`; every comparison streams in key order.

Usage:
    python build/compare_builds.py --baseline OLD.db --candidate NEW.db --out report.json
"""

from __future__ import annotations

import argparse
import heapq
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_manifest import MAX_HYPHENS
from provenance import logical_count_hashes
from wrodfreq.db import connect_readonly

TOP = 25


def quantiles(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    v = sorted(values)
    pick = lambda q: v[min(len(v) - 1, int(q * (len(v) - 1)))]  # noqa: E731
    return {"n": len(v), "min": v[0], "p01": pick(0.01), "p10": pick(0.10), "p50": pick(0.5),
            "p90": pick(0.90), "p99": pick(0.99), "max": v[-1],
            "mean": sum(v) / len(v), "changed_over_0.005": sum(abs(x) > 0.005 for x in v)}


def merge_join(cur_a, cur_b):
    """Yield (key, row_a | None, row_b | None) from two cursors ordered by their first column."""
    a, b = next(cur_a, None), next(cur_b, None)
    while a is not None or b is not None:
        if b is None or (a is not None and a[0] < b[0]):
            yield a[0], a, None
            a = next(cur_a, None)
        elif a is None or b[0] < a[0]:
            yield b[0], None, b
            b = next(cur_b, None)
        else:
            yield a[0], a, b
            a, b = next(cur_a, None), next(cur_b, None)


def compare_merged(old, new) -> dict:
    sql = ("SELECT word, zipf, n_reliable, n_attesting, n_sources, spread, "
           "length(word) - length(replace(word, '-', '')) FROM merged ORDER BY word")
    deltas, movers_up, movers_down = [], [], []
    added, removed = [], []
    n_old = n_new = ship_old = ship_new = 0
    transitions = Counter()
    for word, a, b in merge_join(old.execute(sql), new.execute(sql)):
        if a:
            n_old += 1
            ship_old += a[6] <= MAX_HYPHENS
        if b:
            n_new += 1
            ship_new += b[6] <= MAX_HYPHENS
        if a and b:
            d = b[1] - a[1]
            deltas.append(d)
            item = (abs(d), word, round(a[1], 4), round(b[1], 4), a[2], b[2])
            if d >= 0:
                (heapq.heappush if len(movers_up) < TOP else heapq.heappushpop)(movers_up, item)
            else:
                (heapq.heappush if len(movers_down) < TOP else heapq.heappushpop)(movers_down, item)
            if a[2] != b[2]:
                transitions[f"{a[2]}->{b[2]}"] += 1
        elif a:
            removed.append((word, round(a[1], 4), a[2]))
        else:
            added.append((word, round(b[1], 4), b[2]))
    removed.sort(key=lambda r: -r[1])
    added.sort(key=lambda r: -r[1])
    return {
        "rows": {"baseline": n_old, "candidate": n_new},
        "shipped_rows": {"baseline": ship_old, "candidate": ship_new},
        "added": len(added), "removed": len(removed),
        "added_top": added[:TOP], "removed_top": removed[:TOP],
        "zipf_delta": quantiles(deltas),
        "largest_increases": sorted(movers_up, reverse=True),
        "largest_decreases": sorted(movers_down, reverse=True),
        "n_reliable_transitions": dict(transitions.most_common()),
    }


def compare_source_zipf(old, new) -> dict:
    sql = ("SELECT word || char(1) || source_id, zipf, reliable FROM source_zipf "
           "ORDER BY word, source_id")
    per = {}
    for key, a, b in merge_join(old.execute(sql), new.execute(sql)):
        sid = key.split("\x01", 1)[1]
        p = per.setdefault(sid, {"deltas": [], "added": 0, "removed": 0,
                                 "reliable_gained": 0, "reliable_lost": 0})
        if a and b:
            if a[2] and b[2]:
                p["deltas"].append(b[1] - a[1])
            elif b[2] and not a[2]:
                p["reliable_gained"] += 1
            elif a[2] and not b[2]:
                p["reliable_lost"] += 1
        elif a:
            p["removed"] += 1
        else:
            p["added"] += 1
    return {sid: {**{k: v for k, v in p.items() if k != "deltas"},
                  "zipf_delta": quantiles(p["deltas"])} for sid, p in sorted(per.items())}


def compare_lemmas(old, new) -> dict:
    sql = "SELECT lemma, zipf, family_ratio FROM lemma_zipf ORDER BY lemma"
    zd, rd, ratio_movers = [], [], []
    added = removed = 0
    below = {"baseline": 0, "candidate": 0}
    for lemma, a, b in merge_join(old.execute(sql), new.execute(sql)):
        if a and a[2] is not None and a[2] < 1:
            below["baseline"] += 1
        if b and b[2] is not None and b[2] < 1 - 1e-9:
            below["candidate"] += 1
        if a and b:
            zd.append(b[1] - a[1])
            if a[2] is not None and b[2] is not None:
                rd.append(b[2] - a[2])
                item = (abs(b[2] - a[2]), lemma, a[2], b[2])
                (heapq.heappush if len(ratio_movers) < TOP else heapq.heappushpop)(
                    ratio_movers, item)
        elif a:
            removed += 1
        else:
            added += 1
    n = {"baseline": old.execute("SELECT COUNT(*) FROM lemma_zipf").fetchone()[0],
         "candidate": new.execute("SELECT COUNT(*) FROM lemma_zipf").fetchone()[0]}
    return {"rows": n, "added": added, "removed": removed, "zipf_delta": quantiles(zd),
            "family_ratio_delta": quantiles(rd), "ratio_below_one": below,
            "largest_ratio_changes": sorted(ratio_movers, reverse=True)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare a candidate db with a baseline")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--baseline-counts", type=Path,
                        help="Precomputed baseline logical hashes (JSON with a 'counts' key)")
    args = parser.parse_args()
    old, new = connect_readonly(args.baseline), connect_readonly(args.candidate)
    print("logical count hashes...", flush=True)
    if args.baseline_counts:
        old_counts = json.loads(args.baseline_counts.read_text())["counts"]
    else:
        old_counts = logical_count_hashes(old)
    new_counts = logical_count_hashes(new)
    src = lambda c: {r[0]: r[1:] for r in c.execute(  # noqa: E731
        "SELECT source_id, total_tokens, total_docs, status FROM sources")}
    so, sn = src(old), src(new)
    sources = {}
    for sid in sorted(set(so) | set(sn)):
        oc, nc = old_counts.get(sid, {}), new_counts.get(sid, {})
        sources[sid] = {"total_tokens": [so.get(sid, [None])[0], sn.get(sid, [None])[0]],
                        "total_docs": [so.get(sid, [None, None])[1], sn.get(sid, [None, None])[1]],
                        "rows": [oc.get("rows"), nc.get("rows")],
                        "sum_occurrences": [oc.get("sum_occurrences"), nc.get("sum_occurrences")],
                        "counts_unchanged": oc.get("sha256") == nc.get("sha256")}
    print("merged...", flush=True)
    merged = compare_merged(old, new)
    print("source_zipf...", flush=True)
    per_source = compare_source_zipf(old, new)
    print("lemma_zipf...", flush=True)
    lemmas = compare_lemmas(old, new)
    report = {"sources": sources, "merged": merged, "source_zipf": per_source,
              "lemma": lemmas}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    for sid, s in sources.items():
        print(f"  {sid:7s} tokens {s['total_tokens'][0]:>15,} -> {s['total_tokens'][1]:>15,}  "
              f"rows {s['rows'][0]:>11,} -> {s['rows'][1]:>11,}  "
              f"{'unchanged' if s['counts_unchanged'] else 'CHANGED'}")
    print(f"  merged rows {merged['rows']}, shipped {merged['shipped_rows']}, "
          f"+{merged['added']:,} / -{merged['removed']:,}")
    print(f"  merged zipf delta: {merged['zipf_delta']}")
    print(f"  lemma: {lemmas['rows']}, ratio<1 {lemmas['ratio_below_one']}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
