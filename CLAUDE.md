# infographicsGenerator

Tooling for the WSLH non-fatal overdose biosurveillance one-pager: turn a
REDCap export of specimen + drug-screen records into cleaned datasets and,
eventually, generated infographics.

## Layout

```
new_infogenerator/          active work — the one-pager data pipeline
  data/                      REDCap exports + reference mappings (PHI, git-ignored)
  src/split_data.py          splits the long REDCap export into specimen + qtof
  src/clean_specimen.py      per-column cleaning of the specimen side
  src/clean_qtof.py          text normalization of the drug-screen side
  src/clean_analyte_mapping.py  repairs the analyte -> category vocabulary
  versioned/                 numbered data snapshots (git-ignored) + README
  output/                    scratch output of split_data.py (git-ignored)

biosurveillance-main/       prior/reference project (CDC OD2A submission tooling)
  src/biosurveillance_analytics/     validation + QToF analytics
  src/biosurveillance_dissemination/ docx/viz generation
  data/<year> - Q<n>/                quarterly raw + cdc_output sets
```

Treat `biosurveillance-main/` as reference unless asked otherwise; new work
happens in `new_infogenerator/`.

## Data handling — read this first

**The REDCap data is PHI-sensitive.** It must stay out of git and out of any
external service.

- `.gitignore` excludes `**/data/*.csv`, `**/data/*.xlsx`, `**/output/`, and
  `new_infogenerator/versioned/*` data files. Only `versioned/README.md` is
  tracked from that directory.
- `new_infogenerator/data/Analyte_Category_Mapping.xlsx` is the one tracked file
  in `data/` — it is a reference drug vocabulary, not patient data.
- Never `git add .` in this repo. Stage files explicitly.
- When profiling or reporting on the data, prefer aggregate output (value
  counts, distributions) over dumping patient-level rows.
- Small demographic cells are a re-identification risk. Race categories in the
  current cohort go as low as n=1; aggregate before publishing anything.

## The pipeline

```
data/NonFatalOverdoseBioS-OnePagerData_DATA_LABELS_*.csv   (REDCap LABELS export)
  |  src/split_data.py
  v
versioned/specimen_v1.csv (373 x 28)   versioned/qtof_v1.csv (396 x 6)
  |  src/clean_specimen.py             |  src/clean_qtof.py
  v                                    v
versioned/specimen_v2.csv (373 x 28)   versioned/qtof_v2.csv (396 x 6)

data/Analyte_Category_Mapping.xlsx (445 x 7)
  |  src/clean_analyte_mapping.py
  v
versioned/analyte_mapping_v2.{xlsx,csv} (444 x 7)
```

The REDCap export is a "long" file: each specimen has one `Specimen Form` row
plus one or more `QToF Screen` rows, tagged in the `Repeat Instrument` column.
`split_data.py` separates the two so each side can be cleaned independently and
re-merged later on `Record ID`.

Columns are selected by header **name**, not index, so a reordered export does
not silently corrupt the split. Which side a column lands on matters:
`Sample matrix` is populated only on the QToF rows, so it belongs to
`QTOF_COLS` and is deliberately absent from `SPECIMEN_COLS`.

Run from `new_infogenerator/`:

```
python3 src/split_data.py            # writes output/{specimen,qtof}.csv
python3 src/clean_specimen.py        # specimen_v1 -> specimen_v2
python3 src/clean_qtof.py            # qtof_v1     -> qtof_v2
python3 src/clean_analyte_mapping.py # data/*.xlsx -> analyte_mapping_v2
```

`openpyxl` is **not installed** and should not be assumed. `clean_analyte_mapping.py`
reads and writes xlsx as raw OOXML, and emits a CSV alongside so downstream code
can use the stdlib.

## Cleaning conventions

The working pattern, established across specimen, qtof, and the mapping:

1. **`vN` is immutable.** Never edit a snapshot in place. A cleaning script reads
   `vN` and rewrites `vN+1` from scratch every run, so it is deterministic and
   idempotent — re-running cannot accumulate partial edits. The source-of-truth
   files in `data/` are never modified either.
2. **One column at a time, rules agreed before coding.** `clean_specimen.py`
   holds a `CLEANERS` registry keyed by column name; `RENAMES` and `DROPS` handle
   header changes and dead columns.
3. **Fail loudly, never guess.** Cleaners raise on an unrecognized format and the
   runner reports the line number. Row-level fixes assert the cell's expected
   prior state, `drop_columns` refuses to drop a column that has become
   populated, and `clean_qtof.py` validates `Sample matrix` against a known value
   set. A future export with new junk breaks the run instead of writing a wrong
   value.
4. **Report every change.** Each run prints the full old -> new list with counts
   so the diff can be audited before the output is trusted.
5. **Verify against the baseline.** After a change, confirm the intended columns
   changed and everything else is byte-identical to `vN`. For qtof, also check
   the analyte multiset — every gained or lost token must be explainable.
6. **Correct the root cause, not the symptom.** `Sample matrix` was missing
   because `split_data.py` never selected it; the fix went there and the baseline
   was regenerated, rather than patching it in downstream.

`versioned/README.md` documents what each version contains, the per-column rules,
and the caveats that matter when charting. Update it whenever a version changes.

## Known data caveats

Full detail in `new_infogenerator/versioned/README.md`. The ones that bite:

- **Cohort is 373, not 448.** `Record ID` spans 1-448 with 75 values missing
  entirely from the export. Never derive a denominator from `max(Record ID)`.
- **BAC in the raw export mixes mg/dL and g/dL**, 1000x apart, with no unit on
  some cells. v2 standardizes to g/dL; a bare integer is always mg/dL.
- **`Date completing this form` is a data-entry stamp, not an event date**, and
  is absent for IDs 1-233. It cannot support a time series.
- **Metabolites inflate detection counts.** One fentanyl exposure can appear as
  fentanyl + norfentanyl + 4-ANPP + beta-hydroxyfentanyl. 186 mapping rows carry
  `Flag=Metabolite`.
- **Treatment drugs are mixed in with exposures** (`naloxone`, `ondansetron`),
  and 162 mapping rows have no drug category at all.
- **The 23 qtof second instances are mostly spelling-variant re-entries**, not
  independent screens. Canonicalize before unioning them.
- **`Sample matrix` is blank for 63% of screens** (248 of 396).
- **`Patient's sex ` has a trailing space** in the header, as exported, and
  merges cis with trans in its two labels.
- **Analyte free text: a slash is part of a name, never a delimiter**
  (`4-ANPP/despropionylfentanyl`), and a comma inside parentheses is not a
  delimiter either. Use the paren-aware splitter in `clean_qtof.py`.
- **Never "correct" the mapping's `Analyte` keys.** Misspelled keys are what let
  a typo in the raw QToF text resolve to the right canonical. Only fix the
  `Canonical_*` output side.

## Git

- Default branch is `main`; feature work goes on a branch.
- Do not add `Co-Authored-By:` lines to commits, PR bodies, or issue comments.
- Commit or push only when asked.
