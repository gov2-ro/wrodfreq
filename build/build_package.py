#!/usr/bin/env python3
"""Stage 5 — compile `merged` into the shipped data files (spec §7.6, §10.2).

`msgpack` + `xz` (stdlib `lzma`), per spec: "no SQLite dependency at runtime."
Target < 30 MB for the surface table.

Two files, not one:

  - `ro_surface.msgpack.xz` — the core table (words, zipf, n_reliable,
    n_attesting, spread) needed for zipf_frequency/word_frequency/
    top_n_list/frequency_detail. 29.2 MB measured, under the target.
  - `ro_by_source.msgpack.xz` — the per-source breakdown for by_source()
    only. 9.5 MB measured. A plain combined file measured 38.7 MB, over
    target — by_source is also the one extension spec explicitly frames as
    "clearly marked as such", so splitting it into its own lazily-loaded
    file (only read if by_source() is actually called) both hits the target
    and matches that framing, rather than an arbitrary size-driven cut.

Two encoding choices that mattered for size, both measured, not assumed:

  - Parallel arrays (words[i], zipf[i], ...), not a list of per-word dicts.
    msgpack has no key-interning across maps, so repeating field names
    ("zipf", "n_reliable", ...) once per word across ~6.2M words costs real
    bytes for nothing. `n_sources` is currently identical for every row (all
    five ingested sources are eligible), so it's stored once in the header
    instead of 6.2M times — checked at build time, not assumed, see
    build_payload()'s guard.
  - zipf and spread are quantized to centizipf ints (round(zipf * 100)),
    not left as Python floats. msgpack packs a Python float as 8-byte
    float64 regardless of the value's actual precision; spec already says
    to round to 2 decimals "because the third decimal is noise and it costs
    real bytes across ~2M entries" — quantizing to an int takes that
    reasoning to its actual conclusion instead of rounding-then-repacking
    as a float. Measured: the unquantized core packed 193.4 MB (31.1 MB
    compressed) vs. quantized 97.7 MB packed (29.2 MB compressed) — under
    the 30 MB target only after this change.

**`is_dex` and the lemma layer are deliberately NOT included in either
file.** CLAUDE.md flags the DEX Online licence question as unresolved
before *redistributing* anything derived from it — and `is_dex` is exactly
that: an aggregate of ~587k booleans over `merged`'s own words would let
anyone reconstruct a large fraction of DEX's own headword list by
cross-referencing which shipped words have it set, which is a real
redistribution question, not a hypothetical one. `lemma_frequency()` in the
API degrades gracefully (returns 0.0, matching `zipf_frequency`'s own
unknown-word contract) rather than shipping that data pending an actual
answer to that question — this is a licensing decision, not an engineering
one, and isn't made here.

Deterministic by construction (spec's idempotence ethos, §11.6): words are
stored sorted ascending, arrays hold their exact resulting order, so a
rebuild from unchanged `merged` produces the exact same in-memory structure
every time — this stage joins build/validate.py's check 6.

Usage:
    python build/build_package.py [--db PATH] [--out-dir PATH]

Output: wrodfreq/data/ro_surface.msgpack.xz, wrodfreq/data/ro_by_source.msgpack.xz
"""

from __future__ import annotations

import argparse
import lzma
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import msgpack

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect, eligible_sources

DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "wrodfreq" / "data"
SURFACE_FILENAME = "ro_surface.msgpack.xz"
BY_SOURCE_FILENAME = "ro_by_source.msgpack.xz"
FORMAT_VERSION = 1
ZIPF_SCALE = 100  # centizipf — matches spec's "round to 2 decimals"


def _quantize(zipf: float) -> int:
    return round(zipf * ZIPF_SCALE)


def build_payloads(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """Returns (surface_payload, by_source_payload)."""
    sources = eligible_sources(conn)

    rows = conn.execute(
        "SELECT word, zipf, n_reliable, n_attesting, n_sources, spread "
        "FROM merged ORDER BY word"
    ).fetchall()

    n_sources_values = {r[4] for r in rows}
    if len(n_sources_values) != 1:
        # Can't happen with today's panel (checked at build time, not assumed
        # — see module docstring), but if a future panel ever has eligibility
        # vary per word, storing n_sources once in the header would silently
        # lose that information. Fail loudly rather than ship wrong data.
        raise ValueError(
            f"n_sources is not constant across merged ({n_sources_values}); "
            f"build_package.py's header-only storage assumes it is — see docstring"
        )
    n_sources = n_sources_values.pop()

    words = [r[0] for r in rows]
    zipf = [_quantize(r[1]) for r in rows]
    n_reliable = [r[2] for r in rows]
    n_attesting = [r[3] for r in rows]
    spread = [_quantize(r[5]) for r in rows]

    built = datetime.now(timezone.utc).date().isoformat()

    surface_payload = {
        "format_version": FORMAT_VERSION,
        "built": built,
        "sources": sources,
        "n_sources": n_sources,
        "zipf_scale": ZIPF_SCALE,
        "word_count": len(words),
        "words": words,
        "zipf": zipf,
        "n_reliable": n_reliable,
        "n_attesting": n_attesting,
        "spread": spread,
    }

    placeholders = ",".join("?" * len(sources))
    by_source_rows = conn.execute(
        f"""SELECT word, source_id, zipf FROM source_zipf
            WHERE reliable = 1 AND source_id IN ({placeholders})""",
        sources,
    ).fetchall()
    by_source_map: dict[str, dict[str, int]] = {}
    for word, source_id, word_zipf in by_source_rows:
        by_source_map.setdefault(word, {})[source_id] = _quantize(word_zipf)

    by_source = [
        [by_source_map.get(w, {}).get(s) for s in sources] for w in words
    ]

    by_source_payload = {
        "format_version": FORMAT_VERSION,
        "built": built,
        "sources": sources,
        "zipf_scale": ZIPF_SCALE,
        # No "words" array here — deliberately positional, aligned to the
        # surface file's word order (both come from the same `words` list in
        # this one build run). Re-shipping the word list a second time was
        # the single biggest line item in this file: it took ro_by_source
        # from a measured 9.5 MB to 27.6 MB before this fix. `word_count` is
        # a cheap integrity check, not a substitute for the real word list —
        # the API refuses to use this file if it doesn't match the surface
        # file's word count (see wrodfreq/_surface.py).
        "word_count": len(words),
        "by_source": by_source,
    }

    return surface_payload, by_source_payload


def _write(payload: dict, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    packed = msgpack.packb(payload, use_bin_type=True)
    compressed = lzma.compress(packed, format=lzma.FORMAT_XZ, preset=9)
    out_path.write_bytes(compressed)
    return len(compressed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    conn = connect(args.db)
    print("Building payloads from `merged`...", flush=True)
    surface_payload, by_source_payload = build_payloads(conn)
    conn.close()

    print(f"  {len(surface_payload['words']):,} words, "
          f"{len(surface_payload['sources'])} sources: "
          f"{', '.join(surface_payload['sources'])}", flush=True)

    print("Packing + compressing...", flush=True)
    surface_path = args.out_dir / SURFACE_FILENAME
    by_source_path = args.out_dir / BY_SOURCE_FILENAME
    surface_size = _write(surface_payload, surface_path)
    by_source_size = _write(by_source_payload, by_source_path)

    for path, size, target_mb in [(surface_path, surface_size, 30), (by_source_path, by_source_size, None)]:
        mb = size / (1 << 20)
        over = target_mb is not None and mb > target_mb
        print(f"Wrote {path} — {size:,} bytes ({mb:.1f} MB)"
              f"{f'  ⚠ over the {target_mb} MB target' if over else ''}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
