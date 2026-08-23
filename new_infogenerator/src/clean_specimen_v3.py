"""Reshape the specimen CSV onto the validation template's column set.

``versioned/specimen_v2.csv`` is the cleaned baseline: 373 rows x 28 columns,
still carrying REDCap's shape (checkbox columns, REDCap labels, long headers).
This script reads v2 and writes ``versioned/specimen_v3.csv`` -- the same 373
rows, re-expressed as the 11 columns the validation template
(``data/validation_set.xlsx``) defines for the specimen side.

v2 is never modified. v3 is rewritten from scratch on every run, so the script
is deterministic and idempotent.

The 11 template columns and where each comes from::

    record_id             <- Record ID                        (copy)
    spec_date             <- Date form completed              (ISO -> MM/DD/YYYY)
    location              <- To which facility...             (copy)
    age                   <- Patient's age                    (int)
    sex                   <- Patient's sex                    (2-entry map)
    race                  <- 6 checkbox columns               (collapse)
    ethnicity             <- 3 checkbox columns               (collapse)
    bac                   <- blood alcohol concentration      (float or None)
    od_manner             <- Manner of overdose?              (4-entry map)
    discharge_status      <- 10 checkbox columns              (collapse)
    hospital_stay_length  <- How long was the hospital stay   (int or None)

One sidecar column (prefix ``sc_``) is carried alongside. It is NOT part of the
template -- a downstream ``build_validate.py`` selects ``TEMPLATE_COLS`` and
ignores it::

    sc_specimen_number    the lab's own sample label; secondary join key

Everything the template's vocabulary destroys stays recoverable from v2, which
is immutable: the ICU / psychiatric / detox / death / AMA breakdown behind
``discharge_status``, the 9 blank ``Manner of overdose?`` cells now reported as
``Unknown``, and the below-detection-limit split behind ``bac = 0.000``. Re-join
on ``record_id`` when any of those are needed rather than re-deriving them.

Every column reports its output distribution and every value is checked against
the template's allowed set, so the run fails rather than writing a value the
template would reject.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import re
from collections import Counter
from pathlib import Path

KEY_COL = "Record ID"

# --- source column names, exactly as they appear in v2 ----------------------
# NOTE: the sex header carries a trailing space, as exported by REDCap.
SRC_DATE = "Date form completed (data entry date)"
SRC_LOCATION = "To which facility did the patient present?"
SRC_AGE = "Patient's age"
SRC_SEX = "Patient's sex "
SRC_BAC = "Patient's blood alcohol concentration, g/dL (if known)"
SRC_MANNER = "Manner of overdose?"
SRC_STAY = "How long was the hospital stay (in days)?"
SRC_SPECIMEN = "Specimen number"

RACE_PREFIX = "Patient's race (choice="
ETH_PREFIX = "Patient's Ethnicity (choice="
DISCHARGE_PREFIX = "What was the patient's discharge status? (choice="

TEMPLATE_COLS = [
    "record_id",
    "spec_date",
    "location",
    "age",
    "sex",
    "race",
    "ethnicity",
    "bac",
    "od_manner",
    "discharge_status",
    "hospital_stay_length",
]
SIDECAR_COLS = ["sc_specimen_number"]
OUT_COLS = TEMPLATE_COLS + SIDECAR_COLS

# --- template vocabularies --------------------------------------------------
# Copied from the "details" sheet of data/validation_set.xlsx. Output is checked
# against these, so a source value we have not seen before fails the run.
ALLOWED = {
    "location": {
        "Bellin Health - Green Bay",
        "UW-Health - Madison",
        "Medical College of Wisconsin - Milwaukee (Froedtert)",
    },
    "sex": {"M", "F"},
    "race": {
        "American Indian or Alaskan Native",
        "Asian",
        "Black or African American",
        "Native Hawaiian or Pacific Islander",
        "White",
        "Two or more races",
        "Unknown",
    },
    "ethnicity": {"Hispanic", "Not Hispanic", "Unknown"},
    "od_manner": {"Intentional", "Unintentional", "Assault", "Unknown"},
    "discharge_status": {
        "Discharged",
        "Admitted",
        "Transferred",
        "Other",
        "Unknown",
    },
}

MAX_AGE = 199  # template cap

SEX_MAP = {
    "Male (transgender male)": "M",
    "Female (transgender female)": "F",
}

# The REDCap labels carry a second clause the template drops. The empty string
# is deliberate: 9 records have no answer and the template has no blank value,
# so they become "Unknown", merging with the 87 clinician-recorded "Unknown"s.
# v2 is where the two are still distinguishable.
MANNER_MAP = {
    "Unintentional/Accidental": "Unintentional",
    "Intentional/Suicide": "Intentional",
    "Assault": "Assault",
    "Unknown": "Unknown",
    "": "Unknown",
}

# Agreed 10 -> 5 assignment. Two calls worth recording:
#   * "Death" is the only source of "Other", so Other is an exact proxy for the
#     death count (8). It must not be published as a bare category.
#   * "Left against medical advice" goes to Transferred, not Discharged or
#     Other, so Transferred mixes planned handoffs (12) with self-discharge (6).
DISCHARGE_MAP = {
    "Admitted to the hospital": "Admitted",
    "Admitted to the ICU": "Admitted",
    "Admitted to psychiatric facility": "Admitted",
    "Admitted to detox or substance abuse treatment program": "Admitted",
    "Official discharge": "Discharged",
    "Discharged to law enforcement": "Discharged",
    "Transferred to another facility": "Transferred",
    "Left against medical advice": "Transferred",
    "Death": "Other",
    "Unknown": "Unknown",
}

# Applied only when one patient's checkboxes land in two different buckets.
# Fires once on this export (Record 428: official discharge + detox -> Admitted).
DISCHARGE_PRECEDENCE = ["Other", "Admitted", "Transferred", "Discharged", "Unknown"]

RACE_RELABEL = {"White/Caucasian": "White"}
MULTI_RACE = "Two or more races"

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FLOAT_RE = re.compile(r"^\d+(?:\.\d+)?$")
_BAC_NULL = {"", "unknown"}
_STAY_NULL = {"", "unknown"}


class RowError(Exception):
    """Raised with the offending Record ID so a bad row can be found fast."""


def _choice_cols(fieldnames: list[str], prefix: str) -> list[str]:
    return [c for c in fieldnames if c.startswith(prefix)]


def _label(col: str) -> str:
    """'...(choice=Asian)' -> 'Asian'."""
    match = re.search(r"choice=(.*)\)$", col)
    if match is None:
        raise ValueError(f"not a checkbox column: {col!r}")
    return match.group(1)


def _checked(row: dict[str, str], cols: list[str]) -> list[str]:
    """Labels of the ticked boxes, in source column order."""
    for col in cols:
        if row[col] not in ("Checked", "Unchecked"):
            raise RowError(f"unexpected checkbox value {row[col]!r} in {col!r}")
    return [_label(c) for c in cols if row[c] == "Checked"]


# --- per-column builders ----------------------------------------------------


def build_spec_date(raw: str) -> str | None:
    """ISO 'YYYY-MM-DD' -> 'MM/DD/YYYY'. Blank -> None (219 of 373).

    The source is a data-entry stamp, not a collection date, and is absent for
    every Record ID below 234. See versioned/README.md before using it as a
    time axis.
    """
    value = raw.strip()
    if not value:
        return None
    if not _ISO_RE.match(value):
        raise RowError(f"unrecognized date format {raw!r}")
    return datetime.date.fromisoformat(value).strftime("%m/%d/%Y")


def build_age(raw: str) -> int:
    value = raw.strip()
    if not value.isdigit():
        raise RowError(f"non-integer age {raw!r}")
    age = int(value)
    if age > MAX_AGE:
        raise RowError(f"age {age} exceeds the template maximum of {MAX_AGE}")
    return age


def build_sex(raw: str) -> str:
    """Two source labels, two template values, no blanks -- the map is total.

    The REDCap labels merge cisgender and transgender patients into one option
    each, so 'M' must never be reported as 'cisgender male'.
    """
    try:
        return SEX_MAP[raw]
    except KeyError:
        raise RowError(f"unmapped sex {raw!r}") from None


def build_race(labels: list[str]) -> str:
    """Collapse 6 checkboxes to 1 value.

    Two rules beyond "which box is ticked": 'White/Caucasian' is relabelled to
    the template's 'White', and more than one tick becomes 'Two or more races'.
    The second rule empties a category -- the cohort's only Native Hawaiian or
    Pacific Islander patient is also White, so that value comes out at 0.
    """
    if not labels:
        raise RowError("no race checkbox ticked; the template has no blank value")
    if len(labels) > 1:
        return MULTI_RACE
    return RACE_RELABEL.get(labels[0], labels[0])


def build_ethnicity(labels: list[str]) -> str:
    """Collapse 3 checkboxes to 1 value. Labels already match the template."""
    if len(labels) != 1:
        raise RowError(f"expected exactly one ethnicity tick, got {labels}")
    return labels[0]


def build_bac(raw: str) -> str | None:
    """Pass the numeric string through unchanged; blank/'Unknown' -> None.

    v2 is already g/dL, so the template's "divide by 1000 if > 1" and
    "'<' means 0.0" rules are both no-ops here and are deliberately NOT
    re-applied -- they were written against the raw export.

    0.0 means "tested, at or below the detection limit" (94 records). None
    means "not tested or not recorded" (224). Anything downstream that fills
    nulls with 0 silently merges the two.
    """
    value = raw.strip()
    if value.lower() in _BAC_NULL:
        return None
    if not _FLOAT_RE.match(value):
        raise RowError(f"unparseable BAC {raw!r}")
    return value


def build_od_manner(raw: str) -> str:
    try:
        return MANNER_MAP[raw]
    except KeyError:
        raise RowError(f"unmapped manner of overdose {raw!r}") from None


def build_discharge(labels: list[str]) -> tuple[str, bool]:
    """Collapse 10 checkboxes to 1 value. Returns (value, precedence_fired)."""
    if not labels:
        raise RowError("no discharge checkbox ticked; the template has no blank")
    try:
        buckets = sorted({DISCHARGE_MAP[l] for l in labels}, key=DISCHARGE_PRECEDENCE.index)
    except KeyError as exc:
        raise RowError(f"unmapped discharge status {exc.args[0]!r}") from None
    return buckets[0], len(buckets) > 1


def build_stay(raw: str) -> int | None:
    """int, floored at 1; blank/'Unknown' -> None.

    The floor is the template's rule, already applied in v2: 49 of the 120
    rows that emit 1 were entered as 0, '<1' or '0.4' -- patients never
    admitted overnight. 1 therefore means "a day or less", not "one night".
    """
    value = raw.strip()
    if value.lower() in _STAY_NULL:
        return None
    if not value.isdigit():
        raise RowError(f"non-integer hospital stay {raw!r}")
    return max(1, int(value))


# --- driver -----------------------------------------------------------------


def read_rows(source: Path) -> tuple[list[str], list[dict[str, str]]]:
    with source.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def transform(source: Path, dest: Path) -> None:
    fieldnames, rows = read_rows(source)
    race_cols = _choice_cols(fieldnames, RACE_PREFIX)
    eth_cols = _choice_cols(fieldnames, ETH_PREFIX)
    dis_cols = _choice_cols(fieldnames, DISCHARGE_PREFIX)

    expected = {
        "race": (race_cols, 6),
        "ethnicity": (eth_cols, 3),
        "discharge": (dis_cols, 10),
    }
    for name, (cols, count) in expected.items():
        if len(cols) != count:
            raise SystemExit(
                f"expected {count} {name} checkbox columns in {source.name}, "
                f"found {len(cols)}"
            )

    required = [
        SRC_DATE, SRC_LOCATION, SRC_AGE, SRC_SEX, SRC_BAC, SRC_MANNER,
        SRC_STAY, SRC_SPECIMEN, KEY_COL,
    ]
    missing = [c for c in required if c not in fieldnames]
    if missing:
        raise SystemExit(f"{source.name} is missing expected column(s): {missing}")

    out_rows: list[dict[str, object]] = []
    precedence_hits: list[tuple[str, list[str], str]] = []

    for line, row in enumerate(rows, start=2):
        record_id = row[KEY_COL]
        try:
            discharge_labels = _checked(row, dis_cols)
            discharge, contested = build_discharge(discharge_labels)
            location = row[SRC_LOCATION]
            if location not in ALLOWED["location"]:
                raise RowError(f"unmapped facility {location!r}")

            out_rows.append(
                {
                    "record_id": record_id,
                    "spec_date": build_spec_date(row[SRC_DATE]),
                    "location": location,
                    "age": build_age(row[SRC_AGE]),
                    "sex": build_sex(row[SRC_SEX]),
                    "race": build_race(_checked(row, race_cols)),
                    "ethnicity": build_ethnicity(_checked(row, eth_cols)),
                    "bac": build_bac(row[SRC_BAC]),
                    "od_manner": build_od_manner(row[SRC_MANNER]),
                    "discharge_status": discharge,
                    "hospital_stay_length": build_stay(row[SRC_STAY]),
                    "sc_specimen_number": row[SRC_SPECIMEN],
                }
            )
        except RowError as exc:
            raise SystemExit(
                f"{source.name} line {line} (Record ID {record_id}): {exc}"
            ) from None

        if contested:
            precedence_hits.append((record_id, discharge_labels, discharge))

    # Output must satisfy the template's vocabularies.
    for column, allowed in ALLOWED.items():
        seen = {r[column] for r in out_rows}
        if not seen <= allowed:
            raise SystemExit(
                f"{column}: value(s) outside the template's allowed set: "
                f"{sorted(seen - allowed)}"
            )

    ids = [r["record_id"] for r in out_rows]
    if len(set(ids)) != len(ids):
        dupes = [i for i, n in Counter(ids).items() if n > 1]
        raise SystemExit(f"duplicate record_id(s): {dupes}")

    report(rows, out_rows, precedence_hits)

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUT_COLS)
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"\n  -> {dest}")


def report(
    src_rows: list[dict[str, str]],
    out_rows: list[dict[str, object]],
    precedence_hits: list[tuple[str, list[str], str]],
) -> None:
    print(f"read  {len(src_rows)} rows")
    print(f"wrote {len(out_rows)} rows x {len(OUT_COLS)} columns "
          f"({len(TEMPLATE_COLS)} template + {len(SIDECAR_COLS)} sidecar)")

    for column in TEMPLATE_COLS:
        values = [r[column] for r in out_rows]
        nulls = sum(1 for v in values if v is None)
        print(f"\n{column}  (null: {nulls})")
        if column in ALLOWED:
            for value, count in Counter(values).most_common():
                print(f"    {count:4d}  {value}")
            for value in sorted(ALLOWED[column] - set(values)):
                print(f"    {0:4d}  {value}   <- allowed but unused")
        else:
            filled = [v for v in values if v is not None]
            print(f"    {len(filled)} populated, {len(set(filled))} distinct")

    print("\nsidecars")
    print(f"  sc_specimen_number  -- carried through for "
          f"{sum(1 for r in out_rows if r['sc_specimen_number'])} record(s)")

    if precedence_hits:
        print(f"\ndischarge precedence applied to {len(precedence_hits)} record(s):")
        for record_id, labels, chosen in precedence_hits:
            print(f"    Record {record_id}: {labels} -> {chosen}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=root / "versioned/specimen_v2.csv"
    )
    parser.add_argument(
        "--dest", type=Path, default=root / "versioned/specimen_v3.csv"
    )
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"Source file not found: {args.source}")
    transform(args.source, args.dest)


if __name__ == "__main__":
    main()
