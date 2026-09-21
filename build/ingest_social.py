#!/usr/bin/env python3
"""Stage 1 (social source) — ingest Romanian subreddit dumps into `source_counts`.

Spec §6's "optional seventh": Reddit is *the* register missing from every other
Romanian corpus. `subs` (film dialogue) is scripted and translated; `web` and
`news` are edited prose; `eu` is bureaucratic. Nothing in the panel is
spontaneous, unedited, native, contemporary Romanian, and the zipf 2.0-4.0 band
is exactly where the panel is thinnest (trim engagement 25-71%, measured
2026-09-21).

## Getting the data — this is a manual step, on purpose

There is no API and no HuggingFace mirror worth using (the one published
Romanian Reddit corpus, arXiv 2410.09907, is 23k samples — two orders of
magnitude too small to be a panel source). The data is Watchful1's per-subreddit
extract of the Pushshift dumps, on Academic Torrents:

    https://academictorrents.com/details/1614740ac8c94505e4ecb9d88be8bed7b6afddd4
    "Subreddit comments/submissions 2005-06 to 2024-12"

The top ~40,000 subreddits are published as *separate files*, so a torrent
client can fetch only what is needed rather than the multi-TB whole. Download at
minimum:

    Romania_comments.zst
    Romania_submissions.zst

and drop them in `data/raw/social/` (gitignored). More Romanian subreddits can
be added to that directory later and re-ingested incrementally — the checkpoint
is per file, so adding a file does not redo the ones already counted.

Note the dumps now run to **2024-12**, not the "pre-2023" spec §6 assumed when
it was written. The contemporary window is wider than planned.

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
    python build/ingest_social.py --test          # first 50k records/file, no resume
    python build/ingest_social.py                 # full run
    python build/ingest_social.py --resume        # pick up from checkpoint

Restart loop (stop automatically on success):
    while true; do
        python -u build/ingest_social.py --resume
        [ $? -eq 0 ] && break
        echo "[$(date)] restarting in 15s..." && sleep 15
    done

Output: data/wrodfreq.db, source_id='social'.
Checkpoint: data/checkpoints/social_checkpoint.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect
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
CHECKPOINT = Path("data/checkpoints/social_checkpoint.json")

COMMIT_EVERY = 200_000          # records between DB commits
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
              f"(progress since the last checkpoint is lost)", flush=True)
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

MIN_TOKENS_FOR_LANGID = 4   # below this, a stricter rule applies


def clean_markdown(text: str) -> str:
    """Remove the parts of a Reddit body that are not Romanian prose."""
    text = _CODE_RE.sub(" ", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _URL_RE.sub(" ", text)
    text = _REDDIT_REF.sub(" ", text)
    text = _ENTITY_RE.sub(" ", text)
    return text


def language_signals(tokens: list[str]) -> tuple[int, int, int]:
    """(romanian marker hits, english marker hits, tokens carrying a diacritic)."""
    ro = en = dia = 0
    for t in tokens:
        if t in RO_MARKERS:
            ro += 1
        elif t in EN_MARKERS:
            en += 1
        if not _DIACRITICS.isdisjoint(t):
            dia += 1
    return ro, en, dia


def looks_romanian(tokens: list[str]) -> bool:
    """Whether to count this document's tokens at all.

    Long enough to judge: needs at least two Romanian signals and more Romanian
    than English. Too short to judge: needs a Romanian signal and *no* English
    at all — the asymmetry is deliberate, since a wrong keep pollutes the table
    permanently while a wrong drop only costs a few tokens out of hundreds of
    millions.
    """
    ro, en, dia = language_signals(tokens)
    ro += dia                      # a diacritic is as good as a function word
    if len(tokens) < MIN_TOKENS_FOR_LANGID:
        return ro >= 1 and en == 0
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
    if author in ("[deleted]", "AutoModerator") or author.lower().endswith("bot"):
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
# Database
# ---------------------------------------------------------------------------

def ensure_source_row(conn: sqlite3.Connection, period_note: str) -> None:
    conn.execute(
        """
        INSERT INTO sources (source_id, display_name, url, licence, register,
                              period, period_note, total_tokens, total_docs, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'in_progress')
        ON CONFLICT(source_id) DO UPDATE SET period_note = excluded.period_note
        """,
        (SOURCE_ID, DISPLAY_NAME, URL, LICENCE, REGISTER, PERIOD, period_note),
    )
    conn.commit()


def flush(conn: sqlite3.Connection, word_counts: dict, doc_counts: dict,
          total_tokens: int, total_docs: int, status: str) -> None:
    if word_counts:
        conn.executemany(
            """
            INSERT INTO source_counts (word, source_id, occurrences, documents)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(word, source_id) DO UPDATE SET
                occurrences = occurrences + excluded.occurrences,
                documents   = documents   + excluded.documents
            """,
            [(w, SOURCE_ID, c, doc_counts[w]) for w, c in word_counts.items()],
        )
        word_counts.clear()
        doc_counts.clear()
    conn.execute(
        """
        UPDATE sources SET total_tokens = ?, total_docs = ?, status = ?,
                            ingested_at = ?
        WHERE source_id = ?
        """,
        (total_tokens, total_docs, status,
         datetime.now(timezone.utc).isoformat(), SOURCE_ID),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def _empty_checkpoint() -> dict:
    return {
        "files_done": {},      # filename -> records read (complete files keep their count)
        "files_complete": [],
        "tokens_done": 0,
        "docs_done": 0,
        "authors": [],         # distinct authors seen, for the independence measure
        "author_comment_counts": {},
    }


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        try:
            cp = _empty_checkpoint()
            cp.update(json.loads(CHECKPOINT.read_text()))
            return cp
        except (json.JSONDecodeError, ValueError):
            print("warning: corrupted checkpoint, starting fresh")
    return _empty_checkpoint()


def save_checkpoint(cp: dict) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT.with_suffix(".tmp")
    tmp.write_text(json.dumps(cp))
    tmp.replace(CHECKPOINT)


def independence_note(author_counts: Counter, total_docs: int, files: list[Path]) -> str:
    """Spec §7.2's LUMRO check, measured rather than assumed — see docstring decision 2."""
    n_authors = len(author_counts)
    top100 = sum(c for _, c in author_counts.most_common(100))
    share = (100.0 * top100 / total_docs) if total_docs else 0.0
    subs = ", ".join(sorted(f.name.split("_")[0] for f in files))
    return (
        f"Reddit per-subreddit dumps (Watchful1/Pushshift, through 2024-12); "
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
                    examples_kept.append(f"[ro={score} en={en} n={len(tokens)}] {text[:90]}")
                if score == 2 and en > 0 and len(examples_close) < 5:
                    examples_close.append(f"KEPT  [ro={score} en={en}] {text[:90]}")
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
                    examples_dropped.append(f"[ro={score} en={en} n={len(tokens)}] {text[:90]}")
                if score == 1 and en == 0 and len(examples_close) < 10:
                    examples_close.append(f"DROPPED [ro={score} en={en}] {text[:90]}")

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
    global CHECKPOINT
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR,
                        help="Directory of *.zst per-subreddit dumps")
    parser.add_argument("--calibrate", action="store_true",
                        help="Report language-filter behaviour and exit, no writes")
    parser.add_argument("--calibrate-sample", type=int, default=100_000,
                        help="Records per file to sample when calibrating")
    parser.add_argument("--test", action="store_true",
                        help="First 50k records per file, separate checkpoint")
    parser.add_argument("--limit", type=int, help="Stop after N records per file")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    files = dump_files(args.raw_dir)
    if not files:
        print(f"No *.zst dumps found in {args.raw_dir}/\n\n"
              f"Download at least Romania_comments.zst and Romania_submissions.zst\n"
              f"from the Academic Torrents per-subreddit release and put them there —\n"
              f"see this module's docstring for the torrent link and why this step\n"
              f"cannot be automated.", file=sys.stderr)
        return 1
    print(f"Found {len(files)} dump file(s): {', '.join(f.name for f in files)}", flush=True)

    if args.calibrate:
        return calibrate(files, args.calibrate_sample)

    if args.test:
        CHECKPOINT = CHECKPOINT.with_name(CHECKPOINT.stem + ".test.json")
    record_limit = 50_000 if args.test else args.limit

    cp = load_checkpoint() if args.resume else _empty_checkpoint()
    author_counts = Counter(cp.get("author_comment_counts", {}))

    conn = connect(args.db)
    ensure_source_row(conn, "ingest in progress — independence not yet measured")

    word_counts: dict[str, int] = defaultdict(int)
    doc_counts: dict[str, int] = defaultdict(int)
    total_tokens = cp["tokens_done"]
    total_docs = cp["docs_done"]
    since_commit = 0
    start = time.time()
    # Whether every file ran to its natural end. A --limit/--test run must NOT
    # mark the source 'completed': wrodfreq/db.py's eligible_sources() selects
    # on exactly that value, so a truncated ingest would silently enter the
    # merge as if it were the whole corpus. Same class of footgun as
    # ingest_news.py's --test/--db one (activity-history 2026-09-06).
    ran_to_completion = True

    for path in files:
        if _shutdown:
            ran_to_completion = False
            break
        if path.name in cp["files_complete"]:
            print(f"  [{path.name}] already complete, skipping", flush=True)
            continue

        already = cp["files_done"].get(path.name, 0)
        if already:
            print(f"  [{path.name}] resuming — skipping first {already:,} records "
                  f"(re-reading, zstd has no seek)", flush=True)
        else:
            print(f"  [{path.name}] starting", flush=True)

        seen = 0
        kept = 0
        for line in read_lines_zst(path):
            if _shutdown:
                break
            line = line.strip()
            if not line:
                continue
            seen += 1
            if seen <= already:
                continue
            if record_limit and seen > record_limit:
                ran_to_completion = False
                break

            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            text = record_text(rec)
            if text is None:
                continue
            tokens = tokenize(clean_markdown(text))
            if not tokens or not looks_romanian(tokens):
                continue

            kept += 1
            total_docs += 1
            total_tokens += len(tokens)
            author = rec.get("author") or "[unknown]"
            author_counts[author] += 1

            for w in tokens:
                word_counts[w] += 1
            for w in set(tokens):          # `documents` = one per doc per word
                doc_counts[w] += 1

            since_commit += 1
            if since_commit >= COMMIT_EVERY:
                flush(conn, word_counts, doc_counts, total_tokens, total_docs,
                      "in_progress")
                cp["files_done"][path.name] = seen
                cp["tokens_done"] = total_tokens
                cp["docs_done"] = total_docs
                cp["author_comment_counts"] = dict(author_counts)
                save_checkpoint(cp)
                since_commit = 0
                elapsed = time.time() - start
                print(f"    [{path.name}] {seen:,} read, {kept:,} kept, "
                      f"{total_tokens:,} tokens | {elapsed/60:.1f}m", flush=True)

        flush(conn, word_counts, doc_counts, total_tokens, total_docs, "in_progress")
        cp["files_done"][path.name] = seen
        cp["tokens_done"] = total_tokens
        cp["docs_done"] = total_docs
        cp["author_comment_counts"] = dict(author_counts)
        if not _shutdown and not record_limit:
            cp["files_complete"].append(path.name)
            print(f"  [{path.name}] complete: {seen:,} records read, {kept:,} kept", flush=True)
        save_checkpoint(cp)

    status = "completed" if (ran_to_completion and not _shutdown) else "in_progress"
    flush(conn, word_counts, doc_counts, total_tokens, total_docs, status)

    if status == "completed":
        note = independence_note(author_counts, total_docs, files)
        conn.execute("UPDATE sources SET period_note = ? WHERE source_id = ?",
                     (note, SOURCE_ID))
        conn.commit()
        print(f"\n{note}")

    print(f"\n{SOURCE_ID}: {total_docs:,} documents, {total_tokens:,} tokens, "
          f"status={status}, {(time.time()-start)/60:.1f}m", flush=True)
    if status != "completed" and not _shutdown:
        print("  (status stays 'in_progress' because this run was truncated by "
              "--test/--limit — merge.py will correctly ignore this source)", flush=True)
    if status == "completed":
        print("Next: python build/compute_zipf.py --source social, then merge.py, "
              "build_lemma_layer.py, build_package.py, validate.py")
    conn.close()
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
