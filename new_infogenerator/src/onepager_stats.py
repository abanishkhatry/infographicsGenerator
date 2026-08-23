"""Compute every figure the one-pager shows, from ``versioned/validate_v1.csv``.

Kept separate from rendering on purpose: the study team can sign off on the
numbers before anyone argues about layout, and the layout can be re-cut without
touching the arithmetic. Every figure on the page traces back to this module.

**All counts are patient-level.** validate_v1.csv is one row per
(patient x analyte), so a patient with 21 detections occupies 21 rows. Counting
rows would weight each patient by how many substances they screened positive
for, which tracks severity -- the bias is not random, it inflates the sickest
patients. Every helper here counts distinct ``record_id``.

Denominators are not uniform and each figure carries its own::

    demographics            373 patients
    hospital_stay_length    317   (56 not recorded)
    bac                     149   (224 not tested or not recorded)
    spec_date               154   (a data-entry stamp; unusable as a time axis)

Run directly to print the numbers, or ``--json`` to hand them over for review.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

KEY = "record_id"

AGE_BANDS = [(0, 17, "0-17"), (18, 24, "18-24"), (25, 34, "25-34"),
             (35, 44, "35-44"), (45, 54, "45-54"), (55, 200, "55+")]

# Drug classes the one-pager gives a panel to, with the subtypes shown beneath
# the headline figure. analyte_group_2 is a *subtype* of group_1, not a peer, so
# filtering for fentanyl means group_2 == 'Fentanyl' -- group_1 returns nothing.
PANELS = [
    {
        "key": "opioids",
        "title": "Opioids",
        "noun": "opioid patients",
        "group": "NarcoticAnalgesics",
        "subtypes": ["Fentanyl", "NPSOpioids"],
        # A novel fentanyl analog in 17% of all patients, nearly as often as
        # fentanyl itself. This is what the surveillance exists to catch.
        "spotlight": "meta methyl acetyl fentanyl",
        "spotlight_note": "novel fentanyl analog",
    },
    {
        "key": "stimulants",
        "title": "Stimulants",
        "noun": "stimulant patients",
        "group": "CNSStimulants",
        "subtypes": ["Cocaine", "Amphetamines"],
        # Cocaethylene forms only when cocaine and alcohol are taken together,
        # so it is a direct biomarker of co-use rather than one more detection.
        "spotlight": "cocaethylene",
        "spotlight_note": "forms only when cocaine and alcohol are used together",
    },
]

# Category keys are vocabulary, not page copy. Anything not listed is shown
# as-is.
SUBTYPE_LABELS = {"NPSOpioids": "NPS opioids"}

# Facility names as exported are too long to label a chart with.
SITE_LABELS = {
    "UW-Health - Madison": ("UW-Health", "Madison"),
    "Medical College of Wisconsin - Milwaukee (Froedtert)":
        ("Medical College of Wisconsin", "Milwaukee"),
    "Bellin Health - Green Bay": ("Bellin Health", "Green Bay"),
}

# Administered in the ED, not taken by the patient. Nothing in the validation
# template distinguishes treatment from exposure, so these are excluded from
# "substances detected" figures and reported separately.
TREATMENT_DRUGS = {"naloxone"}

# Cells this small are a re-identification risk once crossed with anything else.
SUPPRESS_BELOW = 11


def band(age: str) -> str:
    value = int(age)
    for low, high, label in AGE_BANDS:
        if low <= value <= high:
            return label
    raise ValueError(f"age {age!r} outside every band")


class Cohort:
    """validate_v1.csv, with patient-level counting built in."""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.analyte_rows = [r for r in rows if r["analyte_name"].strip()]
        # One representative row per patient for the demographic columns, which
        # repeat identically down that patient's rows.
        self.patients: dict[str, dict[str, str]] = {}
        for row in rows:
            self.patients.setdefault(row[KEY], row)
        self.n = len(self.patients)

    def having(self, predicate) -> set[str]:
        """Record ids with at least one analyte row satisfying the predicate."""
        return {r[KEY] for r in self.analyte_rows if predicate(r)}

    def demographic(self, column: str) -> tuple[Counter, int]:
        """Value counts over patients, plus how many patients have a value."""
        values = [p[column] for p in self.patients.values() if p[column].strip()]
        return Counter(values), len(values)

    def numeric(self, column: str) -> dict:
        values = [float(p[column]) for p in self.patients.values()
                  if p[column].strip()]
        return {
            "n": len(values),
            "of": self.n,
            "min": min(values),
            "median": statistics.median(values),
            "max": max(values),
        }

    def age_profile(self, ids: set[str]) -> dict[str, int]:
        counts = Counter(band(self.patients[i]["age"]) for i in ids)
        return {label: counts.get(label, 0) for _, _, label in AGE_BANDS}

    def sex_split(self, ids: set[str]) -> dict[str, int]:
        counts = Counter(self.patients[i]["sex"] for i in ids)
        return {"M": counts.get("M", 0), "F": counts.get("F", 0)}

    def substances(self, predicate, limit: int = 8) -> list[dict]:
        """Substances ranked by patients, carrying the metabolite flag.

        Ranked by patients rather than rows so a substance detected in both ion
        modes does not outrank one detected once.
        """
        by_name: defaultdict[str, set[str]] = defaultdict(set)
        flags: dict[str, bool] = {}
        for row in self.analyte_rows:
            name = row["analyte_name"]
            if name in TREATMENT_DRUGS or not predicate(row):
                continue
            by_name[name].add(row[KEY])
            flags[name] = row["metabolite_flag"] == "True"
        ranked = sorted(by_name.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        return [
            {"name": name, "patients": len(ids), "metabolite": flags[name]}
            for name, ids in ranked[:limit]
        ]


def compute_stats(rows: list[dict[str, str]]) -> dict:
    c = Cohort(rows)
    stats: dict = {"patients": c.n, "analyte_rows": len(c.analyte_rows)}

    locations, _ = c.demographic("location")
    sites = []
    for name, count in locations.most_common():
        short, city = SITE_LABELS.get(name, (name, ""))
        sites.append({"name": name, "short": short, "city": city,
                      "patients": count, "share": count / c.n})
    stats["header"] = {
        "patients": c.n,
        "facilities": len(locations),
        "substances": len({r["analyte_name"] for r in c.analyte_rows}),
        "sites": sites,
        # Two-thirds from one site, so an unstratified figure is largely that
        # site. Worth stating rather than leaving the reader to notice.
        "dominant_share": sites[0]["share"] if sites else 0.0,
    }

    # --- drug-class panels ---------------------------------------------------
    stats["panels"] = []
    for panel in PANELS:
        ids = c.having(lambda r, g=panel["group"]: r["analyte_group_1"] == g)
        entry = {
            "key": panel["key"],
            "title": panel["title"],
            "noun": panel["noun"],
            "patients": len(ids),
            "share": len(ids) / c.n,
            "age": c.age_profile(ids),
            "sex": c.sex_split(ids),
            "substances": c.substances(
                lambda r, g=panel["group"]: r["analyte_group_1"] == g
            ),
            "subtypes": [],
        }
        for subtype in panel["subtypes"]:
            sub = c.having(lambda r, s=subtype: r["analyte_group_2"] == s)
            sex = c.sex_split(sub)
            entry["subtypes"].append(
                {
                    "name": SUBTYPE_LABELS.get(subtype, subtype),
                    "patients": len(sub),
                    "share": len(sub) / c.n,
                    "sex": sex,
                    # The subtype splits are sharper than the class aggregate --
                    # amphetamines 75% male against a 55% cohort -- so the panel
                    # donut averages away a real signal.
                    "male_share": sex["M"] / len(sub) if sub else 0.0,
                    # A split over a handful of patients is reportable but
                    # should not be reported; the renderer omits it.
                    "show_sex": len(sub) >= SUPPRESS_BELOW,
                }
            )
        spotlight = panel["spotlight"]
        spot = c.having(lambda r, s=spotlight: r["analyte_name"] == s)
        if not spot:
            raise SystemExit(
                f"spotlight substance {spotlight!r} has no detections; pick "
                f"another in PANELS before publishing"
            )
        entry["spotlight"] = {
            "name": spotlight,
            "note": panel["spotlight_note"],
            "patients": len(spot),
            "share": len(spot) / c.n,
        }
        stats["panels"].append(entry)

    # --- who -----------------------------------------------------------------
    all_ids = set(c.patients)
    sex, _ = c.demographic("sex")
    manner, manner_n = c.demographic("od_manner")
    stats["who"] = {
        "age": c.numeric("age"),
        "age_profile": c.age_profile(all_ids),
        "minors": sum(1 for p in c.patients.values() if int(p["age"]) < 18),
        "sex": dict(sex),
        "od_manner": {"counts": dict(manner), "n": manner_n},
    }

    # --- outcome -------------------------------------------------------------
    discharge, discharge_n = c.demographic("discharge_status")
    opioid = c.having(lambda r: r["analyte_group_1"] == "NarcoticAnalgesics")
    stimulant = c.having(lambda r: r["analyte_group_1"] == "CNSStimulants")
    stats["outcome"] = {
        "discharge": {"counts": dict(discharge), "n": discharge_n},
        "stay": c.numeric("hospital_stay_length"),
        "polysubstance": {
            "both": len(opioid & stimulant),
            "opioid_only": len(opioid - stimulant),
            "stimulant_only": len(stimulant - opioid),
            "neither": c.n - len(opioid | stimulant),
        },
    }

    # --- footnote material ---------------------------------------------------
    bac = c.numeric("bac")
    positive = {i for i, p in c.patients.items()
                if p["bac"].strip() and float(p["bac"]) > 0}
    cannabis = c.having(lambda r: r["analyte_group_1"] == "Cannabinoids")
    cannabis_parent = c.having(
        lambda r: r["analyte_group_1"] == "Cannabinoids"
        and r["metabolite_flag"] == "False"
    )
    stats["notes"] = {
        "bac": {"tested": bac["n"], "positive": len(positive),
                "median_positive": statistics.median(
                    [float(c.patients[i]["bac"]) for i in positive])},
        "cannabis": {"any": len(cannabis), "parent_only": len(cannabis_parent)},
        "treatment": {
            drug: len(c.having(lambda r, d=drug: r["analyte_name"] == d))
            for drug in sorted(TREATMENT_DRUGS)
        },
        "metabolite_rows": sum(
            1 for r in c.analyte_rows if r["metabolite_flag"] == "True"
        ),
        # Kept for the footnote: these patients have no QToF result on file, so
        # they must not be counted as "no drugs detected".
        "no_analyte_patients": sorted(
            (set(c.patients) - {r[KEY] for r in c.analyte_rows}), key=int
        ),
    }

    stats["suppressed"] = find_small_cells(stats)
    return stats


def find_small_cells(stats: dict) -> list[str]:
    """Figures too small to publish once crossed with anything else."""
    flagged = []
    for label, counts in (
        ("od_manner", stats["who"]["od_manner"]["counts"]),
        ("discharge_status", stats["outcome"]["discharge"]["counts"]),
    ):
        for value, count in counts.items():
            if count < SUPPRESS_BELOW:
                flagged.append(f"{label}={value} (n={count})")
    for panel in stats["panels"]:
        for band_label, count in panel["age"].items():
            if 0 < count < SUPPRESS_BELOW:
                flagged.append(f"{panel['key']} age {band_label} (n={count})")
        for sub in panel["subtypes"]:
            if not sub["show_sex"]:
                flagged.append(
                    f"{panel['key']} {sub['name']} sex split "
                    f"(n={sub['patients']}) — omitted"
                )
    return flagged


def print_stats(stats: dict) -> None:
    p = stats["patients"]
    pct = lambda n: f"{n / p:.0%}"
    print(f"cohort: {p} patients, {stats['analyte_rows']} analyte rows")
    h = stats["header"]
    print(f"header: {h['patients']} patients | {h['facilities']} facilities | "
          f"{h['substances']} substances")
    for site in h["sites"]:
        print(f"    {site['patients']:4d}  {pct(site['patients'])}  "
              f"{site['short']}, {site['city']}")

    for panel in stats["panels"]:
        print(f"\n{panel['title'].upper()}: {panel['patients']} patients "
              f"({panel['share']:.0%})")
        print("    subtypes: " + " · ".join(
            f"{s['name']} {s['patients']} ({s['share']:.0%})"
            + (f", {s['male_share']:.0%} male" if s["show_sex"]
               else ", sex split withheld (small n)")
            for s in panel["subtypes"]))
        s = panel["spotlight"]
        print(f"    spotlight: {s['name']} — {s['patients']} patients "
              f"({s['share']:.0%}), {s['note']}")
        print("    age: " + " | ".join(
            f"{k} {v}" for k, v in panel["age"].items()))
        print(f"    sex: M {panel['sex']['M']} / F {panel['sex']['F']}")
        print("    substances (patients, M = metabolite):")
        for sub in panel["substances"]:
            mark = " M" if sub["metabolite"] else "  "
            print(f"       {sub['patients']:4d}{mark}  {sub['name']}")

    w = stats["who"]
    print(f"\nWHO: age median {w['age']['median']:.0f} "
          f"(range {w['age']['min']:.0f}-{w['age']['max']:.0f}), "
          f"{w['minors']} under 18 ({pct(w['minors'])})")
    print(f"    sex: {w['sex']}")
    print(f"    od_manner: {w['od_manner']['counts']}")

    o = stats["outcome"]
    print(f"\nOUTCOME: {o['discharge']['counts']}")
    print(f"    stay: median {o['stay']['median']:.0f} days "
          f"(n={o['stay']['n']} of {o['stay']['of']})")
    ps = o["polysubstance"]
    print(f"    polysubstance: both {ps['both']} ({pct(ps['both'])}), "
          f"opioid-only {ps['opioid_only']}, stimulant-only "
          f"{ps['stimulant_only']}, neither {ps['neither']}")

    n = stats["notes"]
    print(f"\nNOTES")
    print(f"    bac: {n['bac']['positive']} positive of {n['bac']['tested']} "
          f"tested, median {n['bac']['median_positive']:.3f} g/dL")
    print(f"    cannabis: {n['cannabis']['any']} patients, but only "
          f"{n['cannabis']['parent_only']} with a parent compound "
          f"(98% is metabolite — excluding metabolites nearly erases it)")
    print(f"    treatment drugs excluded from charts: {n['treatment']}")
    print(f"    metabolite rows: {n['metabolite_rows']} of "
          f"{stats['analyte_rows']}")
    print(f"    patients with no QToF result on file: "
          f"{n['no_analyte_patients']} — MISSING, not negative")

    if stats["suppressed"]:
        print(f"\nSMALL CELLS (< {SUPPRESS_BELOW}) — suppress or collapse before "
              f"publishing:")
        for item in stats["suppressed"]:
            print(f"    {item}")


def load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=root / "versioned/validate_v1.csv"
    )
    parser.add_argument("--json", type=Path, help="write the figures as JSON")
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"File not found: {args.source}")
    stats = compute_stats(load(args.source))
    print_stats(stats)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print(f"\n  -> {args.json}")


if __name__ == "__main__":
    main()
