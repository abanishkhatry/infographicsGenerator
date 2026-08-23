"""Extend the analyte mapping with the substances the QToF data needs.

``versioned/analyte_mapping_v2.csv`` is the study team's vocabulary with its
spelling defects repaired. It is missing 35 substances that appear in the QToF
free text, which is why ``qtof_v3`` leaves 42 detections in
``unmatched_analytes``. This script reads v2, appends the missing rows, and
writes ``versioned/analyte_mapping_v3.{csv,xlsx}``.

v2 is never modified and v3 is rebuilt from scratch on every run.

The additions come in three tiers, which differ in *what* had to be decided:

  Tier A (15 rows) -- the substance is already in the vocabulary and only this
      spelling was missing. Each new row is an ``Analyte`` key pointing at an
      existing ``Canonical_Analyte``; its categories and Flag are **inherited
      from the target canonical** rather than retyped, so a new key can never
      disagree with the rows already describing that substance.
      Confirmed by the study team (Heather, Aug 2026), including that the
      ``(+/-)`` stereochemistry prefix may be dropped and that
      ``chloroquine-M (hydroxy metabolite)`` is hydroxychloroquine.

  Tier B (8 rows) -- genuinely new substances that merely *resemble* something
      already mapped. The study team confirmed these are NOT the same analytes
      and supplied every canonical name, category and variant
      (see data/Analyte_Category_Mapping_reviewed_2026-08.xlsx). Getting these
      wrong is the expensive failure mode, so each row records what it must not
      be confused with.

  Tier C (12 rows) -- absent from the vocabulary entirely. Canonical names and
      Known_Variants are the study team's, transcribed verbatim.

Beyond the new rows, this script also **backfills categories** onto existing
canonicals -- see ``CATEGORY_BACKFILL``. The study team proposed three classes
the validation template does not list (Anticonvulsants, Antihistamines,
Anesthetics) and the decision was to adopt them. Adopting a class requires
classifying the drugs of that class already in the vocabulary, or a chart would
report the two newest additions as if they were the whole class.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

from clean_analyte_mapping import write_workbook

FIELDS = [
    "Analyte",
    "Canonical_Analyte",
    "Known_Variants",
    "Drug_Category_1",
    "Drug_Category_2",
    "Drug_Category_3",
    "Flag",
]
CATEGORY_COLS = ("Drug_Category_1", "Drug_Category_2", "Drug_Category_3")

# Shortest believable abbreviation in Known_Variants. Guards against a variant
# that contains the comma delimiter splitting into junk tokens.
MIN_VARIANT_LEN = 3

# --- Tier A: new spelling -> canonical that already exists ------------------
# Categories and Flag are copied from the target's existing row(s).
TIER_A = [
    ("desmethyl fluoxetine", "fluoxetine-desmethyl"),
    ("burprenorphine", "buprenorphine"),
    ("burprenorphine-M (6-glucuronide metabolite)", "buprenorphine-6-glucuronide"),
    ("chloroquine-M (hydroxy metabolite)", "hydroxychloroquine"),
    ("chlordiazepoxide-M (desmethyl metabolite)", "chlordiazepoxide-metabolite"),
    ("THC-M (carboxy-THC-glucuronide)", "delta-9-thc-carboxy-glucuronide"),
    ("meta methyl acetyle fentanyl", "meta methyl acetyl fentanyl"),
    ("meat-methyl Acetyl fentanyl", "meta methyl acetyl fentanyl"),
    ("cis-isofentanyl", "(±)-cis-isofentanyl"),
    ("7-aminoclomazepam", "clonazepam-7-amino"),
    ("certirizine", "cetirizine"),
    ("doxyamine", "doxylamine"),
    ("olanzaprine", "olanzapine"),
    ("metprolol", "metoprolol"),
    ("Metorpolol", "metoprolol"),
]

# --- Tier B: new substances with a dangerous look-alike ---------------------
# (Analyte, Canonical_Analyte, Known_Variants, cat1, cat2, cat3, Flag,
#  must-not-be-confused-with)
#
# Categories and variants are the study team's (Heather, Aug 2026; see
# data/Analyte_Category_Mapping_reviewed_2026-08.xlsx).
#
# Canonicals are lowercased rather than taken verbatim. She Title-Cased the new
# entries, but the file's canonicals are overwhelmingly lowercase and the
# remaining capitals are pre-existing names. Decided Aug 2026: new entries follow
# the lowercase majority, so 'cathinone' sits beside 'cathine', 'mda' beside
# 'mdma', and 'valproic acid' beside 'canrenoic acid'. Lookups are
# case-insensitive either way; this is about anything that groups or sorts on
# canonical name.
#
# MDA is only the second substance in the file to use Drug_Category_3, mirroring
# MDMA. Its variant is written '3-4-methylenedioxyamphetamine' rather than
# '3,4-...': Known_Variants is comma-delimited, so the real name would split into
# a junk one-character token '3' plus the remainder.
TIER_B = [
    ("DL-cathinone", "cathinone", "", "CNSStimulants", "Cathinones", "", "",
     "cathine -- which is norpseudoephedrine, a different compound"),
    ("Chloroquine", "chloroquine", "", "", "", "", "",
     "hydroxychloroquine -- this is the parent drug"),
    ("N-piperidinyl etonitazene", "n-piperidinyl etonitazene", "",
     "NarcoticAnalgesics", "NPSOpioids", "", "",
     "n-piperidinyl 4-hydroxy nitazene -- a different nitazene"),
    ("etonitazene", "etonitazene", "", "NarcoticAnalgesics", "NPSOpioids", "", "",
     "metonitazene -- 96% similar string, different synthetic opioid"),
    ("chlordiazepoxide", "chlordiazepoxide", "", "Benzodiazepines", "", "", "",
     "chlordiazepoxide-metabolite -- this is the parent drug"),
    ("MDA", "mda", "3-4-methylenedioxyamphetamine",
     "CNSStimulants", "Amphetamines", "Hallucinogens", "",
     "mdma -- a different amphetamine"),
    ("meta-methyl fentanyl", "meta-methyl fentanyl", "",
     "NarcoticAnalgesics", "NPSOpioids", "", "",
     "meta-methyl acetyl fentanyl -- a different analog, note the acetyl"),
    ("mirtazapine-n-desmethyl", "mirtazapine-n-desmethyl", "",
     "Antidepressants", "", "", "Metabolite",
     "olanzapine-n-desmethyl -- shares only the -n-desmethyl suffix"),
]

# --- Tier C: absent entirely, supplied by the study team --------------------
# (Analyte, Canonical_Analyte, Known_Variants). Categories for these are set in
# CATEGORY_BACKFILL below, alongside the existing drugs of the same classes.
TIER_C = [
    ("pantoprazole", "pantoprazole", ""),
    ("Melatonin", "melatonin", ""),
    ("Paroxetine", "paroxetine", ""),
    ("donepezil", "donepezil", ""),
    ("lansoprazole", "lansoprazole", "Prevacid"),
    ("eslicarbazepine", "eslicarbazepine", "Eslicarbazepine acetate"),
    ("valproic acid", "valproic acid", "valproate"),
    ("chlorcyclizine", "chlorcyclizine", ""),
    ("Propofol", "propofol", ""),
    ("Yohimbine", "yohimbine", ""),
    ("Pravastatin", "pravastatin", ""),
    ("rosuvastatin", "rosuvastatin", ""),
]

# --- Category backfill: canonical -> (cat1, cat2) ---------------------------
#
# The study team proposed Anticonvulsants (eslicarbazepine, valproic acid),
# Antihistamines (chlorcyclizine) and Anesthetics (propofol). Decided Aug 2026:
# adopt all three as new categories, and add them to the validation template.
#
# Adopting them REQUIRES backfilling the drugs of those classes already in the
# vocabulary. The original file was built around drugs of abuse, so incidental
# clinical medications were left uncategorised -- which means without a backfill
# a chart would read "Anticonvulsants: 2 patients" while nine anticonvulsants sat
# in 'Other'. That is worse than leaving them uncategorised, because it looks
# like real data.
#
# The assignments below are a first pass for study-team review, not their ruling.
# Judgment calls worth checking:
#   * gabapentin (59 patients) and pregabalin (7) are gabapentinoids -- treated
#     as Anticonvulsants here, though both are also prescribed for neuropathic
#     pain. gabapentin alone is over half the class.
#   * lidocaine (37) and its metabolites are local anaesthetics; lidocaine is
#     also an antiarrhythmic, and in this cohort is more likely clinical than
#     ingested.
#   * promethazine is an antihistamine, an antiemetic and a phenothiazine.
#   * Metabolites inherit their parent's class.
CATEGORY_BACKFILL = {
    # --- Anticonvulsants -----------------------------------------------------
    "carbamazepine": ("Anticonvulsants", ""),
    "oxycarbazepine": ("Anticonvulsants", ""),
    "lamotrigine": ("Anticonvulsants", ""),
    "levetiracetam": ("Anticonvulsants", ""),
    "topiramate": ("Anticonvulsants", ""),
    "phenytoin": ("Anticonvulsants", ""),
    "zonisamide": ("Anticonvulsants", ""),
    "lacosamide": ("Anticonvulsants", ""),
    "gabapentin": ("Anticonvulsants", ""),
    "pregabalin": ("Anticonvulsants", ""),
    "valproic acid": ("Anticonvulsants", ""),      # study team
    "eslicarbazepine": ("Anticonvulsants", ""),    # study team
    # --- Antihistamines ------------------------------------------------------
    "diphenhydramine": ("Antihistamines", ""),
    "hydroxyzine": ("Antihistamines", ""),
    "cetirizine": ("Antihistamines", ""),
    "cetirizine-metabolite": ("Antihistamines", ""),
    "cetirizine-n-desalkyl": ("Antihistamines", ""),
    "doxylamine": ("Antihistamines", ""),
    "chlorpheniramine": ("Antihistamines", ""),
    "chlorpheniramine-desmethyl": ("Antihistamines", ""),
    "chlorpheniramine-n-oxide": ("Antihistamines", ""),
    "promethazine": ("Antihistamines", ""),
    "promethazine-sulphoxide": ("Antihistamines", ""),
    "chlorcyclizine": ("Antihistamines", ""),      # study team
    # --- Anesthetics ---------------------------------------------------------
    "propofol": ("Anesthetics", ""),               # study team
    "etomidate": ("Anesthetics", ""),
    "lidocaine": ("Anesthetics", ""),
    "lidocaine-n-deethylated": ("Anesthetics", ""),
    "Glycinexylidide": ("Anesthetics", ""),        # a lidocaine metabolite
    "bupivacaine/levobupivacaine": ("Anesthetics", ""),
    # --- already-allowed category the study team supplied --------------------
    "paroxetine": ("Antidepressants", ""),
}


def make_row(
    analyte: str,
    canonical: str,
    variants: str = "",
    cat1: str = "",
    cat2: str = "",
    cat3: str = "",
    flag: str = "",
) -> dict[str, str]:
    return {
        "Analyte": analyte,
        "Canonical_Analyte": canonical,
        "Known_Variants": variants,
        "Drug_Category_1": cat1,
        "Drug_Category_2": cat2,
        "Drug_Category_3": cat3,
        "Flag": flag,
    }


def build_tier_a(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[str]]:
    """One new key per entry, inheriting the target canonical's categories/Flag.

    Raises if a target canonical does not exist, or if its existing rows already
    disagree about categories or Flag -- there would be nothing to inherit.
    """
    by_canonical: defaultdict[str, set[tuple[str, ...]]] = defaultdict(set)
    for row in rows:
        by_canonical[row["Canonical_Analyte"]].add(
            tuple(row[c] for c in CATEGORY_COLS) + (row["Flag"],)
        )

    new: list[dict[str, str]] = []
    report: list[str] = []
    for analyte, canonical in TIER_A:
        traits = by_canonical.get(canonical)
        if traits is None:
            raise SystemExit(
                f"Tier A target canonical not found in the mapping: {canonical!r}"
            )
        if len(traits) > 1:
            raise SystemExit(
                f"Tier A target {canonical!r} has rows that disagree on "
                f"categories/Flag, so there is nothing to inherit: {sorted(traits)}"
            )
        cats = next(iter(traits))
        row = make_row(analyte, canonical, flag=cats[3])
        for col, value in zip(CATEGORY_COLS, cats):
            row[col] = value
        new.append(row)
        inherited = [v for v in cats[:3] if v] or ["(no category)"]
        report.append(
            f"  {analyte!r}\n      -> {canonical!r}  inherited {inherited}"
            + (f" Flag={cats[3]!r}" if cats[3] else "")
        )
    return new, report


def extend(source: Path, stem: Path) -> None:
    with source.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if fieldnames != FIELDS:
        raise SystemExit(f"{source.name}: unexpected columns {fieldnames}")
    print(f"Read {len(rows)} rows from {source.name}")

    tier_a, a_report = build_tier_a(rows)
    tier_b = [make_row(a, c, v, c1, c2, c3, f) for a, c, v, c1, c2, c3, f, _ in TIER_B]
    tier_c = [make_row(a, c, v) for a, c, v in TIER_C]
    additions = tier_a + tier_b + tier_c

    existing = {r["Analyte"] for r in rows}
    existing_ci = {r["Analyte"].lower() for r in rows}
    for row in additions:
        if row["Analyte"] in existing:
            raise SystemExit(f"Analyte key already present: {row['Analyte']!r}")
        if row["Analyte"].lower() in existing_ci:
            raise SystemExit(
                f"Analyte key collides case-insensitively with an existing key: "
                f"{row['Analyte']!r}"
            )
    dupes = [k for k, n in Counter(r["Analyte"] for r in additions).items() if n > 1]
    if dupes:
        raise SystemExit(f"duplicate Analyte key(s) among the additions: {dupes}")


    out = rows + additions

    # Category backfill. Only fills rows that have no category at all, so a
    # study-team assignment is never overwritten; raises if a target canonical
    # is absent, so the list cannot rot silently.
    backfilled: Counter[str] = Counter()
    known = {r["Canonical_Analyte"] for r in out}
    missing_targets = sorted(set(CATEGORY_BACKFILL) - known)
    if missing_targets:
        raise SystemExit(
            f"CATEGORY_BACKFILL names canonical(s) not in the mapping: "
            f"{missing_targets}"
        )
    for row in out:
        assignment = CATEGORY_BACKFILL.get(row["Canonical_Analyte"])
        if assignment and not any(row[c] for c in CATEGORY_COLS):
            row["Drug_Category_1"], row["Drug_Category_2"] = assignment
            backfilled[assignment[0]] += 1

    # Known_Variants is comma-delimited, so a variant containing a comma splits
    # into junk. '3,4-methylenedioxyamphetamine' would yield a one-character
    # token '3' that then matches any stray '3' in the QToF free text. Checked
    # across the whole output, not just the additions.
    for row in out:
        for variant in row["Known_Variants"].split(","):
            token = variant.strip()
            if token and len(token) < MIN_VARIANT_LEN:
                raise SystemExit(
                    f"{row['Analyte']!r}: Known_Variants {row['Known_Variants']!r} "
                    f"splits to a {len(token)}-character token {token!r}. A variant "
                    f"containing a comma must be rewritten without one."
                )

    # No addition may introduce a canonical whose rows disagree on Flag. The
    # five pre-existing disagreements are tolerated and reported, not silently
    # inherited into new rows.
    flags: defaultdict[str, set[str]] = defaultdict(set)
    for row in out:
        flags[row["Canonical_Analyte"]].add(row["Flag"])
    before: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        before[row["Canonical_Analyte"]].add(row["Flag"])
    introduced = {
        k: sorted(v) for k, v in flags.items() if len(v) > 1 and len(before.get(k, set())) <= 1
    }
    if introduced:
        raise SystemExit(f"additions introduce new Flag disagreement(s): {introduced}")

    report(rows, additions, a_report, out, before, backfilled)

    stem.parent.mkdir(parents=True, exist_ok=True)
    csv_path = stem.with_suffix(".csv")
    xlsx_path = stem.with_suffix(".xlsx")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(out)
    write_workbook(xlsx_path, FIELDS, out)
    print(f"\n  -> {csv_path}")
    print(f"  -> {xlsx_path}")


def report(
    rows: list[dict[str, str]],
    additions: list[dict[str, str]],
    a_report: list[str],
    out: list[dict[str, str]],
    before: dict[str, set[str]],
    backfilled: Counter[str],
) -> None:
    print(f"\nTIER A -- {len(TIER_A)} new spelling(s) of an existing substance")
    for line in a_report:
        print(line)

    print(f"\nTIER B -- {len(TIER_B)} new substance(s) with a look-alike already mapped")
    for analyte, canonical, variants, c1, c2, c3, flag, confusable in TIER_B:
        cats = [c for c in (c1, c2, c3) if c] or ["(no category)"]
        extra = f"  variants={variants!r}" if variants else ""
        extra += f"  Flag={flag!r}" if flag else ""
        print(f"  {analyte!r} -> {canonical!r}  {cats}{extra}")
        print(f"      NOT: {confusable}")

    print(f"\nTIER C -- {len(TIER_C)} substance(s) absent from the vocabulary")
    for analyte, canonical, variants in TIER_C:
        extra = f"  variants={variants!r}" if variants else ""
        assignment = CATEGORY_BACKFILL.get(canonical)
        cat = f"  [{assignment[0]}]" if assignment else "  [Other]"
        print(f"  {analyte!r} -> {canonical!r}{cat}{extra}")

    print(f"\nCATEGORY BACKFILL -- {sum(backfilled.values())} row(s) given a class "
          f"that had none")
    by_category: dict[str, list[str]] = {}
    for canonical, (cat1, _) in CATEGORY_BACKFILL.items():
        by_category.setdefault(cat1, []).append(canonical)
    for category, count in backfilled.most_common():
        drugs = sorted(by_category.get(category, []))
        print(f"  {category:18s} {count:3d} row(s), {len(drugs)} substance(s): {drugs}")

    canonicals_before = {r["Canonical_Analyte"] for r in rows}
    canonicals_after = {r["Canonical_Analyte"] for r in out}
    print(f"\nrows      {len(rows)} -> {len(out)}  (+{len(additions)})")
    print(f"canonicals {len(canonicals_before)} -> {len(canonicals_after)} "
          f"(+{len(canonicals_after - canonicals_before)} new substance(s))")

    capitalised = sorted(c for c in canonicals_after if c != c.lower())
    print(f"\ncasing: {len(canonicals_after) - len(capitalised)} of "
          f"{len(canonicals_after)} canonicals are lowercase, per the file's "
          f"dominant convention. The {len(capitalised)} with capitals are all "
          f"pre-existing: {capitalised}")

    unresolved = {k: sorted(v) for k, v in before.items() if len(v) > 1}
    if unresolved:
        print(f"\nFlag disagreements carried in from v2: {len(unresolved)}")
        for canonical, values in sorted(unresolved.items()):
            print(f"  {canonical!r}: {values}")
        print("  Fix these in clean_analyte_mapping.FLAG_FIXES -- downstream code "
              "should be able to read Flag directly.")
    else:
        print("\nFlag: every canonical agrees with itself (0 disagreements). "
              "Downstream code can read Flag directly.")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=root / "versioned/analyte_mapping_v2.csv"
    )
    parser.add_argument(
        "--stem", type=Path, default=root / "versioned/analyte_mapping_v3"
    )
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"File not found: {args.source}")
    extend(args.source, args.stem)


if __name__ == "__main__":
    main()
