"""THE tokenizer. One copy, imported by every ingester — never duplicated.

Ported from oțios's `process_culturax.py:81-84` (via `dump_parser.py:31-38` for
`normalize()`), with one deliberate change: the `len(t) > 2` filter is dropped.
Romanian's highest-frequency words are 1-2 characters (`de`, `la`, `cu`, `o`, `a`,
`nu`, `se`) — keeping that filter understates the denominator and inflates every
Zipf value derived from it. See docs/wrodfreq-spec.md §3.2.

The character class matches only Romanian letters plus internal hyphen/apostrophe,
so digit runs never match in the first place — numerals are excluded from both the
numerator and the denominator by construction, not by a separate filter.
"""

from __future__ import annotations

import re
import unicodedata

_TOKEN_RE = re.compile(r"[a-zăâîșț](?:[a-zăâîșț\-']*[a-zăâîșț])?")


def normalize(text: str) -> str:
    """Canonical Romanian normalization: lower -> cedilla-to-comma (ş->ș, ţ->ț) -> NFC."""
    return unicodedata.normalize(
        "NFC", text.lower().replace("ş", "ș").replace("ţ", "ț")
    )


def tokenize(text: str) -> list[str]:
    """Split normalized text into Romanian word tokens, short words included."""
    return _TOKEN_RE.findall(normalize(text))
