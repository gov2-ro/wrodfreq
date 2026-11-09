"""Lazy loader for the shipped data files (spec §10.2). No SQLite at runtime.

Three files, loaded independently and lazily — the surface table (words, zipf,
n_reliable, n_attesting, spread) on first call to any core function, the
by-source breakdown only if `by_source()` is called, the lemma layer only if a
lemma function is. See build/build_package.py's docstring for why they're split
and why zipf/spread are quantized ints here (centizipf, `zipf_scale` in the file).

**Data format 2** (ADR-006). Every file carries the build's `build_id`, source
panel and `zipf_scale`; the surface and by-source files also carry the identity of
the ordered word list (`words_sha256`). A companion file from another build is
refused even when its row count matches — positional data must never be read
against the wrong word list. Format 1 files are refused with a rebuild message.
The lemma fallback applies only when the surface header says the build has no
lemma layer; a lemma file from another build is an error, not a fallback.
"""

from __future__ import annotations

import hashlib
import lzma
from dataclasses import dataclass
from pathlib import Path

import msgpack

FORMAT_VERSION = 2

DATA_DIR = Path(__file__).parent / "data"
SURFACE_PATH = DATA_DIR / "ro_surface.msgpack.xz"
BY_SOURCE_PATH = DATA_DIR / "ro_by_source.msgpack.xz"
LEMMA_PATH = DATA_DIR / "ro_lemma.msgpack.xz"


@dataclass(frozen=True)
class FrequencyDetail:
    zipf: float
    n_reliable: int
    n_attesting: int
    n_sources: int
    spread: float


@dataclass(frozen=True)
class LemmaDetail:
    zipf: float
    n_forms: int
    zipf_headword: float | None   # the citation form alone; None if never reliably seen
    family_ratio: float | None


class DataFileError(ValueError):
    """A shipped data file is from another build, truncated, or in an old format."""


def _check_format(payload: dict, path: Path) -> None:
    if payload.get("format_version") != FORMAT_VERSION:
        raise DataFileError(
            f"{path} has data format {payload.get('format_version')!r}; this wrodfreq reads "
            f"format {FORMAT_VERSION}. Rebuild the data with build/build_package.py and "
            f"install the generation, or reinstall the package")


def _check_lengths(payload: dict, keys: tuple[str, ...], n: int, path: Path) -> None:
    for key in keys:
        if len(payload[key]) != n:
            raise DataFileError(f"{path}: `{key}` has {len(payload[key]):,} entries, "
                                f"header says {n:,} — the file is truncated or corrupt")


def _words_sha256(words: list[str]) -> str:
    return hashlib.sha256("\n".join(words).encode("utf-8")).hexdigest()


class _Lemmas:
    def __init__(self, payload: dict):
        self._scale: int = payload["zipf_scale"]
        self._index = {w: i for i, w in enumerate(payload["lemmas"])}
        self._zipf = payload["zipf"]
        self._n_forms = payload["n_forms"]
        self._headword = payload["zipf_headword"]
        self._ratio = payload["family_ratio"]

    def detail(self, lemma: str) -> LemmaDetail | None:
        i = self._index.get(lemma)
        if i is None:
            return None
        sc = self._scale
        hw, fr = self._headword[i], self._ratio[i]
        return LemmaDetail(
            zipf=self._zipf[i] / sc,
            n_forms=self._n_forms[i],
            zipf_headword=None if hw < 0 else hw / sc,
            family_ratio=None if fr < 0 else fr / sc,
        )


_lemmas: _Lemmas | None = None


def load_lemmas() -> _Lemmas | None:
    """The lemma layer, or None if this build intentionally ships none."""
    global _lemmas
    if _lemmas is None:
        surface = load()
        if not surface.lemma:
            return None
        if not LEMMA_PATH.exists():
            raise DataFileError(f"build {surface.build_id} includes a lemma layer but "
                                f"{LEMMA_PATH} is missing — reinstall the package")
        payload = _load_payload(LEMMA_PATH)
        _check_format(payload, LEMMA_PATH)
        surface.check_companion(payload, LEMMA_PATH)
        _check_lengths(payload, ("lemmas", "zipf", "n_forms", "zipf_headword", "family_ratio"),
                       payload["lemma_count"], LEMMA_PATH)
        _lemmas = _Lemmas(payload)
    return _lemmas


def _load_payload(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"wrodfreq's data file is missing at {path} — run "
            f"build/build_package.py, or reinstall the package"
        )
    with lzma.open(path, "rb") as f:
        return msgpack.unpackb(f.read(), raw=False)


class _Surface:
    """Holds the surface table and lazily attaches the by-source table."""

    def __init__(self, payload: dict, path: Path | None = None):
        path = path or SURFACE_PATH
        _check_format(payload, path)
        n = payload["word_count"]
        _check_lengths(payload, ("words", "zipf", "n_reliable", "n_attesting", "spread"), n, path)
        if len(payload["sources"]) != payload["n_sources"]:
            raise DataFileError(f"{path}: source panel and n_sources disagree")
        if _words_sha256(payload["words"]) != payload["words_sha256"]:
            raise DataFileError(f"{path}: the word list does not match its words_sha256")
        self.build_id: str = payload["build_id"]
        self.words_sha256: str = payload["words_sha256"]
        self.lemma: bool = bool(payload["lemma"])
        self.package_version: str | None = payload.get("package_version")
        self.source_tokens: list[int] | None = payload.get("source_tokens")
        self.built: str = payload["built"]
        self.sources: list[str] = payload["sources"]
        self.n_sources: int = payload["n_sources"]
        self._scale: int = payload["zipf_scale"]
        self._word_count: int = payload["word_count"]
        self._words: list[str] = payload["words"]
        self._zipf: list[int] = payload["zipf"]
        self._n_reliable: list[int] = payload["n_reliable"]
        self._n_attesting: list[int] = payload["n_attesting"]
        self._spread: list[int] = payload["spread"]
        self._index: dict[str, int] = {w: i for i, w in enumerate(self._words)}
        self._sorted_words: list[str] | None = None
        self._sorted_ascii_words: list[str] | None = None
        self._by_source: list[list[int | None]] | None = None

    def check_companion(self, payload: dict, path: Path) -> None:
        """A by-source or lemma payload must come from this exact build."""
        if payload.get("build_id") != self.build_id:
            raise DataFileError(
                f"{path} is from build {payload.get('build_id')}, not {self.build_id} — "
                f"the data files were mixed; install one generation's files together")
        if payload.get("sources") != self.sources or payload.get("zipf_scale") != self._scale:
            raise DataFileError(f"{path}: source panel or scale differs from {SURFACE_PATH}")

    def row(self, normalized_word: str) -> int | None:
        return self._index.get(normalized_word)

    def zipf(self, i: int) -> float:
        return self._zipf[i] / self._scale

    def detail(self, i: int) -> FrequencyDetail:
        return FrequencyDetail(
            zipf=self.zipf(i),
            n_reliable=self._n_reliable[i],
            n_attesting=self._n_attesting[i],
            n_sources=self.n_sources,
            spread=self._spread[i] / self._scale,
        )

    def top_n(self, n: int, ascii_only: bool = False) -> list[str]:
        if self._sorted_words is None:
            self._sorted_words = [
                w for _, w in sorted(
                    zip(self._zipf, self._words),
                    key=lambda pair: (-pair[0], pair[1]),
                )
            ]
        if not ascii_only:
            return self._sorted_words[:n]
        # Romanian diacritics (ă â î ș ț) are common even in high-frequency
        # words, so a fixed over-fetch-then-filter multiplier risks
        # returning fewer than n words. Filter the whole sorted list once,
        # cache it — cheap after the first call, and always exactly right.
        if self._sorted_ascii_words is None:
            self._sorted_ascii_words = [w for w in self._sorted_words if w.isascii()]
        return self._sorted_ascii_words[:n]

    def by_source_row(self, i: int) -> dict[str, float | None]:
        if self._by_source is None:
            payload = _load_payload(BY_SOURCE_PATH)
            _check_format(payload, BY_SOURCE_PATH)
            self.check_companion(payload, BY_SOURCE_PATH)
            if (payload["word_count"], payload["words_sha256"]) != (
                    self._word_count, self.words_sha256):
                raise DataFileError(
                    f"{BY_SOURCE_PATH} was built for another word list than {SURFACE_PATH}")
            _check_lengths(payload, ("by_source",), self._word_count, BY_SOURCE_PATH)
            width = len(self.sources)
            if any(len(r) != width for r in payload["by_source"]):
                raise DataFileError(f"{BY_SOURCE_PATH}: a row does not have {width} sources")
            self._by_source = payload["by_source"]
        row = self._by_source[i]
        scale = self._scale
        return {
            source: (None if v is None else v / scale)
            for source, v in zip(self.sources, row)
        }


_surface: _Surface | None = None


def load() -> _Surface:
    global _surface
    if _surface is None:
        _surface = _Surface(_load_payload(SURFACE_PATH), SURFACE_PATH)
    return _surface
