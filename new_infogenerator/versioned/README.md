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
  qtof_v2.csv
  ...
```

- `v1` = the initial split produced by `src/split_data.py` from the
  NonFatalOverdose LABELS export (unfiltered).
- Bump the version number for each subsequent change. Keep `specimen_vN`
  and `qtof_vN` on the same `N` so a version pairs cleanly for re-merging
  on `Record ID`.

## Git

The CSV snapshots are **git-ignored** (they are derived from PHI-sensitive
data and must stay out of version control). Only this README is tracked, so
the directory and the convention live in the repo while the data stays local.
