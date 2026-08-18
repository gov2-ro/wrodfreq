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
  family_ratio    REAL                 -- undivided family total / disambiguated total
) WITHOUT ROWID;
"""


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the wROdfreq SQLite database with schema applied."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    return conn
