"""Render the one-pager as a single self-contained HTML file.

Reads ``versioned/validate_v1.csv`` through ``onepager_stats.compute_stats`` and
writes ``output/onepager.html``. Layout only -- every number comes from the
stats module, so this file contains no arithmetic beyond scaling bars to pixels.

Charts are hand-authored inline SVG: rects, text, and one donut drawn with a
``stroke-dasharray``. No plotting library, no CDN, no external requests, so the
file opens offline and prints to PDF from any browser. The palette follows the
house style already used by ``biosurveillance_dissemination`` (navy #003D78).

The page carries cohort counts derived from PHI. It is a local file and must not
be published to an external service. Small cells are suppressed rather than
drawn -- see ``SUPPRESS_BELOW`` in onepager_stats and the report this prints.
"""

from __future__ import annotations

import argparse
import base64
import html
import math
import struct
from pathlib import Path

from onepager_stats import compute_stats, load
from vocab import SUPPRESS_BELOW

# House navy, from biosurveillance_dissemination/doc_styles.py, plus a maroon
# for the stimulant panel matching the reference one-pager.
THEME = {
    "opioids": {"bar": "#003D78", "dark": "#002B56", "soft": "#DCE6F0"},
    "stimulants": {"bar": "#7B1E3C", "dark": "#5C1229", "soft": "#F2DFE4"},
}
INK = "#16202B"
MUTED = "#5E6E7E"
RULE = "#D5DCE3"

# Study-team vocabulary, ordered most to least common. stacked_row raises if the
# data contains a value this omits.
DISCHARGE_ORDER = ["Admitted", "Discharged", "Transferred", "Other", "Unknown"]

# --- selectable variants -----------------------------------------------------
# The dashboard offers these by key. Everything here is presentation: no variant
# changes a number, and none can bypass the suppression already applied in the
# stats layer.

# The masthead is fixed: centred, the lab over the title over the period. Only
# the title varies, and it varies with the body -- a sheet about three hospitals
# should not be headed the same as a sheet about every drug class.
ORG = "Wisconsin State Laboratory of Hygiene"
CONTACT_EMAIL = "NFODbiosurveillance@dhs.wisconsin.gov"
EYEBROW = "Non-fatal overdose biosurveillance"

# Each body carries its own title and its own footnote. The footnote is fixed
# per body rather than chosen: what needs explaining depends on what is shown,
# and leaving it selectable invited a sheet whose caveats did not match its
# charts. ``caveat`` is the only part that varies -- how to read the figures and
# the cohort note are the same on every sheet and are written in render_footer.
BODIES = {
    "classes": {
        "label": "Opioids vs stimulants (built)",
        "title": "Opioids and stimulants in Wisconsin overdose patients",
        "built": True,
        "note": "",
        "caveat": (
            "Two drug classes of the sixteen recorded, chosen because they "
            "carry the clearest surveillance signal. They are not exclusive "
            "and not exhaustive: {both} patients ({both_pct}) had both an "
            "opioid and a stimulant, and {neither} had neither. Nothing here is "
            "split by site, and {dominant} of the cohort presented at one "
            "hospital."
        ),
    },
    "overview": {
        "label": "All drug classes and how they overlap",
        "title": "What substances contribute to Wisconsin non-fatal overdose",
        "built": True,
        "note": "",
        "caveat": (
            "Every drug class recorded, ranked by how many patients it was "
            "found in, then how often each pair turns up in the same patient. "
            "A patient appears in every class they tested positive for, so the "
            "bars sum to more than {total}. Naloxone is given in the emergency "
            "department rather than taken, so it is set apart in grey and left "
            "out of the overlap grid. Nothing here is split by site, and "
            "{dominant} of the cohort presented at one hospital.<br>"
            "<b>Substances with no drug class.</b> {unclassified}"
        ),
    },
    "facility": {
        "label": "By facility — Madison / Milwaukee / Green Bay",
        "title": "Overdose detections across participating Wisconsin hospitals",
        "built": True,
        "note": "",
        "caveat": (
            "Figures are shares of each site\u2019s own patients, not of the "
            "cohort, because the sites differ by a factor of seven in size "
            "({sites}). A figure marked withheld fell below the reporting "
            "threshold at that site and nowhere else \u2014 it is a small "
            "number, not a zero. Sites also differ in how completely they "
            "record manner of overdose and sample type, so some of the "
            "difference between them is data entry rather than patients."
        ),
    },
}

# SVG carries its own styling as presentation attributes rather than borrowing
# the document's CSS classes. Browsers cascade document CSS into inline SVG;
# WeasyPrint does not, so class-styled text fell back to a ~16px default in the
# PDF, which blew out the bar column and squeezed the age and sex charts off the
# sheet entirely. Attributes render the same everywhere.
SVG_FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"
BAR_VALUE = (f'font-family="{SVG_FONT}" font-size="9" font-weight="600" '
             f'fill="{MUTED}"')
BAR_LABEL = f'font-family="{SVG_FONT}" font-size="10" fill="{INK}"'
AXIS_TEXT = (f'font-family="{SVG_FONT}" font-size="6.2" fill="{MUTED}" '
             f'text-anchor="middle"')
# Own attribute set rather than AXIS_TEXT plus overrides: a repeated attribute
# is not an override in XML, the first one wins.
BAND_LABEL = (f'font-family="{SVG_FONT}" font-size="7.5" fill="{MUTED}" '
              f'text-anchor="start"')

# Printable width of the sheet: 8.5in less the 0.3in page margins the PDF path
# uses. A full-bleed chart draws to this viewBox so its type renders at the size
# it is written at; the screen sheet is ~23px narrower and scales it down.
CONTENT_W = 758

# Space banked above the first facility block, and the size of the map that
# sits in it. Tuned by measurement against the printable height: 260 is the
# largest value that still fits on one page, so this leaves a cushion.
FACILITY_LEAD = 197
MAP_INSET = 5    # breathing room above and below the map inside the band
MAP_INDENT = 120   # map, nudged off the left margin, short of centred
MAP_KEY_INSET = 70  # key, held off the right margin by rather less

MAP_KEY_TITLE = "Where these patients were seen"

#: Marker colours in wisconsin_map_sheet.png, keyed by the city each marker sits
#: on. Sampled from the asset itself rather than eyeballed, so the ring in the
#: key is the exact colour of the ring on the map. Replacing the map means
#: re-sampling these -- map_key raises if a site has no entry.
MAP_MARKERS = {
    "Madison": "#E22A00",
    "Milwaukee": "#0058D5",
    "Green Bay": "#D48400",
}

#: Wisconsin with the three sites marked. Cropped to its ink and pre-scaled
#: from assets/wisconsin_map.png (611 KB) so embedding costs ~56 KB, not
#: ~815 KB. Kept true-colour: a 64-entry adaptive palette spent every slot
#: on the cream county fill and quantised the three markers to brown.
MAP_PATH = Path(__file__).resolve().parents[1] / "assets/wisconsin_map_sheet.png"

# WHO/OUTCOME chart heights, per body. They differ because the sheets have
# different amounts of room: measured by sweep, the classes sheet takes 13/17
# with ~10px to spare, while the overview sheet -- carrying a fifteen-row class
# ranking and an 8x8 grid -- goes to two pages above 11/15.
BAND_ROW_H = {"classes": 13, "overview": 11, "facility": 11}
OBAR_H = {"classes": 17, "overview": 15, "facility": 15}

# Vertical padding on each WHO demographic row, classes sheet only. 1.5px is
# the sheet default; this is the largest value that still renders on one page,
# found by sweep -- 3.0 breaks it.
WHO_STAT_PAD = 2.5

#: The discharge bar: its height, and the smallest share that still gets an
#: inline percentage. 9% is about 33px in the facility sheet's half-width
#: column, which is the narrowest place this bar is drawn.
DISCHARGE_BAR_H = 24
DISCHARGE_LABEL_MIN = 9

#: Space above the eyebrow, facility sheet only.
HEADER_LEAD = 16

#: Gap below the organization line, before the rule. One value, every sheet.
HEADER_PAD = 8

#: Vertical rhythm between the sheet's top-level blocks.
BLOCK_GAP = 9

# Separation between the stacked WHO and OUTCOME blocks. Paid for by taking the
# gap BETWEEN the sheet's blocks from 7px down to 4px: total white space on the
# page is unchanged, it just sits where it separates two things that were
# running together. 18 is the ceiling -- 20 breaks the all-classes sheet.
STACK_GAP = 16

BAR_W = 300          # px available for the longest substance bar
BAR_H = 11
BAR_GAP = 3
AGE_W = 152
AGE_H = 31
DONUT_R = 21


def esc(text: str) -> str:
    return html.escape(str(text))


def pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%"


def pct_f(fraction: float) -> str:
    return f"{round(100 * fraction)}%"


def hbars(items: list[dict], theme: dict, label_key: str, value_key: str,
          hatch_key: str | None = None, hatch_id: str = "") -> str:
    """Horizontal bars, widest first, scaled to the largest value.

    The metabolite hatch pattern is defined inside this SVG rather than once at
    document level: a ``url(#id)`` pointing into a different ``<svg>`` resolves
    in a browser but not in WeasyPrint, where the bars came out solid.
    """
    if not items:
        return ""
    top = max(i[value_key] for i in items)
    parts = []
    for index, item in enumerate(items):
        y = index * (BAR_H + BAR_GAP)
        width = max(2, round(BAR_W * item[value_key] / top))
        hatched = bool(hatch_key and item.get(hatch_key))
        fill = f"url(#{hatch_id})" if hatched else theme["bar"]
        parts.append(
            f'<rect x="0" y="{y}" width="{width}" height="{BAR_H}" '
            f'fill="{fill}" rx="1"/>'
            f'<text x="{width + 6}" y="{y + BAR_H - 3}" {BAR_VALUE}>'
            f'{item[value_key]}</text>'
            f'<text x="{BAR_W + 46}" y="{y + BAR_H - 3}" {BAR_LABEL}>'
            f'{esc(item[label_key])}{" &#8226;" if hatched else ""}</text>'
        )
    height = len(items) * (BAR_H + BAR_GAP)
    defs = hatch_pattern(hatch_id, theme) if hatch_id else ""
    return (f'<svg viewBox="0 0 640 {height}" width="100%" height="{height}" '
            f'role="img">{defs}{"".join(parts)}</svg>')


def age_bars(profile: dict[str, int], theme: dict) -> str:
    """Six age bands as vertical bars. Bands below the suppression floor are
    drawn as an outline, so the shape stays readable without publishing the
    count."""
    top = max(profile.values()) or 1
    step = AGE_W / len(profile)
    width = step - 4
    parts = []
    for index, (label, count) in enumerate(profile.items()):
        height = max(1, round(AGE_H * count / top))
        x = index * step
        small = 0 < count < SUPPRESS_BELOW
        style = (f'fill="none" stroke="{theme["bar"]}" stroke-dasharray="2 1"'
                 if small else f'fill="{theme["bar"]}"')
        parts.append(
            f'<rect x="{x:.1f}" y="{AGE_H - height}" width="{width:.1f}" '
            f'height="{height}" {style} rx="1"/>'
            f'<text x="{x + width / 2:.1f}" y="{AGE_H + 9}" {AXIS_TEXT}>'
            f'{esc(label)}</text>'
        )
    return (f'<svg viewBox="0 0 {AGE_W} {AGE_H + 12}" width="{AGE_W}" '
            f'height="{AGE_H + 12}" role="img">{"".join(parts)}</svg>')


def donut(male: int, female: int, theme: dict, baseline: float) -> str:
    """Male share of this group, with the cohort's share marked for comparison.

    Without the baseline tick a reader cannot tell whether a panel's 64% male is
    high, low, or simply the cohort. It is not the cohort: men are 55% of all
    patients but 64% of both the opioid and the stimulant group.
    """
    total = male + female or 1
    circumference = math.tau * DONUT_R
    filled = circumference * male / total
    size = DONUT_R * 2 + 14
    centre = size / 2
    # Tick at the cohort's male share, on the same clockwise-from-top scale.
    angle = math.tau * baseline - math.pi / 2
    inner, outer = DONUT_R - 7.5, DONUT_R + 7.5
    x1, y1 = centre + inner * math.cos(angle), centre + inner * math.sin(angle)
    x2, y2 = centre + outer * math.cos(angle), centre + outer * math.sin(angle)
    return (
        f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'role="img">'
        f'<circle cx="{centre}" cy="{centre}" r="{DONUT_R}" fill="none" '
        f'stroke="{theme["soft"]}" stroke-width="9"/>'
        f'<circle cx="{centre}" cy="{centre}" r="{DONUT_R}" fill="none" '
        f'stroke="{theme["bar"]}" stroke-width="9" '
        f'stroke-dasharray="{filled:.1f} {circumference:.1f}" '
        f'transform="rotate(-90 {centre} {centre})"/>'
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{INK}" stroke-width="1.4"/>'
        f'</svg>'
    )


def stacked_row(counts: dict[str, int], total: int, theme: dict,
                order: list[str], withheld: set[str]) -> str:
    """Outcome shares as one stacked bar, labelled beneath.

    Segments below the suppression floor still take their true width -- the bar
    must reach 100% -- but neither their inline number nor their caption entry
    carries a count. ``withheld`` is decided in the stats layer; this function
    never compares a count to a threshold itself.

    Raises if ``order`` omits a value present in the data, which would silently
    drop a segment and leave the bar short of 100%.
    """
    missing = [k for k, v in counts.items() if v and k not in order]
    if missing:
        raise SystemExit(
            f"stacked_row: {missing} present in the data but absent from the "
            f"display order, so the bar would not reach 100%"
        )
    # Percentage-width divs, not an SVG. An SVG needs its viewBox in absolute
    # units, and this bar appears in two different column widths -- full width
    # where WHO and OUTCOME are stacked, half width where they sit side by side
    # on the facility sheet. WeasyPrint does not honour preserveAspectRatio to
    # scale a too-wide viewBox down; it renders at 1:1 and clips, which cut the
    # facility bar off after its first segment. Percentages fit any column.
    parts, labels = [], []
    # A ramp rather than one pale tint, so the small segments stay
    # distinguishable and the bar visibly reaches 100%.
    ramp = [theme["bar"], theme["dark"], "#7C93AC", "#A9B9C8", theme["soft"]]
    for position, name in enumerate(order):
        count = counts.get(name, 0)
        if not count:
            continue
        share = 100 * count / total
        shade = ramp[min(position, len(ramp) - 1)]
        ink = "" if position < 3 else f";color:{INK}"
        # Narrow segments carry no inline number: it would not fit in the
        # half-width column, and a withheld one must not be printed at all.
        show = share >= DISCHARGE_LABEL_MIN and name not in withheld
        parts.append(
            f'<div class="oseg" style="width:{share:.2f}%;background:{shade}'
            f'{ink}">{pct(count, total) if show else ""}</div>'
        )
        labels.append(
            esc(name) if name in withheld else f"{esc(name)} {count}"
        )
    return (f'<div class="obar" style="height:{DISCHARGE_BAR_H}px">'
            f'{"".join(parts)}</div>'
            f'<p class="cap">{" &#183; ".join(labels)}</p>')


def hatch_pattern(pattern_id: str, theme: dict) -> str:
    """Diagonal hatch marking a metabolite bar, scoped to one SVG."""
    return (f'<defs><pattern id="{pattern_id}" width="5" height="5" '
            f'patternTransform="rotate(45)" patternUnits="userSpaceOnUse">'
            f'<rect width="5" height="5" fill="{theme["soft"]}"/>'
            f'<line x1="0" y1="0" x2="0" y2="5" stroke="{theme["bar"]}" '
            f'stroke-width="2.2"/></pattern></defs>')


CSS = f"""
@page {{ size: letter portrait; margin: 0.3in; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #EEF1F4; color: {INK};
  font: 400 12px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI",
  system-ui, sans-serif; -webkit-font-smoothing: antialiased; }}
/* Block, not flex. WeasyPrint stretches every child of a column flex container
   to soak up a min-height, ignoring flex-grow and auto margins while it does
   -- so spacing was decided by the renderer, differed per body (the masthead
   rule sat 24.6px lower on the two flex sheets than on the facility one), and
   could not be set in CSS. Block layout leaves heights alone: every gap below
   is the gap that renders, and the leftover height is placed deliberately
   (FACILITY_LEAD on the facility sheet, BLOCK_GAP elsewhere). */
.sheet {{ width: 8.5in; min-height: 11in; margin: 18px auto; padding: 0.42in;
  background: #FFF; box-shadow: 0 2px 14px rgba(0,0,0,.14);
  display: block; }}
.sheet > * {{ margin-bottom: {BLOCK_GAP}px; }}
.sheet > footer {{ margin-bottom: 0; }}
/* The facility sheet opens on a map rather than a dense block, so its masthead
   gets room above the eyebrow that the other two do not need. */
.sheet.facility header {{ padding-top: {HEADER_LEAD}px; }}
h1 {{ margin: 0; font: 600 22px/1.12 Georgia, "Iowan Old Style", serif;
  letter-spacing: -.01em; }}
.eyebrow {{ font: 600 9px/1 system-ui, sans-serif; letter-spacing: .13em;
  text-transform: uppercase; color: {THEME['opioids']['bar']}; }}
header {{ border-bottom: 2px solid {INK}; padding-bottom: {HEADER_PAD}px;
  padding-top: 0;
  display: block; text-align: center; }}
header h1 {{ margin: 1px 0 3px; }}
/* The eyebrow and org kept the browser's default paragraph margins, which put
   ~35px of air into a 60px masthead. */
header .eyebrow {{ margin: 0; }}
header .org {{ margin: 0; color: {MUTED}; font-size: 11px; }}
.kpis {{ display: flex; gap: 0; border: 1px solid {RULE}; }}
.kpi {{ flex: 1; padding: 6px 12px; border-right: 1px solid {RULE}; }}
.kpi:last-child {{ border-right: 0; }}
.kpi b {{ display: block; font: 600 23px/1 Georgia, serif;
  font-variant-numeric: tabular-nums; }}
.kpi span {{ display: block; margin-top: 2px; font-size: 9.5px;
  color: {MUTED}; letter-spacing: .02em; }}
.panel {{ border-top: 1px solid {RULE}; padding-top: 6px; }}
.panel h2 {{ margin: 0 0 7px; font: 600 12px/1 system-ui, sans-serif;
  letter-spacing: .11em; text-transform: uppercase; }}
.panel-body {{ display: grid;
  grid-template-columns: 108px minmax(0, 1fr) 158px;
  gap: 16px; align-items: start; }}
.big {{ font: 600 38px/1 Georgia, serif; letter-spacing: -.02em; }}
.big small {{ display: block; font: 400 9.5px/1.35 system-ui, sans-serif;
  color: {MUTED}; letter-spacing: .02em; margin-top: 3px; }}
.subtypes {{ margin: 7px 0 0; padding: 0; list-style: none; font-size: 10px;
  color: {INK}; display: flex; flex-direction: column; gap: 5px; }}
.subtypes b {{ font-variant-numeric: tabular-nums; }}
.subtypes span {{ display: block; font-size: 8.5px; color: {MUTED};
  font-variant-numeric: tabular-nums; margin-top: 1px; }}
.spotlight {{ border-left: 3px solid currentColor; padding: 1px 0 1px 9px;
  margin-bottom: 5px; }}
.spotlight .n {{ font: 600 17px/1 Georgia, serif;
  font-variant-numeric: tabular-nums; }}
.spotlight .nm {{ font-weight: 600; font-size: 11.5px; }}
.spotlight .no {{ display: block; color: {MUTED}; font-size: 9.5px;
  margin-top: 1px; }}
.aside {{ display: flex; flex-direction: column; gap: 7px; align-items: center; }}
.chart {{ display: flex; flex-direction: column; align-items: center; gap: 3px; }}
.legend {{ font-size: 8.5px; color: {MUTED}; text-align: center; line-height: 1.35; }}
.legend b {{ color: {INK}; font-weight: 600; }}
.tick {{ color: {INK}; }}
.cap {{ margin: 3px 0 0; font-size: 9.5px; color: {MUTED}; }}
.two {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 20px;
  border-top: 1px solid {RULE}; padding-top: 8px; }}
.two.stacked {{ grid-template-columns: minmax(0, 1fr); gap: {STACK_GAP}px; }}
.two h2 {{ margin: 0 0 4px; font: 600 12px/1 system-ui, sans-serif;
  letter-spacing: .11em; text-transform: uppercase; }}
.stat-line {{ display: flex; justify-content: space-between;
  font-size: 10.5px; padding: 1.5px 0; border-bottom: 1px dotted {RULE}; }}
.stat-line b {{ font-variant-numeric: tabular-nums; }}
/* Only WHO, only on this sheet: the three demographic rows carry the most
   white space around them of anything in the block, and this is the one body
   with room to widen them. Swept against the page -- see WHO_STAT_PAD. */
.sheet.classes .who .stat-line {{ padding: {WHO_STAT_PAD}px 0; }}
.flag {{ color: {THEME['stimulants']['bar']}; font-weight: 600; }}
.chart-title {{ margin: 5px 0 2px; font: 600 9px/1 system-ui, sans-serif;
  letter-spacing: .08em; text-transform: uppercase; color: {INK}; }}
.legend-row {{ margin: 0 0 2px; display: flex; flex-wrap: wrap; gap: 9px;
  font-size: 8px; color: {MUTED}; }}
.legend-row span {{ display: inline-flex; align-items: center; gap: 4px; }}
.legend-row i {{ width: 7px; height: 7px; border-radius: 1px; }}
.obar {{ display: flex; overflow: hidden; border-radius: 2px;
  margin-bottom: 4px; }}
.oseg {{ display: flex; align-items: center; justify-content: center;
  color: #FFF; font: 600 8px system-ui, sans-serif;
  font-variant-numeric: tabular-nums; }}
.placeholder {{ flex: 1; display: flex; flex-direction: column; gap: 6px;
  align-items: center; justify-content: center; text-align: center;
  background: #F6F8FA; border: 1px dashed {RULE}; padding: 26px 40px;
  font-size: 10.5px; line-height: 1.5; color: {MUTED}; }}
.placeholder b {{ color: {INK}; font-size: 12px; }}
.placeholder span {{ max-width: 46ch; }}
.facility {{ border-top: 1px solid {RULE}; padding-top: 6px;
  padding-bottom: 1px; }}
/* All the slack the sheet has, banked above the first site so the three blocks
   run continuously into WHO instead of each trailing off into a gap. Measured
   against the printable height -- see the assertion in dashboard.render_pdf.
   The map sits in the left of the band and is sized by it, so the band's height
   is what keeps the sheet on one page however large the source image is. */
.leadband {{ height: {FACILITY_LEAD}px; display: flex; align-items: center;
  justify-content: space-between;
  padding: 0 {MAP_KEY_INSET}px 0 {MAP_INDENT}px; }}
.leadmap {{ display: block; }}
.mapkey {{ flex: 0 0 auto; }}
.mapkey h2 {{ margin: 0 0 11px; font: 600 12.5px/1.2 system-ui, sans-serif;
  letter-spacing: .1em; text-transform: uppercase; color: {INK}; }}
.mapkey ul {{ margin: 0; padding: 0; list-style: none; }}
.mapkey li {{ display: flex; align-items: flex-start; gap: 10px;
  margin-bottom: 10px; }}
.mapkey li:last-child {{ margin-bottom: 0; }}
.mapkey .ring {{ flex: 0 0 auto; margin-top: 1px; }}
.mapkey p {{ margin: 0; }}
.mapkey b {{ display: block; color: {INK};
  font: 600 11.5px/1.25 system-ui, sans-serif; }}
.mapkey span {{ display: block; color: {MUTED};
  font: 400 10px/1.3 system-ui, sans-serif; letter-spacing: .04em;
  text-transform: uppercase; }}
.facility h2 {{ margin: 0 0 5px; display: flex; align-items: baseline; gap: 8px;
  font: 600 12.5px/1 system-ui, sans-serif; letter-spacing: .1em;
  text-transform: uppercase; }}
.facility h2 span {{ font-weight: 400; color: {MUTED}; letter-spacing: .04em; }}
.facility h2 b {{ margin-left: auto; font: 400 10px/1 system-ui, sans-serif;
  letter-spacing: 0; text-transform: none; color: {MUTED};
  font-variant-numeric: tabular-nums; }}
.fgrid {{ display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 116px; gap: 24px; }}
.fprofile {{ padding-left: 13px; border-left: 1px solid {RULE}; }}
.fprofile p {{ margin: 0 0 9px; display: flex; justify-content: space-between;
  gap: 6px; font-size: 9.5px; line-height: 1.35; color: {MUTED}; }}
.fprofile b {{ color: {INK}; font-variant-numeric: tabular-nums; }}
.fhead {{ margin: 0; font: 600 12px/1.2 system-ui, sans-serif;
  letter-spacing: .06em; text-transform: uppercase; }}
.fbig {{ font: 600 32px/1 Georgia, serif; letter-spacing: -.015em;
  margin-right: 5px; font-variant-numeric: tabular-nums; }}
.fsub {{ display: block; margin-top: 3px; font: 400 9.5px/1.35 system-ui,
  sans-serif; letter-spacing: 0; text-transform: none; color: {MUTED};
  font-variant-numeric: tabular-nums; }}
.fsubtypes {{ margin: 3px 0 4px; font-size: 10px; color: {MUTED};
  font-variant-numeric: tabular-nums; }}
.fsubtypes b {{ color: {INK}; }}
.fsubtypes i {{ font-style: normal; }}
footer {{ margin-top: auto; border-top: 1px solid {RULE}; padding-top: 5px;
  font-size: 7.6px; line-height: 1.38; color: {MUTED}; }}
footer b {{ color: {INK}; }}
.contact {{ margin: 3px 0 0; padding-top: 3px;
  border-top: 1px solid {RULE}; }}
.contact a {{ color: {THEME['opioids']['bar']}; font-weight: 600;
  text-decoration: none; }}
@media print {{ body {{ background: #FFF; }}
  /* min-height is 11in on screen so the sheet always looks like a page. In
     print that is *larger* than the printable area (11in less the margins), so
     it alone would force a second page. */
  /* 11in less the 0.3in margins is 10.4in of printable height. Setting the
     sheet just under that lets the footer's auto top margin sit the notes on
     the page bottom, without the 11in screen value that would force a second
     page. */
  .sheet {{ box-shadow: none; margin: 0; width: auto; padding: 0;
    min-height: 10.3in; }} }}
"""


# The facility bars sit in a half-width column, so they need their own
# proportions rather than the full-width panel's.
FBAR_W = 132         # px available for the longest bar
FBAR_H = 11
FBAR_GAP = 4
FBAR_LABEL_X = 168   # where the substance name starts
FBAR_VIEW = 350      # viewBox width the column scales to


def facility_bars(items: list[dict], theme: dict, hatch_id: str) -> str:
    """Substance bars for one drug class at one facility.

    Only substances clearing the reporting threshold reach here, so a short list
    means the site had few patients on that class rather than few substances.
    """
    if not items:
        return ('<p class="cap">No substance reaches the reporting '
                'threshold at this site.</p>')
    top = max(i["patients"] for i in items)
    parts = []
    for index, item in enumerate(items):
        y = index * (FBAR_H + FBAR_GAP)
        width = max(2, round(FBAR_W * item["patients"] / top))
        fill = f"url(#{hatch_id})" if item["metabolite"] else theme["bar"]
        parts.append(
            f'<rect x="0" y="{y}" width="{width}" height="{FBAR_H}" '
            f'fill="{fill}" rx="1"/>'
            f'<text x="{width + 5}" y="{y + FBAR_H - 1}" {BAR_VALUE}>'
            f'{item["patients"]}</text>'
            f'<text x="{FBAR_LABEL_X}" y="{y + FBAR_H - 1}" {BAR_LABEL}>'
            f'{esc(item["name"])}{" &#8226;" if item["metabolite"] else ""}</text>'
        )
    height = len(items) * (FBAR_H + FBAR_GAP)
    return (f'<svg viewBox="0 0 {FBAR_VIEW} {height}" width="100%" '
            f'height="{height}" role="img">'
            f'{hatch_pattern(hatch_id, theme)}{"".join(parts)}</svg>')


# Intent is its own dimension, so it gets its own colours rather than borrowing
# the drug-class navy and maroon. A legend sits directly above the chart.
INTENT_COLOURS = {
    "Unintentional": "#2E6E8E",
    "Intentional": "#7B1E3C",
    "Unknown": "#C3CDD6",
}
OVERLAP_COLOURS = ["#003D78", "#5B3F72", "#7B1E3C", "#C3CDD6"]


def stacked_bands(bands: list[dict], row_h: int) -> str:
    """Age bands as 100% stacked bars, one row each."""
    # Full width either way: stacked under OUTCOME rather than beside it, the
    # old 250px bar used a third of the space available. The row height varies
    # because the two sheets have different amounts of room left -- see
    # BAND_ROW_H, and the sweep recorded there.
    gap, label_w, value_w = row_h // 3, 40, 34
    width = CONTENT_W - label_w - value_w
    parts = []
    for index, band in enumerate(bands):
        y = index * (row_h + gap)
        parts.append(
            f'<text x="0" y="{y + row_h - 3}" {BAND_LABEL}>'
            f'{esc(band["label"])}</text>'
        )
        x = float(label_w)
        for name, count, share in band["segments"]:
            seg = width * share
            parts.append(
                f'<rect x="{x:.1f}" y="{y}" width="{seg:.1f}" height="{row_h}" '
                f'fill="{INTENT_COLOURS[name]}"/>'
            )
            if seg > 26:
                fill = "#FFFFFF" if name != "Unknown" else INK
                parts.append(
                    f'<text x="{x + seg / 2:.1f}" y="{y + row_h - 3.5}" '
                    f'font-family="{SVG_FONT}" font-size="8" '
                    f'font-weight="600" text-anchor="middle" fill="{fill}">'
                    f'{round(share * 100)}%</text>'
                )
            x += seg
        parts.append(
            f'<text x="{label_w + width + 5}" y="{y + row_h - 3}" {BAR_VALUE}>'
            f'{band["patients"]}</text>'
        )
    height = len(bands) * (row_h + gap)
    legend = " ".join(
        f'<span><i style="background:{INTENT_COLOURS[k]}"></i>{k}</span>'
        for k in INTENT_COLOURS
    )
    return (f'<p class="legend-row">{legend}</p>'
            f'<svg viewBox="0 0 {label_w + width + 26} {height}" width="100%" '
            f'height="{height}" role="img">{"".join(parts)}</svg>')


def overlap_bar(poly: dict, total: int, height: int) -> str:
    """Opioid / stimulant overlap as one 100% bar."""
    items = [("Opioid only", poly["opioid_only"]), ("Both", poly["both"]),
             ("Stimulant only", poly["stimulant_only"]),
             ("Neither", poly["neither"])]
    parts, keys, x = [], [], 0.0
    for index, (name, count) in enumerate(items):
        seg = 100 * count / total
        colour = OVERLAP_COLOURS[index]
        parts.append(
            f'<div class="oseg" style="width:{seg:.2f}%;background:{colour}'
            f'{";color:" + INK if index == 3 else ""}">{round(seg)}%</div>'
        )
        keys.append(f'<span><i style="background:{colour}"></i>{name} '
                    f'{count}</span>')
    return (f'<div class="obar" style="height:{height}px">{"".join(parts)}</div>'
            f'<p class="legend-row">{"".join(keys)}</p>')


# The heatmap ramp. Light to dark navy, so a darker cell is a commoner pairing.
HEAT = ["#EDF1F5", "#CBD9E6", "#9DB6CE", "#6E92B4", "#41709B", "#1B5087", "#003D78"]
HEAT_MAX = 0.60      # the ramp tops out here; nothing observed exceeds it


def heat_colour(share: float) -> str:
    index = min(len(HEAT) - 1, int(share / HEAT_MAX * len(HEAT)))
    return HEAT[max(0, index)]


def class_bars(classes: list[dict], total: int) -> str:
    """Every drug class, ranked. Not a breakdown -- the shares sum past 100%."""
    # 'No class assigned' is not a drug class and headed the chart at 74%, which
    # read as a finding. It is described in the footnote instead.
    rows = [c for c in classes if c["show"] and c["raw"] != "Other"]
    withheld = [c for c in classes if not c["show"]]
    top = max(c["patients"] for c in rows)
    # Two columns, ranked down the left then down the right. A single column of
    # fourteen used 520 of the 758px content width and 210px of height; the page
    # has horizontal room to spare and none vertically, so the list trades one
    # for the other and the bars get taller rather than shorter.
    row_h, gap, label_w, width, col_w = 14, 5, 112, 186, CONTENT_W / 2
    per_col = -(-len(rows) // 2)
    parts = []
    for index, cls in enumerate(rows):
        x0 = (index // per_col) * col_w
        y = (index % per_col) * (row_h + gap)
        seg = width * cls["patients"] / top
        aside = cls["kind"] == "aside"
        fill = "#B9C6D4" if aside else THEME["opioids"]["bar"]
        parts.append(
            f'<text x="{x0 + label_w - 6:.1f}" y="{y + row_h - 4}" {BAR_LABEL} '
            f'text-anchor="end">{esc(cls["name"])}</text>'
            f'<rect x="{x0 + label_w:.1f}" y="{y}" width="{seg:.1f}" '
            f'height="{row_h}" fill="{fill}" rx="1"/>'
            f'<text x="{x0 + label_w + seg + 6:.1f}" y="{y + row_h - 4}" '
            f'{BAR_VALUE}>{cls["patients"]} &#183; '
            f'{pct(cls["patients"], total)}</text>'
        )
    height = per_col * (row_h + gap)
    note = ""
    if withheld:
        note = (f'<p class="cap">{", ".join(esc(c["name"]) for c in withheld)} '
                f'withheld &#8212; too few patients to report.</p>')
    return (f'<svg viewBox="0 0 {CONTENT_W} {height}" width="100%" '
            f'height="{height}" role="img">{"".join(parts)}</svg>{note}')


def cooccurrence_grid(matrix: dict) -> str:
    """Class-by-class co-occurrence, read along the row."""
    labels, rows = matrix["labels"], matrix["rows"]
    # Cells fill the content width rather than sitting in the left 416px of
    # it, and the rows are a pixel shorter, which is what the stacked
    # WHO/OUTCOME block below needed to keep the sheet on one page.
    label_w, cell_h, head_h = 116, 15, 20
    cell_w = (CONTENT_W - label_w) / len(labels)
    parts = []
    # Column headers sit flat and un-truncated. They were rotated -32 and cut to
    # 13 characters because the cells were 40px wide; at 80px the longest class
    # name fits horizontally with room to spare.
    for index, label in enumerate(labels):
        x = label_w + index * cell_w + cell_w / 2
        parts.append(
            f'<text x="{x:.1f}" y="{head_h - 7}" {AXIS_TEXT} font-size="7.5">'
            f'{esc(label)}</text>'
        )
    for r, row in enumerate(rows):
        y = head_h + r * cell_h
        parts.append(
            f'<text x="{label_w - 6}" y="{y + cell_h - 7}" {BAR_LABEL} '
            f'font-size="8.5" text-anchor="end">{esc(row["name"])}</text>'
        )
        for cidx, cell in enumerate(row["cells"]):
            x = label_w + cidx * cell_w
            if cell["self"]:
                parts.append(
                    f'<rect x="{x:.1f}" y="{y}" width="{cell_w - 1:.1f}" '
                    f'height="{cell_h - 1}" fill="#FFFFFF" stroke="{RULE}"/>'
                    f'<text x="{x + cell_w / 2:.1f}" y="{y + cell_h - 7}" '
                    f'font-family="{SVG_FONT}" font-size="8.5" font-weight="600" '
                    f'text-anchor="middle" fill="{INK}">{cell["patients"]}</text>'
                )
                continue
            if not cell["show"]:
                parts.append(
                    f'<rect x="{x:.1f}" y="{y}" width="{cell_w - 1:.1f}" '
                    f'height="{cell_h - 1}" fill="#F6F8FA"/>'
                    f'<text x="{x + cell_w / 2:.1f}" y="{y + cell_h - 7}" '
                    f'{AXIS_TEXT} font-size="8">&#183;</text>'
                )
                continue
            colour = heat_colour(cell["share"])
            ink = "#FFFFFF" if cell["share"] >= HEAT_MAX * 0.55 else INK
            parts.append(
                f'<rect x="{x:.1f}" y="{y}" width="{cell_w - 1:.1f}" '
                f'height="{cell_h - 1}" fill="{colour}"/>'
                f'<text x="{x + cell_w / 2:.1f}" y="{y + cell_h - 7}" '
                f'font-family="{SVG_FONT}" font-size="8.5" text-anchor="middle" '
                f'fill="{ink}">{round(cell["share"] * 100)}%</text>'
            )
    height = head_h + len(rows) * cell_h
    width = label_w + len(labels) * cell_w
    scale = "".join(
        f'<span><i style="background:{HEAT[i]}"></i>'
        f'{round(HEAT_MAX * i / len(HEAT) * 100)}%</span>'
        for i in range(len(HEAT))
    )
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'role="img">{"".join(parts)}</svg>'
            f'<p class="legend-row">{scale}</p>')


def render_overview_body(stats: dict, total: int) -> str:
    """Every drug class ranked, then how the classes overlap."""
    return f"""
<section class="panel">
  <h2>Every drug class detected</h2>
  <p class="cap">Share of the {total} patients with at least one detection in
    each class. A patient appears in every class they tested positive for, so
    these do not sum to 100%.</p>
  {class_bars(stats['classes'], total)}
</section>
<section class="panel">
  <h2>How the classes overlap</h2>
  <p class="cap">Read along a row: of the patients with that class, the share
    who also had the class in each column. The diagonal is the number of
    patients in the class. Not symmetric &#8212; half the opioid patients also
    had a stimulant, but a third of the larger cannabis group did.</p>
  {cooccurrence_grid(stats['matrix'])}
</section>"""


def render_facility_class(cls: dict, site_key: str) -> str:
    """One drug class within one facility."""
    theme = THEME[cls["key"]]
    subtypes = " &#183; ".join(
        (f'<b>{pct_f(s["share"])}</b> {esc(s["name"])}' if s["show"]
         else f'<i>{esc(s["name"])} withheld</i>')
        for s in cls["subtypes"]
    )
    sex = (f' &#183; {pct_f(cls["male_share"])} male' if cls["show_sex"] else "")
    return f"""
<div class="fclass" style="color:{theme['bar']}">
  <p class="fhead"><span class="fbig">{pct_f(cls['share'])}</span>
    {esc(cls['title'])}<span class="fsub">{cls['patients']} patients{sex}</span></p>
  <p class="fsubtypes">{subtypes}</p>
  {facility_bars(cls['substances'], theme, f"h-{cls['key']}-{site_key}")}
</div>"""


def png_size(raw: bytes) -> tuple[int, int]:
    """Pixel dimensions from a PNG's IHDR, so no imaging library is needed."""
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        raise SystemExit(f"{MAP_PATH} is not a PNG")
    return struct.unpack(">II", raw[16:24])


def lead_band(facilities: list[dict]) -> str:
    """The sheet's banked space: the state map, then a key naming its markers.

    Embedded as a data URI rather than linked: the output has to stay a single
    self-contained file that opens offline, and WeasyPrint resolves relative
    image paths against the HTML's location, which the dashboard has none of --
    it renders from a string in memory.
    """
    raw = MAP_PATH.read_bytes()
    # Sized in inline CSS, not `height: 100%` and not HTML width/height
    # attributes -- WeasyPrint honours neither, and laid the image out at its
    # intrinsic 535px, overflowing the band and pushing the sheet to two pages.
    # Both sides are scaled from the PNG header, so swapping the asset for one
    # of a different shape needs no edit here.
    src_w, src_h = png_size(raw)
    height = FACILITY_LEAD - 2 * MAP_INSET
    width = round(src_w * height / src_h)
    data = base64.b64encode(raw).decode("ascii")
    return (f'<div class="leadband">'
            f'<img class="leadmap" '
            f'style="width:{width}px;height:{height}px" '
            f'src="data:image/png;base64,{data}" '
            f'alt="Wisconsin, with Madison, Milwaukee and Green Bay marked">'
            f'{map_key(facilities)}'
            f'</div>')


def marker_ring(colour: str, size: int = 13) -> str:
    """One map marker, repeated as a legend swatch.

    SVG rather than a bordered element: WeasyPrint renders `border-radius: 50%`
    against a 3px border as a teardrop, so the CSS version did not read as the
    same symbol as the ring on the map.
    """
    r = size / 2
    return (f'<svg class="ring" width="{size}" height="{size}" '
            f'viewBox="0 0 {size} {size}" aria-hidden="true">'
            f'<circle cx="{r}" cy="{r}" r="{r - 1.6:.1f}" fill="#FFFFFF" '
            f'stroke="{colour}" stroke-width="3"/></svg>')


def map_key(facilities: list[dict]) -> str:
    """Names the three markers, in the order the sections below run.

    The swatch is drawn as a ring rather than a filled chip because that is what
    the markers on the map are; a solid dot would be a different symbol.
    """
    rows = []
    for site in facilities:
        city = site["city"]
        try:
            colour = MAP_MARKERS[city]
        except KeyError:
            raise SystemExit(
                f"no map marker colour for {city!r}; the legend and "
                f"{MAP_PATH.name} have to be updated together"
            ) from None
        rows.append(
            f'<li>{marker_ring(colour)}'
            f'<p><b>{esc(site["short"])}</b><span>{esc(city)}</span></p></li>'
        )
    return (f'<div class="mapkey"><h2>{esc(MAP_KEY_TITLE)}</h2>'
            f'<ul>{"".join(rows)}</ul></div>')


def render_facility_body(facilities: list[dict]) -> str:
    """One section per facility, each carrying the same two drug classes.

    Shares rather than counts throughout: the sites differ by a factor of seven
    in size, so counts would compare catchment rather than drugs.
    """
    sections = [lead_band(facilities)]
    for index, site in enumerate(facilities):
        classes = "".join(
            render_facility_class(c, str(index)) for c in site["classes"]
        )
        profile = "".join(
            f'<p><span>{esc(k)}</span><b>{esc(v)}</b></p>'
            for k, v in site["profile"]
        )
        sections.append(f"""
<section class="facility">
  <h2>{esc(site['short'])}<span>{esc(site['city'])}</span>
    <b>{site['patients']} patients &#183; {pct_f(site['share'])} of the
    cohort</b></h2>
  <div class="fgrid">{classes}
    <div class="fprofile">{profile}</div>
  </div>
</section>""")
    return "".join(sections)


def render_panel(panel: dict, total: int, cohort_male: float) -> str:
    try:
        theme = THEME[panel["key"]]
    except KeyError:
        raise SystemExit(
            f"no THEME colour for panel {panel['key']!r}; add one before "
            f"rendering"
        ) from None
    # Each subtype carries its own male share: they are sharper than the panel
    # aggregate (amphetamines 75% vs the panel's 64%), so showing only the
    # class-level donut would flatten a real difference.
    subtypes = "".join(
        f'<li><b>{pct(s["patients"], total)}</b> {esc(s["name"])}'
        f'<span>'
        + (f'{pct_f(s["male_share"])} male &#183; {s["patients"]} patients'
           if s["show_sex"] else f'{s["patients"]} patients')
        + f'</span></li>'
        for s in panel["subtypes"]
    )
    spot = panel["spotlight"]
    metabolites = any(s["metabolite"] for s in panel["substances"])
    return f"""
<section class="panel" style="color:{theme['bar']}">
  <h2>{esc(panel['title'])}</h2>
  <div class="panel-body">
    <div>
      <div class="big" style="color:{theme['bar']}">
        {pct(panel['patients'], total)}
        <small>of patients<br>({panel['patients']} of {total})</small>
      </div>
      <ul class="subtypes">{subtypes}</ul>
    </div>
    <div>
      <div class="spotlight">
        <span class="n">{spot['patients']}</span>
        <span class="nm">{esc(spot['name'])}</span>
        <span class="no">{esc(spot['note'])}</span>
      </div>
      {hbars(panel['substances'], theme, 'name', 'patients',
             'metabolite', f"h-{panel['key']}")}
      {'<p class="cap">&#8226; hatched = metabolite, not the parent drug</p>'
       if metabolites else ''}
    </div>
    <div class="aside">
      <div class="chart">
        {age_bars(panel['age'], theme)}
        <div class="legend"><b>Age</b> of the {panel['patients']}
          {esc(panel['noun'])}</div>
      </div>
      <div class="chart">
        {donut(panel['sex']['M'], panel['sex']['F'], theme, cohort_male)}
        <div class="legend"><b>Sex</b> of the {panel['patients']}
          {esc(panel['noun'])}<br>
          M {pct(panel['sex']['M'], panel['patients'])} &#183;
          F {pct(panel['sex']['F'], panel['patients'])}<br>
          <span class="tick">&#9866; all patients {pct_f(cohort_male)} M</span>
        </div>
      </div>
    </div>
  </div>
</section>"""


def render_header(title: str, period: str) -> str:
    """Masthead: lab, title, period, centred over a rule.

    Fixed arrangement -- only the title varies, and it comes from the body.
    """
    return (f'<header class="masthead">'
            f'<p class="eyebrow">{esc(EYEBROW)}</p>'
            f'<h1>{esc(title)}</h1>'
            f'<p class="org">{esc(ORG)} &#183; {esc(period)}</p>'
            f'</header>')


def unclassified_note(summary: dict) -> str:
    """One sentence on what the mapping leaves unclassified, and why it matters.

    A third of all detections carry no drug class, so the sheet cannot simply
    omit them. Most have an obvious class the vocabulary does not yet include;
    the adulterants are called out because they are a surveillance signal rather
    than incidental medication.
    """
    parts = []
    for family in summary["families"]:
        top = ", ".join(f"{esc(n)} {k}" for n, k in family["top"])
        parts.append(f"{family['label']} ({top})" if top else family["label"])
    listed = "; ".join(parts)
    return (
        f"{summary['patients']} patients carried at least one of "
        f"{summary['substances']} substances the drug dictionary gives no "
        f"class, so they are left off the chart above. Most have an obvious "
        f"class it does not yet include &#8212; {listed} &#8212; plus "
        f"{summary['unfamilied']} others. The adulterants are the ones worth "
        f"adding: they mark the illicit supply rather than incidental "
        f"medication."
    )


def render_footer(body: dict, stats: dict) -> str:
    """Footnotes: how to read, then the body's own caveat, then the cohort.

    Fixed per body rather than selectable. The first and last blocks are true of
    every sheet; the middle one explains what *this* body shows, which is why it
    travels with the body rather than being chosen separately.
    """
    total = stats["patients"]
    out = stats["outcome"]
    notes = stats["notes"]
    poly = out["polysubstance"]

    reading = (
        f"<b>How to read this.</b> Every percentage counts <b>patients</b>, not "
        f"detections &#8212; one exposure can produce several, so fentanyl and "
        f"its breakdown products are largely the same people. Bars rank "
        f"substances by how many patients they were found in, and hatched bars "
        f"are metabolites. Denominators differ: {total} patients for "
        f"demographics, {out['stay']['n']} for hospital stay, "
        f"{notes['bac']['tested']} for alcohol. Counts too small to report "
        f"without risking a patient&#8217;s identity are withheld."
    )

    caveat = body["caveat"].format(
        total=total,
        dominant=pct_f(stats["header"]["dominant_share"]),
        unclassified=unclassified_note(stats["unclassified"]),
        both=poly["both"],
        both_pct=pct(poly["both"], total),
        neither=poly["neither"],
        sites=", ".join(f"{s['city']} {s['patients']}"
                        for s in stats["header"]["sites"]),
    )

    cohort = (
        f"<b>Cohort.</b> {total} patients presenting to three Wisconsin "
        f"hospitals. {len(notes['analyte_less']['unrecorded'])} have no screen "
        f"result on file and are counted in demographics but not in any "
        f"detection figure; a further "
        f"{len(notes['analyte_less']['true_negative'])} was screened and "
        f"nothing was detected. No time trend is shown &#8212; the only date "
        f"available is a data-entry stamp, not a collection date."
    )

    # Last line, and the only one addressed to the reader rather than about the
    # data, so it is set apart rather than run on after the cohort note.
    contact = (
        f'<p class="contact"><b>Questions about this data, or want to '
        f'contribute to it?</b> Contact '
        f'<a href="mailto:{CONTACT_EMAIL}">{esc(CONTACT_EMAIL)}</a></p>'
    )
    return (f"<footer>{reading}<br><b>About this view.</b> {caveat}<br>"
            f"{cohort}{contact}</footer>")


def render_placeholder(body: dict) -> str:
    """A body variant that is chosen but not yet built.

    Stands **in place of** the panels rather than above them. Showing it above a
    working body made the selection visible but produced a two-page sheet and
    invited the reader to treat the panels below as the thing they had chosen.
    An empty frame is the honest depiction of a body that does not exist yet.
    """
    return f'''
<section class="placeholder">
  <b>Not built yet &#183; {esc(body["label"])}</b>
  <span>{esc(body["note"])}</span>
</section>'''


def render(stats: dict, period: str, body: str = "classes") -> str:
    body_variant = BODIES[body]
    # The WHERE strip exists to warn that an unsplit figure is mostly Madison.
    # A body already split by facility says that better, so it is dropped there
    # rather than stating the same thing twice.
    by_facility = body == "facility"
    total = stats["patients"]
    head = stats["header"]
    who = stats["who"]
    out = stats["outcome"]
    notes = stats["notes"]
    theme = THEME["opioids"]

    cohort_male = who["sex"]["M"] / total

    ps = out["polysubstance"]
    # The two charts under WHO and OUTCOME are cohort-wide. On a sheet whose
    # premise is comparing facilities they are the least aligned content on the
    # page, and they were taking the room the facility sections needed. Each
    # body carries the charts that match its premise.
    age_chart = "" if by_facility else (
        '<p class="chart-title">Manner of overdose, by age</p>'
        + stacked_bands(stats["age_intent"], BAND_ROW_H[body])
    )
    overlap_chart = "" if by_facility else (
        '<p class="chart-title">Opioid and stimulant overlap</p>'
        + overlap_bar(ps, total, OBAR_H[body])
    )

    # An unbuilt body replaces the panels; see render_placeholder.
    if not body_variant["built"]:
        panels_html = render_placeholder(body_variant)
    elif body == "overview":
        panels_html = render_overview_body(stats, total)
    elif by_facility:
        panels_html = render_facility_body(stats["facilities"])
    else:
        panels_html = "".join(
            render_panel(p, total, cohort_male) for p in stats["panels"]
        )

    kpis_html = "" if by_facility or body == "overview" else f"""<div class="kpis">    <div class="kpi"><b>{head['patients']}</b><span>patients screened</span></div>
    <div class="kpi"><b>{head['facilities']}</b><span>hospitals</span></div>
    <div class="kpi"><b>{head['substances']}</b><span>substances detected</span></div>
    <div class="kpi"><b>{pct(out['discharge']['counts'].get('Admitted', 0),
        out['discharge']['n'])}</b><span>admitted to hospital</span></div>
  </div>"""

    # Stacked wherever WHO and OUTCOME carry a chart: side by side, each chart
    # gets half the width and the age bands squeeze to unreadable. The facility
    # body has no charts here, so its two columns stay side by side.
    two_class = "two" if by_facility else "two stacked"
    two_html = f"""<div class="{two_class}">
    <div class="who">
      <h2>Who</h2>
      <div class="stat-line"><span>Median age</span>
        <b>{who['age']['median']:.0f} years</b></div>
      <div class="stat-line"><span>Under 18</span>
        <b class="flag">{who['minors']} &#183; {pct(who['minors'], total)}</b></div>
      <div class="stat-line"><span>Male / Female</span>
        <b>{pct(who['sex']['M'], total)} / {pct(who['sex']['F'], total)}</b></div>
      {age_chart}
    </div>
    <div>
      <h2>Outcome</h2>
      {stacked_row(out['discharge']['counts'], out['discharge']['n'],
                   theme, DISCHARGE_ORDER,
                   stats['withheld']['discharge_status'])}
      <div class="stat-line"><span>Median hospital stay</span>
        <b>{out['stay']['median']:.0f} days</b></div>
      <div class="stat-line"><span>Alcohol positive, of those tested</span>
        <b>{notes['bac']['positive']} of {notes['bac']['tested']}</b></div>
      {overlap_chart}
    </div>
  </div>"""

    return f"""<meta charset="utf-8">
<title>Non-Fatal Overdose Biosurveillance</title>
<style>{CSS}</style>
<div class="sheet {body}">
  {render_header(body_variant["title"], period)}

  {kpis_html}

  {panels_html}

  {two_html}

  {render_footer(body_variant, stats)}
</div>
"""


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=root / "versioned/validate_v1.csv"
    )
    parser.add_argument("--dest", type=Path, default=root / "output/onepager.html")
    parser.add_argument("--period", default="", help="e.g. 'July 2025 - March 2026'")
    parser.add_argument("--body", default="classes", choices=sorted(BODIES))
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"File not found: {args.source}")
    stats = compute_stats(load(args.source))
    page = render(stats, args.period or "Period not stated", args.body)

    args.dest.parent.mkdir(parents=True, exist_ok=True)
    args.dest.write_text(page, encoding="utf-8")

    print(f"rendered {len(page):,} bytes from {stats['patients']} patients")
    if stats["suppressed"]:
        print(f"\nsuppressed or outlined (< {SUPPRESS_BELOW}):")
        for item in stats["suppressed"]:
            print(f"  {item}")
    print("\nreminder: this page carries PHI-derived counts. Local file only --"
          " do not publish it to an external service.")
    print(f"\n  -> {args.dest}")


if __name__ == "__main__":
    main()
