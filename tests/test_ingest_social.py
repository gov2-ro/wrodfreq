"""Tests for ingest_social.py's language filter and markdown cleaning.

This is the first source in the panel that needs language identification at all
— `news` arrived pre-tagged, and `subs`/`eu`/`wiki`/`web` are monolingual by
construction. It is also the source where getting it wrong is worst: `the`,
`and` and `is` are valid token shapes under our character class, so English
leaking through does not produce visible junk, it produces *plausible-looking
English entries in a Romanian frequency table*.

The asymmetry these tests encode: a wrong keep pollutes the table permanently,
a wrong drop costs a few tokens out of hundreds of millions. So the English
cases are the strict ones.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))
_spec = importlib.util.spec_from_file_location("_ingest_social", BUILD / "ingest_social.py")
social = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(social)

from wrodfreq.tokenizer import tokenize


def judge(text: str) -> bool:
    return social.looks_romanian(tokenize(social.clean_markdown(text)))


ROMANIAN = [
    "Cred că este foarte bine să facem asta acum, dar nu știu dacă merge.",
    "Pai eu zic ca trebuie sa incercam, chiar daca nu avem foarte mult timp.",
    "Mulțumesc frate, mișto explicație! Chiar aveam nevoie de asta.",
    "Nu sunt de acord deloc. Părerea mea este că trebuie altceva.",
    "Stie cineva daca trebuie sa platesc acum sau pot sa astept?",
    # no diacritics at all — the common online register, must still pass
    "Asa este, dar parerea mea e ca trebuie sa mai asteptam putin pana atunci.",
]

ENGLISH = [
    "I think this is a really good idea and we should do it now.",
    "What are you talking about? That does not make any sense to me at all.",
    "Does anyone know how much time this would take for the whole thing?",
    "Just wanted to say thanks for all of your help with this, really great.",
]


@pytest.mark.parametrize("text", ROMANIAN)
def test_romanian_is_kept(text):
    assert judge(text) is True


@pytest.mark.parametrize("text", ENGLISH)
def test_english_is_dropped(text):
    assert judge(text) is False


def test_no_romanian_english_homograph_is_a_marker():
    """`care`, `face`, `are`, `in`, `la` etc. are Romanian words AND English words.

    Any of them in RO_MARKERS would classify plain English as Romanian. This
    test exists because adding one is the obvious "fix" when a keep rate looks
    too low, and it is always wrong — the ingester's docstring says so, and
    this makes it fail loudly instead.
    """
    homographs = {"care", "face", "are", "in", "la", "a", "o", "e", "no", "an",
                  "made", "man", "set", "son", "pot", "cam", "sale", "date"}
    leaked = homographs & social.RO_MARKERS
    assert not leaked, f"Romanian/English homographs must not be markers: {leaked}"


def test_marker_sets_do_not_overlap():
    assert not (social.RO_MARKERS & social.EN_MARKERS)


def test_short_text_needs_a_clean_romanian_signal():
    assert judge("Da, mersi mult!") is True        # RO signal, no English
    assert judge("yes thanks") is False            # English present
    assert judge("ok") is False                    # no signal either way


def test_urls_are_stripped_before_tokenizing():
    """The 2026-09-21 URL-slug finding, prevented at the source this time."""
    text = "Uite aici https://example.com/foo-bar-baz-slug si chiar bine asta."
    tokens = tokenize(social.clean_markdown(text))
    assert not any("-" in t for t in tokens), tokens
    assert "example" not in tokens and "slug" not in tokens
    assert "chiar" in tokens and "bine" in tokens   # the Romanian survives


def test_markdown_link_keeps_label_drops_target():
    tokens = tokenize(social.clean_markdown(
        "Vezi [documentatia buna](https://site.ro/ceva-lung-aici) daca vrei."))
    assert "documentatia" in tokens and "buna" in tokens
    assert "site" not in tokens and "lung" not in tokens


def test_reddit_refs_and_entities_are_stripped():
    tokens = tokenize(social.clean_markdown("Salut /r/Romania si /u/cineva &gt; asta"))
    assert "romania" not in tokens and "cineva" not in tokens
    assert "gt" not in tokens and "amp" not in tokens


def test_code_blocks_are_stripped():
    tokens = tokenize(social.clean_markdown("Merge asa `SELECT the_thing FROM table` chiar bine"))
    assert "select" not in tokens and "table" not in tokens
    assert "chiar" in tokens


# ---------------------------------------------------------------------------
# record_text: what counts as a document at all
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rec", [
    {"author": "[deleted]", "body": "Cred că este foarte bine"},
    {"author": "AutoModerator", "body": "Postare ștearsă automat."},
    {"author": "some_bot", "body": "Cred că este foarte bine"},
    {"author": "SomeBot", "body": "Cred că este foarte bine"},
    {"author": "x", "body": "[deleted]"},
    {"author": "x", "body": "[removed]"},
    {"author": "x", "body": ""},
])
def test_non_content_records_are_skipped(rec):
    assert social.record_text(rec) is None


def test_comment_and_submission_shapes():
    assert social.record_text({"author": "x", "body": "salut"}) == "salut"
    # a submission is title + selftext, and a removed selftext still leaves the title
    assert social.record_text(
        {"author": "x", "title": "Intrebare", "selftext": "detalii"}) == "Intrebare\ndetalii"
    assert social.record_text(
        {"author": "x", "title": "Intrebare", "selftext": "[removed]"}) == "Intrebare"
