"""Clean the specimen CSV, one column at a time.

Working pattern: ``versioned/specimen_v1.csv`` is the untouched baseline from
``split_data.py``. This script reads v1, applies a per-column cleaner for every
column we have agreed on, and writes ``versioned/specimen_v2.csv``.

The run is deterministic and always starts from v1, so v2 can be regenerated
from scratch at any time. As we work through the remaining messy columns we add
their cleaners to ``CLEANERS`` below and re-run; v2 is correct as far as the
registered cleaners go, and fully correct once every problem column is listed.

Every value change is reported (old -> new, with a count) so the transformation
can be audited before v2 is trusted.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import re
from collections import Counter
from pathlib import Path

AGE_COL = "Patient's age"
STAY_COL = "How long was the hospital stay (in days)?"
BAC_COL = "Patient's blood alcohol concentration (if known)"
DATE_COL = "Date completing this form"
KEY_COL = "Record ID"

# NOTE: ``Sample matrix`` is empty on every Specimen Form row -- the matrix is
# recorded against the QToF rows instead. It is deliberately left untouched
# here and handled on the qtof side.

UNKNOWN = "Unknown"

# BAC is standardized to g/dL with three decimals -- lossless, since every
# value in the source is a whole number of mg/dL.
BAC_DECIMALS = 3
# Highest censoring threshold the "report as 0" rule covers, in g/dL
# ("<10 mg/dL" == "<0.01 g/dL"). Anything censored above this is unhandled.
BAC_CENSOR_MAX_G_DL = 0.01

# "7 month", "18 mos", "3 months old"
_MONTHS_RE = re.compile(r"^(\d+)\s*(?:mo|mos|month|months)\b", re.IGNORECASE)
# "29", "29 yo", "29 y/o", "29 years old"
_YEARS_RE = re.compile(
    r"^(\d+)\s*(?:y|yo|y/o|yr|yrs|year|years)?\b(?:\s*old)?\s*$", re.IGNORECASE
)

# A date pasted into a duration field, e.g. "2022-08-09".
_DATE_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")
# Free text standing in for "no value recorded".
_NON_VALUES = {"n/a", "na", "unknown", "unk", "none", "-", "?"}
# "3", "0.4", "<1", "<1 day", ">2", ">1 day", "2 days"
_STAY_RE = re.compile(
    r"^([<>]?)\s*(\d+(?:\.\d+)?)\s*(?:d|day|days)?\s*$", re.IGNORECASE
)

# Lab-report labels wrapped around the BAC number. The number sits before the
# label in some cells and after it in others, so strip the label wherever it is.
_BAC_LABEL_RE = re.compile(r"ethanol|serum|,|:", re.IGNORECASE)
# Trailing unit text: "85 mg/dL", "92 mg/dl", "<5mg/dl".
_BAC_UNIT_RE = re.compile(r"\s*mg\s*/\s*d\s*l\s*$", re.IGNORECASE)
# What's left after stripping label and unit: "0.190", ".251", "<0.010", "337".
_BAC_NUM_RE = re.compile(r"^(<?)\s*(\d*\.?\d+)$")
# Tested, no alcohol found -- a result, not a missing value.
_BAC_NEGATIVE = {"negative", "neg", "none detected", "not detected"}


def _normalize_ws(raw: str) -> str:
    """Drop non-breaking spaces, trim, and collapse internal whitespace runs."""
    value = raw.replace(" ", " ").strip()
    return re.sub(r"\s+", " ", value)


def clean_age(raw: str) -> str:
    """Normalize age to a bare integer count of whole years.

    Ages given in months collapse to completed years, so anything under 12
    months becomes ``0``. Trailing unit text and surrounding whitespace are
    dropped. Returns "" for a blank cell and raises on anything unparseable so
    a new junk format can't slip through silently.
    """
    value = _normalize_ws(raw)
    if not value:
        return ""

    m = _MONTHS_RE.match(value)
    if m:
        return str(int(m.group(1)) // 12)

    m = _YEARS_RE.match(value)
    if m:
        return str(int(m.group(1)))

    raise ValueError(f"unparseable age: {raw!r}")


def clean_stay(raw: str) -> str:
    """Normalize length of stay to a bare integer number of days.

    Rules, applied in order:
      * blank stays blank; a mis-entered date or an "N/A"-style non-value
        becomes ``Unknown``.
      * unit text ("day"/"days") is dropped.
      * fractions round up: ``0.4`` -> ``1``.
      * ``<n`` is read as n; ``>n`` as the smallest integer above it, so
        ``>2`` -> ``3``.
      * anything landing at 0 or 1 becomes ``1`` -- a same-day stay counts as
        one day.

    Raises on an unrecognized format rather than guessing.
    """
    value = _normalize_ws(raw)
    if not value:
        return ""

    if _DATE_RE.match(value) or value.lower() in _NON_VALUES:
        return UNKNOWN

    m = _STAY_RE.match(value)
    if not m:
        raise ValueError(f"unparseable hospital stay: {raw!r}")

    op, number = m.group(1), float(m.group(2))
    if op == ">":
        # Smallest integer strictly greater than the stated bound.
        days = math.floor(number) + 1
    else:
        # Plain values and "<n" alike round up to a whole day.
        days = math.ceil(number)

    # A stay of 0-1 days is reported as one day.
    return str(max(days, 1))


def clean_bac(raw: str) -> str:
    """Standardize blood alcohol concentration to g/dL.

    The source mixes two scales 1000x apart, plus lab-report prose and text
    sentinels. Unit is inferred from magnitude, which the source supports: every
    g/dL value carries a decimal point, and a bare integer is always mg/dL
    (confirmed per-facility, and because e.g. "337 g/dL" is impossible).

    Rules:
      * blank stays blank; "N/A"/"Unknown" become ``Unknown``.
      * "Negative" becomes ``0`` -- tested, none detected.
      * a censored value ("<5", "<10", "<0.005", "<0.010") becomes ``0``.
      * a result > 1 is read as mg/dL and divided by 1000.
      * a result < 1 is already g/dL and kept as is.

    Raises on an unrecognized format, or on a censoring threshold above
    ``BAC_CENSOR_MAX_G_DL``, where "report as 0" would not be justified.
    """
    value = _normalize_ws(raw)
    if not value:
        return ""

    if value.lower().rstrip(".") in _NON_VALUES:
        return UNKNOWN
    if value.lower().rstrip(".") in _BAC_NEGATIVE:
        return _fmt_bac(0.0)

    # Peel off lab-report label text and any trailing mg/dL unit. Track whether
    # the unit was explicit -- it makes the scale certain rather than inferred.
    stripped = _normalize_ws(_BAC_LABEL_RE.sub(" ", value))
    had_mg_unit = bool(_BAC_UNIT_RE.search(stripped))
    stripped = _normalize_ws(_BAC_UNIT_RE.sub("", stripped))

    m = _BAC_NUM_RE.match(stripped)
    if not m:
        raise ValueError(f"unparseable BAC: {raw!r}")

    censored, number = m.group(1) == "<", float(m.group(2))

    # mg/dL if the unit said so, or if the magnitude can only be mg/dL.
    in_g_dl = number / 1000 if (had_mg_unit or number > 1) else number

    if censored:
        if in_g_dl > BAC_CENSOR_MAX_G_DL:
            raise ValueError(
                f"censored BAC above the handled threshold: {raw!r} "
                f"({in_g_dl:g} g/dL > {BAC_CENSOR_MAX_G_DL:g} g/dL)"
            )
        # Below the detection limit -- reported as zero.
        return _fmt_bac(0.0)

    return _fmt_bac(in_g_dl)


def _fmt_bac(g_dl: float) -> str:
    return f"{g_dl:.{BAC_DECIMALS}f}"


# Column -> cleaner. Add an entry per column as we agree on its rules.
CLEANERS = {
    AGE_COL: clean_age,
    STAY_COL: clean_stay,
    BAC_COL: clean_bac,
}

# Output header renames, applied after cleaning. Keys are the v1 header names,
# so CLEANERS keeps referring to the original names.
RENAMES = {
    BAC_COL: "Patient's blood alcohol concentration, g/dL (if known)",
    # A batch data-entry stamp, not a clinical event date: the 36 distinct dates
    # each cover a contiguous run of Record IDs. Named explicitly so it doesn't
    # get mistaken for an overdose or admission date. Values are untouched --
    # IDs 1-233 predate the field and are legitimately blank.
    DATE_COL: "Date form completed (data entry date)",
}

# --- Discharge status -------------------------------------------------------
# REDCap exports this one question as a checkbox column per category. 362 of
# 373 rows behave as a single-select; the exceptions were resolved row by row.

DISCHARGE_PREFIX = "What was the patient's discharge status? (choice="
CHECKED, UNCHECKED = "Checked", "Unchecked"

# No "Unknown" category exists in the export, so add one to hold the rows where
# the question was left blank entirely.
DISCHARGE_UNKNOWN = f"{DISCHARGE_PREFIX}Unknown)"

# Record ID -> categories to clear, for rows with two boxes checked where one
# was agreed to be wrong or a duplicate coding of the same event.
DISCHARGE_UNCHECK = {
    "414": ["Official discharge"],  # contradicts Left against medical advice
    "415": ["Official discharge"],  # contradicts Left against medical advice
    "435": ["Transferred to another facility"],  # same event as psych admission
    "436": ["Transferred to another facility"],  # same event as psych admission
}

# Record IDs with no box checked at all -- flagged Unknown rather than guessed.
DISCHARGE_UNKNOWN_IDS = ["1", "15", "29", "286"]


# Categories no patient was ever assigned, so the column carries no
# information. Dropped only if still empty -- see ``drop_columns``.
DROPS = [f"{DISCHARGE_PREFIX}Left without treatment)"]


def _discharge_cols(fieldnames: list[str]) -> list[str]:
    return [c for c in fieldnames if c.startswith(DISCHARGE_PREFIX)]


def drop_columns(
    fieldnames: list[str], rows: list[dict[str, str]]
) -> tuple[list[str], list[str]]:
    """Remove columns that hold no information.

    Refuses to drop a column that has any value other than ``Unchecked`` or
    blank, so if a future export starts populating one of these we fail instead
    of silently discarding real data.
    """
    report: list[str] = []
    fields = list(fieldnames)
    for col in DROPS:
        if col not in fields:
            raise SystemExit(f"drop: column not found: {col!r}")
        populated = {
            (row.get(col) or "").strip()
            for row in rows
        } - {"", UNCHECKED}
        if populated:
            raise SystemExit(
                f"drop: refusing to drop {col!r} -- it now holds {sorted(populated)}"
            )
        fields.remove(col)
        for row in rows:
            row.pop(col, None)
        report.append(f"  {col!r} (all {UNCHECKED}, 0 rows affected)")
    return fields, report


def fix_discharge(
    fieldnames: list[str], rows: list[dict[str, str]]
) -> tuple[list[str], list[str]]:
    """Resolve the agreed discharge-status anomalies in place.

    Adds an ``Unknown`` category column (kept adjacent to the other discharge
    columns) and applies the per-record fixes. Every edit asserts the cell is in
    the state we expect first, so if v1 is ever re-split differently this fails
    loudly instead of silently editing the wrong row.

    Returns the updated field list and a list of report lines.
    """
    cols = _discharge_cols(fieldnames)
    if not cols:
        raise SystemExit("no discharge status columns found")

    # Insert the new category directly after the existing discharge block.
    fields = list(fieldnames)
    fields.insert(fields.index(cols[-1]) + 1, DISCHARGE_UNKNOWN)
    for row in rows:
        row[DISCHARGE_UNKNOWN] = UNCHECKED

    by_id = {row[KEY_COL]: row for row in rows}
    report: list[str] = []

    for record_id, categories in DISCHARGE_UNCHECK.items():
        row = by_id.get(record_id)
        if row is None:
            raise SystemExit(f"discharge fix: Record ID {record_id} not found")
        for category in categories:
            col = f"{DISCHARGE_PREFIX}{category})"
            if col not in fields:
                raise SystemExit(f"discharge fix: no such category {category!r}")
            if row[col] != CHECKED:
                raise SystemExit(
                    f"discharge fix: Record ID {record_id} expected {category!r} "
                    f"to be {CHECKED!r}, found {row[col]!r}"
                )
            row[col] = UNCHECKED
            kept = [
                c[len(DISCHARGE_PREFIX) : -1] for c in cols if row[c] == CHECKED
            ]
            report.append(
                f"  ID {record_id:>4}  unchecked {category!r}; now {kept}"
            )

    for record_id in DISCHARGE_UNKNOWN_IDS:
        row = by_id.get(record_id)
        if row is None:
            raise SystemExit(f"discharge fix: Record ID {record_id} not found")
        checked = [c for c in cols if row[c] == CHECKED]
        if checked:
            raise SystemExit(
                f"discharge fix: Record ID {record_id} expected no box checked, "
                f"found {[c[len(DISCHARGE_PREFIX):-1] for c in checked]}"
            )
        row[DISCHARGE_UNKNOWN] = CHECKED
        report.append(f"  ID {record_id:>4}  no box checked -> Unknown")

    return fields, report


def read_rows(source: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read the CSV, tolerating the stray leading blank line v1 picked up."""
    raw = source.read_text(encoding="utf-8-sig").lstrip("\r\n")
    reader = csv.DictReader(io.StringIO(raw))
    return list(reader.fieldnames or []), list(reader)


def clean(source: Path, dest: Path) -> None:
    fieldnames, rows = read_rows(source)

    expected = list(CLEANERS) + list(RENAMES) + [KEY_COL]
    missing = [c for c in dict.fromkeys(expected) if c not in fieldnames]
    if missing:
        raise SystemExit(f"{source} is missing expected columns: {missing}")

    print(f"Read {len(rows)} rows from {source.name}")

    changes: dict[str, Counter] = {c: Counter() for c in CLEANERS}
    for i, row in enumerate(rows, start=2):  # +2: header is line 1
        for col, cleaner in CLEANERS.items():
            old = row[col] or ""
            try:
                new = cleaner(old)
            except ValueError as exc:
                raise SystemExit(f"{source} line {i}, column {col!r}: {exc}")
            if new != old:
                changes[col][(old, new)] += 1
            row[col] = new

    for col in CLEANERS:
        edits = changes[col]
        total = sum(edits.values())
        print(f"\n{col!r}: {total} cell(s) changed")
        for (old, new), n in sorted(edits.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>3}x  {old!r} -> {new!r}")

    fieldnames, discharge_report = fix_discharge(fieldnames, rows)
    print(f"\n'discharge status': added {DISCHARGE_UNKNOWN!r}")
    print(f"{len(discharge_report)} row fix(es):")
    for line in discharge_report:
        print(line)

    # Post-fix census, so any row still not behaving as a single-select is
    # visible in the run output rather than needing a separate check.
    cols = _discharge_cols(fieldnames)
    per_row = Counter(sum(1 for c in cols if r[c] == CHECKED) for r in rows)
    print(f"  boxes checked per row now: {dict(sorted(per_row.items()))}")
    for row in rows:
        checked = [c[len(DISCHARGE_PREFIX) : -1] for c in cols if row[c] == CHECKED]
        if len(checked) != 1:
            print(f"    ID {row[KEY_COL]:>4} still has {len(checked)}: {checked}")

    fieldnames, drop_report = drop_columns(fieldnames, rows)
    print(f"\ndropped {len(drop_report)} column(s):")
    for line in drop_report:
        print(line)

    out_fields = [RENAMES.get(c, c) for c in fieldnames]
    out_rows = [{RENAMES.get(c, c): row[c] for c in fieldnames} for row in rows]

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=out_fields)
        writer.writeheader()
        writer.writerows(out_rows)

    if RENAMES:
        print("\nrenamed column(s):")
        for old, new in RENAMES.items():
            print(f"  {old!r}\n    -> {new!r}")
    print(f"\n  -> {dest}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=root / "versioned/specimen_v1.csv"
    )
    parser.add_argument(
        "--dest", type=Path, default=root / "versioned/specimen_v2.csv"
    )
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"Source file not found: {args.source}")
    clean(args.source, args.dest)


if __name__ == "__main__":
    main()
