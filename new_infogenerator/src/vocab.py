"""Shared vocabularies and known data exceptions, in one place.

Two kinds of fact live here, kept in separate sections because they answer to
different owners:

  * The **validation template's** allowed values, transcribed from the "details"
    sheet of ``data/validation_set.xlsx``. Owned by whoever maintains that
    template.
  * **Study-team rulings** about specific records that no rule can derive.
    Owned by the study team.

Both were previously transcribed into two or three scripts each, with the
scripts' own comments admitting it ("Mirrors clean_qtof_v3..."). That is a
silent-drift hazard rather than a tidiness one: granting the four pending
category additions meant editing identical sets in two files, and missing one
left ``clean_qtof_v3`` passing while ``build_validate`` aborted after the whole
pipeline had re-run.
"""

from __future__ import annotations

import csv
from pathlib import Path


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Header + row dicts, tolerating a BOM and a stray leading blank line.

    ``utf-8-sig`` matters: an Excel round-trip adds a BOM, which otherwise makes
    the first fieldname ``"\ufeffrecord_id"`` and every column look missing.
    The v1 cleaners already handled this; the v3 scripts each grew their own
    plain-``utf-8`` copy and quietly lost it.
    """
    with path.open(newline="", encoding="utf-8-sig") as fh:
        text = fh.read().lstrip("\r\n")
    reader = csv.DictReader(text.splitlines(True))
    return list(reader.fieldnames or []), list(reader)


# --- the validation template's vocabulary -----------------------------------

#: Allowed values for the columns the specimen side fills. ``build_validate``
#: re-checks these after the join; ``clean_specimen_v3`` enforces them at source.
SPECIMEN_ALLOWED: dict[str, set[str]] = {
    "location": {
        "Bellin Health - Green Bay",
        "UW-Health - Madison",
        "Medical College of Wisconsin - Milwaukee (Froedtert)",
    },
    "sex": {"M", "F"},
    "race": {
        "American Indian or Alaskan Native",
        "Asian",
        "Black or African American",
        "Native Hawaiian or Pacific Islander",
        "White",
        "Two or more races",
        "Unknown",
    },
    "ethnicity": {"Hispanic", "Not Hispanic", "Unknown"},
    "od_manner": {"Intentional", "Unintentional", "Assault", "Unknown"},
    "discharge_status": {
        "Discharged",
        "Admitted",
        "Transferred",
        "Other",
        "Unknown",
    },
}

#: Allowed values for every ``analyte_group_*`` column. ``Other`` is the
#: template's fill for a substance carrying no ``Drug_Category_``, not a class
#: anyone assigns.
GROUP_VALUES: set[str] = {
    "Amphetamines", "Antidepressants", "Antipsychotics", "Barbiturates",
    "Benzodiazepines", "Cannabinoids", "Cathinones", "CSNSStimulants", "Cocaine",
    "DissociativeAnesthetics", "Fentanyl", "Hallucinogens", "MOUD",
    "MuscleRelaxers", "Naloxone", "NarcoticAnalgesics", "NPSOpioids", "Other",
}

#: Categories the pipeline emits by decision (Aug 2026) that the template has
#: yet to list. Where the mapping and the template disagreed, the mapping won
#: and the template gains its spelling. Reported, never raised.
TEMPLATE_ADDITIONS_REQUESTED: set[str] = {
    "CNSStimulants",    # template currently spells this 'CSNSStimulants'
    "Anticonvulsants",
    "Antihistamines",
    "Anesthetics",
}

#: Every category the pipeline may legitimately emit today.
EMITTABLE_GROUPS: set[str] = GROUP_VALUES | TEMPLATE_ADDITIONS_REQUESTED

#: The lab records matrix in lower case; the template wants Title Case.
MATRIX_TITLES: dict[str, str] = {"urine": "Urine", "plasma": "Plasma"}


# --- study-team rulings about specific records -------------------------------
#
# These are NOT derivable from the data as it currently flows: clean_qtof_v3
# sees the difference (a "None detected" sentinel versus an empty ion-mode cell)
# but folds it into a global counter rather than recording it per record. Until
# it emits that as a column, the classification has to be stated.
#
# See versioned/README.md, "The 6 blank analyte_name rows".

#: The screen ran and found nothing. A real result, and a real negative.
TRUE_NEGATIVE_RECORDS: set[str] = {"190"}

#: No result was ever entered — the ion-mode cells are empty in the raw export.
#: MISSING, not negative. Counting these as "no drugs detected" would dilute any
#: "% of patients with X detected" figure with unscreened patients. Three of them
#: (365, 433, 447) are MCW specimens with a recorded matrix, so the sample was
#: probably run and the result simply never keyed in.
UNRECORDED_SCREEN_RECORDS: set[str] = {"142", "143", "365", "433", "447"}

#: Every record expected to reach the output with no analyte.
ANALYTE_LESS_RECORDS: set[str] = TRUE_NEGATIVE_RECORDS | UNRECORDED_SCREEN_RECORDS


# --- disclosure ---------------------------------------------------------------

#: Cells at or below this are a re-identification risk once crossed with
#: anything else, so they are withheld from anything published. Enforced in the
#: stats layer: every count reaches the renderer already adjudicated.
SUPPRESS_BELOW = 11
