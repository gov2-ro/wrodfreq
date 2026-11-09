"""Diagnostic reproductions for the 2026-11-09 audit, at commit 81635a6.

Run from the repository root:
    .venv/bin/python docs/briefs/B7-audit-reproduce.py

This asserts the OLD BROKEN behavior. It is evidence, not the desired test suite:
convert each case to a regression asserting correct behavior during B7. Once the
fixes land this diagnostic is expected to fail; retain it as an audit snapshot.
All writes use temporary directories; no production files or network are used.
Requires the project build extras (zstandard for the acquisition reproduction).
"""

import contextlib
import io
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'build')]
import ingest_wiki as wiki
import ingest_social as social
import fetch_social as fetch
import compute_zipf as compute
import merge
import build_lemma_layer as lemma
import build_package as package
import validate
import ingest_eu as eu
from wrodfreq.db import connect, eligible_sources
from wrodfreq import _surface


def source(c, sid, tokens):
    c.execute("INSERT INTO sources (source_id,display_name,register,period,total_tokens,total_docs,status) VALUES (?,?,'web','contemporary',?,1,'completed')", (sid,sid,tokens))
    c.commit()


with tempfile.TemporaryDirectory(prefix='wrodfreq-audit-') as temp:
    tmp = Path(temp)
    c = connect(tmp / 'replay.db')
    wiki.ensure_source_row(c)
    with patch.object(wiki, 'CHECKPOINT', tmp / 'wiki.json'):
        wiki.save_checkpoint(wiki._empty_checkpoint())
        cp = dict(next_row=1, docs_done=1, tokens_done=5)
        wiki.flush(c, Counter(de=5), Counter(de=1), cp, 'in_progress')
        # Simulate death after the DB commit, before save_checkpoint(cp).
        resumed = wiki.load_checkpoint()
        resumed.update(next_row=1, docs_done=1, tokens_done=5)
        wiki.flush(c, Counter(de=5), Counter(de=1), resumed, 'in_progress')
        got = c.execute('SELECT (SELECT SUM(occurrences) FROM source_counts),total_tokens FROM sources').fetchone()
        assert got == (10, 5)
        print('COMMIT_REPLAY: occurrences=10, denominator=5 after replaying one committed batch')
    c.close()

    c = connect(tmp / 'ghost.db')
    source(c, 'wiki', 1000)
    c.execute("INSERT INTO source_counts VALUES ('ghost','wiki',10,1)")
    compute.compute_all(c)
    c.execute("DELETE FROM source_counts WHERE word='ghost'")
    c.commit()
    compute.compute_all(c)
    merge.run_merge(c, eligible_sources(c), set())
    got = c.execute("SELECT word,zipf FROM merged WHERE word='ghost'").fetchone()
    assert got is not None
    print('STALE_DERIVATION:', got, 'survives after deletion from source_counts')
    c.close()

    c = connect(tmp / 'ratio.db')
    source(c, 'small', 1000)
    source(c, 'large', 1_000_000_000)
    c.executemany('INSERT INTO source_counts VALUES (?,?,?,1)', [('shared','small',10),('shared','large',6)])
    c.commit()
    lemma.run(c, eligible_sources(c), {'shared':['alpha','beta']})
    ratios = c.execute('SELECT lemma,family_ratio FROM lemma_zipf').fetchall()
    assert all(ratio < 1 for _,ratio in ratios)
    print('FAMILY_RATIO:', ratios, 'although loose counts >= split counts in every source')

    # Date-dependent payloads: same counts and source panel, different build day.
    compute.compute_all(c)
    merge.run_merge(c, eligible_sources(c), set())
    class DayOne:
        @staticmethod
        def now(tz): return datetime(2026,11,8,tzinfo=timezone.utc)
    class DayTwo:
        @staticmethod
        def now(tz): return datetime(2026,11,9,tzinfo=timezone.utc)
    with patch.object(package, 'datetime', DayOne): a = package.build_payloads(c)
    with patch.object(package, 'datetime', DayTwo): b = package.build_payloads(c)
    assert a != b
    print('REPRODUCIBILITY: identical inputs produce unequal payloads on successive UTC dates')
    c.close()

    raw = tmp / 'social'
    raw.mkdir()
    (raw / 'Romania_comments.zst').touch()
    lines = [json.dumps({'author':'person','body':text}) for text in ['nu primul','nu secundul','nu tertul']]
    smoke_db = tmp / 'smoke.db'
    with patch.object(social, 'CHECKPOINT', tmp / 'smoke.json'), patch.object(social, 'read_lines_zst', lambda path: iter(lines)), patch.object(social, '_shutdown', False):
        for options in [['--test'], []]:
            with patch.object(sys, 'argv', ['ingest_social','--raw-dir',str(raw),'--db',str(smoke_db)] + options):
                with contextlib.redirect_stdout(io.StringIO()): social.main()
    c = sqlite3.connect(smoke_db)
    got = c.execute('SELECT (SELECT SUM(occurrences) FROM source_counts),total_tokens FROM sources').fetchone()
    assert got == (12, 6)
    print('SMOKE_THEN_FULL:', got, '(sum occurrences, stored denominator)')
    c.close()

    social_db = tmp / 'social.db'
    with patch.object(social, 'CHECKPOINT', tmp / 'social.json'), patch.object(social, 'read_lines_zst', lambda path: iter(lines)), patch.object(social, '_shutdown', False):
        with patch.object(sys, 'argv', ['ingest_social','--raw-dir',str(raw),'--db',str(social_db),'--limit','1']):
            with contextlib.redirect_stdout(io.StringIO()): social.main()
        cp = social.load_checkpoint()
        assert cp['files_done']['Romania_comments.zst'] == 2
        with patch.object(sys, 'argv', ['ingest_social','--raw-dir',str(raw),'--db',str(social_db),'--resume']):
            with contextlib.redirect_stdout(io.StringIO()): social.main()
    c = sqlite3.connect(social_db)
    words = dict(c.execute('SELECT word,occurrences FROM source_counts'))
    assert 'primul' in words and 'tertul' in words and 'secundul' not in words
    print('SOCIAL_LIMIT_RESUME: middle record silently lost:', words)
    c.close()

    fetch_raw = tmp / 'fetch'
    records = [dict(author='person',body=f'nu randul {i}',subreddit='Romania',created_utc=100-i) for i in range(4)]
    cp = {}
    def pages(kind, sub, before):
        if before is None: return records[:2]
        if before == 99: return records[2:]
        return []
    import zstandard
    with patch.object(fetch, 'RAW_DIR', fetch_raw), patch.object(fetch, 'CHECKPOINT', tmp / 'fetch.json'), patch.object(fetch, 'get_page', pages), patch.object(fetch,'PAGE_DELAY',0), patch.object(fetch,'_shutdown',False):
        fetch.fetch('Romania','comments',cp,2)
        out = fetch_raw / 'Romania_comments.zst'
        first = zstandard.ZstdDecompressor().decompress(out.read_bytes()).decode().splitlines()
        fetch.fetch('Romania','comments',cp,None)
        last = zstandard.ZstdDecompressor().decompress(out.read_bytes()).decode().splitlines()
        assert len(first) == 2 and len(last) == 2 and cp['Romania/comments']['count'] == 4
        assert json.loads(last[0])['created_utc'] == 98
        print('FETCH_LIMIT_RESUME: checkpoint claims 4 records; compressed output retains only last 2')

    # Reconstruct the observed old checkpoint without depending on private data/.
    eu_cp = tmp / 'eu.json'
    historical = eu._empty_checkpoint()
    old_totals = [9_570_511, 74_845_446]
    assert len(historical['parts']) == len(old_totals)
    for part, tokens in zip(historical['parts'].values(), old_totals):
        part.update(completed=True, tokens_done=tokens, lines_done=1)
    eu_cp.write_text(json.dumps(historical))
    c = connect(tmp / 'eu.db')
    eu.ensure_source_row(c, 'fixture')
    c.execute("UPDATE sources SET total_tokens=84722606,total_docs=4938644,status='completed' WHERE source_id='eu'")
    c.commit()
    c.close()
    with patch.object(eu, 'CHECKPOINT', eu_cp), patch.object(sys, 'argv', ['ingest_eu','--resume','--db',str(tmp / 'eu.db')]):
        with contextlib.redirect_stdout(io.StringIO()): rc = eu.main()
    c = sqlite3.connect(tmp / 'eu.db')
    total = c.execute('SELECT total_tokens FROM sources').fetchone()[0]
    assert rc == 0 and total == 84415957
    print('COMPLETED_EU_RESUME: denominator reset from 84,722,606 to 84,415,957')
    c.close()

    p = tmp / 'validation.db'
    c = connect(p)
    source(c, 'wiki', 1000)
    c.executemany("INSERT INTO source_counts VALUES (?,'wiki',?,1)", list(zip(validate.FUNCTION_WORDS, [50,30,20,15,12])))
    c.commit()
    with contextlib.redirect_stdout(io.StringIO()):
        compute.compute_all(c)
        merge.run_merge(c, eligible_sources(c), set())
    c.execute("UPDATE source_counts SET occurrences=1 WHERE word='de'")
    c.commit()
    c.close()
    with patch.object(sys, 'argv', ['validate','--db',str(p),'--dex-db',str(tmp / 'missing.db'),'--wordfreq-python',str(tmp / 'missing-python')]):
        with contextlib.redirect_stdout(io.StringIO()): rc = validate.main()
    c = sqlite3.connect(p)
    assert rc == 0 and c.execute("SELECT * FROM merged WHERE word='de'").fetchone() is None
    print('VALIDATION_GATE: exit 0, 3/3 checks passed, but final rebuilt merged lacks required de')
    c.close()

    surface_payload = dict(built='2026-11-09', sources=['social','wiki'], n_sources=2, zipf_scale=100, word_count=1, words=['alpha'], zipf=[200], n_reliable=[2], n_attesting=[2], spread=[100])
    old_by_source = dict(sources=['news','wiki'], word_count=1, by_source=[[100,200]])
    data = _surface._Surface(surface_payload)
    with patch.object(_surface, '_load_payload', lambda path: old_by_source):
        result = data.by_source_row(0)
    assert result == {'social':1.0,'wiki':2.0}
    print('MIXED_BUILD_FILES: old news frequency silently presented as social:', result)

print('All audit reproductions confirmed; production database untouched.')
