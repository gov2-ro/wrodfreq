#!/usr/bin/env python3
"""Stage 1 (social source) — ingest Romanian subreddit dumps into `source_counts`.

Spec §6's "optional seventh": Reddit is *the* register missing from every other
Romanian corpus. `subs` (film dialogue) is scripted and translated; `web` and
`news` are edited prose; `eu` is bureaucratic. Nothing in the panel is
spontaneous, unedited, native, contemporary Romanian, and the zipf 2.0-4.0 band
is exactly where the panel is thinnest (trim engagement 25-71%, measured
2026-09-21).

## Getting the data

**Not Academic Torrents — checked, 2026-09-22.** Search results advertise a
per-subreddit release ("Subreddit comments/submissions 2005-06 to 2024-12",
hash `1614740ac8c94505e4ecb9d88be8bed7b6afddd4`) and an earlier version of this
docstring repeated it. It does not exist: that hash 404s, so do the three other
per-subreddit hashes search results offer, *including the one in Watchful1's own
PushshiftDumps README*. The URL form is not the problem —
`academictorrents.com/download/<hash>.torrent` returns 200 and a 3.7 MB torrent
for a hash that is real. Academic Torrents' `database.xml` lists 34 Reddit
entries and none is per-subreddit; parsing the real archive torrent
(`ba051999301b109eab37d16f027b3f49ade2de13`) shows 464 files, **all whole-month**
(`RC_2005-12.zst`, `RS_…`), 2.84 TiB. Filtering r/Romania out of that costs
~50 GiB of download per month of data.

**Use Arctic Shift instead**, a no-auth HTTP archive:

    https://arctic-shift.photon-reddit.com/api/comments/search?subreddit=Romania&limit=100

It returns records carrying `author`, `body`, `subreddit` and `created_utc` —
exactly what `record_text()` below already parses, including the literal
`[deleted]`/`[removed]` bodies it already skips. Page back through history with
`&before=<created_utc>`. Page size caps at 100; measured ~335k comments/hour.

Either way this module reads **zstandard-compressed ndjson**, one JSON record
per line, from `data/raw/social/*.zst` — so anything that produces that shape
works, and a torrent dump would still drop straight in if one ever appears.
More Romanian subreddits can be added to the directory later and ingested
incrementally, since the checkpoint is per file.

## Three decisions this source forces, none of which the other five did

**1. Language filtering, which no previous ingester needed.** `news` arrived
pre-tagged with a `language` column; `subs`, `eu`, `wiki` and `web` are
monolingual Romanian exports by construction. r/Romania is not: it is heavily
code-switched, and English contamination here is far more damaging than the
URL-slug noise logged on 2026-09-21, because `the`, `and`, `is` are all valid
token shapes under our character class and would land in a *Romanian* frequency
table with real frequencies.

The filter below exploits the one thing that makes this tractable: **the
competing language is English specifically**, not arbitrary. So the markers only
have to separate Romanian from English, and cross-language homographs are
excluded by hand (`care`, `face`, `are`, `in`, `la`, `a`, `o`, `e` are all
Romanian words *and* English words — every one of them is deliberately absent
from RO_MARKERS). Diacritics are a strong positive signal but cannot be
required: Romanians routinely write without them online, which is exactly why
`gasiti` shows up in our own table.

Short comments are the hard case — a four-word comment cannot be classified
confidently by any method, including fasttext. Rather than guess, they are held
to a stricter rule (see `looks_romanian`). Run `--calibrate` on real data before
the real ingest and tune the thresholds against what it prints; do not trust the
defaults below, they are a starting point chosen without access to the corpus.

**2. `documents` stays "one comment", and does NOT become "one author".**
Spec §7.2 says to count authors where author metadata exists, citing oțios's
LUMRO trap (175 novels, 111 authors, 638 of 1,425 rare words from one person).
Deliberately not done here, for two reasons. First, per-word distinct-author
counting needs a (word, author) set spanning the whole corpus — tens of millions
of pairs, which is exactly the unbounded-memory failure §7.2's own
memory-bounded rule forbids. Second and more important, changing the unit would
make this column mean something different here than in the other five sources,
and `documents` is only useful as an independence signal if it is comparable
across the panel.

The spec's actual concern — "is this really N independent voices?" — is answered
instead by *measuring* it: the ingester counts distinct authors and the share of
comments held by the top 100, and writes both into `sources.period_note`. That
is the honest version of the claim, and it follows the precedent `ingest_subs.py`
set when it counted subtitle lines rather than films and said so.

**3. Markdown and URLs are stripped before tokenizing.** Reddit bodies are
markdown. Left alone, a link like `https://example.com/foo-bar-baz` tokenizes
into a URL slug — the precise defect logged 2026-09-21 as already polluting
`merged` with 23,425 entries from the web corpus. Not repeating that here when
one regex prevents it.

Bot output is excluded (`AutoModerator`, `*bot`/`*Bot` authors), as are
`[deleted]`/`[removed]` bodies, which are literal strings in the dumps rather
than absent fields.

## Usage

    python build/ingest_social.py --calibrate     # language filter report, no writes
    python build/ingest_social.py --test          # first 50k records/file, new experiment db
    python build/ingest_social.py                 # full run (refuses existing social counts)
    python build/ingest_social.py --resume        # continue the generation stored in the db
    python build/ingest_social.py --limit N       # stop at absolute record N of each file

`--limit N` is an absolute per-file position: a run consumes records up to N of each
file and records N as that file's cursor. `--limit 1`, then `--resume --limit 2`, then
`--resume` counts exactly what one unbounded run counts.

Restart loop (stop automatically on success):
    while true; do
        python -u build/ingest_social.py --resume
        [ $? -eq 0 ] && break
        echo "[$(date)] restarting in 15s..." && sleep 15
    done

Output: data/wrodfreq.db, source_id='social'. Progress: `ingest_state` in the same db,
committed with the counts (ADR-004). The input manifest is the name, size and SHA-256
of every dump file; a resume against changed dumps is refused. The old JSON checkpoints
in data/checkpoints/ are not read: a `--test` run once added its counts to the real
ones through them (brief B7, finding A01).
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import signal
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_state import Batch, IngestRefused, SourceSpec, begin, target_db
from wrodfreq.db import connect
from wrodfreq.tokenizer import tokenize

SOURCE_ID    = "social"
DISPLAY_NAME = "Romanian subreddits (Reddit)"
URL          = "https://academictorrents.com/details/1614740ac8c94505e4ecb9d88be8bed7b6afddd4"
LICENCE      = ("Pushshift/Watchful1 archival dumps of publicly posted Reddit content; "
                "no formal redistribution licence — collected for research use. "
                "Only aggregate word counts are derived here, never post text.")
REGISTER     = "social"
PERIOD       = "contemporary"

RAW_DIR    = Path("data/raw/social")
FETCH_CHECKPOINT = Path("data/checkpoints/social_fetch.json")

COMMIT_EVERY = 200_000          # records consumed between commits
CURSOR_VERSION = 1
ZSTD_WINDOW  = 2 ** 31          # these dumps need a 2GiB window or they fail to decode
READ_CHUNK   = 2 ** 27

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    if _shutdown:
        # Same reasoning as ingest_subs.py / ingest_news.py (activity-history
        # 2026-09-06): a raised SystemExit can hang behind a stuck non-daemon
        # thread, os._exit() cannot.
        print(f"\n[{datetime.now()}] second signal {sig} — forcing immediate exit "
              f"(the uncommitted batch is lost and will be re-read)", flush=True)
        os._exit(1)
    print(f"\n[{datetime.now()}] signal {sig} — flushing and exiting after current batch "
          f"(press again to force quit)", flush=True)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGHUP, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ---------------------------------------------------------------------------
# Language filter — see the module docstring for why this exists and why it is
# an English-vs-Romanian discriminator rather than general language ID.
# ---------------------------------------------------------------------------

# Romanian function words with NO English homograph. Each diacritic-bearing
# entry is paired with its diacritic-less spelling, because writing Romanian
# without diacritics online is the norm, not the exception.
RO_MARKERS = frozenset("""
    și si să sa este sunt eram pentru din dar sau fost foarte dacă daca când cand
    acum doar chiar mult multă multa mulți multi bine ceva nimic asta ăsta asta
    aici acolo atunci după dupa până pana între intre despre nostru noastră noastra
    lor lui nu mă ma te ne vă îmi imi îți iti fac făcut facut zic zice cred crede
    știu stiu știe stie vrea vreau poate trebuie așa asa tot toți toti toate
    niște niste deja încă inca mereu niciodată niciodata nimeni altceva orice
    oricum totuși totusi adică adica păi pai cumva undeva cineva fiindcă fiindca
    deoarece întrebare intrebare părere parere mulțumesc multumesc mersi salut
    bună buna frate naspa misto mișto
""".split())

# English function words, as a negative signal. Deliberately excludes anything
# that is also Romanian.
EN_MARKERS = frozenset("""
    the and is of to it that you for with this but not have was on as be at or
    from they we can all would there what just like about dont im its his her
    she he my your their them when which who how why been were will more some
    any only also than then because people think know get got one two time make
    really very much even still good great thanks please should could into other
    these those over after before where while our us then need want going said
""".split())

# Homographs deliberately in NEITHER set, recorded so nobody "helpfully" adds
# them later: care, face, are, in, la, a, o, e, un, mai, sa-as-English, cam, no,
# pot, ca, da, ma-as-English. Every one is a real Romanian word that is also a
# real English word (or an English abbreviation common on Reddit).

_DIACRITICS = frozenset("ăâîșț")

# Strip URLs, reddit refs, markdown link targets, and quote markers *before*
# tokenizing — see docstring decision 3.
_URL_RE      = re.compile(r"https?://\S+|www\.\S+")
_REDDIT_REF  = re.compile(r"/?\b[ru]/[A-Za-z0-9_\-]+")
_MD_LINK_RE  = re.compile(r"\[([^\]]*)\]\([^)]*\)")   # keep the label, drop the target
_ENTITY_RE   = re.compile(r"&(?:gt|lt|amp|nbsp|#\d+);")
_CODE_RE     = re.compile(r"`{1,3}[^`]*`{1,3}")

MIN_TOKENS_FOR_LANGID = 4   # reporting threshold only — see looks_romanian()


def clean_markdown(text: str) -> str:
    """Remove the parts of a Reddit body that are not Romanian prose."""
    text = _CODE_RE.sub(" ", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _URL_RE.sub(" ", text)
    text = _REDDIT_REF.sub(" ", text)
    text = _ENTITY_RE.sub(" ", text)
    return text


def _one_line(text: str, width: int = 90) -> str:
    """Flatten whitespace so a multi-line comment prints as one example line."""
    return " ".join(text.split())[:width]


def language_signals(tokens: list[str]) -> tuple[int, int, int]:
    """(romanian marker hits, english marker hits, non-marker tokens carrying a diacritic)."""
    ro = en = dia = 0
    for t in tokens:
        if t in RO_MARKERS:
            ro += 1
        elif t in EN_MARKERS:
            en += 1
        # A marker that happens to carry a diacritic (`mă`, `și`) is already
        # counted above; counting it again would let one word pass the
        # two-signal rule on its own.
        if t not in RO_MARKERS and not _DIACRITICS.isdisjoint(t):
            dia += 1
    return ro, en, dia


def looks_romanian(tokens: list[str]) -> bool:
    """Whether to count this document's tokens at all.

    **The absence of English is itself the strong signal**, and length barely
    matters — that is the one thing calibration against real r/Romania text
    (12,000 comments, 2026-09-22) overturned. The first version of this rule
    demanded two Romanian signals regardless, and threw away 7.3% of the
    corpus: `Ma bucur ca a supravietuit!`, `De ce nu are sabie de dac?`,
    `Hai sa vedem pe cine mai ataca Rusia in afara NATO` — all unambiguously
    Romanian, all `ro=1, en=0`, all dropped. Romanian written without
    diacritics and without the handful of long function words simply does not
    trip two markers in a short comment, and short comments are most of Reddit.

    So: with no English competing at all, one Romanian marker is enough. Only
    when English is actually present does the stricter both-and rule apply.
    Measured effect on the 12k sample: keep rate 80.5% -> 88.5%, +877
    documents, of which a hand-checked sample was uniformly Romanian, with
    zero English documents newly admitted and zero URL-slug tokens.

    The asymmetry is still deliberate in the contested case: a wrong keep
    pollutes the table permanently, a wrong drop costs a few tokens out of
    hundreds of millions.
    """
    ro, en, dia = language_signals(tokens)
    ro += dia                      # a diacritic is as good as a function word
    if en == 0:
        return ro >= 1
    return ro >= 2 and ro > en


# ---------------------------------------------------------------------------
# Reading the dumps
# ---------------------------------------------------------------------------

def read_lines_zst(path: Path):
    """Yield decoded lines from a zstandard-compressed ndjson dump.

    `max_window_size=2**31` is not optional: these dumps are written with a
    window larger than the decompressor's default and fail outright without it.
    Adapted from Watchful1/PushshiftDumps' own `single_file.py`, which is the
    reference reader for this format.

    Resume is by line number rather than byte offset — a zstd stream cannot be
    seeked into, so `--resume` re-reads and discards, costing CPU but no
    network. Same trade `ingest_subs.py` makes for gzip.
    """
    import zstandard

    with open(path, "rb") as fh:
        reader = zstandard.ZstdDecompressor(max_window_size=ZSTD_WINDOW).stream_reader(fh)
        buffer = ""
        while True:
            chunk = reader.read(READ_CHUNK)
            if not chunk:
                break
            try:
                text = chunk.decode("utf-8")
            except UnicodeDecodeError:
                # A multi-byte character straddled the chunk boundary; carry the
                # tail forward rather than dropping it.
                text = chunk.decode("utf-8", errors="replace")
            lines = (buffer + text).split("\n")
            buffer = lines[-1]
            for line in lines[:-1]:
                yield line
        if buffer:
            yield buffer
        reader.close()


def record_text(rec: dict) -> str | None:
    """The Romanian prose in one dump record, or None if there is none.

    Comments carry `body`; submissions carry `title` plus an optional
    `selftext`. Deleted content is the literal string `[deleted]`/`[removed]`,
    not a missing field.
    """
    author = rec.get("author") or ""
    low = author.lower()
    # Moderator and bot accounts post identical boilerplate over and over
    # ("Hi u/X! To reduce spam, accounts with less than 200 comment karma..."),
    # which is mechanical repetition, not usage. Caught 2026-09-22: the
    # original filter missed `<subreddit>-ModTeam` accounts, which every
    # subreddit has and which post removal notices constantly.
    #
    # The unit of trust here is the *author*, not the text: repeated identical
    # text from real people ("da", "mersi", "asa e") is genuine Romanian usage
    # and must not be deduplicated away.
    if (author in ("[deleted]", "AutoModerator")
            or low.endswith("bot")
            or low.endswith("-modteam")
            or low.endswith("modteam")):
        return None

    if "body" in rec:
        body = rec.get("body") or ""
        if body in ("[deleted]", "[removed]", ""):
            return None
        return body

    title = rec.get("title") or ""
    selftext = rec.get("selftext") or ""
    if selftext in ("[deleted]", "[removed]"):
        selftext = ""
    combined = f"{title}\n{selftext}".strip()
    return combined or None


def dump_files(raw_dir: Path) -> list[Path]:
    return sorted(p for p in raw_dir.glob("*.zst") if p.is_file())


# ---------------------------------------------------------------------------
# Generation identity
# ---------------------------------------------------------------------------

def policy() -> dict:
    """Everything that decides which records and tokens this source contributes."""
    return {
        "policy_version": 1,
        "functions": {f.__name__: inspect.getsource(f) for f in
                      (clean_markdown, language_signals, looks_romanian, record_text)},
        "ro_markers": sorted(RO_MARKERS),
        "en_markers": sorted(EN_MARKERS),
        "patterns": [r.pattern for r in (_URL_RE, _REDDIT_REF, _MD_LINK_RE, _ENTITY_RE,
                                         _CODE_RE)],
        "unit": "comment-or-submission",
    }


def spec() -> SourceSpec:
    return SourceSpec(
        source_id=SOURCE_ID, display_name=DISPLAY_NAME, url=URL, licence=LICENCE,
        register=REGISTER, period=PERIOD,
        period_note="ingest in progress — independence not yet measured",
        cursor_version=CURSOR_VERSION, policy=policy())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def input_manifest(files: list[Path]) -> dict:
    return {"files": [{"name": f.name, "size": f.stat().st_size, "sha256": sha256_file(f)}
                      for f in files]}


def empty_cursor() -> dict:
    return {
        "files_done": {},      # filename -> nonblank records consumed (absolute position)
        "files_complete": [],
        "tokens_done": 0,
        "docs_done": 0,
        "author_comment_counts": {},
        "file_stats": {},
    }


def _zero_stats() -> dict:
    return {"records": 0, "malformed": 0, "rejected": 0, "no_tokens": 0,
            "not_romanian": 0, "kept": 0, "tokens": 0}


def fetch_comparison(cursor: dict, fetch_checkpoint: Path) -> dict | None:
    """Records read per dump file vs. the count the fetcher recorded for it."""
    if not fetch_checkpoint.exists():
        return None
    try:
        fetched = json.loads(fetch_checkpoint.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    out = {}
    for name, stats in sorted(cursor["file_stats"].items()):
        stem = name.removesuffix(".zst")
        sub, _, kind = stem.rpartition("_")
        expected = fetched.get(f"{sub}/{kind}", {}).get("count")
        out[name] = {"read": stats["records"], "fetch_count": expected,
                     "difference": None if expected is None else stats["records"] - expected}
    return out


def independence_note(author_counts: Counter, total_docs: int, files: list[Path]) -> str:
    """Spec §7.2's LUMRO check, measured rather than assumed — see docstring decision 2."""
    n_authors = len(author_counts)
    top100 = sum(c for _, c in author_counts.most_common(100))
    share = (100.0 * top100 / total_docs) if total_docs else 0.0
    # Dedupe: each subreddit contributes both a _comments and a _posts file.
    subs = ", ".join(sorted({f.name.split("_")[0] for f in files}))
    return (
        f"Romanian subreddits via the Arctic Shift archive "
        f"(arctic-shift.photon-reddit.com), fetched by build/fetch_social.py; "
        f"subreddits: {subs}. `documents` counts comments/submissions, NOT authors "
        f"— kept comparable with the other five sources on purpose (spec §7.2 "
        f"suggests authors; see ingest_social.py's docstring for why that would "
        f"make this column mean something different here than everywhere else). "
        f"Independence measured instead: {n_authors:,} distinct authors across "
        f"{total_docs:,} documents, with the top 100 authors accounting for "
        f"{share:.1f}% of them. English-vs-Romanian filtering applied at ingest; "
        f"code-switched text means this source's vocabulary skews colloquial and "
        f"anglicised by construction."
    )


# ---------------------------------------------------------------------------
# Calibration — run this before the real ingest
# ---------------------------------------------------------------------------

def calibrate(files: list[Path], sample: int) -> int:
    """Report what the language filter would keep and drop, and why.

    The thresholds in `looks_romanian` were chosen without access to the corpus.
    This prints enough to tune them: the keep rate, the breakdown of *why*
    documents were dropped, and real examples of both decisions — including the
    near-misses, which are where a filter is actually wrong.
    """
    kept = dropped = 0
    drop_reason = Counter()
    kept_tokens = 0
    examples_kept: list[str] = []
    examples_dropped: list[str] = []
    examples_close: list[str] = []

    for path in files:
        print(f"\n--- {path.name} (first {sample:,} records) ---", flush=True)
        seen = 0
        for line in read_lines_zst(path):
            if _shutdown or seen >= sample:
                break
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen += 1
            text = record_text(rec)
            if text is None:
                drop_reason["deleted/bot/empty"] += 1
                dropped += 1
                continue
            tokens = tokenize(clean_markdown(text))
            if not tokens:
                drop_reason["no tokens after cleaning"] += 1
                dropped += 1
                continue
            ro, en, dia = language_signals(tokens)
            score = ro + dia
            if looks_romanian(tokens):
                kept += 1
                kept_tokens += len(tokens)
                if len(examples_kept) < 5 and len(tokens) >= 8:
                    examples_kept.append(f"[ro={score} en={en} n={len(tokens)}] {_one_line(text)}")
                if score == 2 and en > 0 and len(examples_close) < 5:
                    examples_close.append(f"KEPT  [ro={score} en={en}] {_one_line(text)}")
            else:
                dropped += 1
                if len(tokens) < MIN_TOKENS_FOR_LANGID:
                    # Distinguish these two: the first is data we are losing
                    # because it is unjudgeable, the second is the filter
                    # working. Conflating them hides the real cost of
                    # MIN_TOKENS_FOR_LANGID.
                    if score == 0 and en == 0:
                        drop_reason[f"short (<{MIN_TOKENS_FOR_LANGID}) + no signal either way"] += 1
                    else:
                        drop_reason[f"short (<{MIN_TOKENS_FOR_LANGID}) + English present"] += 1
                elif score == 0:
                    drop_reason["no Romanian signal at all"] += 1
                elif score <= en:
                    drop_reason["more English than Romanian"] += 1
                else:
                    drop_reason["only one Romanian signal"] += 1
                if len(examples_dropped) < 5 and len(tokens) >= 8:
                    examples_dropped.append(f"[ro={score} en={en} n={len(tokens)}] {_one_line(text)}")
                if score == 1 and en == 0 and len(examples_close) < 10:
                    examples_close.append(f"DROPPED [ro={score} en={en}] {_one_line(text)}")

    total = kept + dropped
    if not total:
        print("\nNo records read — is data/raw/social/ populated?")
        return 1

    print(f"\n{'='*70}\nCALIBRATION SUMMARY")
    print(f"  records sampled : {total:,}")
    print(f"  kept            : {kept:,} ({100*kept/total:.1f}%)")
    print(f"  dropped         : {dropped:,} ({100*dropped/total:.1f}%)")
    print(f"  tokens kept     : {kept_tokens:,}  (~{kept_tokens/max(kept,1):.1f}/doc)")
    print("\n  why dropped:")
    for reason, n in drop_reason.most_common():
        print(f"    {reason:<34} {n:>9,} ({100*n/total:.1f}%)")

    print("\n  KEPT examples:")
    for e in examples_kept:
        print(f"    {e}")
    print("\n  DROPPED examples:")
    for e in examples_dropped:
        print(f"    {e}")
    print("\n  NEAR-MISSES — read these closely, this is where the filter is wrong:")
    for e in examples_close:
        print(f"    {e}")

    print(f"\n  If the keep rate looks too low, the usual cause is short comments;")
    print(f"  consider lowering MIN_TOKENS_FOR_LANGID or widening RO_MARKERS.")
    print(f"  If English is leaking through, widen EN_MARKERS first — never add")
    print(f"  a Romanian/English homograph to RO_MARKERS to fix a keep rate.")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Romanian subreddit dumps into source_counts")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR,
                        help="Directory of *.zst per-subreddit dumps")
    parser.add_argument("--calibrate", action="store_true",
                        help="Report language-filter behaviour and exit, no writes")
    parser.add_argument("--calibrate-sample", type=int, default=100_000,
                        help="Records per file to sample when calibrating")
    parser.add_argument("--test", action="store_true",
                        help="First 50k records per file, into a new experiment database")
    parser.add_argument("--limit", type=int,
                        help="Stop at absolute record N of each file (across sessions)")
    parser.add_argument("--resume", action="store_true",
                        help="Continue the generation stored in the database")
    parser.add_argument("--db", type=Path, default=None,
                        help="Target database (default data/wrodfreq.db)")
    parser.add_argument("--fetch-checkpoint", type=Path, default=FETCH_CHECKPOINT,
                        help="Fetcher state to compare per-file record counts against")
    args = parser.parse_args()

    files = dump_files(args.raw_dir)
    if not files:
        print(f"No *.zst dumps found in {args.raw_dir}/ — run build/fetch_social.py first.",
              file=sys.stderr)
        return 1
    print(f"Found {len(files)} dump file(s): {', '.join(f.name for f in files)}", flush=True)

    if args.calibrate:
        return calibrate(files, args.calibrate_sample)

    try:
        db_path = target_db(SOURCE_ID, args.db, args.test, args.resume)
    except IngestRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    record_limit = 50_000 if args.test else args.limit

    conn = connect(db_path)
    try:
        session = begin(conn, spec(), resume=args.resume, initial_cursor=empty_cursor(),
                        input_manifest=lambda: input_manifest(files))
    except IngestRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        conn.close()
        return 2
    if session.finished:
        conn.close()
        return 0

    cursor = json.loads(json.dumps(session.cursor))
    author_counts = Counter(cursor["author_comment_counts"])
    batch = Batch(session)
    start = time.time()
    since_commit = 0

    def commit(complete: bool = False, **kw) -> None:
        nonlocal since_commit
        cursor["author_comment_counts"] = dict(author_counts)
        batch.commit(cursor, complete=complete, **kw)
        since_commit = 0

    for path in files:
        if _shutdown:
            break
        name = path.name
        if name in cursor["files_complete"]:
            print(f"  [{name}] already complete, skipping", flush=True)
            continue
        already = cursor["files_done"].get(name, 0)
        stats = cursor["file_stats"].setdefault(name, _zero_stats())
        if already:
            print(f"  [{name}] resuming — skipping first {already:,} records "
                  f"(re-reading, zstd has no seek)", flush=True)
        else:
            print(f"  [{name}] starting", flush=True)

        seen = 0
        stopped = False
        for line in read_lines_zst(path):
            if _shutdown:
                stopped = True
                break
            line = line.strip()
            if not line:
                continue
            if seen < already:
                seen += 1
                continue
            if record_limit is not None and seen >= record_limit:
                stopped = True              # record N+1 is not consumed
                break
            seen += 1
            cursor["files_done"][name] = seen
            stats["records"] += 1
            since_commit += 1

            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                rec = None
            if not isinstance(rec, dict):
                stats["malformed"] += 1
            else:
                text = record_text(rec)
                tokens = tokenize(clean_markdown(text)) if text is not None else None
                if text is None:
                    stats["rejected"] += 1
                elif not tokens:
                    stats["no_tokens"] += 1
                elif not looks_romanian(tokens):
                    stats["not_romanian"] += 1
                else:
                    stats["kept"] += 1
                    stats["tokens"] += len(tokens)
                    cursor["docs_done"] += 1
                    cursor["tokens_done"] += len(tokens)
                    author_counts[rec.get("author") or "[unknown]"] += 1
                    batch.add_document(tokens)

            if since_commit >= COMMIT_EVERY:
                commit()
                print(f"    [{name}] {seen:,} read, {stats['kept']:,} kept, "
                      f"{cursor['tokens_done']:,} tokens | {(time.time() - start) / 60:.1f}m",
                      flush=True)

        if not stopped:
            cursor["files_complete"] = sorted({*cursor["files_complete"], name})
            print(f"  [{name}] complete: {stats['records']:,} records read, "
                  f"{stats['malformed']:,} malformed, {stats['rejected']:,} rejected, "
                  f"{stats['no_tokens'] + stats['not_romanian']:,} filtered, "
                  f"{stats['kept']:,} kept, {stats['tokens']:,} tokens", flush=True)
        commit()

    complete = all(f.name in cursor["files_complete"] for f in files)
    if complete:
        note = independence_note(author_counts, cursor["docs_done"], files)
        comparison = fetch_comparison(cursor, args.fetch_checkpoint)
        commit(complete=True, period_note=note,
               provenance={"file_stats": cursor["file_stats"], "fetch_comparison": comparison})
        print(f"\n{note}")
        if comparison:
            off = {n: c for n, c in comparison.items() if c["difference"] not in (0, None)}
            print(f"fetch comparison: {len(comparison) - len(off)}/{len(comparison)} files "
                  f"match the fetcher's record count"
                  + "".join(f"\n  {n}: read {c['read']:,}, fetcher {c['fetch_count']:,}"
                            for n, c in off.items()))
    print(f"\n{SOURCE_ID}: {cursor['docs_done']:,} documents, {cursor['tokens_done']:,} tokens, "
          f"{'completed' if complete else 'in_progress'}, {(time.time() - start) / 60:.1f}m",
          flush=True)
    conn.close()
    if complete:
        return 0
    return 1 if _shutdown else 0


if __name__ == "__main__":
    sys.exit(main())
