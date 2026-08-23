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
  specimen_v3.csv          qtof_v3.csv          (validation-template shape)
  analyte_mapping_v2.xlsx  analyte_mapping_v2.csv   (spelling repairs)
  analyte_mapping_v3.xlsx  analyte_mapping_v3.csv   (+ 35 QToF additions,
                                                    study-team review, backfill)
  validate_v1.csv                                   (the template's 17 columns)
```

`validate_v1.csv` is the joined deliverable rather than a cleaning generation.
It bumps when either input side bumps. The rendered one-pager is **not** here —
it goes to `output/`, which is git-ignored, because it is a build artefact.

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
  |                              |
  |  src/clean_specimen_v3.py    |  src/clean_qtof_v3.py  <--- needs mapping_v3
  v                              v
specimen_v3.csv  373 x 12      qtof_v3.csv  2709 x 9
  (11 template cols + 1 sidecar)  (one row per Record ID x analyte,
                                   classified and instance-merged)

data/Analyte_Category_Mapping.xlsx  445 x 7   (study team's reference vocabulary)
  |
  |  src/clean_analyte_mapping.py      repairs spelling defects
  v
analyte_mapping_v2.{xlsx,csv}  444 x 7
  |
  |  src/extend_analyte_mapping.py     adds the substances QToF needs
  v
analyte_mapping_v3.{xlsx,csv}  479 x 7  ------> consumed by clean_qtof_v3.py

specimen_v3.csv  373 x 12      qtof_v3.csv  2709 x 9
  |                              |
  +--------------+---------------+
                 |  src/build_validate.py     joins on record_id
                 v
        validate_v1.csv  2709 x 17     the validation template's shape
                 |
                 |  src/onepager_stats.py     computes every figure
                 |  src/build_onepager.py     renders layout only
                 v
        output/onepager.html               one page, self-contained
```

Each `vN` is immutable and is never edited in place. Every cleaning script reads
its baseline and rewrites its output from scratch on each run, so runs are
deterministic and idempotent — re-running can never accumulate half-applied
edits. Regenerate everything with:

```
python3 src/split_data.py            # -> output/, copy to versioned/*_v1.csv
python3 src/clean_specimen.py        # specimen_v1 -> specimen_v2
python3 src/clean_qtof.py            # qtof_v1     -> qtof_v2
python3 src/clean_analyte_mapping.py  # data/*.xlsx -> analyte_mapping_v2
python3 src/extend_analyte_mapping.py # mapping_v2  -> analyte_mapping_v3
python3 src/clean_specimen_v3.py      # specimen_v2 -> specimen_v3
python3 src/clean_qtof_v3.py          # qtof_v2 + mapping_v3 -> qtof_v3
python3 src/build_validate.py         # specimen_v3 + qtof_v3 -> validate_v1
python3 src/onepager_stats.py         # review the figures (--json to hand over)
python3 src/build_onepager.py         # validate_v1 -> output/onepager.html
```

Order matters in one place: `clean_qtof_v3.py` reads `analyte_mapping_v3.csv`,
so the mapping chain must run first.

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

## specimen_v3 — reshaped onto the validation template

373 rows (unchanged), 28 columns -> 12. The 11 columns the validation template
(`data/validation_set.xlsx`) defines for the specimen side, plus 1 `sc_` sidecar.
Values are checked against the template's allowed sets, so an unseen source value
fails the run.

| Template column | From | Rule |
| --- | --- | --- |
| `record_id` | `Record ID` | Copy. String — do not zero-pad; the mapping and v1/v2 all use bare integers. |
| `spec_date` | `Date form completed` | ISO -> `MM/DD/YYYY`. **219 null (59%)** — see caveats. |
| `location` | `To which facility...` | Copy. All 3 values already match the template byte-for-byte. |
| `age` | `Patient's age` | `int`. 100% populated, 0-94. |
| `sex` | `Patient's sex ` | 2-entry map, total: `Male (transgender male)` -> `M`, `Female (transgender female)` -> `F`. |
| `race` | 6 checkbox columns | Collapse. `White/Caucasian` -> `White`; >1 tick -> `Two or more races` (IDs 107, 285). |
| `ethnicity` | 3 checkbox columns | Collapse only — labels already match, no relabel, no derived value. |
| `bac` | blood alcohol concentration | Numeric string passthrough; blank/`Unknown` -> null. **224 null (60%)**. |
| `od_manner` | `Manner of overdose?` | 4-entry map. `Unintentional/Accidental` -> `Unintentional`, `Intentional/Suicide` -> `Intentional`. 9 blanks -> `Unknown`. |
| `discharge_status` | 10 checkbox columns | Collapse 10 -> 5, see below. |
| `hospital_stay_length` | `How long was the hospital stay` | `int` floored at 1; blank/`Unknown` -> null. **56 null**. |

`discharge_status`, the agreed 10 -> 5 assignment:

| Bucket | n | Source checkboxes |
| --- | --- | --- |
| `Admitted` | 240 | hospital 169, ICU 44, psychiatric 20, detox 9 |
| `Discharged` | 103 | official discharge 99, discharged to law enforcement 5 |
| `Transferred` | 18 | transferred to another facility 12, **left AMA 6** |
| `Other` | 8 | **death 8 — the only source** |
| `Unknown` | 4 | unknown 4 |

Precedence `Other > Admitted > Transferred > Discharged > Unknown` applies only
when one patient's ticks land in two buckets. It fires **once**: Record 428
(official discharge + detox) -> `Admitted`. IDs 331 and 343 resolve without it.

One sidecar (prefix `sc_`, **not** part of the template — a downstream
`build_validate.py` selects the 11 template columns and ignores it):

| Sidecar | Carries |
| --- | --- |
| `sc_specimen_number` | The lab's own sample label; secondary join key to qtof. |

Everything the collapse destroys stays recoverable from v2, which is immutable.
Re-join on `record_id` rather than re-deriving:

- the ICU (44) / psychiatric (20) / detox (9) / death (8) / AMA (6) breakdown
  behind `discharge_status`;
- the 9 blank `Manner of overdose?` cells, now reported as `Unknown` alongside
  the 87 clinician-recorded ones;
- the below-detection-limit split behind `bac = 0.000` — though note v2 *also*
  cannot separate that one, since `<0.010` (72), `Negative` (14) and a bare `0`
  (8) are already collapsed there. Recovering it means going back to v1 or
  changing `clean_specimen.py` — see Open items.

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

Coverage, as measured by the `qtof_v3` run: **all 2935 analyte detections
(100%)** resolve against `analyte_mapping_v3`. Against `analyte_mapping_v2` it
was 98.57%, with 38 raw tokens / 42 detections stranded; the 35 substances behind
those were added in v3. See the qtof_v3 section for the rung breakdown.

## qtof_v3 — analytes canonicalized, classified, and merged per record

**2709 rows x 9 columns**, from 396 screens over 373 records. The QToF side in
the validation template's shape, with everything resolved.

| Column | Source | Content |
| --- | --- | --- |
| `Record ID` | v2 | 373 patients, 1-21 rows each (median 7). |
| `Specimen number` | v2 | Must agree across a record's instances; a disagreement raises. |
| `Sample matrix` | v2 + fill | **Fully populated**: urine 2477, plasma 232. Becomes `wslh_matrix` at the join (rename + Title Case). |
| `analyte_name` | ion columns -> mapping | One `Canonical_Analyte` per row. 2703 rows, **281 distinct canonicals**. 6 blank. |
| `analyte_group_1` | mapping | **16 values** including the `Other` fill. Sums to 2703 — the one clean partition. |
| `analyte_group_2` | mapping | 647 rows. 5 values: Cocaine 203, NPSOpioids 178, Fentanyl 160, Amphetamines 97, Cathinones 9. |
| `analyte_group_3` | mapping | **3 rows** — mdma and mda only. |
| `metabolite_flag` | mapping `Flag` | `True` 866 / `False` 1837. Real data, not a pipeline rule. |
| `unmatched_analytes` | ion columns | **Empty on every row**; kept as the guard that makes a future export's new junk visible. |

Only 6 blanks exist anywhere in the template-bound columns, all on the same 6
rows. `Sample matrix` and `unmatched_analytes` are both fully resolved.

### Grain: one row per (Record ID x analyte)

The explode is **forced, not chosen** — a screen can list 21 analytes with 21
different classifications, and a single `analyte_group_1` cell cannot hold them.

The 23 records with two QToF rows are **merged**: analytes unioned, then
deduplicated. A union rather than "keep instance 1" is required — 16 of the 23
carry analytes on instance 2 that instance 1 lacks (24 canonicals), so discarding
an instance would lose real detections. Merging is only safe *after*
canonicalization, because the instances differ mainly by spelling. Records 329
and 366 had no analytes on their first instance and gain them from their second.

`Repeat Instance` is therefore **dropped** — with the instances merged it
identifies nothing. It remains in v2, which is immutable.

**232 duplicate detections collapse** in total: substances that ionize in both
modes, case variants landing on one canonical, and 117 recorded on both
instances. The run asserts every output row is a unique
(`Record ID`, `analyte_name`) pair and raises otherwise.

### Resolution: a four-rung chain, first hit wins

| Rung | Detections | Cumulative |
| --- | --- | --- |
| 1. exact `Analyte` key | 2246 | 76.52% |
| 2. `Analyte` key, casefolded + whitespace-collapsed | 686 | **99.90%** |
| 3. `Canonical_Analyte`, likewise | 3 | **100.00%** |
| 4. `Known_Variants`, likewise | **0** | 100.00% |
| unmatched | **0** | — |

Rung 2 is nearly the whole gain. Rung 3 exists because many canonicals are not
themselves `Analyte` keys, so a correctly spelled canonical would otherwise miss.
Rung 4 earns nothing on this export — no technician typed an abbreviation — but
is wired in because that is the column's purpose. (Against `analyte_mapping_v2`
this ran at 98.57% with 42 stranded detections; `analyte_mapping_v3` closes it.)

Deliberately **not** done: fuzzy or nearest-neighbour matching. Drug names differ
by systematic one- and two-character affixes, so edit distance cannot separate a
typo from a distinct substance — `etonitazene`/`metonitazene` are 96% similar and
are different synthetic opioids, and `chlordiazepoxide` is 94% similar to its own
metabolite. Unresolved tokens are queued for review, never guessed. The `Analyte`
keys are likewise never edited; normalization happens at lookup only.

Also handled:

- The 39 `None detected` sentinels are stripped before lookup. They are
  per-*column* markers: a screen can carry one in negative mode while positive
  mode lists a dozen real detections.
- `F-alpha-PPP` is dropped from the variant index as **ambiguous** — it is listed
  against both `3'-fluoro-` and `4'-fluoro-alpha-pyrrolidinopropiophenone`.

### Classification

`analyte_group_1`, the only column that partitions the 2703 analyte rows exactly:

| Category | Rows | Share | | Category | Rows | Share |
| --- | --- | --- | --- | --- | --- | --- |
| **Other** | 601 | **22.2%** | | MOUD | 63 | 2.3% |
| NarcoticAnalgesics | 436 | 16.1% | | Naloxone | 53 | 2.0% |
| CNSStimulants | 342 | 12.7% | | Anesthetics | 44 | 1.6% |
| Cannabinoids | 308 | 11.4% | | MuscleRelaxers | 24 | 0.9% |
| Antidepressants | 250 | 9.2% | | Barbiturates | 16 | 0.6% |
| Benzodiazepines | 146 | 5.4% | | Hallucinogens | 3 | 0.1% |
| Antihistamines | 124 | 4.6% | | | | |
| Anticonvulsants | 107 | 4.0% | | | | |
| DissociativeAnesthetics | 103 | 3.8% | | | | |
| Antipsychotics | 83 | 3.1% | | | | |

The three levels use **disjoint vocabularies**, not one shared list: no value
appears in both `analyte_group_1` and `analyte_group_2`. So filtering for
fentanyl specifically means `analyte_group_2 == 'Fentanyl'` — `analyte_group_1`
returns zero. Only `Hallucinogens` crosses levels (1 and 3).

`metabolite_flag` is 32% overall but **wildly uneven by class**, which matters
more than the headline:

| Class | Metabolite share | | Class | Metabolite share |
| --- | --- | --- | --- | --- |
| **Cannabinoids** | **98%** (301/308) | | CNSStimulants | 19% |
| MOUD | 59% | | Other | 9% |
| Benzodiazepines | 49% | | Anesthetics | 9% |
| DissociativeAnesthetics | 44% | | Antihistamines | 4% |
| Antidepressants | 42% | | MuscleRelaxers | 4% |
| NarcoticAnalgesics | 36% | | Anticonvulsants / Naloxone / Barbiturates | **0%** |
| Antipsychotics | 24% | | | |

Filtering `metabolite_flag = False` does **not** scale the data down evenly — it
nearly erases cannabis (308 rows -> 7) while leaving anticonvulsants, naloxone
and barbiturates untouched. Any "excluding metabolites" view changes the drug
*mix*, not just the totals. 307 of 373 patients (82%) carry at least one
metabolite.

### Three decisions encoded as constants

**Category spelling — the mapping wins** (Aug 2026). Where the mapping and the
template disagree, the mapping's spelling is emitted and the *template* gains it.
Tracked in `TEMPLATE_ADDITIONS_REQUESTED`; any category in neither the template
nor that set **raises**. Four values are pending, covering 617 rows:

| Value | Rows | Template needs |
| --- | --- | --- |
| `CNSStimulants` | 342 | rename from `CSNSStimulants` (a typo) |
| `Antihistamines` | 124 | add |
| `Anticonvulsants` | 107 | add |
| `Anesthetics` | 44 | add |

**`Sample matrix`: unrecorded means urine.** The study team confirmed no plasma
was collected during the earlier part of the study, so 2031 rows / 248 patients
are filled with `urine` (`MATRIX_BLANK_FILL`). This is an **imputation, not a
measurement** — see caveats. It is what lets `wslh_matrix` satisfy a template
that allows only `Plasma` | `Urine` with no blank. Record 386, whose two
instances disagreed (`plasma` vs `urine`), resolves to the fill value, which is
also one of its two recorded values; reported every run.

**The `Flag` stand-in is retired.** Five canonicals used to have rows that
disagreed on `Flag`; the study team resolved all five as metabolites and the
answers now live in `clean_analyte_mapping.FLAG_FIXES`, so the mapping is
self-consistent. `FLAG_CONFLICT_METABOLITE_WINS` is left **False** on purpose: a
disagreement now means the mapping has regressed, and that should stop the run
rather than be papered over by a tie-break.

### The 6 blank `analyte_name` rows

Deliberate, but they are **not all the same thing** and must not be read as "no
drugs detected":

| Record | `qtof_v1` ion columns | Meaning |
| --- | --- | --- |
| 190 | `'None'` / `'None'` | genuine negative — the screen ran and found nothing |
| 142, 143, 365, 433, 447 | **both empty** | **no result was ever entered** |

Only **one** of the six is a true negative. The other five have completely empty
ion-mode cells in the raw export — no analytes and no sentinel — so their screen
is *missing*, not negative. Three of them (365, 433, 447) are MCW specimens with
a recorded `Sample matrix`, which suggests the sample was collected and run.

Keeping the rows preserves those patients' demographics. But **counting the five
as negatives would dilute any "% of patients with X detected" figure** with
unscreened patients. Exclude them from analyte-based denominators, or report them
as missing. `qtof_v1` has 7 all-empty rows; two are second instances of Records
329 and 366, whose first instances do carry analytes, so the merge resolves those.

## analyte_mapping_v2 — what changed

`data/Analyte_Category_Mapping.xlsx` is the study team's file and is never
modified. v2 repairs:

| Change | Detail |
| --- | --- |
| Whitespace | 30 `Canonical_Analyte` cells had a trailing space, making e.g. `'cathine '` and `'cathine'` distinct keys. Now 0 dirty cells. |
| Category spellings | 8 cells across 5 typo clusters, so 23 category strings collapse to **17** real categories: `NarcoticAnalgesic`/`NarcoticAnagesics` -> `NarcoticAnalgesics`, `CNSSimulants` -> `CNSStimulants`, `DissociativeAnesthetic` -> `DissociativeAnesthetics`, `NSPOpioids` -> `NPSOpioids`, `Hallucinogen` -> `Hallucinogens` |
| Canonical spellings | 15 cells. Canonicals that were themselves misspelled, so correct input produced wrong output: `disulfram` -> `disulfiram`, `lorsartan` -> `losartan`, `aspirin-salicyclic acid` -> `aspirin-salicylic acid`. Three others differed from an existing twin only by case. |
| `metaprolol` merge | `metaprolol` was a misspelling given its own canonical, splitting one beta blocker across two names with ~5 detections on the wrong branch. Study team confirmed (Heather, Aug 2026): `metaprolol` -> `metoprolol`, and its metabolite row's doubly-typo'd canonical `metaprolol-hydorxy` -> `metoprolol-hydroxy`. |
| Flag repair | 1 row. The merge above left `metaprolol-hydroxy` sharing a canonical with a `Flag=Metabolite` twin while blank itself; set to `Metabolite`. A **targeted** fix via `FLAG_FIXES`, not a general backfill — the five pre-existing Flag disagreements stay visible and unresolved. |
| Duplicate row | Removed the blank-`Flag` twin of the mCPP row, keeping the `Flag=Metabolite` one. 445 -> 444 rows. |
| Category backfill | `olazapine` (variant of `olanzapine`) had no category; filled with `Antipsychotics` from its sibling row. 0 category conflicts remain. |

**The `Analyte` lookup keys are never corrected.** Misspelled keys are the point
of that column — they are what lets a typo in the raw QToF text find the right
canonical. Only the `Canonical_*` output side is fixed. All 444 keys survive.

Both outputs have identical content; the CSV exists because `openpyxl` is not
installed, so pipeline code reads the CSV.

## analyte_mapping_v3 — the substances QToF needs

v2 held the study team's vocabulary with its defects repaired, but was missing
35 substances that appear in the QToF free text — which is why `qtof_v3` first
ran at 98.57% with 42 detections stranded in `unmatched_analytes`.
`src/extend_analyte_mapping.py` reads v2 and appends those rows: **444 -> 479
rows, 295 -> 315 canonicals**. QToF coverage goes to **100%**.

Category vocabulary after all edits: the mapping uses **20** distinct categories
(15 in `Drug_Category_1`, 5 in `Drug_Category_2`, 1 in `Drug_Category_3`; the
columns are disjoint apart from `Hallucinogens`). The template needs those 20
plus `Other`, which exists only as the template's fill — **21 options**, up from
the 18 it allows today.

The additions came back from the study team (Heather, Aug 2026) in three tiers,
which differ in what had to be decided:

| Tier | Rows | What it is | Who decided |
| --- | --- | --- | --- |
| **A** | 15 | The substance was already in the vocabulary; only this *spelling* was missing. Each new row is an `Analyte` key pointing at an existing canonical. | Study team confirmed all 15 |
| **B** | 8 | Genuinely new substances that merely **resemble** something already mapped. Confirmed as NOT the same analytes. | Canonical names follow standard toxicology nomenclature |
| **C** | 12 | Absent from the vocabulary entirely. | Study team supplied canonical + `Known_Variants` verbatim |

Tier A rows **inherit** their categories and `Flag` from the target canonical
rather than being retyped, so a new key can never disagree with the rows already
describing that substance. `build_tier_a` raises if a target is missing or if its
existing rows already disagree.

Tier B records what each substance must **not** be confused with, because that is
the expensive failure mode:

| Analyte | Canonical | Not to be confused with |
| --- | --- | --- |
| `DL-cathinone` | `cathinone` | `cathine` — which is norpseudoephedrine |
| `Chloroquine` | `chloroquine` | `hydroxychloroquine` — this is the parent drug |
| `N-piperidinyl etonitazene` | `n-piperidinyl etonitazene` | `n-piperidinyl 4-hydroxy nitazene` |
| `etonitazene` | `etonitazene` | `metonitazene` — 96% similar string, different opioid |
| `chlordiazepoxide` | `chlordiazepoxide` | `chlordiazepoxide-metabolite` — this is the parent |
| `MDA` | `mda` | `mdma` |
| `meta-methyl fentanyl` | `meta-methyl fentanyl` | `meta-methyl acetyl fentanyl` — note the acetyl |
| `mirtazapine-n-desmethyl` | `mirtazapine-n-desmethyl` (Flag=Metabolite) | `olanzapine-n-desmethyl` |

The study team reviewed the whole file and returned it (Heather, Aug 2026;
archived at `data/Analyte_Category_Mapping_reviewed_2026-08.xlsx`). Her answers
are folded into the **scripts**, not just the data, so the chain still reproduces
from source: the 8 `Flag` values into `clean_analyte_mapping.FLAG_FIXES`, and the
Tier B canonicals, categories and variants into `TIER_B`.

Two deliberate departures from her file, both documented in code:

- **`MDA`'s variant** is written `3-4-methylenedioxyamphetamine`, not
  `3,4-...`. `Known_Variants` is comma-delimited, so the real name would split
  into a junk one-character token `3` that then matched any stray `3` in the
  QToF text. A `MIN_VARIANT_LEN` guard now **raises** on any variant token under
  3 characters, across the whole file.
- **Canonicals are lowercased.** She Title-Cased the 20 new entries; the file is
  overwhelmingly lowercase, so they follow the majority — `cathinone` beside
  `cathine`, `mda` beside `mdma`, `valproic acid` beside `canrenoic acid`.
  303 of 315 are now lowercase and the 12 remaining capitals are all
  pre-existing. Lookups are case-insensitive either way; this is about anything
  that groups or sorts on canonical name.

### Category backfill

The study team proposed three classes the template does not list —
`Anticonvulsants`, `Antihistamines`, `Anesthetics`. Decided Aug 2026: adopt all
three. Adopting a class **requires** classifying the drugs of that class already
in the vocabulary, or a chart would report the two newest additions as if they
were the whole class. `CATEGORY_BACKFILL` fills 43 rows that had no category:

| Category | Rows | Substances |
| --- | --- | --- |
| `Antihistamines` | 18 | 12 — diphenhydramine, hydroxyzine, cetirizine (+2 metabolites), doxylamine, chlorpheniramine (+2), promethazine (+1), chlorcyclizine |
| `Anticonvulsants` | 16 | 12 — carbamazepine, oxcarbazepine, lamotrigine, levetiracetam, topiramate, phenytoin, zonisamide, lacosamide, gabapentin, pregabalin, valproic acid, eslicarbazepine |
| `Anesthetics` | 8 | 6 — propofol, etomidate, lidocaine (+2 metabolites), bupivacaine/levobupivacaine |
| `Antidepressants` | 1 | paroxetine (the one study-team proposal the template already allowed) |

It only fills rows with **no** category, so a study-team assignment is never
overwritten, and it **raises** if a named canonical is absent so the list cannot
rot silently. Effect on qtof_v3: `Other` falls from 876 rows to 601, and
uncategorised canonicals from 132 to 102.

These assignments are a **first pass for review, not the study team's ruling**.
Three judgment calls worth checking: `gabapentin` (59 patients) and `pregabalin`
(7) are gabapentinoids, also prescribed for neuropathic pain, and gabapentin
alone is over half its class; `lidocaine` (37) is a local anaesthetic but also an
antiarrhythmic and in this cohort more likely clinical than ingested;
`promethazine` is an antihistamine, an antiemetic and a phenothiazine.
Metabolites inherit their parent's class throughout.

The script refuses to write if an addition duplicates an existing `Analyte` key
(case-insensitively), or if it would introduce a **new** `Flag` disagreement.

## validate_v1 — the joined deliverable

**2709 rows x 17 columns over 373 patients.** `src/build_validate.py` joins
`specimen_v3` (373 x 12) to `qtof_v3` (2709 x 9) on the record id and emits the
validation template's columns, in its order. Both inputs are untouched; the
output is rebuilt from scratch on every run.

By this point the work is mechanical — the shaping happened upstream:

| Step | Detail |
| --- | --- |
| `Record ID` -> `record_id` | rename, then join. The key is a perfect 1:1 — no specimen-only or qtof-only ids, so no patient is lost and no analyte row orphaned. |
| `Sample matrix` -> `wslh_matrix` | rename + Title Case (`urine` -> `Urine`) |
| Dropped | `sc_specimen_number`, `Specimen number`, `unmatched_analytes` |
| Reordered | to the template's 17-column order |

The join is **one-to-many**: 373 patients fan out to 2709 rows, so every
demographic value repeats down its patient's rows. `race == 'White'` matches
**1977 rows but 248 patients**.

Four guards, each of which stops the run rather than writing something wrong:

- **`unmatched_analytes` must be empty before it is dropped.** Otherwise a
  future export's unresolved tokens would vanish at the join — that column is
  the entire reason the review queue lives in the data.
- **Record-id mismatch** in either direction raises.
- **`Sample matrix` outside `{urine, plasma}`** raises rather than silently
  becoming blank.
- **Partially-blank analyte fields** raise; they must be blank together or not
  at all. An analyte-less record not in the known set of six also raises.

Two things are **reported, not raised**: the 617 rows using a category the
template has yet to list (see Open items), and the 6 analyte-less rows, split
into the one true negative and the five with no screen on file.
`KEEP_ANALYTE_LESS_ROWS` keeps them so those patients stay in the demographic
denominators, at the cost of 6 rows the template has no value for.

## The one-pager

Two scripts, **standard library only** — matplotlib, pandas, numpy and jinja2
are all absent from this environment, so nothing is assumed. `output/` is
git-ignored; the rendered page is a build artefact, not a snapshot.

| Script | Job |
| --- | --- |
| `src/onepager_stats.py` | Computes every figure. Prints them; `--json` hands them over for review. |
| `src/build_onepager.py` | Layout only — no arithmetic beyond scaling bars to pixels. |

The split is deliberate: the study team can sign off on **the numbers** before
anyone argues about **the layout**, and the layout can be re-cut without touching
the arithmetic. Every number on the page traces to one function.

**Charts are hand-authored inline SVG** — `<rect>` bars, one `<pattern>` in
`<defs>` for the metabolite hatch, and a `<circle>` with `stroke-dasharray` for
the sex donut. No plotting library, no CDN, **no external references at all**, so
the file opens offline and prints to PDF via `@page letter portrait`. Palette is
the house navy from `biosurveillance_dissemination/doc_styles.py` (`#003D78`)
plus a maroon for the stimulant panel, matching the reference one-pager in
`sample_images/`.

### Two safeguards built in

**Patient-level counting is enforced in the stats layer.** Every helper counts
distinct `record_id`; there is no path by which a row count reaches the page.

**Small cells are handled, not drawn.** `SUPPRESS_BELOW = 11` drops
`od_manner = Assault` (2), `discharge_status = Unknown` (4) and `Other` (8) from
the WHO list; an age band under the floor renders as a dashed outline so the
shape survives without publishing the count; a subtype sex split is withheld and
the line falls back to a bare patient count. **Every suppression is printed on
each run**, and the run also reminds the operator that the page carries
PHI-derived counts and must not be published to an external service.

### Editorial decisions encoded in the scripts

- **`naloxone` (53 patients) is excluded from the bars** as treatment rather than
  exposure — `TREATMENT_DRUGS` in the stats module — and the footer says so.
  Nothing in the validation template distinguishes the two.
- **Metabolite bars are hatched, not collapsed.** `fentanyl-nor` (75) and
  `4-anpp` (42) are visibly the same people as `Fentanyl` (84), and the legend
  says as much. Collapsing to parent drugs would be cleaner but would drop
  cannabis from 45% of patients to 1.6%.
- **Cannabis gets a footnote, not a panel** — 167 patients, but only 6 with a
  parent compound, so it indicates earlier use rather than acute intoxication.
- **Substances are ranked by patients, not rows**, so one detected in both ion
  modes does not outrank one detected once.
- **The sex donut carries a cohort baseline tick.** Without it, a panel's "64%
  male" is uninterpretable. It is not the cohort: men are 55% of all patients
  but 64% of both drug classes, and the subtypes are sharper still — Fentanyl
  71%, Amphetamines 75%, while Antidepressants flip to 43%. Each subtype
  therefore carries its own male share; the class aggregate was flattening a
  20-point gap.
- **The `WHERE` panel is a proportional bar, not a list.** Two-thirds of the
  cohort is one hospital, which is the most important caveat for reading
  everything below it, and the caption states that consequence from the data
  rather than hardcoding it.
- **`spec_date` cannot supply the reporting period**, so it is a `--period`
  argument.

### Known placeholders

The headline (`"What Wisconsin overdose patients tested positive for"`) and the
`--period` value both need the study team's wording. `benzoylecgonine` (101)
tops the stimulant chart because it is cocaine's metabolite, so it outranks
`cocaine` (67) while being the same people — correct as lab output, but worth
deciding whether cocaine-type should collapse to a single bar.

## Caveats for anyone charting these

- **Cohort size is 373, not 448.** `Record ID` runs 1-448 with 75 values
  missing; those IDs appear nowhere in the export (deleted or never created in
  REDCap). Never take a denominator from `max(Record ID)`.
- **qtof_v3 has one row per analyte, not per patient.** 2709 rows describe 373
  patients. Every patient-level statistic must use `nunique(Record ID)`, never
  `len(df)` — counting rows weights each patient by how many substances they
  screened positive for, which correlates with severity, so the bias is not
  random.
- **qtof_v3 `Other` is 22% of analyte rows (601).** It is the template's fill for
  a substance carrying no `Drug_Category_`, not a real drug class, and it is a
  *mix*: mostly drugs with an obvious class the vocabulary lacks — analgesics and
  NSAIDs 215 rows (acetaminophen alone 150), antiemetics 67, xanthines 59, beta
  blockers 47 — plus genuinely unclassifiable one-offs. It also still hides
  `quinine` (26), `xylazine` (12) and `levamisole` (3), which are markers of the
  illicit supply rather than incidental medication. A "drug classes detected"
  chart showing `Other` as the largest slice is partly reporting a gap in the
  mapping, not a finding.
- **qtof_v3 `wslh_matrix` is 91% urine by imputation, not measurement.** 2031 of
  2709 rows / 248 of 373 patients had no recorded matrix and are filled with
  `urine` on the study team's confirmation that no plasma was collected earlier
  in the study. The column now validates, but it no longer distinguishes measured
  urine from assumed urine, so a urine-vs-plasma comparison is mostly assumption
  on the urine side. The 232 plasma rows are all measured.
- **Raw analyte counts overstate exposure.** The top canonicals are two THC
  metabolites and acetaminophen; `Fentanyl` + `fentanyl-nor` + `4-anpp` +
  `beta-hydroxyfentanyl` are one exposure counted four times; and `naloxone`
  (53 patients) is administered treatment, not exposure. Use `metabolite_flag`
  to collapse, and note that nothing in the template distinguishes a treatment
  drug from an exposure.
- **specimen_v3 `bac`: `0.0` is a measurement, empty is not.** `0.0` (94) means
  *tested, at or below the detection limit*; an empty cell (224) means *not
  tested or not recorded*. Any `fillna(0)` downstream silently turns 224
  untested patients into zero-BAC patients. **The denominator for alcohol is
  149, not 373** — among those tested, 55 (37%) were positive, median 0.183
  g/dL, 46 at or above 0.08. Testing was not random, so that 37% cannot be
  extrapolated to the untested.
- **specimen_v3 `race`: the collapse empties a category.** `Native Hawaiian or
  Pacific Islander` comes out at **0** because its only patient (ID 285) is also
  White and is absorbed by `Two or more races`. The true count is 1, not 0.
  Four of seven race values are at or below n=7 — never publish a raw race
  table, and never cross race with `location` or `od_manner` for publication.
- **specimen_v3 `discharge_status`: `Other` is an exact proxy for deaths.**
  `Death` is its only source, so publishing "Other: 8 (2.1%)" discloses the
  death count under a label that does not say so. `Unknown` (4) and `od_manner`
  = `Assault` (2) are likewise too small to publish as slices.
- **specimen_v3 `hospital_stay_length`: `1` is a floor, not a measurement.** Of
  the 120 rows emitting `1`, only 71 were entered as `1`; 49 were forced up from
  `0`, `<1` or `0.4` — patients never admitted overnight. Publish the **median
  (2 days)**, never the mean. For the 8 deaths this column is time-to-death.
- **specimen_v3 `od_manner` flips hard by age.** 0 intentional under age 10
  (20 of 25 unintentional), majority intentional at 10-17, back to unintentional
  at 45+. A single cohort-wide intentionality figure describes none of the three
  groups. `Unknown` is 31% at UW-Health vs 8% at MCW, so the cohort-wide 25.7%
  is largely a Madison completeness artifact.
- **`race` and `ethnicity` are separate axes, not one partition.** 26 of the 248
  `White` patients are also `Hispanic`, so the two breakdowns overlap and must
  not be stacked or summed.
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

### Blocking — waiting on the study team

- **Four template additions, 617 rows.** Caitlin's validation template allows 18
  `analyte_group` values; the pipeline emits **21** (the mapping's 20 plus the
  `Other` fill). Needed: rename `CSNSStimulants` -> `CNSStimulants` (342 rows,
  the `CSNS` form is a typo), and add `Antihistamines` (124),
  `Anticonvulsants` (107) and `Anesthetics` (44). Until then those rows are
  reported as **pending additions**, not errors.
- **Five patients have no QToF result on file** — Records 142, 143, 365, 433,
  447 have completely empty ion-mode cells in the raw export. Three (365, 433,
  447) are MCW specimens with a recorded matrix, so the sample was probably run
  and the result simply never entered. Worth asking whether it can be recovered;
  otherwise they must be excluded from analyte-based denominators.
- **Review the category backfill.** 43 rows were classified into the three new
  classes as a first pass, not a ruling. Specifically: `gabapentin` (59
  patients) and `pregabalin` as Anticonvulsants, `lidocaine` (37) as an
  Anesthetic, and `promethazine` as an Antihistamine.
- **`Death` (8 patients) has no template value** and currently becomes `Other`
  in `discharge_status`, on a project titled **non-fatal** overdose
  biosurveillance. Either the template gains a `Death` value or this is
  documented as intended. Note `Other` is now an exact proxy for the death count.
- **`spec_date` is a data-entry stamp, not a collection date**, and is absent for
  every Record ID below 234. If no true collection date exists, the one-pager
  cannot carry a time axis.

### Worth raising, not blocking

- **`Other` is still 601 rows (22%)**, and it is a mix rather than a residue.
  Most of it has an obvious class the vocabulary lacks: analgesics/NSAIDs 215
  rows (acetaminophen alone 150), antiemetics 67, xanthines 59, beta blockers
  47, cardiovascular 37, diuretics 18. Accepted as-is for now. The highest-value
  addition would be an **adulterant** class — `quinine` (26), `xylazine` (12)
  and `levamisole/tetramisole` (3) total 41 rows and are markers of the illicit
  supply, currently invisible alongside Tylenol.
- **Treatment drugs are not distinguishable from exposures.** `naloxone` (53),
  `ondansetron` (32) and `metoclopramide` (8) are given in the ED. The template
  has no field for it, and the raw export's "medications administered prior to
  collection" column was dropped by `split_data.py` — pulling it back would
  settle this.

### Decided

- **Category spelling: the mapping wins.** The template conforms, not the data.
- **Categories are plural.** `Cathinone` -> `Cathinones`, matching the template
  and the rest of the vocabulary (16 existing rows changed).
- **New canonicals are lowercase**, following the file's dominant convention.
- **Unrecorded `Sample matrix` means urine** — no plasma was collected earlier in
  the study. 2031 rows / 248 patients filled. An **imputation**; see caveats.
- **The five `Flag` disagreements are all metabolites**, `4-anpp` included. Fixed
  in the mapping, so the pipeline's tie-break rule is retired.
- **Adopt `Anticonvulsants` / `Antihistamines` / `Anesthetics`** and backfill the
  existing drugs of those classes.

### Next steps

- **The one-pager headline and reporting period** need the study team's wording;
  both are placeholders today and `--period` is a CLI argument because
  `spec_date` cannot supply it.
- **Decide whether cocaine-type collapses to one bar.** `benzoylecgonine` (101)
  currently outranks `cocaine` (67) on the stimulant chart while being the same
  people, because it is cocaine's metabolite.
- Move the below-LOD / `Negative` / true-zero distinction into
  `clean_specimen.py` so v2 carries it and `sc_bac_below_lod` becomes derivable.
  Today all three are `0.000` and the split is unrecoverable downstream.
- Confirm `Left against medical advice` -> `Transferred` (agreed, 6 records).
  AMA is self-discharge, not a planned handoff, so `Transferred` mixes the two.
- The mapping must be applied **exactly once** — many canonicals are not
  themselves `Analyte` keys, so re-running a canonicalized file through it is not
  a no-op.
- Decide whether Record 190 (the one true negative) belongs in `validate.csv` as
  a row with a blank `analyte_name`, given the template wants a value on every
  row.

### Done

- **specimen_v3** — 28 columns reshaped onto the template's 11, including the
  10 -> 5 `discharge_status` collapse (IDs 331/343 resolve without a rule, 428
  via precedence) and the 6 -> 1 `race` and 3 -> 1 `ethnicity` collapses.
- **analyte_mapping_v3** — 35 missing substances added (Tier A 15, B 8, C 12),
  the `metaprolol` -> `metoprolol` merge, the study team's review folded into the
  scripts, and 43 rows backfilled into three new classes.
- **qtof_v3** — 100% analyte coverage with `unmatched_analytes` empty; drug
  categories and a real `metabolite_flag` attached; the 23 two-instance records
  merged by union-then-dedupe, dropping 117 duplicate rows without losing the 24
  canonicals that appear only on a second instance; `Sample matrix` fully
  populated.
- `Known_Variants` wired in as rung 4 (0 hits on this export; `F-alpha-PPP`
  excluded as ambiguous, and a `MIN_VARIANT_LEN` guard added after the study
  team's `3,4-...` entry would have split into junk).
- **validate_v1** — both sides joined into the template's 17 columns, 2709 rows
  over 373 patients, every value in an allowed set or an agreed pending
  addition. Four integrity guards raise rather than write something wrong.
- **The one-pager** — `output/onepager.html`, one page, self-contained, stdlib
  only, with patient-level counting and small-cell suppression enforced in the
  stats layer.

## Git

The data snapshots here are **git-ignored** — they are derived, reproducible from
`data/` via `src/`, and the CSVs are PHI-sensitive. Only this README is tracked,
so the directory and the convention live in the repo while the data stays local.
