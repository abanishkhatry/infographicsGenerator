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

from vocab import (
    EMITTABLE_GROUPS,
    SPECIMEN_ALLOWED,
    SUPPRESS_BELOW,
    TRUE_NEGATIVE_RECORDS,
    UNRECORDED_SCREEN_RECORDS,
    read_rows,
)

KEY = "record_id"

AGE_BANDS = [(0, 17, "0-17"), (18, 24, "18-24"), (25, 34, "25-34"),
             (35, 44, "35-44"), (45, 54, "45-54"), (55, 200, "55+")]

# Wider bands for the age-by-intent chart. The six-band split puts intentional
# overdoses at 45-54 below the reporting threshold, and a suppressed segment in
# a stacked bar is worse than a coarser bar: the reader can subtract it from the
# band total. Four bands keep every cell reportable.
INTENT_BANDS = [(0, 17, "0-17"), (18, 34, "18-34"),
                (35, 54, "35-54"), (55, 200, "55+")]
INTENT_ORDER = ["Unintentional", "Intentional", "Unknown"]

# Short labels for the class chart and the co-occurrence grid. The vocabulary
# names are accurate but too long to head a matrix column.
CLASS_LABELS = {
    "NarcoticAnalgesics": "Opioids",
    "CNSStimulants": "Stimulants",
    "DissociativeAnesthetics": "Dissociatives",
    "Cannabinoids": "Cannabis",
    "Antidepressants": "Antidepressants",
    "Antihistamines": "Antihistamines",
    "Anticonvulsants": "Anticonvulsants",
    "Benzodiazepines": "Benzodiazepines",
    "Antipsychotics": "Antipsychotics",
    "MuscleRelaxers": "Muscle relaxers",
    "Other": "No class assigned",
}

# Kept out of the co-occurrence grid. 'Other' is the template's fill for a
# substance carrying no category, so it co-occurs with everything by being 74%
# of the cohort and tells you nothing. Naloxone is administered in the ED, so
# pairing it with a drug class measures treatment, not co-use.
NOT_A_CLASS = {"Other", "Naloxone"}

# Eight rows keeps every cell in the grid above the reporting floor. The ninth
# class introduces one suppressed pair and the tenth introduces six.
MATRIX_SIZE = 8

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

_unknown_sites = set(SITE_LABELS) - SPECIMEN_ALLOWED["location"]
if _unknown_sites:
    raise SystemExit(
        f"SITE_LABELS names facilit(ies) the template does not allow: "
        f"{sorted(_unknown_sites)}. A stale key falls back to the full name in "
        f"the chart legend rather than failing, so it is checked at import."
    )

_unknown_groups = {
    p["group"] for p in PANELS if p["group"] not in EMITTABLE_GROUPS
} | {
    s for p in PANELS for s in p["subtypes"] if s not in EMITTABLE_GROUPS
}
if _unknown_groups:
    raise SystemExit(
        f"PANELS names categor(ies) the pipeline cannot emit: "
        f"{sorted(_unknown_groups)}. A panel whose group never matches renders "
        f"0% with no error, so this is checked at import."
    )


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

    def at(self, location: str) -> set[str]:
        """Record ids seen at one facility."""
        return {i for i, p in self.patients.items() if p["location"] == location}

    def substances(self, predicate, limit: int = 8) -> list[dict]:
        """Substances ranked by patients, carrying the metabolite flag.

        Ranked by patients rather than rows so a substance detected in both ion
        modes does not outrank one detected once, and filtered to the reporting
        threshold here rather than at each call site -- the class panels were
        publishing counts of 10 and 6 while the facility panels suppressed them.
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
            for name, ids in ranked
            if len(ids) >= SUPPRESS_BELOW
        ][:limit]


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
    panel_ids: dict[str, set[str]] = {}
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
        panel_ids[panel["key"]] = ids
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
    # Reuse the sets the panel loop already built: deriving them again from
    # literal group names would let PANELS and this figure drift apart.
    opioid = panel_ids["opioids"]
    stimulant = panel_ids["stimulants"]
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
        # The six analyte-less patients are NOT interchangeable. Only one is a
        # true negative; the rest have no screen result on file and must not be
        # counted as "no drugs detected" -- doing so dilutes any detection rate
        # with unscreened patients. Reported separately for exactly that reason.
        "analyte_less": analyte_less_split(c),
    }

    stats["age_intent"] = age_by_intent(c)
    stats["classes"] = class_ranking(c)
    stats["unclassified"] = unclassified_summary(c)
    stats["matrix"] = cooccurrence(c)
    stats["facilities"] = facility_panels(c)
    stats["suppressed"] = find_small_cells(stats)
    # Adjudicate every publishable count once, here, so the renderer never
    # compares a count to SUPPRESS_BELOW itself. Previously it re-applied the
    # rule for od_manner and skipped it entirely for discharge_status, which put
    # a flagged cell on the page.
    stats["withheld"] = {
        "od_manner": {k for k, v in stats["who"]["od_manner"]["counts"].items()
                      if v < SUPPRESS_BELOW},
        "discharge_status": {k for k, v in stats["outcome"]["discharge"]["counts"].items()
                             if v < SUPPRESS_BELOW},
    }
    return stats


def class_ranking(c: Cohort) -> list[dict]:
    """Every drug class, ranked by how many patients it was found in.

    Not a breakdown: a patient appears in every class they tested positive for,
    so the shares sum to far more than 100%. ``kind`` separates the two entries
    that are not drug classes, so the renderer can set them apart rather than
    letting 'no class assigned' head the chart as though it were a finding.
    """
    groups: defaultdict[str, set[str]] = defaultdict(set)
    for row in c.analyte_rows:
        groups[row["analyte_group_1"]].add(row[KEY])
    ranked = []
    for name, ids in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        ranked.append({
            "name": CLASS_LABELS.get(name, name),
            "raw": name,
            "patients": len(ids),
            "share": len(ids) / c.n,
            "show": len(ids) >= SUPPRESS_BELOW,
            "kind": "aside" if name in NOT_A_CLASS else "class",
        })
    return ranked


#: Pharmacological families for the substances the mapping leaves unclassified.
#: This grouping is clinical knowledge, not something the data carries -- the
#: whole point is that these have no category -- so it is stated here and the
#: counts are computed from the file. Anything unlisted falls to "other".
UNCLASSIFIED_FAMILIES = [
    ("analgesics", ["acetaminophen", "naproxen", "ibuprofen", "meloxicam",
                    "ketorolac", "celecoxib", "aspirin", "aspirin-salicylic acid",
                    "aspirin-gentisic acid"]),
    ("antiemetics", ["ondansetron", "ondansetron-7-hydroxy",
                     "ondansetron-8-hydroxy", "metoclopramide",
                     "prochlorperazine"]),
    ("caffeine and other xanthines", ["caffeine", "theophylline", "theobromine",
                                      "nicotine", "cotinine", "pseudoephedrine"]),
    ("beta blockers", ["metoprolol", "metoprolol-hydroxy", "Propranolol",
                       "atenolol", "carvedilol", "labetalol", "etilefrine"]),
    # Called out separately because these are markers of the illicit supply
    # rather than incidental medication, and they are the strongest argument
    # for the vocabulary gaining a class.
    ("drug-supply adulterants", ["quinine", "xylazine", "levamisole/tetramisole"]),
]


def unclassified_summary(c: Cohort) -> dict:
    """What sits behind the substances the mapping gives no drug class.

    A third of all detections carry no category, so the sheet has to say what
    they are. Most have an obvious class the vocabulary simply does not include.
    """
    by_name: defaultdict[str, set[str]] = defaultdict(set)
    for row in c.analyte_rows:
        if row["analyte_group_1"] == "Other":
            by_name[row["analyte_name"]].add(row[KEY])
    patients = {i for ids in by_name.values() for i in ids}

    families, claimed = [], set()
    for label, members in UNCLASSIFIED_FAMILIES:
        found = [(n, len(by_name[n])) for n in members if n in by_name]
        if not found:
            continue
        claimed.update(n for n, _ in found)
        found.sort(key=lambda kv: -kv[1])
        families.append({
            "label": label,
            "detections": sum(n for _, n in found),
            "top": [(n, k) for n, k in found if k >= SUPPRESS_BELOW][:3],
        })
    families.sort(key=lambda f: -f["detections"])
    return {
        "patients": len(patients),
        "substances": len(by_name),
        "families": families,
        "unfamilied": len(set(by_name) - claimed),
    }


def cooccurrence(c: Cohort) -> dict:
    """How often each pair of drug classes turns up in the same patient.

    Cells are read along the row: of the patients with the row's class, what
    share also had the column's. Asymmetric on purpose -- half of the 133 opioid
    patients also had a stimulant, but a third of the far larger cannabis group
    did, and one number would hide that.

    Rates rather than counts, because counts here mostly measure class size:
    the two biggest classes co-occur most simply by being biggest.
    """
    groups: defaultdict[str, set[str]] = defaultdict(set)
    for row in c.analyte_rows:
        groups[row["analyte_group_1"]].add(row[KEY])
    names = [n for n, _ in sorted(groups.items(), key=lambda kv: -len(kv[1]))
             if n not in NOT_A_CLASS][:MATRIX_SIZE]
    rows = []
    for a in names:
        cells = []
        for b in names:
            if a == b:
                cells.append({"self": True, "patients": len(groups[a])})
                continue
            both = len(groups[a] & groups[b])
            cells.append({
                "self": False,
                "patients": both,
                "share": both / len(groups[a]),
                "show": both >= SUPPRESS_BELOW,
            })
        rows.append({"name": CLASS_LABELS.get(a, a), "patients": len(groups[a]),
                     "cells": cells})
    return {"labels": [CLASS_LABELS.get(n, n) for n in names], "rows": rows}


def age_by_intent(c: Cohort) -> list[dict]:
    """How the manner of overdose changes with age.

    The most striking pattern in the cohort and previously only a caption: no
    intentional overdose under 10, a majority intentional through adolescence,
    and unintentional again in later life. Raises rather than publishing if a
    band ever falls below the threshold, since a stacked bar makes a suppressed
    segment recoverable by subtraction.
    """
    bands = []
    for low, high, label in INTENT_BANDS:
        ids = [i for i, p in c.patients.items() if low <= int(p["age"]) <= high]
        counts = Counter(c.patients[i]["od_manner"] for i in ids)
        small = [k for k in INTENT_ORDER if 0 < counts[k] < SUPPRESS_BELOW]
        if small:
            raise SystemExit(
                f"age band {label} has {small} below {SUPPRESS_BELOW}; widen "
                f"INTENT_BANDS rather than publishing a stacked bar whose "
                f"hidden segment can be subtracted out."
            )
        bands.append({
            "label": label,
            "patients": len(ids),
            "segments": [(k, counts[k], counts[k] / len(ids) if ids else 0)
                         for k in INTENT_ORDER],
        })
    return bands


def _days(value: float) -> str:
    n = round(value)
    return f"{n} day" if n == 1 else f"{n} days"


def facility_panels(c: Cohort) -> list[dict]:
    """The same two drug classes, one set per facility.

    Everything here is a share of that facility's own patients, not of the
    cohort -- the sites differ by a factor of seven in size, so counts alone
    would say more about catchment than about drugs.

    Suppression bites unevenly and is applied per figure rather than per site:
    Amphetamines is reportable at two of the three sites, Cocaine at two, and
    the number of substances clearing the floor ranges from two to five. Each
    figure therefore carries its own flag instead of a site being judged
    publishable or not as a whole.
    """
    panels = []
    for name, count in c.demographic("location")[0].most_common():
        short, city = SITE_LABELS.get(name, (name, ""))
        ids = c.at(name)
        classes = []
        for panel in PANELS:
            group = panel["group"]
            in_class = ids & c.having(lambda r, g=group: r["analyte_group_1"] == g)
            sex = c.sex_split(in_class)
            subtypes = []
            for subtype in panel["subtypes"]:
                sub = ids & c.having(
                    lambda r, s=subtype: r["analyte_group_2"] == s
                )
                subtypes.append({
                    "name": SUBTYPE_LABELS.get(subtype, subtype),
                    "patients": len(sub),
                    "share": len(sub) / len(ids) if ids else 0.0,
                    "show": len(sub) >= SUPPRESS_BELOW,
                })
            substances = c.substances(
                lambda r, g=group, i=ids: (
                    r["analyte_group_1"] == g and r[KEY] in i
                ),
                limit=4,
            )
            classes.append({
                "key": panel["key"],
                "title": panel["title"],
                "patients": len(in_class),
                "share": len(in_class) / len(ids) if ids else 0.0,
                "male_share": sex["M"] / len(in_class) if in_class else 0.0,
                "show_sex": len(in_class) >= SUPPRESS_BELOW,
                "subtypes": subtypes,
                "substances": substances,
            })
        # A short profile of the site's patients, to sit beside its drug
        # columns. Only figures that clear the floor at *every* site are here:
        # a row that reads "withheld" for one site and not another invites the
        # reader to work out the missing value from the total. Under-18,
        # intentional, and BAC-positive all fail that test at Green Bay.
        stays = [int(c.patients[i]["hospital_stay_length"]) for i in ids
                 if c.patients[i]["hospital_stay_length"].strip()]
        sex = c.sex_split(ids)
        admitted = sum(1 for i in ids
                       if c.patients[i]["discharge_status"] == "Admitted")
        panels.append({
            "name": name, "short": short, "city": city,
            "patients": count, "share": count / c.n, "classes": classes,
            "profile": [
                ("Median age",
                 f"{statistics.median([int(c.patients[i]['age']) for i in ids]):.0f}"),
                ("Male", f"{sex['M'] / len(ids):.0%}"),
                ("Admitted", f"{admitted / len(ids):.0%}"),
                ("Median stay", _days(statistics.median(stays))
                 if stays else "—"),
            ],
        })
    return panels


def analyte_less_split(c: Cohort) -> dict:
    """Classify the patients with no analyte row. See vocab.py for the ruling."""
    observed = set(c.patients) - {r[KEY] for r in c.analyte_rows}
    unexpected = observed - TRUE_NEGATIVE_RECORDS - UNRECORDED_SCREEN_RECORDS
    if unexpected:
        raise SystemExit(
            f"analyte-less record(s) with no ruling in vocab.py: "
            f"{sorted(unexpected, key=int)}. Classify them before publishing — "
            f"a true negative and a missing screen mean different things."
        )
    return {
        "true_negative": sorted(observed & TRUE_NEGATIVE_RECORDS, key=int),
        "unrecorded": sorted(observed & UNRECORDED_SCREEN_RECORDS, key=int),
        "total": len(observed),
    }


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
    cannabis_metabolite = 1 - n["cannabis"]["parent_only"] / n["cannabis"]["any"]
    print(f"    cannabis: {n['cannabis']['any']} patients, but only "
          f"{n['cannabis']['parent_only']} with a parent compound "
          f"({cannabis_metabolite:.0%} metabolite-only — excluding metabolites "
          f"nearly erases it)")
    print(f"    treatment drugs excluded from charts: {n['treatment']}")
    print(f"    metabolite rows: {n['metabolite_rows']} of "
          f"{stats['analyte_rows']}")
    al = n["analyte_less"]
    print(f"    analyte-less patients: {al['total']} — "
          f"{len(al['true_negative'])} true negative {al['true_negative']}, "
          f"{len(al['unrecorded'])} with no screen on file {al['unrecorded']} "
          f"(MISSING, not negative)")

    print(f"\nDRUG CLASSES, ranked by patients")
    for cl in stats["classes"]:
        mark = "" if cl["kind"] == "class" else "   (not a drug class)"
        val = f"{cl['patients']:4d} ({cl['share']:3.0%})" if cl["show"] else "withheld"
        print(f"  {cl['name']:22s} {val}{mark}")

    u = stats["unclassified"]
    print(f"\nUNCLASSIFIED — {u['patients']} patients, {u['substances']} substances")
    for fam in u["families"]:
        top = ", ".join(f"{n} {k}" for n, k in fam["top"]) or "none above the floor"
        print(f"  {fam['label']:28s} {fam['detections']:4d} detections   {top}")
    print(f"  {'(no family assigned here)':28s} {u['unfamilied']:4d} substances")

    print(f"\nCO-OCCURRENCE — of the row class, share who also had the column")
    m = stats["matrix"]
    print(f"  {'':17s}" + "".join(f"{l[:8]:>9s}" for l in m["labels"]))
    for row in m["rows"]:
        line = f"  {row['name'][:15]:17s}"
        for cell in row["cells"]:
            if cell["self"]:
                line += f"{cell['patients']:>9d}"
            else:
                line += f"{cell['share']:8.0%} " if cell["show"] else f"{'·':>9s}"
        print(line)

    print(f"\nAGE BY INTENT")
    for b in stats["age_intent"]:
        print(f"  {b['label']:6s} n={b['patients']:3d}  " + "  ".join(
            f"{k} {n} ({sh:.0%})" for k, n, sh in b["segments"]))

    print(f"\nBY FACILITY")
    for f in stats["facilities"]:
        print(f"  {f['short']}, {f['city']} — {f['patients']} patients")
        print(f"      profile: " + " · ".join(
            f"{k} {v}" for k, v in f["profile"]))
        for cl in f["classes"]:
            subs = " · ".join(
                f"{s['name']} {s['patients']}" if s["show"]
                else f"{s['name']} withheld"
                for s in cl["subtypes"]
            )
            print(f"      {cl['title']:11s} {cl['patients']:3d} "
                  f"({cl['share']:3.0%})  {subs}")
            print(f"                  substances shown: "
                  f"{[x['name'] for x in cl['substances']]}")

    if stats["suppressed"]:
        print(f"\nSMALL CELLS (< {SUPPRESS_BELOW}) — suppress or collapse before "
              f"publishing:")
        for item in stats["suppressed"]:
            print(f"    {item}")


def load(path: Path) -> list[dict[str, str]]:
    return read_rows(path)[1]


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
