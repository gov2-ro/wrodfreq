"""Run one real ingester with offline fixtures, in its own process (kill tests).

    python tests/ingest_driver.py <source> <fixture_dir> -- <ingester args>

Set WRODFREQ_FAULT=<point>:<n> to make build/ingest_state.py exit with status 86 at
a transaction boundary — the deterministic stand-in for `kill -9`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_fakes import run  # noqa: E402

if __name__ == "__main__":
    source, fixture_dir, sep, *args = sys.argv[1:]
    assert sep == "--"
    sys.exit(run(source, Path(fixture_dir), args))
