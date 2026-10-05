#!/usr/bin/env python3
"""Summarise a labelled data/review/hyphen_sample.csv: junk rate per stratum.

A stratum is only worth dropping as a unit if almost all of it is junk (J) and almost none
is real (K). Unlabelled rows and '?' are reported separately, not guessed.

Usage:  python build/summarize_hyphen_labels.py [path]
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

ap = argparse.ArgumentParser(description="Junk rate per stratum of a labelled hyphen sample.")
ap.add_argument("path", nargs="?", type=Path, default=Path("data/review/hyphen_sample.csv"))
path = ap.parse_args().path
by = defaultdict(Counter)
bad = []
with open(path, newline="", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        lab = r["label"].strip().upper() or "UNLABELLED"
        if lab not in {"K", "J", "?", "UNLABELLED"}:
            bad.append((r["word"], lab)); lab = "other"
        by[r["stratum"]][lab] += 1
print(f"{'stratum':<16}{'K keep':>8}{'J junk':>8}{'?':>6}{'todo':>6}   junk share of decided")
for s, c in by.items():
    k, j = c["K"], c["J"]
    share = f"{100 * j / (k + j):.0f}%" if k + j else "n/a"
    print(f"{s:<16}{k:>8}{j:>8}{c['?']:>6}{c['UNLABELLED']:>6}   {share}")
if bad:
    print("\nUnrecognised labels (use K, J or ?):", bad[:10])
