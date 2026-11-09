"""Logical content hashes and derived-stage records (ADR-004, ADR-006; brief B7).

A logical hash describes table *content* in a canonical order, independent of the
SQLite page layout. Two copies of a database (a backup, a rebuild) have different
file bytes and equal logical hashes.

Stage records (`stage_state`) say what each derived stage was computed from:

  zipf:<source>  {generation, seq, total_tokens}  — stage 2, per source
  merge          {sources, zipf runs, dex}         — stage 3
  lemma          {merge run, dex map}              — stage 4

Every count-changing transaction deletes all stage records, so a stage whose record
is missing or does not match the current inputs is stale.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_state import canonical, read_states, sha256_text


def _fmt(value) -> str:
    if isinstance(value, float):
        return repr(value)
    return "\\N" if value is None else str(value)


def logical_count_hashes(conn: sqlite3.Connection, sources: list[str] | None = None) -> dict:
    """{source: {sha256, rows, sum_occurrences, sum_documents}} in one PK-order pass.

    Rows are hashed in word order within each source as `word\\tocc\\tdocs\\n`.
    """
    want = None if sources is None else set(sources)
    hashers: dict[str, dict] = {}
    for word, sid, occ, docs in conn.execute(
            "SELECT word, source_id, occurrences, documents FROM source_counts "
            "ORDER BY word, source_id"):
        if want is not None and sid not in want:
            continue
        h = hashers.get(sid)
        if h is None:
            h = hashers[sid] = {"h": hashlib.sha256(), "rows": 0, "occ": 0, "docs": 0}
        h["h"].update(f"{word}\t{occ}\t{docs}\n".encode("utf-8"))
        h["rows"] += 1
        h["occ"] += occ
        h["docs"] += docs
    out = {}
    for sid in sorted(set(hashers) | (want or set())):
        h = hashers.get(sid)
        out[sid] = ({"sha256": h["h"].hexdigest(), "rows": h["rows"],
                     "sum_occurrences": h["occ"], "sum_documents": h["docs"]} if h else
                    {"sha256": hashlib.sha256().hexdigest(), "rows": 0,
                     "sum_occurrences": 0, "sum_documents": 0})
    return out


def table_hash(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict:
    """{sha256, rows} of a query's rows, tab-separated, floats in repr form."""
    h = hashlib.sha256()
    n = 0
    for row in conn.execute(sql, params):
        h.update(("\t".join(_fmt(v) for v in row) + "\n").encode("utf-8"))
        n += 1
    return {"sha256": h.hexdigest(), "rows": n}


def derived_hashes(conn: sqlite3.Connection) -> dict:
    """Logical hashes of the three derived tables (for rebuild comparison)."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out = {}
    if "source_zipf" in tables:
        out["source_zipf"] = table_hash(
            conn, "SELECT word, source_id, zipf, reliable FROM source_zipf "
                  "ORDER BY word, source_id")
    if "merged" in tables:
        out["merged"] = table_hash(conn, "SELECT * FROM merged ORDER BY word")
    if "lemma_zipf" in tables:
        out["lemma_zipf"] = table_hash(conn, "SELECT * FROM lemma_zipf ORDER BY lemma")
    out["sources"] = table_hash(
        conn, "SELECT source_id, display_name, url, licence, register, period, period_note, "
              "total_tokens, total_docs, zipf_floor, status FROM sources ORDER BY source_id")
    return out


# ---------------------------------------------------------------------------
# Stage records
# ---------------------------------------------------------------------------

def run_id(inputs: dict) -> str:
    return sha256_text(canonical(inputs))[:32]


def write_stage(conn: sqlite3.Connection, stage: str, inputs: dict) -> str:
    """Record a stage's inputs. Call inside the transaction that wrote its rows."""
    rid = run_id(inputs)
    conn.execute("INSERT OR REPLACE INTO stage_state (stage, run_id, inputs) VALUES (?,?,?)",
                 (stage, rid, canonical(inputs)))
    return rid


def read_stages(conn: sqlite3.Connection) -> dict[str, dict]:
    try:
        rows = conn.execute("SELECT stage, run_id, inputs FROM stage_state").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {s: {"run_id": r, "inputs": json.loads(i)} for s, r, i in rows}


def zipf_inputs(conn: sqlite3.Connection, source_id: str) -> dict:
    state = read_states(conn).get(source_id)
    total = conn.execute("SELECT total_tokens FROM sources WHERE source_id = ?",
                         (source_id,)).fetchone()
    return {"source": source_id,
            "generation": state and state["generation"],
            "seq": state and state["seq"],
            "total_tokens": total and total[0]}


def stage_problems(conn: sqlite3.Connection, sources: list[str], lemma: bool) -> list[str]:
    """Why the derived stages do not describe the current counts (empty = consistent).

    `sources` is the eligible panel; `lemma` says whether the lemma stage must exist.
    """
    problems: list[str] = []
    stages = read_stages(conn)
    states = read_states(conn)
    zipf_runs = {}
    for sid in sources:
        state = states.get(sid)
        if state is None:
            problems.append(f"{sid}: no ingest_state (unadopted legacy counts?)")
        elif state["state"] not in ("completed", "sealed_legacy"):
            problems.append(f"{sid}: ingest_state is {state['state']}")
        rec = stages.get(f"zipf:{sid}")
        if rec is None:
            problems.append(f"{sid}: stage 2 has not run on the current counts")
            continue
        if rec["inputs"] != zipf_inputs(conn, sid):
            problems.append(f"{sid}: stage 2 ran on generation/seq/total "
                            f"{rec['inputs']} — current is {zipf_inputs(conn, sid)}")
        zipf_runs[sid] = rec["run_id"]
    merge = stages.get("merge")
    if merge is None:
        problems.append("stage 3 (merge) has not run on the current stage 2 output")
    else:
        if merge["inputs"].get("sources") != sources:
            problems.append(f"merge panel {merge['inputs'].get('sources')} != eligible {sources}")
        if merge["inputs"].get("zipf_runs") != zipf_runs:
            problems.append("merge was computed from a different stage 2 output")
    if lemma:
        rec = stages.get("lemma")
        if rec is None:
            problems.append("stage 4 (lemma) has not run on the current merge")
        elif merge is not None and rec["inputs"].get("merge_run") != merge["run_id"]:
            problems.append("lemma layer was computed from a different merge")
    return problems
