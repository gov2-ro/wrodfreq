"""Social acquisition: capped, interrupted and finalized crawls keep every record once
(brief B7 Phase 2 / finding A05, and all recovery gaps of brief B4).

Everything compares the *decoded records* of the final archive with those of one
uninterrupted crawl — not only counts. No network: `get_page` serves a fixed history.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import zstandard

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))

KEY = "Romania/comments"
HISTORY = [{"author": f"a{i}", "body": f"comentariul numărul {i}", "subreddit": "Romania",
            "created_utc": 1_700_000_000 - 10 * i} for i in range(9)]   # newest first


class Killed(BaseException):
    pass


def _load():
    spec = importlib.util.spec_from_file_location("_fetch_social_fin", BUILD / "fetch_social.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fs(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(mod, "CHECKPOINT", tmp_path / "cp.json")
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)

    def pages(kind, sub, before):
        older = [r for r in HISTORY if before is None or r["created_utc"] < before]
        return older[:2]
    monkeypatch.setattr(mod, "get_page", pages)
    return mod


def ndjson(fs) -> Path:
    return fs.RAW_DIR / "Romania_comments.ndjson"


def archive(fs) -> Path:
    return fs.RAW_DIR / "Romania_comments.zst"


def decoded(path: Path) -> list[dict]:
    data = zstandard.ZstdDecompressor().decompress(path.read_bytes(), max_output_size=1 << 26)
    return [json.loads(line) for line in data.decode().splitlines()]


def expected_records(fs) -> list[dict]:
    return [fs.slim(r, fs.COMMENT_FIELDS) for r in HISTORY]


def run(fs, cp, max_records=None):
    return fs.fetch("Romania", "comments", cp, max_records)


def test_uninterrupted_crawl_finalizes_a_verified_archive(fs):
    cp = {}
    run(fs, cp)
    assert decoded(archive(fs)) == expected_records(fs)
    assert not ndjson(fs).exists()
    assert cp[KEY]["done"] and cp[KEY]["finalized"]["records"] == len(HISTORY)
    assert json.loads(fs.CHECKPOINT.read_text())[KEY]["finalized"] == cp[KEY]["finalized"]


@pytest.mark.parametrize("caps", [[2], [2, 4], [1, 3, 7], [8]])
def test_capped_then_continued_equals_uninterrupted(fs, caps):
    cp = {}
    for cap in caps:
        run(fs, cp, cap)
        assert not archive(fs).exists(), "an unfinished crawl must not be compacted"
        assert not cp[KEY]["done"]
        lines = ndjson(fs).read_text().splitlines()
        assert [json.loads(x) for x in lines] == expected_records(fs)[:len(lines)]
    run(fs, cp)
    assert decoded(archive(fs)) == expected_records(fs)


def test_shutdown_mid_crawl_then_resume(fs, monkeypatch):
    real = fs.get_page
    calls = {"n": 0}

    def stop_after_two(kind, sub, before):
        calls["n"] += 1
        if calls["n"] == 3:
            fs._shutdown = True
        return real(kind, sub, before)
    monkeypatch.setattr(fs, "get_page", stop_after_two)
    cp = {}
    run(fs, cp)
    assert not cp[KEY]["done"] and not archive(fs).exists()
    fs._shutdown = False
    monkeypatch.setattr(fs, "get_page", real)
    run(fs, json.loads(fs.CHECKPOINT.read_text()))
    assert decoded(archive(fs)) == expected_records(fs)


def test_kill_mid_page_duplicates_nothing(fs, monkeypatch):
    """Records appended after the last checkpoint are trimmed, then refetched once."""
    cp = {}
    run(fs, cp, 4)
    saved = json.loads(fs.CHECKPOINT.read_text())
    with ndjson(fs).open("a") as fh:                     # written, never checkpointed
        fh.write(json.dumps(fs.slim(HISTORY[4], fs.COMMENT_FIELDS), ensure_ascii=False) + "\n")
    run(fs, saved)
    assert decoded(archive(fs)) == expected_records(fs)


@pytest.mark.parametrize("point", ["compressed", "replaced", "recorded"])
def test_death_between_finalization_steps_recovers(fs, point):
    def die(p):
        if p == point:
            raise Killed(p)
    fs.FAULT_HOOK = die
    cp = {}
    with pytest.raises(Killed):
        run(fs, cp)
    assert not (fs.RAW_DIR / "Romania_comments.zst.tmp").exists() or point == "compressed"
    fs.FAULT_HOOK = lambda p: None
    cp = json.loads(fs.CHECKPOINT.read_text())
    assert cp[KEY]["done"]
    run(fs, cp)
    assert decoded(archive(fs)) == expected_records(fs)
    assert not ndjson(fs).exists()
    assert cp[KEY]["finalized"]["records"] == len(HISTORY)


def test_failed_compression_keeps_the_log_and_retries(fs, monkeypatch):
    real = fs.compress_verified

    def broken(nd, zst):
        raise OSError("disk full")
    monkeypatch.setattr(fs, "compress_verified", broken)
    cp = {}
    with pytest.raises(OSError):
        run(fs, cp)
    assert ndjson(fs).exists() and not archive(fs).exists()
    assert "finalized" not in cp[KEY]
    monkeypatch.setattr(fs, "compress_verified", real)
    run(fs, json.loads(fs.CHECKPOINT.read_text()))
    assert decoded(archive(fs)) == expected_records(fs)


def test_existing_archive_without_log_is_verified_and_kept(fs):
    """The 30 archives on disk were made before finalization was recorded."""
    fs.RAW_DIR.mkdir(parents=True)
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in expected_records(fs))
    archive(fs).write_bytes(zstandard.ZstdCompressor().compress(data.encode()))
    original = archive(fs).read_bytes()
    cp = {KEY: {"before": 1, "count": len(HISTORY), "done": True}}
    run(fs, cp)
    assert archive(fs).read_bytes() == original
    assert cp[KEY]["finalized"]["records"] == len(HISTORY)


def test_archive_with_wrong_record_count_is_unrecoverable(fs):
    fs.RAW_DIR.mkdir(parents=True)
    archive(fs).write_bytes(zstandard.ZstdCompressor().compress(b'{"a":1}\n'))
    with pytest.raises(fs.Unrecoverable):
        run(fs, {KEY: {"before": 1, "count": 5, "done": True}})


def test_done_with_no_material_is_unrecoverable(fs):
    with pytest.raises(fs.Unrecoverable, match="refetched"):
        run(fs, {KEY: {"before": 1, "count": 5, "done": True}})


def test_old_capped_compaction_is_restored_and_continued(fs):
    """Pre-B7 `--max-records` compacted an unfinished crawl; its prefix must survive."""
    fs.RAW_DIR.mkdir(parents=True)
    prefix = expected_records(fs)[:2]
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in prefix)
    archive(fs).write_bytes(zstandard.ZstdCompressor().compress(data.encode()))
    cp = {KEY: {"before": HISTORY[1]["created_utc"], "count": 2, "done": False}}
    run(fs, cp)
    assert decoded(archive(fs)) == expected_records(fs)


# --- B4 gaps -----------------------------------------------------------------

def _log(fs, records: list[bytes]) -> Path:
    fs.RAW_DIR.mkdir(parents=True, exist_ok=True)
    ndjson(fs).write_bytes(b"".join(records))
    return ndjson(fs)


def _lines(fs, n: int) -> list[bytes]:
    return [(json.dumps(r, ensure_ascii=False) + "\n").encode() for r in expected_records(fs)[:n]]


def test_gap1_damaged_line_mid_file_aborts_without_truncating(fs):
    good = _lines(fs, 4)
    p = _log(fs, good[:2] + [b"garbage\n"] + good[2:])
    before = p.read_bytes()
    with pytest.raises(fs.Unrecoverable, match="damaged"):
        run(fs, {KEY: {"before": 1, "count": 4, "done": False}})       # no bytes
    assert p.read_bytes() == before


def test_gap1_torn_final_line_is_trimmed(fs):
    good = _lines(fs, 3)
    _log(fs, good + [b'{"author": "x", "bo'])
    cp = {KEY: {"before": 1, "count": 3, "done": False}}
    run(fs, cp)
    assert decoded(archive(fs)) == expected_records(fs)


@pytest.mark.parametrize("nbytes", [0, 10 ** 9])
def test_gap2_zero_or_oversized_bytes_rebuild_instead_of_truncating(fs, nbytes):
    good = _lines(fs, 4)
    _log(fs, good)
    cp = {KEY: {"before": HISTORY[1]["created_utc"], "count": 4, "done": False, "bytes": nbytes}}
    run(fs, cp)
    assert decoded(archive(fs)) == expected_records(fs)


def test_gap3_mid_line_bytes_never_leave_a_torn_record(fs):
    good = _lines(fs, 4)
    _log(fs, good)
    mid = len(good[0]) + len(good[1]) + 5
    cp = {KEY: {"before": HISTORY[1]["created_utc"], "count": 2, "done": False, "bytes": mid}}
    run(fs, cp)
    assert decoded(archive(fs)) == expected_records(fs)


def test_gap4_no_recoverable_timestamp_is_unrecoverable(fs):
    _log(fs, [b'{"author": "a", "body": "x"}\n', b'{"author": "b", "created_utc": "ieri"}\n'])
    with pytest.raises(fs.Unrecoverable, match="created_utc"):
        run(fs, {KEY: {"before": 1, "count": 2, "done": False}})


def test_gap5_count_disagreement_is_reported(fs, capsys):
    _log(fs, _lines(fs, 6))
    run(fs, {KEY: {"before": HISTORY[1]["created_utc"], "count": 2, "done": False}}, 6)
    assert "+4: the unaccounted window" in capsys.readouterr().out


def test_coherent_bytes_with_wrong_line_count_rebuilds(fs):
    good = _lines(fs, 4)
    _log(fs, good)
    size = sum(len(x) for x in good)
    cp = {KEY: {"before": HISTORY[1]["created_utc"], "count": 2, "done": False, "bytes": size}}
    run(fs, cp)
    assert decoded(archive(fs)) == expected_records(fs)


def test_missing_log_with_a_nonzero_count_is_unrecoverable(fs):
    with pytest.raises(fs.Unrecoverable, match="missing"):
        run(fs, {KEY: {"before": 1, "count": 3, "done": False, "bytes": 99}})


def test_compress_every_dead_code_is_gone():
    assert "COMPRESS_EVERY" not in (BUILD / "fetch_social.py").read_text()
