#!/usr/bin/env python3
"""
Quote-aware scanner for the DEX MySQL dump's `INSERT INTO ... VALUES (...),(...);` lines.

Why this exists: `extract_lexemes.py` splits tuples with `re.findall(r'\\(([^)]+)\\)')`,
which silently drops or mangles any row containing a literal `)` inside a quoted field.
Measured against the full dump that loses ~48k of 365,869 `Lexeme` rows (13%) and leaves
648 column-shifted rows whose `frequency` lands as TEXT. The scanner below tracks string
state and escaping, so a `)` inside a definition or a form is just a character.

The primitives were first written inline in `extract_dict_sources.py`; they are shared by
three extractors now, which is the point at which this repo's "one script per stage" rule
says a helper module is warranted (see CLAUDE.md → Conventions).

Usage:
    from dump_parser import normalize, strip_line_prefix, parse_tuples

    PREFIX = "INSERT INTO `Lexeme` VALUES "
    for line in f:
        if line.startswith(PREFIX):
            for fields in parse_tuples(strip_line_prefix(line, PREFIX), max_index=9):
                lexeme_id, form_no_accent, frequency = fields[0], fields[2], fields[9]
"""

from __future__ import annotations

import unicodedata
from typing import Iterator


def normalize(text: str) -> str:
    """Canonical Romanian normalization used across the pipeline.

    lower → cedilla-to-comma (ş→ș, ţ→ț) → NFC. Diacritics are *kept*; this matches
    `process_corpus.py:26-37`, so keys line up with `corpus_frequencies.db`.
    """
    return unicodedata.normalize(
        'NFC', text.lower().replace('ş', 'ș').replace('ţ', 'ț'))


def read_quoted(s: str, i: int) -> tuple[str | None, int]:
    """Read a MySQL-escaped quoted string or NULL starting at s[i]. Returns (value, new_i)."""
    n = len(s)
    if i < n and s[i] == "'":
        i += 1
        buf: list[str] = []
        while i < n:
            c = s[i]
            if c == '\\' and i + 1 < n:
                nxt = s[i + 1]
                buf.append("'" if nxt == "'" else ('\\' if nxt == '\\' else nxt))
                i += 2
            elif c == "'":
                i += 1
                break
            else:
                buf.append(c)
                i += 1
        return ''.join(buf), i
    if s[i:i + 4] == 'NULL':
        return None, i + 4
    return None, i


def read_unquoted(s: str, i: int) -> tuple[str, int]:
    """Read an unquoted field (integer/decimal) until ',' or ')'. Returns (value, new_i)."""
    n = len(s)
    start = i
    while i < n and s[i] not in (',', ')'):
        i += 1
    return s[start:i].strip(), i


def read_field(s: str, i: int) -> tuple[str | None, int]:
    """Read one field of any type, dispatching on what starts at s[i]."""
    if i < len(s) and s[i] == "'":
        return read_quoted(s, i)
    if s[i:i + 4] == 'NULL':
        return None, i + 4
    return read_unquoted(s, i)


def skip_to_tuple_end(s: str, i: int) -> int:
    """From inside a tuple, advance past its matching ')'. Honors string escaping."""
    n = len(s)
    depth = 1
    in_str = False
    while i < n and depth > 0:
        c = s[i]
        if in_str:
            if c == '\\' and i + 1 < n:
                i += 2
            elif c == "'":
                in_str = False
                i += 1
            else:
                i += 1
        else:
            if c == "'":
                in_str = True
                i += 1
            elif c == '(':
                depth += 1
                i += 1
            elif c == ')':
                depth -= 1
                i += 1
            else:
                i += 1
    return i


def strip_line_prefix(line: str, prefix: str) -> str:
    """Drop the `INSERT INTO ... VALUES ` prefix and the trailing `;`."""
    s = line[len(prefix):].rstrip('\n')
    if s.endswith(';'):
        s = s[:-1]
    return s


def parse_tuples(body: str, max_index: int) -> Iterator[list[str | None]]:
    """Yield the first `max_index + 1` fields of every tuple in an INSERT body.

    Fields past `max_index` are skipped without being decoded — that is what keeps this
    cheap on tables like `Definition`, whose `internalRep` longtext dwarfs everything else.
    A tuple that ends early yields a short list; callers should index defensively.
    """
    i, n = 0, len(body)
    while i < n:
        while i < n and body[i] != '(':
            i += 1
        if i >= n:
            break
        i += 1  # step over '('
        fields: list[str | None] = []
        for _ in range(max_index + 1):
            if i >= n or body[i] == ')':
                break
            value, i = read_field(body, i)
            fields.append(value)
            if i < n and body[i] == ',':
                i += 1
        yield fields
        i = skip_to_tuple_end(body, i)
