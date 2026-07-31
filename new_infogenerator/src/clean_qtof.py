"""Clean the QToF drug-screen CSV.

Reads the untouched ``versioned/qtof_v1.csv`` baseline and writes
``versioned/qtof_v2.csv`` from scratch on every run, so the script is
deterministic and idempotent. Shape is preserved: 396 rows x 5 columns, one row
per screen.

This pass is text normalization only. It does NOT canonicalize analyte names
against the mapping, collapse metabolites, merge the 23 second instances, or
explode to one row per analyte -- those need decisions still outstanding.

What it does:
  * Trims whitespace on every cell and collapses internal whitespace runs
    inside each analyte name.
  * Repairs a specific unbalanced parenthesis (Record ID 60).
  * Splits the two ion-mode lists paren-aware, so a comma *inside* a
    parenthetical qualifier is not treated as a delimiter.
  * Splits the handful of tokens where the delimiter was omitted -- a period
    ("MDA. cocaine") or a double space after a name (") N-desethyl...").
  * Normalizes the five "nothing found" spellings onto one sentinel.

Ordering matters: the missing-delimiter splits run BEFORE whitespace is
collapsed, because collapsing runs of spaces first would destroy the double
space that marks the omission.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
from collections import Counter
from pathlib import Path

KEY_COL = "Record ID"
INSTANCE_COL = "Repeat Instance"
SPEC_COL = "Specimen number"
MATRIX_COL = "Sample matrix"
POS_COL = "Positive Ion Mode"
NEG_COL = "Negative Ion Mode"
ANALYTE_COLS = (POS_COL, NEG_COL)

# Sample matrix -- the specimen type the screen was run on. Recorded only on the
# QToF rows, and blank for the 248 screens that predate the field.
MATRIX_VALUES = {"urine", "plasma"}

# The one spelling every "nothing found" cell is normalized onto.
SENTINEL = "None detected"
# Lowercased, trailing-period-stripped forms that mean the same thing.
SENTINEL_FORMS = {
    "none detected",
    "none",
    "none observed",
    "no drugs detected",
    "not detected",
    "negative",
    "-",
}

# Analytes are separated by ", ". A slash is part of a NAME
# ("4-ANPP/despropionylfentanyl", "citalopram/escitalopram" are alias pairs for
# one substance) and must never be treated as a delimiter.
JOINER = ", "

# A period used in place of a comma: "MDA. cocaine".
_PERIOD_FUSION = re.compile(r"(?<=\w)\.[ \t]+(?=\w)")
# A double space used in place of a comma: ") N-desethyl", "fentanyl  Haloperidol".
# The lookbehind deliberately excludes a comma, because most double spaces in
# this file are just sloppy spacing AFTER a comma and are already delimited.
_SPACE_FUSION = re.compile(r"(?<=[\w)])[ \t]{2,}(?=[\w(])")

# Unbalanced parentheses, repaired by Record ID. The expected current value is
# asserted first so this fails loudly rather than editing the wrong cell.
PAREN_REPAIRS = {
    ("60", NEG_COL): (
        "THC-M (carboxy THC metabolite), THC-M (carboxy-THC glucuronide",
        "THC-M (carboxy THC metabolite), THC-M (carboxy-THC glucuronide)",
    ),
}

# A delimiter omitted between two analytes separated by a SINGLE space, which
# cannot be detected by rule: legitimate names contain single spaces
# ("meta-methyl acetyl fentanyl"). Repaired by hand, and only here.
#
# ID 238 read "... norfentanyl, fentanyl 1-(3-chlorophenyl)piperazine (mCPP),
# olanzapine ...". Both are real analytes and 11 other rows list
# "1-(3-chlorophenyl)piperazine (mCPP)" properly comma-delimited, so the missing
# comma sits between "fentanyl" and "1-".
SPACE_FUSION_REPAIRS = {
    ("238", POS_COL): (
        "fentanyl 1-(3-chlorophenyl)piperazine (mCPP)",
        "fentanyl, 1-(3-chlorophenyl)piperazine (mCPP)",
    ),
}


def split_analytes(text: str) -> list[str]:
    """Split an ion-mode cell into analyte names.

    Splits on top-level commas only: a comma inside parentheses belongs to the
    qualifier, e.g. "lidocaine-M (MEGX, N deethylated metabolite)".
    """
    out: list[str] = []
    depth = 0
    current = ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append(current)
            current = ""
        else:
            current += ch
    out.append(current)
    return [t for t in (re.sub(r"\s+", " ", p).strip() for p in out) if t]


def clean_analyte_cell(raw: str) -> tuple[str, list[str]]:
    """Normalize one ion-mode cell. Returns the cell and a list of notes."""
    notes: list[str] = []
    text = raw.replace(" ", " ")
    if not text.strip():
        return "", notes

    # Missing-delimiter repairs first -- see module docstring on ordering.
    fused = _PERIOD_FUSION.sub(JOINER, text)
    if fused != text:
        notes.append("period used as delimiter")
        text = fused
    fused = _SPACE_FUSION.sub(JOINER, text)
    if fused != text:
        notes.append("double space used as delimiter")
        text = fused

    tokens = split_analytes(text)

    sentinels = [t for t in tokens if t.lower().rstrip(".") in SENTINEL_FORMS]
    analytes = [t for t in tokens if t.lower().rstrip(".") not in SENTINEL_FORMS]

    if sentinels and analytes:
        # A "none" mixed in with real detections is contradictory; the
        # detections win and the sentinel is dropped.
        notes.append(f"dropped sentinel {sentinels!r} alongside real analytes")
    elif sentinels:
        if any(t != SENTINEL for t in sentinels):
            notes.append(f"sentinel {sentinels[0]!r} -> {SENTINEL!r}")
        return SENTINEL, notes

    # The same analyte listed twice in one cell is a typing slip, not two
    # detections. Keep the first occurrence, preserving its original casing.
    seen: set[str] = set()
    deduped: list[str] = []
    dropped: list[str] = []
    for analyte in analytes:
        key = analyte.lower()
        if key in seen:
            dropped.append(analyte)
            continue
        seen.add(key)
        deduped.append(analyte)
    if dropped:
        notes.append(f"dropped repeated analyte {dropped!r}")

    return JOINER.join(deduped), notes


def read_rows(source: Path) -> tuple[list[str], list[dict[str, str]]]:
    raw = source.read_text(encoding="utf-8-sig").lstrip("\r\n")
    reader = csv.DictReader(io.StringIO(raw))
    return list(reader.fieldnames or []), list(reader)


def clean(source: Path, dest: Path) -> None:
    fieldnames, rows = read_rows(source)
    for col in (KEY_COL, INSTANCE_COL, SPEC_COL, MATRIX_COL, *ANALYTE_COLS):
        if col not in fieldnames:
            raise SystemExit(f"{source} is missing expected column {col!r}")
    print(f"Read {len(rows)} rows x {len(fieldnames)} cols from {source.name}")

    # --- targeted paren repairs -------------------------------------------
    applied = 0
    for (record_id, col), (expected, repaired) in PAREN_REPAIRS.items():
        matches = [r for r in rows if r[KEY_COL] == record_id and r[col].strip() == expected]
        if not matches:
            raise SystemExit(
                f"paren repair: Record ID {record_id} column {col!r} did not "
                f"contain the expected value {expected!r}"
            )
        for r in matches:
            r[col] = repaired
            applied += 1
    print(f"\nparenthesis repairs: {applied}")
    for (record_id, col), (_, repaired) in PAREN_REPAIRS.items():
        print(f"  ID {record_id} {col}: closing ')' added -> {repaired!r}")

    # --- targeted single-space delimiter repairs ---------------------------
    print(f"\nsingle-space delimiter repairs: {len(SPACE_FUSION_REPAIRS)}")
    for (record_id, col), (expected, repaired) in SPACE_FUSION_REPAIRS.items():
        matches = [r for r in rows if r[KEY_COL] == record_id and expected in r[col]]
        if not matches:
            raise SystemExit(
                f"space-fusion repair: Record ID {record_id} column {col!r} did "
                f"not contain {expected!r}"
            )
        for r in matches:
            r[col] = r[col].replace(expected, repaired)
        print(f"  ID {record_id} {col}: {expected!r} -> {repaired!r}")

    # --- sample matrix -----------------------------------------------------
    matrix_trimmed = 0
    for r in rows:
        value = re.sub(r"\s+", " ", (r[MATRIX_COL] or "").replace(" ", " ")).strip()
        if value != r[MATRIX_COL]:
            matrix_trimmed += 1
        r[MATRIX_COL] = value
    unexpected = {r[MATRIX_COL] for r in rows} - MATRIX_VALUES - {""}
    if unexpected:
        raise SystemExit(f"{MATRIX_COL}: unexpected value(s) {sorted(unexpected)}")
    counts = Counter(r[MATRIX_COL] or "(blank)" for r in rows)
    print(f"\n{MATRIX_COL!r}: {matrix_trimmed} cell(s) trimmed; {dict(counts)}")

    # --- specimen number whitespace ---------------------------------------
    spec_trimmed = 0
    for r in rows:
        stripped = re.sub(r"\s+", " ", r[SPEC_COL].replace(" ", " ")).strip()
        if stripped != r[SPEC_COL]:
            spec_trimmed += 1
        r[SPEC_COL] = stripped
    print(f"\n{SPEC_COL!r}: {spec_trimmed} cell(s) trimmed")

    # --- analyte columns ---------------------------------------------------
    for col in ANALYTE_COLS:
        changed = 0
        note_counts: Counter[str] = Counter()
        detail: list[str] = []
        for r in rows:
            before = r[col]
            after, notes = clean_analyte_cell(before)
            if after != before:
                changed += 1
            r[col] = after
            for note in notes:
                key = note.split(":")[0].split(" ->")[0]
                note_counts[key] += 1
                detail.append(f"  ID {r[KEY_COL]:>4} inst {r[INSTANCE_COL]}: {note}")
        print(f"\n{col!r}: {changed} cell(s) changed")
        for note, n in note_counts.most_common():
            print(f"  {n:>3}x  {note}")
        for line in detail:
            print(line)

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n  -> {dest}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root / "versioned/qtof_v1.csv")
    parser.add_argument("--dest", type=Path, default=root / "versioned/qtof_v2.csv")
    args = parser.parse_args()
    if not args.source.exists():
        raise SystemExit(f"Source file not found: {args.source}")
    clean(args.source, args.dest)


if __name__ == "__main__":
    main()
