"""Regression tests for fetch_social.py's checkpoint recovery.

The near-miss (2026-10-02): a checkpoint written before the `bytes` field existed
was read with `bytes` defaulting to 0. The resume check then saw the whole 82 MB
append-log as "unaccounted" and would have truncated it to nothing while leaving
`before` stale, silently losing ~260k records that would never be refetched. A
missing byte count is unknown, not zero.

Everything here is synthetic, in a tmp dir. fetch_social's RAW_DIR / CHECKPOINT
are redirected there and get_page is replaced, so there is no network and no
contact with data/.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))
_spec = importlib.util.spec_from_file_location("_fetch_social", BUILD / "fetch_social.py")
fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs)

KEY = "Romania/comments"
OLDEST = 1_600_000_000
NEWEST = OLDEST + 9 * 10_000_000   # newest of _records(10)


def _line(ts: int) -> bytes:
    rec = {"author": "a", "body": f"ceva {ts}", "subreddit": "Romania", "created_utc": ts}
    return (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")


def _records(n: int = 10) -> list[bytes]:
    # newest first, as the crawl writes them
    return [_line(OLDEST + (n - 1 - i) * 10_000_000) for i in range(n)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Redirect paths into tmp_path; any network call fails loudly; fetch() stops
    right after the resume logic (get_page raises KeyboardInterrupt -> break),
    so it neither finishes the subreddit nor compresses/deletes the ndjson."""
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(fs, "RAW_DIR", raw)
    monkeypatch.setattr(fs, "CHECKPOINT", tmp_path / "cp" / "social_fetch.json")
    monkeypatch.setattr(fs, "PAGE_DELAY", 0)

    def no_network(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(fs, "get_page", no_network)

    def no_compress(*a, **k):
        raise AssertionError("compress() must not run in these tests")

    monkeypatch.setattr(fs, "compress", no_compress)
    return raw


def _write(raw: Path, chunks: list[bytes]) -> Path:
    p = raw / "Romania_comments.ndjson"
    p.write_bytes(b"".join(chunks))
    return p


def _resume(cp: dict) -> int:
    return fs.fetch("Romania", "comments", cp, None)


def _lines(p: Path) -> list[bytes]:
    return p.read_bytes().splitlines(keepends=True)


# --- rebuild_state_from_file ------------------------------------------------

def test_recovery_values_are_exact(env):
    recs = _records(10)
    p = _write(env, recs)
    count, oldest, nbytes = fs.rebuild_state_from_file(p, KEY)
    assert count == 10
    assert oldest == OLDEST
    assert nbytes == sum(len(r) for r in recs) == p.stat().st_size


def test_oldest_is_min_not_last_line(env):
    # out-of-order timestamps: the minimum, wherever it sits
    p = _write(env, [_line(500), _line(100), _line(300)])
    assert fs.rebuild_state_from_file(p, KEY)[1] == 100


def test_torn_final_line_excluded_from_recovery(env):
    recs = _records(5)
    p = _write(env, recs + [b'{"author": "a", "body": "tru'])
    count, oldest, nbytes = fs.rebuild_state_from_file(p, KEY)
    assert count == 5
    assert nbytes == sum(len(r) for r in recs)
    assert oldest == OLDEST


def test_torn_line_that_happens_to_be_valid_json_is_still_not_a_record(env):
    # complete JSON but no trailing newline: the write was interrupted
    recs = _records(4)
    p = _write(env, recs + [_line(NEWEST + 5).rstrip(b"\n")])
    count, _, nbytes = fs.rebuild_state_from_file(p, KEY)
    assert count == 4
    assert nbytes == sum(len(r) for r in recs)


# --- fetch(): missing `bytes` ----------------------------------------------

def test_missing_bytes_does_not_truncate(env):
    recs = _records(10)
    p = _write(env, recs)
    cp = {KEY: {"before": NEWEST, "count": 10, "done": False}}   # no "bytes"
    _resume(cp)
    assert _lines(p) == recs


def test_missing_bytes_corrects_before_backwards(env):
    # real-world shape: stale `before` is NEWER than the oldest record on disk
    recs = _records(10)
    p = _write(env, recs)
    stale_before = NEWEST - 1000
    assert stale_before > OLDEST
    cp = {KEY: {"before": stale_before, "count": 3, "done": False}}
    _resume(cp)
    st = cp[KEY]
    assert st["before"] == OLDEST
    assert st["count"] == 10
    assert st["bytes"] == p.stat().st_size
    # and it was persisted
    assert json.loads(fs.CHECKPOINT.read_text())[KEY]["before"] == OLDEST


def test_missing_bytes_with_torn_tail_trims_file(env):
    recs = _records(6)
    p = _write(env, recs + [b'{"author": "x", "bo'])
    cp = {KEY: {"before": NEWEST, "count": 6, "done": False}}
    _resume(cp)
    assert p.read_bytes() == b"".join(recs)
    assert cp[KEY]["count"] == 6
    assert cp[KEY]["bytes"] == len(b"".join(recs))


# --- fetch(): normal path ---------------------------------------------------

def test_present_bytes_truncates_unaccounted_tail(env):
    recs = _records(10)
    p = _write(env, recs)
    accounted = sum(len(r) for r in recs[:7])
    cp = {KEY: {"before": 123, "count": 7, "done": False, "bytes": accounted}}
    _resume(cp)
    assert _lines(p) == recs[:7]


def test_present_bytes_equal_to_size_leaves_file_alone(env):
    recs = _records(5)
    p = _write(env, recs)
    cp = {KEY: {"before": 123, "count": 5, "done": False, "bytes": p.stat().st_size}}
    _resume(cp)
    assert _lines(p) == recs
    assert cp[KEY]["before"] == 123      # normal path must not rewrite `before`


# --- fetch(): done ----------------------------------------------------------

def test_done_is_skipped_untouched(env):
    recs = _records(5)
    p = _write(env, recs)
    before_state = {"before": 42, "count": 5, "done": True}   # no bytes, file present
    cp = {KEY: dict(before_state)}
    assert _resume(cp) == 0
    assert _lines(p) == recs                 # not truncated
    assert cp[KEY] == before_state           # not recovered / rewritten
    assert not fs.CHECKPOINT.exists()        # nothing saved
