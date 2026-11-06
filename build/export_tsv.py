#!/usr/bin/env python3
"""Stage 5b — export the final table as a gzipped TSV for readers (spec §10.2).

The file holds the same rows and rounded values as `ro_surface.msgpack.xz`, for
people who do not use Python. The most frequent words come first.

The output is deterministic: the row order is fixed and the gzip header holds no
timestamp. A rebuild from unchanged `merged` gives a byte-identical file.

`is_dex` and the lemma layer stay out of this file (docs/release-data-hosting.md).

Usage:
    python build/export_tsv.py [--db PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import gzip
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_package import MAX_HYPHENS, ZIPF_SCALE, _quantize
from wrodfreq.db import DEFAULT_DB_PATH, connect

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "release" / "ro_surface.tsv.gz"
HEADER = "word\tzipf\tn_reliable\tn_attesting\tspread\n"


def _centi(value: float) -> str:
    return f"{_quantize(value) / ZIPF_SCALE:.2f}"


def export_tsv(conn: sqlite3.Connection, out_path: Path) -> int:
    """Writes the file and returns the number of rows, without the header."""
    rows = conn.execute(
        "SELECT word, zipf, n_reliable, n_attesting, spread "
        "FROM merged WHERE length(word) - length(replace(word, '-', '')) <= ? "
        "ORDER BY zipf DESC, word",
        (MAX_HYPHENS,),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    count = 0
    with open(tmp_path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0) as gz:
            gz.write(HEADER.encode())
            for word, zipf, n_reliable, n_attesting, spread in rows:
                line = f"{word}\t{_centi(zipf)}\t{n_reliable}\t{n_attesting}\t{_centi(spread)}\n"
                gz.write(line.encode())
                count += 1
    tmp_path.replace(out_path)
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    conn = connect(args.db)
    count = export_tsv(conn, args.out)
    conn.close()
    size = args.out.stat().st_size
    print(f"Wrote {args.out} — {count:,} rows, {size:,} bytes ({size / (1 << 20):.1f} MiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
