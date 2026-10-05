#!/usr/bin/env python3
"""
Build the inflected-form → lemma map, so corpus counts can be aggregated per lemma.

Why: `process_culturax.py` counts raw tokens, and Romanian is heavily inflected, so a
lemma is only ever credited with its citation form. Measured against the current data:

    înmărmuri   317  →  paradigm ≈ 6,474   (înmărmurit alone is 5,846)
    moleși      310  →  paradigm ≈ 9,205
    cătrăni      59  →  paradigm ≈ 1,842

Every verb in the shortlist is therefore pushed toward `extinct`. The dump already
carries the fix: `InflectedForm` (~9.0M rows) maps each surface form to its `lexemeId`.

Both tables are read in one pass. `InflectedForm` is dumped *before* `Lexeme` (the dump
is ordered by table name), so forms are staged first and joined to lemmas at the end.

`Lexeme` is re-read here rather than taken from `lexemes.db` on purpose: that DB was built
by `extract_lexemes.py`, whose regex tuple-splitter drops ~48k rows (13%) and shifts the
columns of 648 more. This script uses the quote-aware scanner in `dump_parser.py`, so the
`lexeme` table it writes is complete and can be diffed against the old one.

Output: data/processed/inflected_forms.db

    lexeme(lexeme_id PK, lemma, frequency)      complete, unlike lexemes.db
    inflected(form, lexeme_id)                  ~9.0M rows, form is NFC-normalized
    form_lemma(form, lemma, lexeme_id, n_lemmas)
        `form`     — normalized surface form, matches corpus_frequencies.db keys
        `lemma`    — normalized Lexeme.formNoAccent
        `n_lemmas` — how many distinct lemmas claim this form. Ambiguous forms ("mare"
                     is both adjective and noun) are credited to every claimant; callers
                     that care can divide by, or filter on, this column. Over-crediting
                     is the safe direction here — it can only prevent a false "extinct".

Usage:
    python extract_inflected_forms.py                                    # full dump
    python extract_inflected_forms.py --dump data/dictionaries/dex-database-sample.sql
    python extract_inflected_forms.py --limit 100000                     # smoke test
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from dump_parser import normalize, parse_tuples, strip_line_prefix

DEX_SQL_PATH = Path('data/dictionaries/dex-database.sql')
OUTPUT_DB = Path('data/processed/inflected_forms.db')

_IF_PREFIX = "INSERT INTO `InflectedForm` VALUES "
_LX_PREFIX = "INSERT INTO `Lexeme` VALUES "

# InflectedForm: (id, form, formNoAccent, formUtf8General, lexemeId, ...)
_IF_FORM_NO_ACCENT, _IF_LEXEME_ID = 2, 4
# Lexeme: (id, form, formNoAccent, formUtf8General, reverse, number, description,
#          noAccent, consistentAccent, frequency, ...)
_LX_ID, _LX_FORM_NO_ACCENT, _LX_FREQUENCY = 0, 2, 9

BATCH = 50_000


def _to_float(value: str | None) -> float | None:
    """DEX frequency as a real, or None. Guards the column-shifted rows."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def build(dump_path: Path, out_path: Path, limit: int | None) -> dict[str, int]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    conn = sqlite3.connect(str(out_path))
    conn.execute('PRAGMA journal_mode = OFF')
    conn.execute('PRAGMA synchronous = OFF')
    conn.execute('CREATE TABLE inflected (form TEXT, lexeme_id INTEGER)')
    conn.execute('CREATE TABLE lexeme (lexeme_id INTEGER PRIMARY KEY, '
                 'lemma TEXT, frequency REAL)')

    stats = {'inflected': 0, 'lexeme': 0, 'if_skipped': 0, 'lx_skipped': 0}
    if_batch: list[tuple[str, int]] = []
    lx_batch: list[tuple[int, str, float | None]] = []

    def flush() -> None:
        if if_batch:
            conn.executemany('INSERT INTO inflected VALUES (?,?)', if_batch)
            if_batch.clear()
        if lx_batch:
            conn.executemany('INSERT OR IGNORE INTO lexeme VALUES (?,?,?)', lx_batch)
            lx_batch.clear()

    with open(dump_path, encoding='utf-8', errors='replace') as f:
        for lineno, line in enumerate(f, 1):
            if line.startswith(_IF_PREFIX):
                body = strip_line_prefix(line, _IF_PREFIX)
                for t in parse_tuples(body, max_index=_IF_LEXEME_ID):
                    if len(t) <= _IF_LEXEME_ID:
                        stats['if_skipped'] += 1
                        continue
                    form, lex_id = t[_IF_FORM_NO_ACCENT], t[_IF_LEXEME_ID]
                    if not form or not lex_id:
                        stats['if_skipped'] += 1
                        continue
                    try:
                        if_batch.append((normalize(form), int(lex_id)))
                    except ValueError:
                        stats['if_skipped'] += 1
                        continue
                    stats['inflected'] += 1
                if len(if_batch) >= BATCH:
                    flush()
                if limit is not None and stats['inflected'] >= limit:
                    break

            elif line.startswith(_LX_PREFIX):
                body = strip_line_prefix(line, _LX_PREFIX)
                for t in parse_tuples(body, max_index=_LX_FREQUENCY):
                    if len(t) <= _LX_FREQUENCY:
                        stats['lx_skipped'] += 1
                        continue
                    lex_id, form = t[_LX_ID], t[_LX_FORM_NO_ACCENT]
                    if not lex_id or not form:
                        stats['lx_skipped'] += 1
                        continue
                    try:
                        lx_batch.append((int(lex_id), normalize(form),
                                         _to_float(t[_LX_FREQUENCY])))
                    except ValueError:
                        stats['lx_skipped'] += 1
                        continue
                    stats['lexeme'] += 1
                if len(lx_batch) >= BATCH:
                    flush()

            if lineno % 200_000 == 0:
                print(f'  ...{lineno:,} lines · {stats["inflected"]:,} forms · '
                      f'{stats["lexeme"]:,} lexemes', file=sys.stderr)

    flush()
    conn.commit()

    print('  joining forms to lemmas ...', file=sys.stderr)
    conn.execute('CREATE INDEX idx_inflected_lex ON inflected(lexeme_id)')
    conn.execute("""
        CREATE TABLE form_lemma AS
        SELECT i.form                AS form,
               l.lemma               AS lemma,
               i.lexeme_id           AS lexeme_id,
               0                     AS n_lemmas
          FROM inflected i
          JOIN lexeme    l ON l.lexeme_id = i.lexeme_id
         GROUP BY i.form, l.lemma
    """)
    conn.execute('CREATE INDEX idx_fl_form  ON form_lemma(form)')
    conn.execute('CREATE INDEX idx_fl_lemma ON form_lemma(lemma)')
    conn.execute("""
        UPDATE form_lemma
           SET n_lemmas = (SELECT COUNT(*) FROM form_lemma f2 WHERE f2.form = form_lemma.form)
    """)
    conn.commit()

    stats['form_lemma'] = conn.execute('SELECT COUNT(*) FROM form_lemma').fetchone()[0]
    stats['orphan_forms'] = conn.execute(
        'SELECT COUNT(*) FROM inflected i LEFT JOIN lexeme l USING (lexeme_id) '
        'WHERE l.lexeme_id IS NULL').fetchone()[0]
    conn.close()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dump', type=Path, default=DEX_SQL_PATH,
                        help='DEX MySQL dump path (default: %(default)s)')
    parser.add_argument('--out', type=Path, default=OUTPUT_DB,
                        help='output SQLite DB (default: %(default)s)')
    parser.add_argument('--limit', type=int, default=None,
                        help='cap InflectedForm rows processed (smoke test)')
    args = parser.parse_args()

    if not args.dump.exists():
        print(f'Dump not found: {args.dump}', file=sys.stderr)
        return 1

    print(f'Streaming {args.dump} ...', file=sys.stderr)
    stats = build(args.dump, args.out, args.limit)
    print(f'  inflected rows : {stats["inflected"]:,} '
          f'(skipped {stats["if_skipped"]:,})', file=sys.stderr)
    print(f'  lexemes        : {stats["lexeme"]:,} '
          f'(skipped {stats["lx_skipped"]:,})', file=sys.stderr)
    print(f'  form→lemma     : {stats["form_lemma"]:,}', file=sys.stderr)
    print(f'  orphan forms   : {stats["orphan_forms"]:,} (lexemeId not in Lexeme)',
          file=sys.stderr)
    print(f'Wrote {args.out}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
