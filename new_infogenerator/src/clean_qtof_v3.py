"""Canonicalize the QToF analyte free text and attach its drug classification.

``versioned/qtof_v2.csv`` holds two hand-typed, comma-separated analyte lists
per screen -- one per ion mode. This script reads v2, resolves every token
through ``versioned/analyte_mapping_v3.csv``, and writes
``versioned/qtof_v3.csv``::

    Record ID, Specimen number, Sample matrix                    (carried)
    analyte_name                                                 (canonical)
    analyte_group_1, analyte_group_2, analyte_group_3            (categories)
    metabolite_flag                                              (True/False)
    unmatched_analytes                                           (guard)

v2 is never modified and v3 is rewritten from scratch on every run, so the
script is deterministic and idempotent.

GRAIN: one row per (Record ID x canonical analyte), 2709 rows from 373 records.
The explode is forced rather than chosen -- a screen can list 21 analytes with
21 different classifications, and a single ``analyte_group_1`` cell cannot hold
them. This is also the shape the validation template defines.

The 23 records with two QToF rows are **merged**: their analytes are unioned and
deduplicated, dropping 117 rows where the same substance was recorded on both
instances. A union rather than "keep instance 1" is required -- 16 of the 23
records carry analytes on instance 2 that instance 1 lacks (24 canonicals in
total), so discarding an instance would lose real detections. Merging is only
safe *after* canonicalization, because the instances differ mainly by spelling.

``Repeat Instance`` is therefore dropped: with the instances merged it no longer
identifies anything. It remains in v2, which is immutable.

Records with no analytes still emit **one** row with the analyte fields blank
(6 records: 142, 143, 190, 365, 433, 447). Dropping them would remove those
patients from every denominator downstream, but they are NOT interchangeable:

  * Record 190 is a genuine negative -- qtof_v1 recorded "None" in both ion modes.
  * The other five have **completely empty** ion-mode cells in the raw export --
    no analytes and no sentinel. Their screen result was never entered, so they
    are *missing*, not negative. Three of the five (365, 433, 447) are MCW
    specimens with a recorded Sample matrix, which suggests the sample was run.

Treating the five as "no drugs detected" would dilute any "% of patients with X
detected" figure with unscreened patients. Exclude them from analyte-based
denominators or report them as missing -- see versioned/README.md.

How a token is resolved. Four indexes are tried in order and the first hit
wins; the rung that fired is counted and reported::

    1. exact ``Analyte`` key
    2. ``Analyte`` key, casefolded and whitespace-collapsed
    3. ``Canonical_Analyte``, likewise -- many canonicals are not themselves
       ``Analyte`` keys, so a correctly spelled canonical would otherwise miss
    4. ``Known_Variants``, likewise

Deliberately NOT done: fuzzy or nearest-neighbour matching. Drug names differ
by systematic one- and two-character affixes, so edit distance cannot tell a
typo from a distinct substance -- ``etonitazene`` and ``metonitazene`` are 96%
similar and are different synthetic opioids; ``chlordiazepoxide`` is 94%
similar to its own metabolite. Unresolved tokens go to ``unmatched_analytes``
for human review; they are never guessed and never silently dropped. Against
``analyte_mapping_v3`` that column is empty, and it is kept as the guard that
makes a future export's new junk visible.

The ``Analyte`` keys are also never edited. A misspelled key is what lets a
typo in the raw text find the right canonical, so normalization happens at
lookup time only.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

from clean_qtof import split_analytes

KEY_COL = "Record ID"
ION_COLS = ("Positive Ion Mode", "Negative Ion Mode")
# Repeat Instance is deliberately absent: the instances are merged, so it no
# longer identifies anything. Specimen number agrees across both instances of all
# 23 merged records; Sample matrix agrees for 22 and conflicts on Record 386.
CARRIED_COLS = ["Record ID", "Specimen number", "Sample matrix"]
GROUP_COLS = ["analyte_group_1", "analyte_group_2", "analyte_group_3"]
OUT_COLS = (
    CARRIED_COLS + ["analyte_name"] + GROUP_COLS + ["metabolite_flag", "unmatched_analytes"]
)

MAP_ANALYTE = "Analyte"
MAP_CANONICAL = "Canonical_Analyte"
MAP_VARIANTS = "Known_Variants"
MAP_CATEGORIES = ("Drug_Category_1", "Drug_Category_2", "Drug_Category_3")
MAP_FLAG = "Flag"

# "None detected" is a per-column sentinel, not an analyte. A screen can carry
# it in one ion mode while the other lists a dozen real detections, so it is
# stripped before lookup rather than treated as a screen-level flag.
SENTINEL = "none detected"

DELIMITER = ", "
_FORBIDDEN_IN_CANONICAL = (",", ";", "|", "\t")

RUNGS = ("exact key", "casefold key", "canonical self-key", "known variant")

# The template's rule: a substance with no Drug_Category_ at all is reported as
# 'Other' in analyte_group_1, not left blank.
OTHER = "Other"
METABOLITE = "Metabolite"

# Five canonicals used to have mapping rows that disagreed on Flag, so metabolite
# status depended on which spelling a technician typed. The study team resolved
# all five as metabolites (Heather, Aug 2026) and the answers are applied in
# clean_analyte_mapping.FLAG_FIXES, so the mapping is now self-consistent.
#
# Left False deliberately: a disagreement now means the mapping has regressed or
# gained a new defect, and that should stop the run rather than be papered over
# by a tie-break. Set True only as a temporary measure while a new conflict is
# being adjudicated.
FLAG_CONFLICT_METABOLITE_WINS = False

# The validation template's allowed analyte_group vocabulary, from the "details"
# sheet of data/validation_set.xlsx.
TEMPLATE_GROUPS = {
    "Amphetamines", "Antidepressants", "Antipsychotics", "Barbiturates",
    "Benzodiazepines", "Cannabinoids", "Cathinones", "CSNSStimulants", "Cocaine",
    "DissociativeAnesthetics", "Fentanyl", "Hallucinogens", "MOUD",
    "MuscleRelaxers", "Naloxone", "NarcoticAnalgesics", "NPSOpioids", OTHER,
}

# DECIDED (Aug 2026): where the mapping and the template spell a category
# differently, the mapping wins and the template gains the mapping's spelling.
# 'CSNS' is not a real abbreviation and reads as the template's own typo.
# So this stays empty; populate it only to make the data conform instead.
CATEGORY_RENAMES: dict[str, str] = {}

# Categories emitted that the validation template does not yet list. These are
# not errors -- they are the pending template edits implied by the decision
# above, and are reported as such so the request does not get forgotten. Any
# category outside both TEMPLATE_GROUPS and this set is a genuine violation.
TEMPLATE_ADDITIONS_REQUESTED = {
    "CNSStimulants",   # template currently spells this 'CSNSStimulants'
    # Decided Aug 2026: adopt the three classes the study team proposed and add
    # them to the template. Adopting them also meant backfilling the drugs of
    # those classes already in the vocabulary -- see
    # extend_analyte_mapping.CATEGORY_BACKFILL -- so the counts describe the
    # whole class rather than only the newest additions.
    "Anticonvulsants",
    "Antihistamines",
    "Anesthetics",
}

# Sample matrix is unrecorded for most screens. The study team confirmed
# (Aug 2026) that no plasma was collected during the earlier part of the study,
# so an unrecorded matrix means urine. Filling it is what lets wslh_matrix
# satisfy the template, which allows only Plasma | Urine and has no blank.
#
# This is an IMPUTATION, not a measurement: it turns 249 of 373 patients into
# "urine" by inference. See versioned/README.md -- a urine-vs-plasma comparison
# is mostly assumption on the urine side. Set to None to leave blanks blank.
MATRIX_BLANK_FILL = "urine"

# Record 386's two instances report different matrices (plasma vs urine) and
# nothing in the data breaks the tie. It resolves to MATRIX_BLANK_FILL, which is
# also one of its two recorded values, so it lands where a blank would. Reported
# every run rather than resolved quietly.
MATRIX_CONFLICT_TO_FILL = True


def normalize(text: str) -> str:
    """Lookup key: whitespace-collapsed and casefolded. Applied to both sides."""
    return " ".join(text.split()).lower()


class Index:
    """Lookup rungs plus per-canonical classification, built from the mapping."""

    def __init__(self, mapping: list[dict[str, str]]) -> None:
        self.exact: dict[str, str] = {}
        self.casefold: dict[str, str] = {}
        self.canonical: dict[str, str] = {}
        self.variant: dict[str, str] = {}
        self.ambiguous_variants: dict[str, list[str]] = {}
        self.traits: dict[str, tuple[tuple[str, str, str], bool]] = {}
        self.flag_conflicts: dict[str, list[str]] = {}
        self.category_conflicts: dict[str, list[tuple[str, ...]]] = {}

        variants: defaultdict[str, set[str]] = defaultdict(set)
        seen: defaultdict[str, set[tuple[str, ...]]] = defaultdict(set)
        for row in mapping:
            analyte = row[MAP_ANALYTE].strip()
            canonical = row[MAP_CANONICAL].strip()
            if not analyte or not canonical:
                raise SystemExit(f"mapping row with empty key or canonical: {row}")
            self.exact[analyte] = canonical
            self.casefold[normalize(analyte)] = canonical
            self.canonical[normalize(canonical)] = canonical
            seen[canonical].add(
                tuple(row[c].strip() for c in MAP_CATEGORIES)
                + (row[MAP_FLAG].strip(),)
            )
            for variant in row[MAP_VARIANTS].split(","):
                if variant.strip():
                    variants[normalize(variant)].add(canonical)

        # A variant listed against two different canonicals cannot resolve --
        # 'F-alpha-PPP' is shared by the 3'-fluoro and 4'-fluoro compounds.
        # Dropping it surfaces those tokens for review instead of assigning them
        # to whichever row happened to be read first.
        for variant, canonicals in variants.items():
            if len(canonicals) == 1:
                self.variant[variant] = next(iter(canonicals))
            else:
                self.ambiguous_variants[variant] = sorted(canonicals)

        for canonical, rows in seen.items():
            self.traits[canonical] = self._resolve_traits(canonical, rows)

        self._check_collisions(mapping)
        self._check_delimiter()

    def _resolve_traits(
        self, canonical: str, rows: set[tuple[str, ...]]
    ) -> tuple[tuple[str, str, str], bool]:
        """Collapse a canonical's rows into one (categories, is_metabolite)."""
        categories = {r[:3] for r in rows}
        if len(categories) > 1:
            self.category_conflicts[canonical] = sorted(categories)
            raise SystemExit(
                f"{canonical!r} has rows with different categories, so there is "
                f"no basis for choosing: {sorted(categories)}"
            )
        flags = {r[3] for r in rows}
        if len(flags) > 1:
            self.flag_conflicts[canonical] = sorted(flags)
            if not FLAG_CONFLICT_METABOLITE_WINS:
                raise SystemExit(
                    f"{canonical!r} has rows disagreeing on Flag: {sorted(flags)}"
                )
        return next(iter(categories)), METABOLITE in flags

    def _check_collisions(self, mapping: list[dict[str, str]]) -> None:
        """Normalization must never merge two distinct substances."""
        for label, keyfn in (
            ("casefold key", lambda r: normalize(r[MAP_ANALYTE])),
            ("canonical self-key", lambda r: normalize(r[MAP_CANONICAL])),
        ):
            seen: defaultdict[str, set[str]] = defaultdict(set)
            for row in mapping:
                seen[keyfn(row)].add(row[MAP_CANONICAL].strip())
            clashes = {k: sorted(v) for k, v in seen.items() if len(v) > 1}
            if clashes:
                raise SystemExit(
                    f"{label}: normalization merges distinct canonicals: {clashes}"
                )

    def _check_delimiter(self) -> None:
        offenders = [
            c
            for c in self.canonical.values()
            if any(ch in c for ch in _FORBIDDEN_IN_CANONICAL)
        ]
        if offenders:
            raise SystemExit(
                f"canonical(s) contain a delimiter character, so "
                f"{DELIMITER!r}-joined cells could not be split back: {offenders}"
            )

    def resolve(self, token: str) -> tuple[str | None, str | None]:
        """Return (canonical, rung) or (None, None) if nothing matches."""
        if token in self.exact:
            return self.exact[token], "exact key"
        key = normalize(token)
        if key in self.casefold:
            return self.casefold[key], "casefold key"
        if key in self.canonical:
            return self.canonical[key], "canonical self-key"
        if key in self.variant:
            return self.variant[key], "known variant"
        return None, None

    def groups(self, canonical: str) -> list[str]:
        """The three analyte_group values, with the template's 'Other' fill."""
        categories, _ = self.traits[canonical]
        renamed = [CATEGORY_RENAMES.get(c, c) for c in categories]
        if not any(renamed):
            return [OTHER, "", ""]
        return renamed

    def is_metabolite(self, canonical: str) -> bool:
        return self.traits[canonical][1]


def read_rows(source: Path) -> tuple[list[str], list[dict[str, str]]]:
    with source.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def _merge_carried(
    record_id: str,
    record_screens: list[dict[str, str]],
    matrix_conflicts: dict[str, list[str]],
) -> dict[str, str]:
    """One value per carried column for a record, across its instance(s).

    ``Specimen number`` must agree -- it does for all 23 merged records, and a
    disagreement would mean the instances describe different samples, which is
    not something to paper over.

    ``Sample matrix`` takes the single non-blank value if there is one, and
    MATRIX_BLANK_FILL otherwise. Record 386 reports plasma on one instance and
    urine on the other; it resolves to the fill value and is reported.
    """
    merged = {KEY_COL: record_id}

    specimens = {s["Specimen number"] for s in record_screens}
    if len(specimens) > 1:
        raise SystemExit(
            f"Record {record_id}: instances disagree on Specimen number "
            f"{sorted(specimens)} -- they may not be the same sample"
        )
    merged["Specimen number"] = next(iter(specimens))

    matrices = {s["Sample matrix"] for s in record_screens if s["Sample matrix"]}
    if len(matrices) > 1:
        matrix_conflicts[record_id] = sorted(matrices)
        if not MATRIX_CONFLICT_TO_FILL:
            raise SystemExit(
                f"Record {record_id}: instances disagree on Sample matrix "
                f"{sorted(matrices)}"
            )
        merged["Sample matrix"] = MATRIX_BLANK_FILL or ""
    else:
        merged["Sample matrix"] = next(iter(matrices), MATRIX_BLANK_FILL or "")
    return merged


def transform(source: Path, mapping_path: Path, dest: Path) -> None:
    fieldnames, screens = read_rows(source)
    missing = [c for c in CARRIED_COLS + list(ION_COLS) if c not in fieldnames]
    if missing:
        raise SystemExit(f"{source.name} is missing expected column(s): {missing}")

    with mapping_path.open(newline="", encoding="utf-8") as fh:
        mapping = list(csv.DictReader(fh))
    index = Index(mapping)

    rung_hits: Counter[str] = Counter()
    unmatched: Counter[str] = Counter()
    flag_applied: Counter[str] = Counter()
    sentinels = 0
    collapsed = 0
    merged_records: dict[str, int] = {}
    matrix_conflicts: dict[str, list[str]] = {}
    empty_records: list[str] = []
    out_rows: list[dict[str, str]] = []

    # Group the screens by Record ID so the 23 two-instance records merge. Order
    # follows first appearance in v2, so the output stays comparable.
    grouped: dict[str, list[dict[str, str]]] = {}
    for screen in screens:
        grouped.setdefault(screen[KEY_COL], []).append(screen)

    for record_id, record_screens in grouped.items():
        if len(record_screens) > 1:
            merged_records[record_id] = len(record_screens)

        resolved: set[str] = set()
        unresolved: set[str] = set()
        per_record_hits = 0
        for screen in record_screens:
            for column in ION_COLS:
                for raw in split_analytes(screen[column]):
                    token = raw.strip()
                    if not token:
                        continue
                    if token.lower() == SENTINEL:
                        sentinels += 1
                        continue
                    canonical, rung = index.resolve(token)
                    if canonical is None:
                        unresolved.add(token)
                        unmatched[token] += 1
                    else:
                        resolved.add(canonical)
                        rung_hits[rung] += 1
                        per_record_hits += 1

        # One row per surviving canonical. The drop covers three overlapping
        # causes: a substance ionizing in both modes, case variants collapsing
        # onto one name, and the same substance recorded on both instances.
        deduped = sorted(resolved, key=str.lower)
        collapsed += per_record_hits - len(deduped)

        carried = _merge_carried(record_id, record_screens, matrix_conflicts)
        unmatched_cell = DELIMITER.join(sorted(unresolved, key=str.lower))

        if not deduped:
            empty_records.append(record_id)
            row = dict(carried)
            row["analyte_name"] = ""
            for col in GROUP_COLS:
                row[col] = ""
            row["metabolite_flag"] = ""
            row["unmatched_analytes"] = unmatched_cell
            out_rows.append(row)
            continue

        for canonical in deduped:
            if canonical in index.flag_conflicts:
                flag_applied[canonical] += 1
            row = dict(carried)
            row["analyte_name"] = canonical
            for col, value in zip(GROUP_COLS, index.groups(canonical)):
                row[col] = value
            row["metabolite_flag"] = str(index.is_metabolite(canonical))
            row["unmatched_analytes"] = unmatched_cell
            out_rows.append(row)

    report(
        screens, grouped, out_rows, index, rung_hits, unmatched, sentinels,
        collapsed, empty_records, flag_applied, merged_records, matrix_conflicts,
    )

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUT_COLS)
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"\n  -> {dest}")


def report(
    screens: list[dict[str, str]],
    grouped: dict[str, list[dict[str, str]]],
    out_rows: list[dict[str, str]],
    index: Index,
    rung_hits: Counter[str],
    unmatched: Counter[str],
    sentinels: int,
    collapsed: int,
    empty_records: list[str],
    flag_applied: Counter[str],
    merged_records: dict[str, int],
    matrix_conflicts: dict[str, list[str]],
) -> None:
    print(f"read  {len(screens)} screens over {len(grouped)} record(s)")
    print(f"wrote {len(out_rows)} rows x {len(OUT_COLS)} columns "
          f"(one row per Record ID x analyte)")

    print(f"\ninstance merge: {len(merged_records)} record(s) had 2 QToF rows, "
          f"now one row set each")
    if merged_records:
        print(f"  {sorted(merged_records, key=int)}")
    if matrix_conflicts:
        print(f"  Sample matrix conflict(s) -> {MATRIX_BLANK_FILL or 'blank'}:")
        for record_id, values in matrix_conflicts.items():
            print(f"    Record {record_id}: {values}")
    if MATRIX_BLANK_FILL:
        filled = sum(1 for r in out_rows if not any(
            s2["Sample matrix"] for s2 in grouped[r[KEY_COL]]))
        pats = len({r[KEY_COL] for r in out_rows if not any(
            s2["Sample matrix"] for s2 in grouped[r[KEY_COL]])})
        counts = Counter(r["Sample matrix"] for r in out_rows)
        print(f"\nSample matrix: unrecorded -> {MATRIX_BLANK_FILL!r} on {filled} row(s) "
              f"/ {pats} patient(s) — an IMPUTATION, not a measurement")
        for value, count in counts.most_common():
            print(f"  {count:5d}  {value or '(blank)'}")
    print(f"\nmapping index: {len(index.exact)} keys -> "
          f"{len(set(index.exact.values()))} canonicals, "
          f"{len(index.variant)} usable variant(s)")
    if index.ambiguous_variants:
        print("  variants dropped as ambiguous (listed against >1 canonical):")
        for variant, canonicals in index.ambiguous_variants.items():
            print(f"    {variant!r} -> {canonicals}")

    total = sum(rung_hits.values()) + sum(unmatched.values())
    print(f"\n'{SENTINEL}' sentinel stripped: {sentinels}")
    print(f"analyte tokens: {total}")
    running = 0
    for rung in RUNGS:
        running += rung_hits[rung]
        print(f"  {rung:20s} {rung_hits[rung]:5d}   cumulative {running:5d}"
              f"  {running / total:7.2%}")
    print(f"  {'UNMATCHED':20s} {sum(unmatched.values()):5d}"
          f"   {len(unmatched)} distinct token(s)")
    for token, count in sorted(unmatched.items(), key=lambda kv: (-kv[1], kv[0].lower())):
        print(f"      {count:3d}  {token}")

    analyte_rows = [r for r in out_rows if r["analyte_name"]]
    canonicals = {r["analyte_name"] for r in analyte_rows}
    print(f"\nanalyte_name: {len(analyte_rows)} row(s), "
          f"{len(canonicals)} distinct canonical(s)")
    print(f"  duplicate detections collapsed: {collapsed} "
          f"(both ion modes, case variants, and both instances)")
    pairs = {(r[KEY_COL], r["analyte_name"]) for r in analyte_rows}
    if len(pairs) != len(analyte_rows):
        raise SystemExit(
            f"{len(analyte_rows) - len(pairs)} duplicate (Record ID, canonical) "
            f"row(s) survived the merge"
        )
    print(f"  every row is a unique (Record ID, canonical) pair")
    print(f"  records with no analyte: {len(empty_records)} -> Record ID(s) "
          f"{sorted(set(empty_records), key=int)} (one blank row each)")

    def _note(value: str) -> str:
        if value in TEMPLATE_GROUPS:
            return ""
        if value in TEMPLATE_ADDITIONS_REQUESTED:
            return "   <-- template addition requested"
        return "   *** NOT a valid template value"

    print("\nanalyte_group_1:")
    for value, count in Counter(r["analyte_group_1"] for r in analyte_rows).most_common():
        print(f"  {count:5d}  {value}{_note(value)}")
    for level in GROUP_COLS[1:]:
        counts = Counter(r[level] for r in analyte_rows if r[level])
        print(f"\n{level}: {sum(counts.values())} row(s) populated")
        for value, count in counts.most_common():
            print(f"  {count:5d}  {value}{_note(value)}")

    emitted = {r[c] for r in analyte_rows for c in GROUP_COLS if r[c]}
    pending = sorted(emitted & TEMPLATE_ADDITIONS_REQUESTED)
    if pending:
        affected = sum(
            1 for r in analyte_rows if any(r[c] in pending for c in GROUP_COLS)
        )
        print(f"\npending template additions: {pending} on {affected} row(s). The "
              f"mapping's spelling is kept by decision; the template needs these "
              f"values added.")
    invalid = sorted(emitted - TEMPLATE_GROUPS - TEMPLATE_ADDITIONS_REQUESTED)
    if invalid:
        affected = sum(
            1 for r in analyte_rows if any(r[c] in invalid for c in GROUP_COLS)
        )
        raise SystemExit(
            f"{len(invalid)} category value(s) are in neither the template nor the "
            f"agreed additions, on {affected} row(s): {invalid}"
        )

    flags = Counter(r["metabolite_flag"] for r in analyte_rows)
    print(f"\nmetabolite_flag: True {flags['True']}, False {flags['False']}")
    if flag_applied:
        print(f"  'Metabolite wins' applied to {len(flag_applied)} canonical(s) whose "
              f"mapping rows disagree, on {sum(flag_applied.values())} row(s):")
        for canonical, count in flag_applied.most_common():
            print(f"    {count:4d}  {canonical}  {index.flag_conflicts[canonical]}")
        print("    Stand-in pending the study team; fix the mapping to stop it firing.")

    rows_with = sum(1 for r in out_rows if r["unmatched_analytes"])
    print(f"\nunmatched_analytes: {rows_with} row(s) populated")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root / "versioned/qtof_v2.csv")
    parser.add_argument(
        "--mapping", type=Path, default=root / "versioned/analyte_mapping_v3.csv"
    )
    parser.add_argument("--dest", type=Path, default=root / "versioned/qtof_v3.csv")
    args = parser.parse_args()

    for path in (args.source, args.mapping):
        if not path.exists():
            raise SystemExit(f"File not found: {path}")
    transform(args.source, args.mapping, args.dest)


if __name__ == "__main__":
    main()
