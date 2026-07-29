# Versioned data snapshots

Every time we change `specimen.csv` or `qtof.csv` (filtering, cleaning,
re-splitting a new export), the result is saved here as a new numbered
version so we can compare and roll back.

## Naming convention

Flat directory, version suffix in the filename:

```
versioned/
  specimen_v1.csv
  qtof_v1.csv
  specimen_v2.csv
  ...
```

- `v1` = the initial split produced by `src/split_data.py` from the
  NonFatalOverdose LABELS export (unfiltered).
- Bump the version number for each subsequent change. Keep `specimen_vN`
  and `qtof_vN` on the same `N` where both sides change together, so a
  version pairs cleanly for re-merging on `Record ID`.

## Pipeline

```
data/NonFatalOverdoseBioS-OnePagerData_DATA_LABELS_*.csv   (REDCap LABELS export)
  |
  |  src/split_data.py        splits the long export by Repeat Instrument
  v
versioned/specimen_v1.csv     373 rows x 29 cols  (one row per specimen)
versioned/qtof_v1.csv         396 rows x  5 cols  (one row per QToF screen)
  |
  |  src/clean_specimen.py    per-column cleaning, specimen side only
  v
versioned/specimen_v2.csv     373 rows x 29 cols
```

`v1` is the untouched baseline and is never edited in place. `clean_specimen.py`
always reads `specimen_v1.csv` and rewrites `specimen_v2.csv` from scratch, so
the run is deterministic and idempotent — re-running can never accumulate
half-applied edits. Regenerate with:

```
python3 src/clean_specimen.py
```

Every run prints the full old -> new change list for each column so the
transformation can be audited before v2 is trusted.

## What v2 contains

Cleaning rules were agreed column by column. Unparseable values raise with a
line number rather than silently defaulting, so a new junk format in a future
export fails loudly.

| Column | Change |
| --- | --- |
| `Patient's age` | Bare integers only (0-94). Unit text dropped (`29 yo` -> `29`); ages in months collapse to completed years, so under 12 months -> `0`. 3 cells changed. |
| `How long was the hospital stay (in days)?` | Bare integers, or `Unknown`, or blank. Unit text dropped; fractions round up; `<n` read as n; `>n` -> smallest integer above it; anything landing at 0-1 -> `1`. A mis-entered date and an `N/A` became `Unknown`. 62 cells changed. |
| `Patient's blood alcohol concentration (if known)` | Standardized to g/dL at 3 decimals and **renamed** to `Patient's blood alcohol concentration, g/dL (if known)`. Source mixed mg/dL and g/dL 1000x apart: results > 1 are mg/dL and divided by 1000, results < 1 are already g/dL. Censored values (`<5`, `<10`, `<0.010`) and `Negative` -> `0.000`; `N/A`/`Unknown` -> `Unknown`. Lab-report prose (`ETHANOL, SERUM: <0.010`) stripped. 139 cells changed. |
| `What was the patient's discharge status?` | 8 rows fixed: IDs 414/415 lost `Official discharge` (contradicted `Left against medical advice`); IDs 435/436 lost `Transferred to another facility` (duplicate coding of the psych admission); IDs 1/15/29/286 had no box checked and are now flagged in a **new** `(choice=Unknown)` column. The all-`Unchecked` `(choice=Left without treatment)` column was **dropped**. |
| `Date completing this form` | Values untouched; **renamed** to `Date form completed (data entry date)`. It is a batch data-entry stamp, not a clinical event date — see caveats below. |

Deliberately left as-is:

- `Specimen number` — 13 inconsistent site formats, but `Record ID` is the join
  key, so it was not worth canonicalizing.
- `Sample matrix` — empty on every Specimen Form row; the matrix is recorded
  against the QToF rows and is handled on the qtof side, not here.
- `Patient's sex ` — the LABELS export merges cis and trans into two labels
  (`Male (transgender male)`, `Female (transgender female)`); the distinction is
  not recoverable from this file. Header keeps its trailing space, as exported.
- `Patient's race` / `Patient's Ethnicity` — no defects found. Two rows (107,
  285) legitimately have two races checked.
- `Record ID` — verified clean: 373 unique, no duplicates, no blanks.

## Caveats for anyone charting v2

- **Cohort size is 373, not 448.** `Record ID` runs 1-448 with 75 values
  missing; those IDs appear nowhere in the export (deleted or never created in
  REDCap). Never take a denominator from `max(Record ID)`.
- **`Record ID` joins 1:1 with qtof, but one-to-many by row.** Every specimen
  has at least one QToF row and there are no orphan screens; 350 specimens have
  1 screen and 23 have 2, so a join fans those 23 out.
- **BAC: 94 of 149 numeric values are `0.000`.** That single value merges true
  zeros, `Negative`, and below-detection-limit results, so any mean is pulled
  hard toward zero. Only 55 rows have detectable alcohol. v1 retains the
  distinction.
- **Hospital stay has a spike at 1 day (120 cells).** Partly an artifact of the
  `0-1 -> 1` rule, which merges same-day discharge with one overnight.
- **The date column cannot support a time series.** IDs 1-233 are 100% undated
  and IDs 234-448 are 100% dated — the field entered use partway through the
  study. Its 36 distinct dates each cover a contiguous block of Record IDs,
  i.e. batch data entry, not patient events.
- **Small race categories carry disclosure risk.** Native Hawaiian/Pacific
  Islander = 1, Asian = 5, American Indian/Alaskan Native = 7. Consider
  aggregating for any published output.
- **Three discharge rows still have two boxes checked** (331, 343, 428), all
  pairing a primary disposition with `Admitted to detox or substance abuse
  treatment program`.

## Planned for v3

- Merge the 10 discharge checkbox columns into a single `discharge_status`
  column. Needs a rule for the three rows above.
- Clean `Sample matrix` on the qtof side and carry it across if wanted.

## Git

The CSV snapshots are **git-ignored** (they are derived from PHI-sensitive
data and must stay out of version control). Only this README is tracked, so
the directory and the convention live in the repo while the data stays local.
