"""Join the specimen and QToF sides into the validation template's shape.

Reads ``versioned/specimen_v3.csv`` (373 x 12, one row per patient) and
``versioned/qtof_v3.csv`` (2709 x 9, one row per patient x analyte), joins them
on the record id, and writes ``versioned/validate_v1.csv`` -- the 17 columns
``data/validation_set.xlsx`` defines, in its order.

Both inputs are left untouched and the output is rewritten from scratch on every
run, so the script is deterministic and idempotent.

By this point the work is mechanical; the shaping happened upstream. What is
left::

    Record ID       -> record_id        (rename, then join)
    Sample matrix   -> wslh_matrix      (rename + Title Case the values)
    drop            sc_specimen_number, Specimen number, unmatched_analytes
    reorder         to the template's column order

The join is one-to-many: 373 patients fan out to 2709 analyte rows, so every
demographic value repeats down its patient's rows. **Any patient-level statistic
must use ``nunique(record_id)``, never ``len(df)``** -- counting rows weights
each patient by how many substances they screened positive for, which correlates
with severity, so the bias is not random.

Every value is checked against the template's allowed set. Two vocabularies are
reported rather than raising:

  * ``TEMPLATE_ADDITIONS_REQUESTED`` -- categories the pipeline emits by decision
    that the template has yet to list. Correct data, pending a template edit.
  * the blank analyte fields on the analyte-less rows, which the template has no
    value for. See ANALYTE_LESS_RECORDS.

Anything else outside an allowed set stops the run.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

from vocab import (
    ANALYTE_LESS_RECORDS,
    GROUP_VALUES,
    MATRIX_TITLES,
    SPECIMEN_ALLOWED,
    TEMPLATE_ADDITIONS_REQUESTED,
    TRUE_NEGATIVE_RECORDS,
    UNRECORDED_SCREEN_RECORDS,
    read_rows,
)

SPECIMEN_KEY = "record_id"
QTOF_KEY = "Record ID"

# The template's 17 columns, in the order data/validation_set.xlsx lists them.
TEMPLATE_COLS = [
    "record_id",
    "wslh_matrix",
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
    "analyte_name",
    "analyte_group_1",
    "analyte_group_2",
    "analyte_group_3",
    "metabolite_flag",
]

# Which side each column comes from. wslh_matrix is handled separately because
# it is the only one that needs a rename *and* a value transform.
FROM_SPECIMEN = [
    "spec_date", "location", "age", "sex", "race", "ethnicity", "bac",
    "od_manner", "discharge_status", "hospital_stay_length",
]
FROM_QTOF = [
    "analyte_name", "analyte_group_1", "analyte_group_2", "analyte_group_3",
    "metabolite_flag",
]

MATRIX_SRC = "Sample matrix"
MATRIX_DEST = "wslh_matrix"

# Working columns that exist to make the pipeline auditable, not to be published.
DROPPED = ["sc_specimen_number", "Specimen number", "unmatched_analytes"]

# --- template vocabularies ---------------------------------------------------
# Shared with clean_specimen_v3 and clean_qtof_v3 so the three cannot drift; see
# src/vocab.py. The analyte_group_* columns share one vocabulary.
ALLOWED: dict[str, set[str]] = {
    **SPECIMEN_ALLOWED,
    "wslh_matrix": set(MATRIX_TITLES.values()),
    "analyte_group_1": GROUP_VALUES,
    "analyte_group_2": GROUP_VALUES,
    "analyte_group_3": GROUP_VALUES,
    "metabolite_flag": {"True", "False"},
}

# Columns the template allows to be empty. spec_date, bac and
# hospital_stay_length are genuinely optional; the analyte fields are empty only
# on the analyte-less rows below.
NULLABLE = {
    "spec_date", "bac", "hospital_stay_length",
    "analyte_group_2", "analyte_group_3",
}
ANALYTE_FIELDS = ["analyte_name", "analyte_group_1", "metabolite_flag"]

# The analyte-less records, and the ruling on what each one means, live in
# src/vocab.py -- onepager_stats needs the same distinction, and it was
# previously stated here only.

# Keep the analyte-less rows so those patients stay in the demographic
# denominators. Their analyte fields are blank, which the template has no value
# for, so a strict validator will flag them -- that is the honest trade. Set
# False to drop them instead, losing 6 patients from every denominator.
KEEP_ANALYTE_LESS_ROWS = True


def build(specimen_path: Path, qtof_path: Path, dest: Path) -> None:
    spec_fields, spec_rows = read_rows(specimen_path)
    qtof_fields, qtof_rows = read_rows(qtof_path)

    for label, fields, needed in (
        (specimen_path.name, spec_fields, [SPECIMEN_KEY, *FROM_SPECIMEN]),
        (qtof_path.name, qtof_fields, [QTOF_KEY, MATRIX_SRC, *FROM_QTOF]),
    ):
        missing = [c for c in needed if c not in fields]
        if missing:
            raise SystemExit(f"{label} is missing expected column(s): {missing}")

    # unmatched_analytes is dropped here, so its emptiness must be asserted
    # first -- otherwise a future export's unresolved tokens would vanish.
    if "unmatched_analytes" in qtof_fields:
        stranded = [r for r in qtof_rows if r["unmatched_analytes"].strip()]
        if stranded:
            raise SystemExit(
                f"{qtof_path.name}: {len(stranded)} row(s) still have "
                f"unmatched_analytes; resolve them in the mapping before "
                f"building the template output. First few: "
                f"{[r['unmatched_analytes'] for r in stranded[:5]]}"
            )

    specimens = {r[SPECIMEN_KEY]: r for r in spec_rows}
    if len(specimens) != len(spec_rows):
        dupes = [k for k, n in Counter(r[SPECIMEN_KEY] for r in spec_rows).items() if n > 1]
        raise SystemExit(f"duplicate {SPECIMEN_KEY} in {specimen_path.name}: {dupes}")

    qtof_ids = {r[QTOF_KEY] for r in qtof_rows}
    spec_only = sorted(set(specimens) - qtof_ids, key=int)
    qtof_only = sorted(qtof_ids - set(specimens), key=int)
    if spec_only or qtof_only:
        raise SystemExit(
            f"record id mismatch — specimen-only: {spec_only}, qtof-only: {qtof_only}"
        )

    out_rows: list[dict[str, str]] = []
    dropped_rows: list[str] = []
    for row in qtof_rows:
        record_id = row[QTOF_KEY]
        analyte_less = not row["analyte_name"].strip()
        if analyte_less and not KEEP_ANALYTE_LESS_ROWS:
            dropped_rows.append(record_id)
            continue

        specimen = specimens[record_id]
        out = {"record_id": record_id}

        matrix = row[MATRIX_SRC].strip()
        if matrix and matrix not in MATRIX_TITLES:
            raise SystemExit(
                f"Record {record_id}: unexpected {MATRIX_SRC} {matrix!r}; "
                f"expected one of {sorted(MATRIX_TITLES)}"
            )
        out[MATRIX_DEST] = MATRIX_TITLES.get(matrix, "")

        for column in FROM_SPECIMEN:
            out[column] = specimen[column]
        for column in FROM_QTOF:
            out[column] = row[column]
        out_rows.append(out)

    validate(out_rows)
    report(spec_rows, qtof_rows, out_rows, dropped_rows)

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=TEMPLATE_COLS)
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"\n  -> {dest}")


def validate(rows: list[dict[str, str]]) -> None:
    """Raise on anything the template forbids that we have not agreed to."""
    for column, allowed in ALLOWED.items():
        values = {r[column] for r in rows if r[column].strip()}
        offenders = values - allowed - TEMPLATE_ADDITIONS_REQUESTED
        if offenders:
            affected = sum(1 for r in rows if r[column] in offenders)
            raise SystemExit(
                f"{column}: {len(offenders)} value(s) the template forbids, on "
                f"{affected} row(s): {sorted(offenders)}"
            )

    for column in TEMPLATE_COLS:
        if column in NULLABLE or column in ANALYTE_FIELDS:
            continue
        blanks = sum(1 for r in rows if not r[column].strip())
        if blanks:
            raise SystemExit(f"{column}: {blanks} blank value(s), none permitted")

    # The analyte fields are blank together or not at all.
    for row in rows:
        blank = [c for c in ANALYTE_FIELDS if not row[c].strip()]
        if blank and len(blank) != len(ANALYTE_FIELDS):
            raise SystemExit(
                f"Record {row['record_id']}: analyte fields partially blank "
                f"({blank}); they must be blank together or not at all"
            )
        if blank and row["record_id"] not in ANALYTE_LESS_RECORDS:
            raise SystemExit(
                f"Record {row['record_id']} has no analyte but is not a known "
                f"analyte-less record. Check the QToF side before publishing."
            )


def report(
    spec_rows: list[dict[str, str]],
    qtof_rows: list[dict[str, str]],
    out_rows: list[dict[str, str]],
    dropped_rows: list[str],
) -> None:
    patients = {r["record_id"] for r in out_rows}
    print(f"read  {len(spec_rows)} patient row(s) + {len(qtof_rows)} analyte row(s)")
    print(f"wrote {len(out_rows)} rows x {len(TEMPLATE_COLS)} columns "
          f"over {len(patients)} patient(s)")
    print(f"dropped column(s): {DROPPED}")
    if dropped_rows:
        print(f"dropped {len(dropped_rows)} analyte-less row(s): "
              f"{sorted(set(dropped_rows), key=int)}")

    per = Counter(r["record_id"] for r in out_rows)
    counts = sorted(per.values())
    print(f"\nrows per patient: min {counts[0]}, median "
          f"{counts[len(counts) // 2]}, max {counts[-1]}")
    print("  patient-level statistics must use nunique(record_id), not len(df)")

    print(f"\n{'column':22s} {'blank':>6}  distinct / values")
    print("-" * 92)
    for column in TEMPLATE_COLS:
        values = [r[column] for r in out_rows]
        blanks = sum(1 for v in values if not v.strip())
        filled = {v for v in values if v.strip()}
        if column in ALLOWED and len(filled) <= 8:
            counts_by = Counter(v for v in values if v.strip())
            detail = ", ".join(f"{v} {n}" for v, n in counts_by.most_common())
        else:
            detail = f"{len(filled)} distinct"
        print(f"{column:22s} {blanks:6d}  {detail}")

    pending: dict[str, int] = defaultdict(int)
    for row in out_rows:
        for column in ("analyte_group_1", "analyte_group_2", "analyte_group_3"):
            if row[column] in TEMPLATE_ADDITIONS_REQUESTED:
                pending[row[column]] += 1
    if pending:
        total = sum(1 for r in out_rows if any(
            r[c] in TEMPLATE_ADDITIONS_REQUESTED
            for c in ("analyte_group_1", "analyte_group_2", "analyte_group_3")))
        print(f"\nPENDING TEMPLATE ADDITIONS — {total} row(s) use a category the "
              f"template does not list yet:")
        for value, count in sorted(pending.items(), key=lambda kv: -kv[1]):
            print(f"  {count:5d}  {value}")
        print("  Correct data by decision; the template needs these values.")

    blank_rows = [r for r in out_rows if not r["analyte_name"].strip()]
    if blank_rows:
        ids = {r["record_id"] for r in blank_rows}
        print(f"\nANALYTE-LESS ROWS — {len(blank_rows)}, kept so those patients stay "
              f"in the demographic denominators:")
        print(f"  true negative (screen ran, found nothing): "
              f"{sorted(ids & TRUE_NEGATIVE_RECORDS, key=int)}")
        print(f"  screen result never entered: "
              f"{sorted(ids & UNRECORDED_SCREEN_RECORDS, key=int)}")
        print("  The second group is MISSING, not negative — exclude from "
              "analyte-based denominators.")

    suffix = (" (or an agreed pending addition)"
              if TEMPLATE_ADDITIONS_REQUESTED else "")
    print(f"\nvalidation: every value is in the template's allowed set{suffix}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--specimen", type=Path, default=root / "versioned/specimen_v3.csv"
    )
    parser.add_argument("--qtof", type=Path, default=root / "versioned/qtof_v3.csv")
    parser.add_argument(
        "--dest", type=Path, default=root / "versioned/validate_v1.csv"
    )
    args = parser.parse_args()

    for path in (args.specimen, args.qtof):
        if not path.exists():
            raise SystemExit(f"File not found: {path}")
    build(args.specimen, args.qtof, args.dest)


if __name__ == "__main__":
    main()
