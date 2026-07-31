# Versioned data snapshots

Every time we change a dataset (filtering, cleaning, re-splitting a new export),
the result is saved here as a new numbered version so we can compare and roll
back.

## Naming convention

Flat directory, version suffix in the filename:

```
versioned/
  specimen_v1.csv          qtof_v1.csv          (baselines from split_data.py)
  specimen_v2.csv          qtof_v2.csv          (cleaned)
  analyte_mapping_v2.xlsx  analyte_mapping_v2.csv
```

- `v1` = the untouched baseline produced by `src/split_data.py` from the
  NonFatalOverdose LABELS export (unfiltered).
- Bump the version number for each subsequent change. Keep `specimen_vN` and
  `qtof_vN` on the same `N` where both sides change together, so a version pairs
  cleanly for re-merging on `Record ID`.
- The analyte mapping starts at `v2` because its baseline is the study team's
  `data/Analyte_Category_Mapping.xlsx`, which is the implicit v1 and is never
  modified.

## Pipeline

```
data/NonFatalOverdoseBioS-OnePagerData_DATA_LABELS_*.csv   (REDCap LABELS export)
  |
  |  src/split_data.py         splits the long export by Repeat Instrument
  v
specimen_v1.csv  373 x 28      qtof_v1.csv  396 x 6
  |                              |
  |  src/clean_specimen.py       |  src/clean_qtof.py
  v                              v
specimen_v2.csv  373 x 28      qtof_v2.csv  396 x 6

data/Analyte_Category_Mapping.xlsx  445 x 7   (study team's reference vocabulary)
  |
  |  src/clean_analyte_mapping.py
  v
analyte_mapping_v2.{xlsx,csv}  444 x 7
```

Each `vN` is immutable and is never edited in place. Every cleaning script reads
its baseline and rewrites its output from scratch on each run, so runs are
deterministic and idempotent — re-running can never accumulate half-applied
edits. Regenerate everything with:

```
python3 src/split_data.py            # -> output/, copy to versioned/*_v1.csv
python3 src/clean_specimen.py        # specimen_v1 -> specimen_v2
python3 src/clean_qtof.py            # qtof_v1     -> qtof_v2
python3 src/clean_analyte_mapping.py # data/*.xlsx -> analyte_mapping_v2
```

Every run prints the full old -> new change list so the transformation can be
audited before the output is trusted.

## specimen_v2 — what changed

Rules were agreed column by column. Unparseable values raise with a line number
rather than silently defaulting, so new junk in a future export fails loudly.

| Column | Change |
| --- | --- |
| `Patient's age` | Bare integers only (0-94). Unit text dropped (`29 yo` -> `29`); ages in months collapse to completed years, so under 12 months -> `0`. 3 cells. |
| `How long was the hospital stay (in days)?` | Bare integers, `Unknown`, or blank. Unit text dropped; fractions round up; `<n` read as n; `>n` -> smallest integer above; anything at 0-1 -> `1`. A mis-entered date and an `N/A` became `Unknown`. 62 cells. |
| `Patient's blood alcohol concentration (if known)` | Standardized to g/dL at 3 decimals, **renamed** to `Patient's blood alcohol concentration, g/dL (if known)`. Source mixed mg/dL and g/dL 1000x apart: results > 1 are mg/dL and divided by 1000, results < 1 are already g/dL. Censored values (`<5`, `<10`, `<0.010`) and `Negative` -> `0.000`; `N/A`/`Unknown` -> `Unknown`. Lab-report prose stripped. 139 cells. |
| `What was the patient's discharge status?` | 8 rows fixed: IDs 414/415 lost `Official discharge` (contradicted `Left against medical advice`); IDs 435/436 lost `Transferred to another facility` (duplicate coding of the psych admission); IDs 1/15/29/286 had no box checked and are flagged in a **new** `(choice=Unknown)` column. The all-`Unchecked` `(choice=Left without treatment)` column was **dropped**. |
| `Date completing this form` | Values untouched; **renamed** to `Date form completed (data entry date)` — it is a batch data-entry stamp, not a clinical event date. |

Deliberately left as-is: `Specimen number` (13 site formats, but `Record ID` is
the join key), `Patient's sex ` (the LABELS export merges cis and trans into two
labels; header keeps its trailing space as exported), `Patient's race` /
`Patient's Ethnicity` (no defects; IDs 107 and 285 legitimately have two races),
and `Record ID` (verified clean: 373 unique, no duplicates or blanks).

`Sample matrix` is **not** a specimen column. It is empty on every Specimen Form
row because the matrix is recorded against the QToF rows, so `split_data.py`
selects it into the qtof side only.

## qtof_v2 — what changed

Text normalization only. This pass does **not** canonicalize analyte names
against the mapping, collapse metabolites, merge the 23 second instances, or
explode to one row per analyte.

| Change | Detail |
| --- | --- |
| Whitespace | 152 edge-dirty cells -> 0; 27 cells with internal double spaces -> 0 |
| Trailing commas | 131 cells -> 0 (no more empty tokens) |
| Unbalanced parenthesis | ID 60 `Negative Ion Mode` gained its closing `)` |
| Paren-aware splitting | Commas inside a parenthetical qualifier are not delimiters, e.g. `lidocaine-M (MEGX, N deethylated metabolite)` |
| Missing delimiters | 6 repairs: a period used as a comma (IDs 176, 178), a double space after a name (IDs 91, 145, 239), and one single-space fusion (ID 238, `fentanyl 1-(3-chlorophenyl)piperazine (mCPP)`) |
| Sentinels | 6 spellings (`None`, `-`, `None detected.`, `None observed`, `Negative`, `None detected`) collapsed onto `None detected`, 39 cells |
| Repeated analytes | Dropped 2 in-cell duplicates (ID 214 `THC-M (carboxy THC metabolite)`, ID 269 `acetaminophen`) |
| `Sample matrix` | Now carried through from the export: 94 urine, 54 plasma, 248 blank. Values validated; an unexpected value raises. |

A slash is part of an analyte **name**, never a delimiter —
`4-ANPP/despropionylfentanyl` and `citalopram/escitalopram` are alias pairs for
one substance. 14 distinct tokens contain one and all are preserved.

Coverage against `analyte_mapping_v2`: **2890 of 2935 analyte instances (98.5%)**
and 391 of 429 distinct spellings (91%) map. The unmapped tail is 38 distinct
tokens / 45 instances — mostly analytes genuinely absent from the mapping, plus
about 8 misspellings.

## analyte_mapping_v2 — what changed

`data/Analyte_Category_Mapping.xlsx` is the study team's file and is never
modified. v2 repairs:

| Change | Detail |
| --- | --- |
| Whitespace | 30 `Canonical_Analyte` cells had a trailing space, making e.g. `'cathine '` and `'cathine'` distinct keys. Now 0 dirty cells. |
| Category spellings | 8 cells across 5 typo clusters, so 23 category strings collapse to **17** real categories: `NarcoticAnalgesic`/`NarcoticAnagesics` -> `NarcoticAnalgesics`, `CNSSimulants` -> `CNSStimulants`, `DissociativeAnesthetic` -> `DissociativeAnesthetics`, `NSPOpioids` -> `NPSOpioids`, `Hallucinogen` -> `Hallucinogens` |
| Canonical spellings | 13 cells. Three canonicals were themselves misspelled, so correct input produced wrong output: `disulfram` -> `disulfiram`, `lorsartan` -> `losartan`, `aspirin-salicyclic acid` -> `aspirin-salicylic acid`. Three others differed from an existing twin only by case. |
| Duplicate row | Removed the blank-`Flag` twin of the mCPP row, keeping the `Flag=Metabolite` one. 445 -> 444 rows. |
| Category backfill | `olazapine` (variant of `olanzapine`) had no category; filled with `Antipsychotics` from its sibling row. 0 category conflicts remain. |

**The `Analyte` lookup keys are never corrected.** Misspelled keys are the point
of that column — they are what lets a typo in the raw QToF text find the right
canonical. Only the `Canonical_*` output side is fixed. All 444 keys survive.

Both outputs have identical content; the CSV exists because `openpyxl` is not
installed, so pipeline code reads the CSV.

## Caveats for anyone charting these

- **Cohort size is 373, not 448.** `Record ID` runs 1-448 with 75 values
  missing; those IDs appear nowhere in the export (deleted or never created in
  REDCap). Never take a denominator from `max(Record ID)`.
- **`Record ID` joins 1:1 between the files but one-to-many by row.** Every
  specimen has at least one QToF row and there are no orphans; 350 specimens
  have 1 screen and 23 have 2, so a join fans those 23 out.
- **The 23 second screens are mostly re-entries, not new screens.** All are
  urine; 22 of 23 pair urine with urine. Comparing analyte sets, only 2 are
  identical and the other 21 differ mostly by *spelling*. They contribute just
  28 genuinely new positive analytes. Union them only after canonicalizing, or
  spelling variants will double-count.
- **Metabolites inflate any "substances detected" count.** 186 mapping rows are
  flagged `Metabolite`. The top two detections are both THC metabolites, and
  `fentanyl-nor` / `4-anpp` / `beta-hydroxyfentanyl` accompany `Fentanyl`, so one
  fentanyl exposure can register as four detections. Decide explicitly whether
  metabolites collapse into the parent drug.
- **Treatment drugs are mixed in with exposures.** `naloxone` (53 records) and
  `ondansetron` (32) are almost certainly administered in the ED, not taken by
  the patient. 162 mapping rows have no drug category at all, including the most
  common detections (`acetaminophen`, `diphenhydramine`, `gabapentin`,
  `caffeine`). A "drugs detected" chart that does not separate treatment from
  exposure will mislead.
- **`Sample matrix` is missing for 248 of 396 screens (63%),** so any
  matrix-stratified figure covers only 148. Matrix matters: urine holds
  metabolites for days while plasma reflects what is circulating now, and urine
  is metabolite-rich — which is partly why a urinary THC metabolite is the single
  most common detection.
- **BAC: 94 of 149 numeric values are `0.000`.** That value merges true zeros,
  `Negative`, and below-detection results, so any mean is pulled hard toward
  zero. Only 55 rows have detectable alcohol. `specimen_v1` retains the
  distinction.
- **Hospital stay has a spike at 1 day (120 cells),** partly an artifact of the
  `0-1 -> 1` rule, which merges same-day discharge with one overnight.
- **The date column cannot support a time series.** IDs 1-233 are 100% undated
  and IDs 234-448 are 100% dated — the field entered use partway through the
  study. Its 36 distinct dates each cover a contiguous block of Record IDs.
- **Small race categories carry disclosure risk.** Native Hawaiian/Pacific
  Islander = 1, Asian = 5, American Indian/Alaskan Native = 7. Aggregate for any
  published output.
- **Three discharge rows still have two boxes checked** (331, 343, 428), all
  pairing a primary disposition with `Admitted to detox or substance abuse
  treatment program`.

## Open items

- Canonicalize qtof analytes through `analyte_mapping_v2.csv`, failing loudly on
  unmapped tokens so the list stays a shrinking worklist.
- Add the ~25 analytes qtof needs that the mapping lacks, plus ~8 misspellings.
- Decide the qtof output shape: one row per screen, or exploded to one row per
  (Record ID, analyte).
- Decide the metabolite-collapse rule and the instance-1/instance-2 merge rule.
- Merge the 10 specimen discharge checkbox columns into a single
  `discharge_status` (planned for specimen_v3); needs a rule for IDs 331/343/428.
- Mapping still not idempotent: 73 canonicals are not themselves `Analyte` keys,
  so the mapping must be applied exactly once.
- `Known_Variants` (22 rows) is not wired in as lookup keys.

## Git

The data snapshots here are **git-ignored** — they are derived, reproducible from
`data/` via `src/`, and the CSVs are PHI-sensitive. Only this README is tracked,
so the directory and the convention live in the repo while the data stays local.
