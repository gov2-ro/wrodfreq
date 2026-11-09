"""Transactional ingest progress, shared by all six ingesters (ADR-004, brief B7 Phase 1).

The authoritative cursor lives in the same SQLite file as the counts. One batch is
one transaction: check the writer's generation and sequence, apply the count and
document deltas, move the source totals by the same deltas, store the new cursor,
and commit. A batch that does not commit leaves no trace and stays replayable.
The caller clears its in-memory buffers only after `commit_batch()` returns.

JSON checkpoints are no longer written or read. The old files under
`data/checkpoints/` are evidence only; `build/adopt_legacy.py` records their hashes.

States of `ingest_state.state`:
  in_progress    a generation is being ingested; `--resume` continues it
  completed      the generation consumed all of its input; `--resume` is a no-op
  sealed_legacy  counts adopted from before this table existed; `--resume` is a no-op

Fault injection for the kill tests: set `WRODFREQ_FAULT=<point>:<n>` and the
process exits with status 86 the n-th time it reaches <point>. Points:
`before_commit`, `during_apply`, `after_commit`. In-process tests replace
`FAULT_HOOK` instead.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wrodfreq.db import DEFAULT_DB_PATH, db_id as read_db_id

STATE_VERSION = 1
TOKENIZER_PATH = ROOT / "wrodfreq" / "tokenizer.py"
EXPERIMENT_DIR = ROOT / "data" / "experiments"
CANONICAL_DB = (ROOT / "data" / "wrodfreq.db").resolve()
APPLY_CHUNK = 50_000
FAULT_EXIT = 86


class IngestRefused(RuntimeError):
    """The requested run would mix generations or trust unverifiable state."""


class StaleWriter(IngestRefused):
    """Another writer committed since this session read the state."""


# ---------------------------------------------------------------------------
# Canonical JSON, fingerprints
# ---------------------------------------------------------------------------

def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tokenizer_hash() -> str:
    return hashlib.sha256(TOKENIZER_PATH.read_bytes()).hexdigest()


def policy_hash(source_id: str, policy: dict) -> str:
    """Fingerprint of everything that decides which tokens a source contributes."""
    return sha256_text(canonical({"source_id": source_id, "tokenizer": tokenizer_hash(),
                                  "policy": policy}))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Fault injection
# ---------------------------------------------------------------------------

_fault_hits: dict[str, int] = {}


def _env_fault(point: str) -> None:
    spec = os.environ.get("WRODFREQ_FAULT")
    if not spec:
        return
    want, _, n = spec.partition(":")
    if want != point:
        return
    _fault_hits[point] = _fault_hits.get(point, 0) + 1
    if _fault_hits[point] == int(n or 1):
        sys.stdout.flush()
        os._exit(FAULT_EXIT)


FAULT_HOOK: Callable[[str], None] = _env_fault


def fault(point: str) -> None:
    FAULT_HOOK(point)


# ---------------------------------------------------------------------------
# Target database selection (experiment isolation)
# ---------------------------------------------------------------------------

def is_canonical(path: Path) -> bool:
    resolved = Path(path).resolve()
    return resolved in (CANONICAL_DB, Path(DEFAULT_DB_PATH).resolve())


def target_db(source_id: str, db: Path | None, test: bool, resume: bool) -> Path:
    """The database a run writes to.

    A `--test` run never writes to the canonical database. Without `--db` it gets
    a fresh, uniquely named experiment database under `data/experiments/`.
    """
    if test:
        if resume:
            raise IngestRefused("--test always starts a fresh experiment; it cannot --resume")
        if db is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = EXPERIMENT_DIR / f"{source_id}-{stamp}-{uuid.uuid4().hex[:6]}.db"
            print(f"--test: writing to experiment database {path}", flush=True)
            return path
        if is_canonical(db):
            raise IngestRefused(
                f"--test refuses the canonical database {db}; omit --db to get an "
                f"experiment database under {EXPERIMENT_DIR}")
        return db
    return DEFAULT_DB_PATH if db is None else db


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------

@dataclass
class SourceSpec:
    source_id: str
    display_name: str
    url: str | None
    licence: str | None
    register: str
    period: str
    period_note: str | None
    cursor_version: int
    policy: dict


@dataclass
class IngestSession:
    conn: sqlite3.Connection
    spec: SourceSpec
    generation: str
    seq: int
    state: str
    cursor: dict
    input_manifest: dict
    provenance: dict = field(default_factory=dict)

    @property
    def finished(self) -> bool:
        """True for a completed or sealed generation: the run is a no-op."""
        return self.state in ("completed", "sealed_legacy")

    def commit_batch(self, occ: dict, docs: dict, cursor: dict, tokens_delta: int,
                     docs_delta: int, complete: bool = False,
                     period_note: str | None = None, provenance: dict | None = None) -> None:
        """Apply one batch atomically. Leaves `occ`/`docs` untouched; clear them after."""
        if self.finished:
            raise IngestRefused(f"{self.spec.source_id}: generation is {self.state}")
        conn = self.conn
        sid = self.spec.source_id
        fault("before_commit")
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT generation, seq, state, db_id FROM ingest_state WHERE source_id = ?",
                (sid,)).fetchone()
            if row is None or tuple(row[:3]) != (self.generation, self.seq, "in_progress"):
                raise StaleWriter(
                    f"{sid}: state moved under this writer (expected generation "
                    f"{self.generation} seq {self.seq}, found {row and tuple(row[:3])}) — "
                    f"refusing to apply the batch")
            if row[3] != read_db_id(conn):
                raise IngestRefused(f"{sid}: ingest_state belongs to another database")
            items = [(w, sid, c, docs.get(w, 0)) for w, c in occ.items()]
            for i in range(0, len(items), APPLY_CHUNK):
                conn.executemany(
                    """INSERT INTO source_counts (word, source_id, occurrences, documents)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(word, source_id) DO UPDATE SET
                           occurrences = occurrences + excluded.occurrences,
                           documents   = documents   + excluded.documents""",
                    items[i:i + APPLY_CHUNK])
                fault("during_apply")
            status = "completed" if complete else "in_progress"
            conn.execute(
                "UPDATE sources SET total_tokens = total_tokens + ?, "
                "total_docs = total_docs + ?, status = ?, ingested_at = ?"
                + (", period_note = ?" if period_note is not None else "")
                + " WHERE source_id = ?",
                (tokens_delta, docs_delta, status, now(),
                 *((period_note,) if period_note is not None else ()), sid))
            new_prov = dict(self.provenance)
            if provenance:
                new_prov.update(provenance)
            conn.execute(
                "UPDATE ingest_state SET seq = seq + 1, cursor = ?, state = ?, "
                "provenance = ?, updated_at = ? WHERE source_id = ?",
                (canonical(cursor), status, canonical(new_prov), now(), sid))
            # Every derived stage now describes counts that no longer exist.
            conn.execute("DELETE FROM stage_state")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        self.seq += 1
        self.cursor = json.loads(canonical(cursor))
        self.state = status
        self.provenance = new_prov
        fault("after_commit")


class Batch:
    """In-memory deltas for one batch. Cleared only after a successful commit."""

    def __init__(self, session: IngestSession):
        self.session = session
        self.occ: dict[str, int] = defaultdict(int)
        self.docs: dict[str, int] = defaultdict(int)
        self.tokens = 0
        self.ndocs = 0

    def add_document(self, tokens: list[str]) -> None:
        for w in tokens:
            self.occ[w] += 1
        for w in set(tokens):
            self.docs[w] += 1
        self.tokens += len(tokens)
        self.ndocs += 1

    def commit(self, cursor: dict, complete: bool = False, **kw) -> None:
        self.session.commit_batch(self.occ, self.docs, cursor, self.tokens, self.ndocs,
                                  complete=complete, **kw)
        self.occ.clear()
        self.docs.clear()
        self.tokens = 0
        self.ndocs = 0


def _source_has_data(conn: sqlite3.Connection, sid: str) -> tuple[bool, tuple | None]:
    has_counts = conn.execute(
        "SELECT 1 FROM source_counts WHERE source_id = ? LIMIT 1", (sid,)).fetchone() is not None
    src = conn.execute("SELECT total_tokens, total_docs, status FROM sources WHERE source_id = ?",
                       (sid,)).fetchone()
    nonempty = has_counts or (src is not None and (src[0] or src[1] or src[2] == "completed"))
    return nonempty, src


def begin(conn: sqlite3.Connection, spec: SourceSpec, *, resume: bool,
          initial_cursor: dict,
          input_manifest: dict | Callable[[], dict] | None,
          upstream: dict | None = None,
          verify_manifest_on_resume: bool = True) -> IngestSession:
    """Open the source's generation for writing, or refuse.

    Fresh run (`resume=False`): refuses any existing state or data for the source.
    Resume: continues the stored generation after checking that it belongs to this
    database, uses the same cursor format, and was made with the same tokenizer and
    source policy. If `input_manifest` is given it must equal the stored one.
    With nothing stored and no data, a resume starts fresh (restart loops pass
    `--resume` from the first iteration). `input_manifest` may be a callable; it is
    evaluated only after the refusal checks pass, so a refused fresh run downloads
    nothing. A source whose inputs are pinned at the first run (subs, eu: an OPUS
    URL and version) passes `verify_manifest_on_resume=False`, reads the pinned
    manifest from the session, and verifies its raw file itself.
    """
    conn.isolation_level = None          # explicit BEGIN/COMMIT in commit_batch()
    sid = spec.source_id
    this_db = read_db_id(conn)
    if this_db is None:
        raise IngestRefused("database has no db_meta.db_id — open it with wrodfreq.db.connect()")
    row = conn.execute(
        "SELECT state_version, generation, seq, state, cursor_version, cursor, policy_hash, "
        "input_manifest, input_hash, db_id, provenance FROM ingest_state WHERE source_id = ?",
        (sid,)).fetchone()
    nonempty, src = _source_has_data(conn, sid)
    p_hash = policy_hash(sid, spec.policy)

    if row is None:
        if nonempty:
            if src is not None and src[2] == "completed":
                hint = (f"run `python build/adopt_legacy.py --db <db> --source {sid}` to seal "
                        f"the completed counts, or ingest into an empty staged database")
            else:
                hint = ("its old JSON checkpoint cannot be trusted against these counts; "
                        "rebuild the source into an empty staged database")
            raise IngestRefused(f"{sid}: the database already holds counts for this source "
                                f"but no ingest_state — {hint}")
        manifest = input_manifest() if callable(input_manifest) else (input_manifest or {})
        generation = uuid.uuid4().hex
        stamp = now()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """INSERT INTO sources (source_id, display_name, url, licence, register,
                                        period, period_note, total_tokens, total_docs, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'in_progress')
                   ON CONFLICT(source_id) DO UPDATE SET
                     display_name = excluded.display_name, url = excluded.url,
                     licence = excluded.licence, register = excluded.register,
                     period = excluded.period, period_note = excluded.period_note,
                     total_tokens = 0, total_docs = 0, status = 'in_progress'""",
                (sid, spec.display_name, spec.url, spec.licence, spec.register, spec.period,
                 spec.period_note))
            conn.execute(
                "INSERT INTO ingest_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, STATE_VERSION, generation, 0, "in_progress", spec.cursor_version,
                 canonical(initial_cursor), p_hash, canonical(manifest),
                 sha256_text(canonical(manifest)), canonical(upstream or {"status": "unknown"}),
                 this_db, "{}", stamp, stamp))
            conn.execute("DELETE FROM stage_state")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        print(f"[{sid}] new generation {generation[:12]}", flush=True)
        return IngestSession(conn, spec, generation, 0, "in_progress",
                             json.loads(canonical(initial_cursor)), manifest)

    (state_version, generation, seq, state, cursor_version, cursor, stored_policy,
     stored_manifest, stored_input_hash, owner_db, provenance) = row
    if not resume:
        raise IngestRefused(
            f"{sid}: generation {generation[:12]} already exists ({state}, seq {seq}); a fresh "
            f"run never adds to it. Use --resume to continue it, or an empty staged database.")
    if owner_db != this_db:
        raise IngestRefused(f"{sid}: ingest_state was written by database {owner_db}, not this "
                            f"one ({this_db}); rebind it deliberately when importing a source")
    if state_version != STATE_VERSION:
        raise IngestRefused(f"{sid}: ingest_state version {state_version} is not {STATE_VERSION}")
    session = IngestSession(conn, spec, generation, seq, state, json.loads(cursor),
                            json.loads(stored_manifest), json.loads(provenance))
    if state == "sealed_legacy":
        print(f"[{sid}] sealed legacy counts (generation {generation[:12]}) — nothing to "
              f"resume; a new generation needs an empty staged database", flush=True)
        return session
    if state == "completed":
        print(f"[{sid}] generation {generation[:12]} is already complete — nothing to do",
              flush=True)
        return session
    if cursor_version != spec.cursor_version:
        raise IngestRefused(f"{sid}: stored cursor format v{cursor_version}, this ingester "
                            f"writes v{spec.cursor_version}; rebuild into an empty staged database")
    if stored_policy != p_hash:
        raise IngestRefused(
            f"{sid}: the tokenizer or the source policy changed since generation "
            f"{generation[:12]} began; its counts cannot be continued. Rebuild the source "
            f"into an empty staged database.")
    if input_manifest is not None and verify_manifest_on_resume:
        manifest = input_manifest() if callable(input_manifest) else input_manifest
        if sha256_text(canonical(manifest)) != stored_input_hash:
            raise IngestRefused(
                f"{sid}: the raw inputs changed since generation {generation[:12]} began "
                f"(stored manifest {stored_input_hash[:12]}, now "
                f"{sha256_text(canonical(manifest))[:12]}). Restore the original inputs, or "
                f"rebuild into an empty staged database.\n  stored: "
                f"{_short(json.loads(stored_manifest))}\n  now:    {_short(manifest)}")
    return session


def _short(obj, limit: int = 400) -> str:
    text = canonical(obj)
    return text if len(text) <= limit else text[:limit] + "…"


# ---------------------------------------------------------------------------
# Count changes outside an ingest (migrations, source replacement)
# ---------------------------------------------------------------------------

def record_count_change(conn: sqlite3.Connection, source_id: str, reason: str) -> None:
    """Call inside the transaction that changed `source_id`'s counts.

    Moves the source's sequence on (so a stale ingest writer is refused), notes the
    reason, and invalidates every derived stage.
    """
    row = conn.execute("SELECT provenance FROM ingest_state WHERE source_id = ?",
                       (source_id,)).fetchone()
    if row is not None:
        prov = json.loads(row[0])
        prov.setdefault("count_changes", []).append({"reason": reason, "at": now()})
        conn.execute("UPDATE ingest_state SET seq = seq + 1, provenance = ?, updated_at = ? "
                     "WHERE source_id = ?", (canonical(prov), now(), source_id))
    conn.execute("DELETE FROM stage_state")


def read_states(conn: sqlite3.Connection) -> dict[str, dict]:
    """{source_id: {generation, seq, state, ...}} — empty if the table is absent."""
    try:
        rows = conn.execute(
            "SELECT source_id, generation, seq, state, policy_hash, input_hash, upstream, "
            "provenance, db_id FROM ingest_state").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r[0]: {"generation": r[1], "seq": r[2], "state": r[3], "policy_hash": r[4],
                   "input_hash": r[5], "upstream": json.loads(r[6]),
                   "provenance": json.loads(r[7]), "db_id": r[8]} for r in rows}
