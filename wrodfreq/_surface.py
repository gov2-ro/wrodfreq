"""Lazy loader for the shipped data files (spec §10.2). No SQLite at runtime.

Two files, loaded independently and lazily — the surface table (words, zipf,
n_reliable, n_attesting, spread) on first call to any core function, the
by-source breakdown only if `by_source()` is actually called. See
build/build_package.py's docstring for why they're split and why zipf/spread
are quantized ints here (centizipf, `zipf_scale` in the file) rather than
floats.
"""

from __future__ import annotations

import lzma
from dataclasses import dataclass
from pathlib import Path

import msgpack

DATA_DIR = Path(__file__).parent / "data"
SURFACE_PATH = DATA_DIR / "ro_surface.msgpack.xz"
BY_SOURCE_PATH = DATA_DIR / "ro_by_source.msgpack.xz"


@dataclass(frozen=True)
class FrequencyDetail:
    zipf: float
    n_reliable: int
    n_attesting: int
    n_sources: int
    spread: float


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

    def __init__(self, payload: dict):
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
            if payload["word_count"] != self._word_count:
                raise ValueError(
                    f"{BY_SOURCE_PATH} doesn't match {SURFACE_PATH} "
                    f"({payload['word_count']:,} vs {self._word_count:,} words) — "
                    f"rebuild both together with build/build_package.py"
                )
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
        _surface = _Surface(_load_payload(SURFACE_PATH))
    return _surface
