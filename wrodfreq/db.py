"""The wROdfreq schema (docs/wrodfreq-spec.md §7.1) and a connect() helper.

One SQLite file, `data/wrodfreq.db`, gitignored — see the repo's `.gitignore` and
CLAUDE.md's "no `.db` in git, ever" rule.

Deviation from the §7.1 code sample: `sources.zipf_floor` is added here. §8.1
says explicitly to "store the resulting per-source Zipf floor in `sources` so it
is inspectable"; the DDL sample in §7.1 omits the column. The column is added to
satisfy the narrative requirement — see docs/activity-history.md.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

DEFAULT_DB_PATH = Path("data/wrodfreq.db")

SCHEMA_SQL = """
-- one row per source
CREATE TABLE IF NOT EXISTS sources (
  source_id     TEXT PRIMARY KEY,      -- 'web', 'news', ...
  display_name  TEXT NOT NULL,
  url           TEXT,
  licence       TEXT,
  register      TEXT NOT NULL,         -- web | news | conversational | encyclopedic | literary | formal | social
  period        TEXT NOT NULL,         -- contemporary | mixed | historical
  period_note   TEXT,                  -- e.g. '1945-present, undated'
  total_tokens  INTEGER NOT NULL,      -- ALL alphabetic tokens (spec §3.2)
  total_docs    INTEGER NOT NULL,
  zipf_floor    REAL,                  -- derived reliability floor, spec §8.1
  ingested_at   TIMESTAMP,
  status        TEXT DEFAULT 'in_progress'   -- in_progress | completed | rejected
);

-- raw surface-form counts, per source
CREATE TABLE IF NOT EXISTS source_counts (
  word          TEXT NOT NULL,
  source_id     TEXT NOT NULL REFERENCES sources(source_id),
  occurrences   INTEGER NOT NULL,
  documents     INTEGER NOT NULL,
  PRIMARY KEY (word, source_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_sc_word ON source_counts(word);

-- derived: per-source Zipf, with the abstention flag (spec §8.1)
CREATE TABLE IF NOT EXISTS source_zipf (
  word          TEXT NOT NULL,
  source_id     TEXT NOT NULL,
  zipf          REAL,                  -- NULL when below that source's floor
  reliable      INTEGER NOT NULL,      -- 1 = clears the floor, 0 = abstains
  PRIMARY KEY (word, source_id)
) WITHOUT ROWID;

-- the published table (spec §8.2) — not populated until merge.py exists
CREATE TABLE IF NOT EXISTS merged (
  word            TEXT PRIMARY KEY,
  zipf            REAL NOT NULL,       -- trimmed mean over reliable contemporary sources
  n_reliable      INTEGER NOT NULL,    -- how many sources cleared the floor  <- corroboration
  n_attesting     INTEGER NOT NULL,    -- how many saw it at all (>=1 occurrence)
  n_sources       INTEGER NOT NULL,    -- how many were eligible to see it
  zipf_min        REAL,
  zipf_max        REAL,
  spread          REAL,                -- zipf_max - zipf_min  <- register/diachrony signal
  is_dex          INTEGER DEFAULT 0    -- present in the DEX lexeme set
) WITHOUT ROWID;

-- optional layer (spec §9) — not populated until build_lemma_layer.py exists
CREATE TABLE IF NOT EXISTS lemma_zipf (
  lemma           TEXT PRIMARY KEY,
  zipf            REAL NOT NULL,
  n_forms         INTEGER NOT NULL,
  zipf_headword   REAL,                -- the citation form alone, for comparison
  family_ratio    REAL                 -- loose / disambiguated, same source panel (ADR-005)
) WITHOUT ROWID;

-- the identity of this database file (ADR-004)
CREATE TABLE IF NOT EXISTS db_meta (
  key            TEXT PRIMARY KEY,
  value          TEXT NOT NULL
);

-- authoritative ingest progress, committed with the counts it describes (ADR-004)
CREATE TABLE IF NOT EXISTS ingest_state (
  source_id      TEXT PRIMARY KEY,
  state_version  INTEGER NOT NULL,
  generation     TEXT NOT NULL,        -- one id per fresh ingest or legacy adoption
  seq            INTEGER NOT NULL,     -- +1 for every transaction that changes counts
  state          TEXT NOT NULL,        -- in_progress | completed | sealed_legacy
  cursor_version INTEGER NOT NULL,
  cursor         TEXT NOT NULL,        -- source-specific JSON position
  policy_hash    TEXT NOT NULL,        -- tokenizer + source policy, or 'unknown-legacy'
  input_manifest TEXT NOT NULL,        -- canonical JSON of the raw inputs
  input_hash     TEXT NOT NULL,
  upstream       TEXT NOT NULL,        -- JSON; known snapshot/revision, or unknown
  db_id          TEXT NOT NULL,        -- db_meta.db_id of the database this state belongs to
  provenance     TEXT NOT NULL DEFAULT '{}',
  started_at     TEXT,
  updated_at     TEXT
);

-- what each derived stage was computed from (ADR-006)
CREATE TABLE IF NOT EXISTS stage_state (
  stage          TEXT PRIMARY KEY,     -- 'zipf:<source_id>' | 'merge' | 'lemma'
  run_id         TEXT NOT NULL,        -- hash of `inputs`
  inputs         TEXT NOT NULL         -- canonical JSON
) WITHOUT ROWID;
"""


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the wROdfreq SQLite database with schema applied.

    This writes: it applies the schema and gives the file an identity. Use
    `connect_readonly()` to inspect a database.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=60)
    conn.executescript(SCHEMA_SQL)
    conn.execute("INSERT OR IGNORE INTO db_meta (key, value) VALUES ('db_id', ?)",
                 (uuid.uuid4().hex,))
    conn.commit()
    return conn


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open an existing database read-only. Creates nothing; fails if it is absent."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    return sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)


def db_id(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute("SELECT value FROM db_meta WHERE key = 'db_id'").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


# Shared by build/merge.py and build/build_lemma_layer.py, whose lemma-level
# panel must be the same set of sources the surface-form merge used.
def eligible_sources(conn: sqlite3.Connection) -> list[str]:
    """Contemporary, fully-ingested sources — the default merge's inputs (spec §6.2)."""
    rows = conn.execute(
        "SELECT source_id FROM sources WHERE period = 'contemporary' "
        "AND status = 'completed' ORDER BY source_id"
    ).fetchall()
    return [r[0] for r in rows]
