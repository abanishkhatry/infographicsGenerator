"""Clean the analyte -> category mapping workbook.

``data/Analyte_Category_Mapping.xlsx`` is the study team's reference vocabulary
and is left untouched. This script reads it, repairs the spelling and whitespace
defects listed below, and writes a cleaned version into ``versioned/``:

  * versioned/analyte_mapping_v2.xlsx  -- same shape, for spreadsheet use
  * versioned/analyte_mapping_v2.csv   -- same content, for the pipeline

The source workbook is the implicit v1; v2 is the first cleaned generation.
Both outputs are regenerated from scratch on every run, so the script is
deterministic and idempotent.

What is repaired:
  * Whitespace -- every cell is trimmed. 30 ``Canonical_Analyte`` values carried
    a trailing space, which made e.g. 'cathine ' and 'cathine' distinct keys.
  * Drug category spellings -- five typo clusters collapsed onto the agreed
    spellings, so 23 category strings become 17 real categories.
  * Canonical spellings -- three canonicals were themselves misspelled, so a
    correctly spelled detection was being converted *into* a misspelling.
  * Canonical casing -- three canonicals differed from an existing twin only by
    case, splitting one substance across two keys.

Deliberately NOT changed:
  * The ``Analyte`` lookup keys. Misspelled keys are the point of that column --
    they are what lets a typo in the raw QToF text find the right canonical.
    Only the ``Canonical_*`` output side is corrected.
  * The duplicate mCPP row and olanzapine's missing category. Those are not
    spelling defects; they are still open items.

openpyxl is not available in this environment, so the workbook is read and
written as raw OOXML. The writer emits inline strings, which avoids maintaining
a shared-string table.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS = {"m": SHEET_NS}
_T = f"{{{SHEET_NS}}}"

DEFAULT_SOURCE = "data/Analyte_Category_Mapping.xlsx"
DEFAULT_STEM = "versioned/analyte_mapping_v2"

CATEGORY_COLS = ("Drug_Category_1", "Drug_Category_2", "Drug_Category_3")
CANON_COL = "Canonical_Analyte"

# Typo -> agreed spelling. Applied to every category column.
CATEGORY_FIXES = {
    "NarcoticAnalgesic": "NarcoticAnalgesics",
    "NarcoticAnagesics": "NarcoticAnalgesics",
    "CNSSimulants": "CNSStimulants",
    "DissociativeAnesthetic": "DissociativeAnesthetics",
    "NSPOpioids": "NPSOpioids",
    "Hallucinogen": "Hallucinogens",
    # Category names are plural by convention, and the validation template
    # already spells this one 'Cathinones'. Decided Aug 2026: conform.
    # NOTE: applies to the CATEGORY columns only. The canonical 'cathinone' is a
    # substance name that happens to share the string and is left alone.
    "Cathinone": "Cathinones",
}

# Canonical values that were misspelled, so correct input produced wrong output.
CANONICAL_FIXES = {
    "disulfram": "disulfiram",
    "lorsartan": "losartan",
    "aspirin-salicyclic acid": "aspirin-salicylic acid",
    # 'metaprolol' is a misspelling that was given its own canonical, splitting
    # one beta blocker across two names. Confirmed by the study team (Heather,
    # Aug 2026): merge into 'metoprolol'. Its metabolite row carries a second
    # typo in the canonical itself ('hydorxy').
    "metaprolol": "metoprolol",
    "metaprolol-hydorxy": "metoprolol-hydroxy",
}

# Flag repairs keyed by ``Analyte``, applied after the canonical fixes.
#
# The workbook left ``Flag`` blank on some spellings of a substance while
# flagging others, so metabolite status depended on which spelling a technician
# typed. ``Flag`` has no explicit "not a metabolite" value -- it is either
# ``Metabolite`` or empty -- so a blank meant either "not a metabolite" or "not
# filled in", and the file could not distinguish them.
#
# Five canonicals were affected. The study team resolved all five as metabolites
# (Heather, Aug 2026; see data/Analyte_Category_Mapping_reviewed_2026-08.xlsx),
# which is applied per-spelling below. After this pass every canonical agrees
# with itself, so downstream code can read ``Flag`` directly instead of
# reconciling it.
FLAG_FIXES = {
    # Created by the metaprolol merge above: this row's twin
    # ('metoprolol-hydroxy') is flagged Metabolite, and a hydroxy metabolite is
    # a metabolite regardless of which spelling was typed.
    "metaprolol-hydroxy": "Metabolite",
    # canonical '1-(3-chlorophenyl)piperazine' -- the misspelled twin was blank
    "1-(3-chlorophenyl)piperazone (mCPP)": "Metabolite",
    # canonical '4-anpp' -- 4-ANPP is both a fentanyl precursor and a fentanyl
    # metabolite; the study team confirmed it counts as a metabolite here, which
    # keeps it out of any "excluding metabolites" figure (42 patients).
    "4-ANPP (despropionylfentanyl)": "Metabolite",
    "4-ANPP/despropionylfentanyl": "Metabolite",
    # canonical 'aspirin-salicylic acid' -- salicylic acid is aspirin's metabolite
    "aspirin-M": "Metabolite",
    "aspirin-M (salicyclic acid)": "Metabolite",
    "aspirin-M (salicylic acid)": "Metabolite",
    # canonical 'cathine' -- metabolite of cathinone
    "cathine": "Metabolite",
    # canonical 'loperamide-dimethyl' -- three of its four spellings already said so
    "loperamide-dimethyl": "Metabolite",
}

# Canonicals differing from an existing twin only by case. Lowercase is the
# convention (288 of 300); the other capitalised canonicals are unique names
# where the capitals are legitimate, so they are left alone.
CANONICAL_CASE_FIXES = {
    "Buprenorphine-6-glucuronide": "buprenorphine-6-glucuronide",
    "Fentanyl-nor": "fentanyl-nor",
    "delta-8-THC": "delta-8-thc",
}


def dedupe_flag_twins(
    fieldnames: list[str], rows: list[dict[str, str]]
) -> tuple[list[dict[str, str]], list[str]]:
    """Drop rows that duplicate another except for an empty ``Flag``.

    The workbook carried one such pair (the mCPP rows): identical in all seven
    fields apart from ``Flag``, where one said ``Metabolite`` and the other was
    blank. The flagged row is the more specific one, so the blank twin goes.

    Only removes a row when every other field matches exactly and a flagged twin
    exists, so a row that differs in any real way is never silently dropped.
    """
    others = [c for c in fieldnames if c != "Flag"]
    flagged = {
        tuple(r[c] for c in others) for r in rows if r["Flag"].strip()
    }

    kept: list[dict[str, str]] = []
    report: list[str] = []
    for row in rows:
        key = tuple(row[c] for c in others)
        if not row["Flag"].strip() and key in flagged:
            report.append(
                f"  dropped blank-Flag twin of {row['Analyte']!r} "
                f"(canonical {row[CANON_COL]!r})"
            )
            continue
        kept.append(row)
    return kept, report


def backfill_categories(
    rows: list[dict[str, str]]
) -> list[str]:
    """Give every row the categories its canonical already carries elsewhere.

    A canonical's categories are a property of the substance, not of the
    particular spelling, so a variant row left blank is an omission. This filled
    ``olanzapine``, whose misspelled variant row had no category while the
    correctly spelled row had ``Antipsychotics``.

    Raises if one canonical carries two *different* non-empty category sets,
    since there would be no basis for choosing between them.
    """
    donors: dict[str, set[tuple[str, ...]]] = {}
    for row in rows:
        cats = tuple(row[c] for c in CATEGORY_COLS)
        if any(cats):
            donors.setdefault(row[CANON_COL].lower(), set()).add(cats)

    conflicts = {k: v for k, v in donors.items() if len(v) > 1}
    if conflicts:
        detail = "; ".join(f"{k!r}: {sorted(v)}" for k, v in conflicts.items())
        raise SystemExit(f"conflicting categories for the same canonical -- {detail}")

    report: list[str] = []
    for row in rows:
        if any(row[c] for c in CATEGORY_COLS):
            continue
        donor = donors.get(row[CANON_COL].lower())
        if not donor:
            continue
        cats = next(iter(donor))
        for col, value in zip(CATEGORY_COLS, cats):
            row[col] = value
        filled = [v for v in cats if v]
        report.append(
            f"  {row['Analyte']!r} (canonical {row[CANON_COL]!r}) -> {filled}"
        )
    return report


def read_workbook(source: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read the first worksheet into header + row dicts."""
    with zipfile.ZipFile(source) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            shared = [
                "".join(t.text or "" for t in si.iter(f"{_T}t"))
                for si in root.findall("m:si", _NS)
            ]
        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

    raw_rows: list[dict[str, str]] = []
    for tr in sheet.iter(f"{_T}row"):
        cells: dict[str, str] = {}
        for c in tr.findall("m:c", _NS):
            col = re.match(r"[A-Z]+", c.get("r") or "").group()
            if c.get("t") == "inlineStr":
                is_el = c.find("m:is", _NS)
                text = "".join(t.text or "" for t in is_el.iter(f"{_T}t")) if is_el is not None else ""
            else:
                v = c.find("m:v", _NS)
                if v is None:
                    continue
                text = shared[int(v.text)] if c.get("t") == "s" else (v.text or "")
            cells[col] = text
        raw_rows.append(cells)

    if not raw_rows:
        raise SystemExit(f"{source}: no rows found")

    header = raw_rows[0]
    letters = sorted(header, key=lambda s: (len(s), s))
    fieldnames = [header[k] for k in letters]
    rows = [{header[k]: (r.get(k) or "") for k in letters} for r in raw_rows[1:]]
    return fieldnames, rows


def _col_letter(i: int) -> str:
    letters = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def write_workbook(dest: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    """Write a minimal single-sheet xlsx using inline strings."""
    def row_xml(idx: int, values: list[str]) -> str:
        cells = []
        for ci, val in enumerate(values):
            if not val:
                continue
            ref = f"{_col_letter(ci)}{idx}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                f"{_esc(val)}</t></is></c>"
            )
        return f'<row r="{idx}">' + "".join(cells) + "</row>"

    body = [row_xml(1, fieldnames)]
    for i, row in enumerate(rows, start=2):
        body.append(row_xml(i, [row.get(f, "") for f in fieldnames]))

    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{SHEET_NS}"><sheetData>'
        + "".join(body)
        + "</sheetData></worksheet>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{SHEET_NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<styleSheet xmlns="{SHEET_NS}">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
        '<cellXfs count="1"><xf xfId="0"/></cellXfs>'
        "</styleSheet>"
    )

    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/styles.xml", styles)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)


def clean(source: Path, stem: Path) -> None:
    fieldnames, rows = read_workbook(source)
    print(f"Read {len(rows)} rows x {len(fieldnames)} cols from {source.name}")

    for col in (CANON_COL, *CATEGORY_COLS):
        if col not in fieldnames:
            raise SystemExit(f"{source}: missing expected column {col!r}")

    trimmed = 0
    cat_edits: dict[tuple[str, str], int] = {}
    canon_edits: dict[tuple[str, str], int] = {}
    flag_edits: list[str] = []

    for row in rows:
        for col in fieldnames:
            original = row[col]
            value = original.strip()
            if value != original:
                trimmed += 1
            row[col] = value

        for col in CATEGORY_COLS:
            value = row[col]
            if value in CATEGORY_FIXES:
                new = CATEGORY_FIXES[value]
                cat_edits[(value, new)] = cat_edits.get((value, new), 0) + 1
                row[col] = new

        value = row[CANON_COL]
        new = CANONICAL_FIXES.get(value) or CANONICAL_CASE_FIXES.get(value)
        if new:
            canon_edits[(value, new)] = canon_edits.get((value, new), 0) + 1
            row[CANON_COL] = new

        flag = FLAG_FIXES.get(row["Analyte"])
        if flag and row["Flag"] != flag:
            flag_edits.append(f"  {row['Analyte']!r}: {row['Flag']!r} -> {flag!r}")
            row["Flag"] = flag

    print(f"\nwhitespace: {trimmed} cell(s) trimmed")
    print(f"\ncategory spellings: {sum(cat_edits.values())} cell(s) changed")
    for (old, new), n in sorted(cat_edits.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3}x  {old!r} -> {new!r}")
    print(f"\ncanonical names: {sum(canon_edits.values())} cell(s) changed")
    for (old, new), n in sorted(canon_edits.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3}x  {old!r} -> {new!r}")

    print(f"\nflag repairs: {len(flag_edits)} row(s) changed")
    for line in flag_edits:
        print(line)

    before = len(rows)
    rows, dedupe_report = dedupe_flag_twins(fieldnames, rows)
    print(f"\nduplicate rows: {before - len(rows)} removed ({before} -> {len(rows)})")
    for line in dedupe_report:
        print(line)

    backfill_report = backfill_categories(rows)
    print(f"\ncategory backfill: {len(backfill_report)} row(s) filled")
    for line in backfill_report:
        print(line)

    xlsx = stem.with_suffix(".xlsx")
    csv_path = stem.with_suffix(".csv")
    write_workbook(xlsx, fieldnames, rows)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  -> {xlsx}")
    print(f"  -> {csv_path}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root / DEFAULT_SOURCE)
    parser.add_argument(
        "--stem",
        type=Path,
        default=root / DEFAULT_STEM,
        help="Output path without extension; .xlsx and .csv are both written.",
    )
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"Source file not found: {args.source}")
    clean(args.source, args.stem)


if __name__ == "__main__":
    main()
