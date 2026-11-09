#!/usr/bin/env python3
"""Read-only structural integrity check of a wROdfreq database (brief B7, Phase 0).

This check never writes to the database. It opens the file with `mode=ro`, streams
every table in primary-key order, and reports each broken invariant by name and
source. `build/validate.py` reuses `check_database()`.

What it checks:

  - Raw counts: per source, `SUM(occurrences) == sources.total_tokens`;
    non-negative totals; every stored occurrence count is positive;
    `0 <= documents <= occurrences`; every source reference exists; every word
    is a valid token key (`tokenize(word) == [word]`).
  - Stage 2 (`source_zipf`): no derived row without its raw row; no raw row
    without its derived row once a source has derived rows; `reliable` agrees
    with the count; `zipf` is NULL exactly when the source abstains, and is
    otherwise the value the count and the denominator give.
  - Stage 3 (`merged`): every row is recomputed from the reliable per-source
    values of the eligible panel. The check reports a stale, missing or extra
    row, and any row that breaks `0 < n_reliable <= n_attesting <= n_sources`.
  - Stage 4 (`lemma_zipf`): finite values, `n_forms >= 1`, `family_ratio >= 1`
    within FAMILY_RATIO_TOLERANCE, and `zipf_headword` equal to the surface
    value of the citation form.

It does not sum per-word `documents` and compare the result with
`sources.total_docs`. Those units do not add that way. It also does not test
exact distinct-document semantics for migrated rows; the migrations approximate
`documents` on purpose.

Usage:
    python build/integrity.py --db PATH [--json REPORT] [--quiet]

Exit status: 0 if no invariant is broken, 1 if one is, 2 if the database cannot be
opened.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.tokenizer import tokenize
from wrodfreq.zipf import MIN_OCC_PER_SOURCE, merge_zipf, zipf_from_counts

# Two floating-point paths that compute the same Zipf value agree to far better
# than this; a real defect moves a value by orders of magnitude more.
ZIPF_TOLERANCE = 1e-9
# The matched-panel family_ratio is >= 1 by construction (ADR-005). Only
# floating-point noise may take it below 1.
FAMILY_RATIO_TOLERANCE = 1e-9
MAX_EXAMPLES = 5
VALID_STATUS = {"in_progress", "completed", "rejected"}
VALID_PERIOD = {"contemporary", "mixed", "historical"}


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """A read-only connection. Fails (and creates nothing) if the file is absent."""
    if not Path(db_path).is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    return sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)


@dataclass
class Report:
    issues: dict[str, dict] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    def add(self, code: str, source: str | None, example) -> None:
        key = f"{code}:{source}" if source else code
        entry = self.issues.setdefault(
            key, {"code": code, "source": source, "count": 0, "examples": []})
        entry["count"] += 1
        if len(entry["examples"]) < MAX_EXAMPLES:
            entry["examples"].append(example)

    @property
    def ok(self) -> bool:
        return not self.issues

    def sources_with_issues(self) -> list[str]:
        return sorted({i["source"] for i in self.issues.values() if i["source"]})

    def to_json(self) -> dict:
        return {"ok": self.ok, "issues": sorted(self.issues.values(),
                                                 key=lambda i: (i["code"], i["source"] or "")),
                "stats": self.stats}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def _grouped(cursor, key_index: int = 0):
    """Yield (word, [rows]) from a cursor ordered by word."""
    for word, rows in itertools.groupby(cursor, key=lambda r: r[key_index]):
        yield word, list(rows)


def _check_sources(conn, report: Report) -> dict[str, dict]:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sources)")]
    sources = {}
    for row in conn.execute("SELECT * FROM sources ORDER BY source_id"):
        s = dict(zip(cols, row))
        sources[s["source_id"]] = s
        sid = s["source_id"]
        if not isinstance(s["total_tokens"], int) or s["total_tokens"] < 0:
            report.add("negative_or_invalid_total_tokens", sid, s["total_tokens"])
        if not isinstance(s["total_docs"], int) or s["total_docs"] < 0:
            report.add("negative_or_invalid_total_docs", sid, s["total_docs"])
        if s["status"] not in VALID_STATUS:
            report.add("invalid_status", sid, s["status"])
        if s["period"] not in VALID_PERIOD:
            report.add("invalid_period", sid, s["period"])
    return sources


def eligible_from(sources: dict[str, dict]) -> list[str]:
    """Same rule as wrodfreq.db.eligible_sources(), from already-read rows."""
    return sorted(sid for sid, s in sources.items()
                  if s["period"] == "contemporary" and s["status"] == "completed")


def check_database(conn: sqlite3.Connection, progress: bool = False) -> Report:
    """Stream every table and return the broken invariants. Never writes."""
    report = Report()
    started = time.time()
    tables = _tables(conn)
    for required in ("sources", "source_counts"):
        if required not in tables:
            report.add("missing_table", None, required)
            return report

    sources = _check_sources(conn, report)
    eligible = eligible_from(sources)
    eligible_set = set(eligible)
    totals = {sid: s["total_tokens"] for sid, s in sources.items()}
    has_zipf = "source_zipf" in tables
    has_merged = "merged" in tables

    # Sources whose stage-2 rows exist at all: for those, every raw row needs a
    # derived row. A source with none has simply not been through stage 2.
    derived_sources: set[str] = set()
    if has_zipf:
        derived_sources = {sid for (sid,) in conn.execute(
            "SELECT DISTINCT source_id FROM source_zipf")}

    sums = {sid: 0 for sid in sources}
    rows_per_source = {sid: 0 for sid in sources}
    docs_sums = {sid: 0 for sid in sources}
    # Same canonical form as provenance.logical_count_hashes(): word order within
    # a source, `word\tocc\tdocs\n`.
    hashers = {sid: hashlib.sha256() for sid in sources}
    n_words = n_merged = 0

    counts_cur = conn.cursor().execute(
        "SELECT word, source_id, occurrences, documents FROM source_counts "
        "ORDER BY word, source_id")
    groups = {"c": _grouped(counts_cur)}
    if has_zipf:
        zipf_cur = conn.cursor().execute(
            "SELECT word, source_id, zipf, reliable FROM source_zipf ORDER BY word, source_id")
        groups["z"] = _grouped(zipf_cur)
    if has_merged:
        merged_cur = conn.cursor().execute(
            "SELECT word, zipf, n_reliable, n_attesting, n_sources, zipf_min, zipf_max, "
            "spread FROM merged ORDER BY word")
        groups["m"] = _grouped(merged_cur)

    heads = {k: next(g, None) for k, g in groups.items()}
    while True:
        live = [h[0] for h in heads.values() if h is not None]
        if not live:
            break
        word = min(live)
        rows = {}
        for k in heads:
            h = heads[k]
            if h is not None and h[0] == word:
                rows[k] = h[1]
                heads[k] = next(groups[k], None)
        n_words += 1
        if progress and n_words % 2_000_000 == 0:
            print(f"  ... {n_words:,} words checked, {time.time() - started:.0f}s",
                  file=sys.stderr, flush=True)

        counts = {r[1]: r for r in rows.get("c", ())}
        if counts and tokenize(word) != [word]:
            for sid in counts:
                report.add("invalid_token_key", sid, word)
        for sid, (_, _, occ, docs) in counts.items():
            if sid not in sources:
                report.add("orphan_count_source", sid, word)
                continue
            if not isinstance(occ, int) or occ <= 0:
                report.add("nonpositive_occurrences", sid, [word, occ])
            if not isinstance(docs, int) or docs < 0 or (isinstance(occ, int) and docs > occ):
                report.add("documents_out_of_range", sid, [word, occ, docs])
            if isinstance(occ, int):
                sums[sid] += occ
            if isinstance(docs, int):
                docs_sums[sid] += docs
            rows_per_source[sid] += 1
            hashers[sid].update(f"{word}\t{occ}\t{docs}\n".encode("utf-8"))

        derived = {r[1]: r for r in rows.get("z", ())}
        reliable_eligible: list[float] = []
        attesting_eligible = 0
        for sid, (_, _, zipf, reliable) in derived.items():
            if sid not in sources:
                report.add("orphan_zipf_source", sid, word)
                continue
            if sid not in counts:
                report.add("derived_row_without_count", sid, word)
                continue
            occ = counts[sid][2]
            expect_reliable = int(occ >= MIN_OCC_PER_SOURCE)
            if reliable not in (0, 1) or reliable != expect_reliable:
                report.add("reliable_flag_wrong", sid, [word, occ, reliable])
            elif reliable:
                if not _finite(zipf):
                    report.add("zipf_not_finite", sid, [word, zipf])
                elif totals[sid] <= 0 or abs(zipf - zipf_from_counts(occ, totals[sid])) > ZIPF_TOLERANCE:
                    report.add("zipf_stale", sid, [word, occ, zipf])
            elif zipf is not None:
                report.add("abstention_not_null", sid, [word, zipf])
            if sid in eligible_set:
                attesting_eligible += 1
                if reliable == 1 and _finite(zipf):
                    reliable_eligible.append(zipf)
        for sid in counts:
            if sid in derived_sources and sid not in derived:
                report.add("count_row_without_derived", sid, word)

        if has_merged:
            m = rows.get("m")
            if m:
                n_merged += 1
                _, zipf, n_rel, n_att, n_src, zmin, zmax, spread = m[0]
                if not (_finite(zipf) and isinstance(n_rel, int) and isinstance(n_att, int)
                        and isinstance(n_src, int) and 0 < n_rel <= n_att <= n_src):
                    report.add("merged_counts_out_of_order", None, [word, n_rel, n_att, n_src])
                if not (_finite(spread) and spread >= 0):
                    report.add("merged_spread_invalid", None, [word, spread])
            if not has_zipf:
                continue
            if reliable_eligible:
                expected = (merge_zipf(reliable_eligible), len(reliable_eligible),
                            attesting_eligible, len(eligible), min(reliable_eligible),
                            max(reliable_eligible))
                if not m:
                    report.add("merged_row_missing", None, word)
                else:
                    _, zipf, n_rel, n_att, n_src, zmin, zmax, spread = m[0]
                    got_ok = (
                        (n_rel, n_att, n_src) == expected[1:4]
                        and _finite(zipf) and abs(zipf - expected[0]) <= ZIPF_TOLERANCE
                        and _finite(zmin) and abs(zmin - expected[4]) <= ZIPF_TOLERANCE
                        and _finite(zmax) and abs(zmax - expected[5]) <= ZIPF_TOLERANCE
                        and _finite(spread)
                        and abs(spread - (expected[5] - expected[4])) <= ZIPF_TOLERANCE)
                    if not got_ok:
                        report.add("merged_row_stale", None,
                                   [word, [zipf, n_rel, n_att, n_src], list(expected[:4])])
            elif m:
                report.add("merged_row_without_reliable_source", None, word)

    for sid, s in sources.items():
        if sums[sid] != s["total_tokens"]:
            report.add("token_sum_mismatch", sid,
                       {"sum_occurrences": sums[sid], "total_tokens": s["total_tokens"],
                        "excess": sums[sid] - s["total_tokens"]})
        if sid in eligible_set and sid not in derived_sources and rows_per_source[sid]:
            report.add("stage2_not_computed", sid, rows_per_source[sid])

    lemma_stats = _check_lemmas(conn, report, has_merged) if "lemma_zipf" in tables else {}

    report.stats = {**lemma_stats,
        "sources": {sid: {"total_tokens": s["total_tokens"], "sum_occurrences": sums[sid],
                          "rows": rows_per_source[sid], "status": s["status"]}
                    for sid, s in sources.items()},
        "count_hashes": {sid: {"sha256": hashers[sid].hexdigest(), "rows": rows_per_source[sid],
                               "sum_occurrences": sums[sid], "sum_documents": docs_sums[sid]}
                         for sid in sources},
        "eligible": eligible,
        "words": n_words,
        "merged_rows": n_merged,
        "elapsed_s": round(time.time() - started, 1),
    }
    return report


def _check_lemmas(conn, report: Report, has_merged: bool) -> dict:
    n = 0
    below = 0
    for lemma, zipf, n_forms, hw, ratio in conn.execute(
            "SELECT lemma, zipf, n_forms, zipf_headword, family_ratio FROM lemma_zipf"):
        n += 1
        if not _finite(zipf):
            report.add("lemma_zipf_not_finite", None, [lemma, zipf])
        if not isinstance(n_forms, int) or n_forms < 1:
            report.add("lemma_n_forms_invalid", None, [lemma, n_forms])
        if hw is not None and not _finite(hw):
            report.add("lemma_headword_not_finite", None, [lemma, hw])
        if ratio is None or not _finite(ratio):
            report.add("lemma_family_ratio_missing", None, [lemma, ratio])
        elif ratio < 1 - FAMILY_RATIO_TOLERANCE:
            below += 1
            report.add("lemma_family_ratio_below_one", None, [lemma, ratio])
    if has_merged:
        for lemma, hw, mz in conn.execute(
                "SELECT l.lemma, l.zipf_headword, m.zipf FROM lemma_zipf l "
                "LEFT JOIN merged m ON m.word = l.lemma"):
            if (hw is None) != (mz is None) or (
                    hw is not None and abs(hw - mz) > ZIPF_TOLERANCE):
                report.add("lemma_headword_stale", None, [lemma, hw, mz])
    return {"lemmas": n, "lemma_ratio_below_one": below}


def print_report(report: Report) -> None:
    for issue in sorted(report.issues.values(), key=lambda i: (i["code"], i["source"] or "")):
        where = f" [{issue['source']}]" if issue["source"] else ""
        print(f"  FAIL {issue['code']}{where}: {issue['count']:,} — e.g. "
              f"{json.dumps(issue['examples'][:3], ensure_ascii=False)}")
    if report.ok:
        print("  ok — no broken invariant")
    else:
        bad = report.sources_with_issues()
        print(f"  {len(report.issues)} broken invariant(s)"
              f"{'; sources: ' + ', '.join(bad) if bad else ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only integrity check of a wROdfreq db")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--json", type=Path, help="Write the report as JSON to this path")
    parser.add_argument("--quiet", action="store_true", help="No progress lines")
    args = parser.parse_args()

    try:
        conn = open_readonly(args.db)
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"cannot open {args.db} read-only: {exc}", file=sys.stderr)
        return 2
    try:
        print(f"integrity check of {args.db} (read-only)")
        report = check_database(conn, progress=not args.quiet)
    finally:
        conn.close()
    print_report(report)
    if args.json:
        if args.json.resolve() == args.db.resolve():
            print("refusing to write the report over the database", file=sys.stderr)
            return 2
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
