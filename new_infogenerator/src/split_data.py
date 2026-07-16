"""Split the NonFatalOverdose REDCap export into two CSVs.

The REDCap export is a "long" file: each specimen has one ``Specimen Form``
row (demographics / clinical fields) plus one or more ``QToF Screen`` rows
(the lab drug-screen results). This script separates those two concerns so
each side can be filtered independently and later re-merged.

Outputs (both keyed on ``Record ID``):
  * specimen.csv - one row per specimen: demographics + clinical fields.
  * qtof.csv     - one row per QToF screen: the raw free-text detections.

The specimen number is carried onto both files as a human-readable label
(backfilled onto the qtof rows, which don't carry it in the source export).

Source is the LABELS export, so values are already human-readable
(e.g. "Male", "UW-Health - Madison", "Checked"). No decode step is needed.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

# REDCap tags the two row types in this column.
INSTRUMENT_COL = "Repeat Instrument"
SPECIMEN_INSTRUMENT = "Specimen Form"
QTOF_INSTRUMENT = "QToF Screen"

KEY_COL = "Record ID"
SPEC_NUM_COL = "Specimen number"

# Columns that belong to each output, selected by header NAME (not index) so
# the split is robust to column reordering in future exports.
SPECIMEN_COLS = [
    "Record ID",
    "Specimen number",
    "Date completing this form",
    "To which facility did the patient present?",
    "Patient's age",
    "Patient's sex ",
    "Patient's race (choice=American Indian or Alaskan Native)",
    "Patient's race (choice=Asian)",
    "Patient's race (choice=Black or African American)",
    "Patient's race (choice=Native Hawaiian or Pacific Islander)",
    "Patient's race (choice=White/Caucasian)",
    "Patient's race (choice=Unknown)",
    "Patient's Ethnicity (choice=Hispanic)",
    "Patient's Ethnicity (choice=Not Hispanic)",
    "Patient's Ethnicity (choice=Unknown)",
    "Patient's blood alcohol concentration (if known)",
    "Manner of overdose?",
    "Sample matrix",
    "What was the patient's discharge status? (choice=Official discharge)",
    "What was the patient's discharge status? (choice=Death)",
    "What was the patient's discharge status? (choice=Left without treatment)",
    "What was the patient's discharge status? (choice=Left against medical advice)",
    "What was the patient's discharge status? (choice=Admitted to the hospital)",
    "What was the patient's discharge status? (choice=Admitted to the ICU)",
    "What was the patient's discharge status? (choice=Admitted to psychiatric facility)",
    "What was the patient's discharge status? (choice=Transferred to another facility)",
    "What was the patient's discharge status? (choice=Admitted to detox or substance abuse treatment program)",
    "What was the patient's discharge status? (choice=Discharged to law enforcement)",
    "How long was the hospital stay (in days)?",
]

QTOF_COLS = [
    "Record ID",
    "Repeat Instance",
    "Specimen number",  # backfilled from the specimen row
    "Positive Ion Mode",
    "Negative Ion Mode",
]

DEFAULT_SOURCE = (
    "data/NonFatalOverdoseBioS-OnePagerData_DATA_LABELS_2026-07-12_1336.csv"
)


def _require_columns(fieldnames: list[str], needed: list[str], src: Path) -> None:
    """Fail loudly if the export is missing a column we select by name."""
    missing = [c for c in needed if c not in fieldnames]
    if missing:
        raise SystemExit(
            f"Source {src} is missing expected columns: {missing}\n"
            f"Available columns: {fieldnames}"
        )


def split(source: Path, out_dir: Path) -> tuple[Path, Path]:
    with source.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        _require_columns(fieldnames, [INSTRUMENT_COL, KEY_COL], source)
        _require_columns(
            fieldnames,
            [c for c in SPECIMEN_COLS if c != SPEC_NUM_COL] + ["Positive Ion Mode"],
            source,
        )
        rows = list(reader)

    specimen_rows = [r for r in rows if r[INSTRUMENT_COL] == SPECIMEN_INSTRUMENT]
    qtof_rows = [r for r in rows if r[INSTRUMENT_COL] == QTOF_INSTRUMENT]

    # Backfill: qtof rows carry Record ID but no specimen number in the source.
    spec_num_by_id = {
        r[KEY_COL]: r[SPEC_NUM_COL] for r in specimen_rows if r[KEY_COL]
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    specimen_path = out_dir / "specimen.csv"
    qtof_path = out_dir / "qtof.csv"

    with specimen_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SPECIMEN_COLS, extrasaction="ignore")
        writer.writeheader()
        for r in specimen_rows:
            writer.writerow({c: r.get(c, "") for c in SPECIMEN_COLS})

    with qtof_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=QTOF_COLS)
        writer.writeheader()
        for r in qtof_rows:
            out = {c: r.get(c, "") for c in QTOF_COLS}
            if not out.get(SPEC_NUM_COL):
                out[SPEC_NUM_COL] = spec_num_by_id.get(r[KEY_COL], "")
            writer.writerow(out)

    print(f"Read {len(rows)} rows from {source.name}")
    print(f"  -> {specimen_path}  ({len(specimen_rows)} rows)")
    print(f"  -> {qtof_path}  ({len(qtof_rows)} rows)")

    orphans = sorted({r[KEY_COL] for r in qtof_rows} - set(spec_num_by_id))
    if orphans:
        print(f"WARNING: {len(orphans)} qtof Record IDs have no specimen row: {orphans}")

    return specimen_path, qtof_path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=root / DEFAULT_SOURCE,
        help="Path to the NonFatalOverdose LABELS CSV export.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=root / "output",
        help="Directory to write specimen.csv and qtof.csv into.",
    )
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"Source file not found: {args.source}")
    split(args.source, args.out_dir)


if __name__ == "__main__":
    main()
