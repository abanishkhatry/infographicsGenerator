# infographicsGenerator

Tooling for the WSLH non-fatal overdose biosurveillance one-pager: turn a
REDCap export of specimen + drug-screen records into cleaned datasets and,
eventually, generated infographics.

## Layout

```
new_infogenerator/          active work — the one-pager data pipeline
  data/                     REDCap exports + reference mappings (PHI, git-ignored)
  src/split_data.py          splits the long REDCap export into specimen + qtof
  src/clean_specimen.py      per-column cleaning of the specimen side
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
  `new_infogenerator/versioned/*.csv`. Only the `versioned/README.md` is tracked
  from that directory.
- `new_infogenerator/data/Analyte_Category_Mapping.xlsx` is the one tracked file
  in `data/` — it is a reference mapping, not patient data.
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
versioned/specimen_v1.csv (373 x 29)   versioned/qtof_v1.csv (396 x 5)
  |  src/clean_specimen.py
  v
versioned/specimen_v2.csv (373 x 29)
```

The REDCap export is a "long" file: each specimen has one `Specimen Form` row
plus one or more `QToF Screen` rows, tagged in the `Repeat Instrument` column.
`split_data.py` separates the two so each side can be cleaned independently and
re-merged later on `Record ID`.

Both scripts select columns by header **name**, not index, so a reordered
export does not silently corrupt the split.

Run them from `new_infogenerator/`:

```
python3 src/split_data.py       # writes output/specimen.csv + output/qtof.csv
python3 src/clean_specimen.py   # reads versioned/specimen_v1.csv -> specimen_v2.csv
```

## Cleaning conventions

The working pattern, established for v1 -> v2 and worth keeping:

1. **`vN` is immutable.** Never edit a snapshot in place. A cleaning script
   reads `vN` and rewrites `vN+1` from scratch every run, so it is
   deterministic and idempotent — re-running cannot accumulate partial edits.
2. **One column at a time, rules agreed before coding.** `clean_specimen.py`
   holds a `CLEANERS` registry keyed by column name; add a function per column.
   `RENAMES` and `DROPS` handle header changes and dead columns.
3. **Fail loudly, never guess.** Cleaners raise `ValueError` on an unrecognized
   format and the runner reports the line number. Row-level fixes assert the
   cell is in the expected state before changing it, and `drop_columns` refuses
   to drop a column that has become populated. A future export with new junk
   breaks the run instead of silently writing a wrong value.
4. **Report every change.** Each run prints the full old -> new list with counts
   so the diff can be audited before the output is trusted.
5. **Verify against the baseline.** After a change, confirm the intended
   columns changed and everything else is byte-identical to `vN`.

`versioned/README.md` documents what each version actually contains, the
per-column rules applied, and the caveats that matter when charting. Update it
whenever a version changes.

## Known data caveats

Full detail in `new_infogenerator/versioned/README.md`. The ones that bite:

- **Cohort is 373, not 448.** `Record ID` spans 1-448 with 75 values missing
  entirely from the export. Never derive a denominator from `max(Record ID)`.
- **BAC in the raw export mixes mg/dL and g/dL**, 1000x apart, with no unit on
  some cells. v2 standardizes to g/dL. Bare integers are always mg/dL.
- **`Date completing this form` is a data-entry stamp, not an event date**, and
  is absent for IDs 1-233. It cannot support a time series.
- **`Sample matrix` is empty on every specimen row** — it lives on the QToF rows.
- **`Patient's sex ` has a trailing space** in the header, as exported, and
  merges cis with trans in its two labels.

## Git

- Default branch is `main`; feature work goes on a branch.
- Do not add `Co-Authored-By:` lines to commits, PR bodies, or issue comments.
- Commit or push only when asked.
