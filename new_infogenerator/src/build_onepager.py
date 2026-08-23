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
import html
from pathlib import Path

from onepager_stats import SUPPRESS_BELOW, compute_stats, load

# House navy, from biosurveillance_dissemination/doc_styles.py, plus a maroon
# for the stimulant panel matching the reference one-pager.
THEME = {
    "opioids": {"bar": "#003D78", "dark": "#002B56", "soft": "#DCE6F0"},
    "stimulants": {"bar": "#7B1E3C", "dark": "#5C1229", "soft": "#F2DFE4"},
}
INK = "#16202B"
MUTED = "#5E6E7E"
RULE = "#D5DCE3"

BAR_W = 300          # px available for the longest substance bar
BAR_H = 13
BAR_GAP = 5
AGE_W = 152
AGE_H = 46
DONUT_R = 26


def esc(text: str) -> str:
    return html.escape(str(text))


def pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%"


def pct_f(fraction: float) -> str:
    return f"{round(100 * fraction)}%"


def hbars(items: list[dict], theme: dict, label_key: str, value_key: str,
          hatch_key: str | None = None, hatch_id: str = "") -> str:
    """Horizontal bars, widest first, scaled to the largest value."""
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
            f'<text x="{width + 6}" y="{y + BAR_H - 3}" class="bv">'
            f'{item[value_key]}</text>'
            f'<text x="{BAR_W + 46}" y="{y + BAR_H - 3}" class="bl">'
            f'{esc(item[label_key])}{" &#8226;" if hatched else ""}</text>'
        )
    height = len(items) * (BAR_H + BAR_GAP)
    return (f'<svg viewBox="0 0 640 {height}" width="100%" height="{height}" '
            f'role="img">{"".join(parts)}</svg>')


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
            f'<text x="{x + width / 2:.1f}" y="{AGE_H + 9}" class="ax">'
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
    circumference = 2 * 3.14159 * DONUT_R
    filled = circumference * male / total
    size = DONUT_R * 2 + 14
    centre = size / 2
    # Tick at the cohort's male share, on the same clockwise-from-top scale.
    angle = 2 * 3.14159 * baseline - 3.14159 / 2
    inner, outer = DONUT_R - 7.5, DONUT_R + 7.5
    import math
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
                order: list[str]) -> str:
    """Outcome shares as one stacked bar, labelled beneath.

    Segments below the suppression floor still take their true width -- the bar
    has to reach 100% -- but carry no inline number.
    """
    parts, labels, x = [], [], 0.0
    # A ramp rather than one pale tint, so the small segments stay
    # distinguishable and the bar visibly reaches 100%.
    ramp = [theme["bar"], theme["dark"], "#7C93AC", "#A9B9C8", theme["soft"]]
    for position, name in enumerate(order):
        count = counts.get(name, 0)
        if not count:
            continue
        width = 420 * count / total
        shade = ramp[min(position, len(ramp) - 1)]
        text = "#FFFFFF" if position < 3 else INK
        parts.append(
            f'<rect x="{x:.1f}" y="0" width="{width:.1f}" height="26" '
            f'fill="{shade}"/>'
        )
        if width > 44:
            parts.append(
                f'<text x="{x + width / 2:.1f}" y="17" class="sv" '
                f'fill="{text}">{pct(count, total)}</text>'
            )
        labels.append(f"{esc(name)} {count}")
        x += width
    return (f'<svg viewBox="0 0 420 26" width="100%" height="26" role="img">'
            f'{"".join(parts)}</svg>'
            f'<p class="cap">{" &#183; ".join(labels)}</p>')


def sites_bar(sites: list[dict], total: int) -> str:
    """The three hospitals as one proportional bar plus a keyed legend.

    A list of names hides what matters: the cohort is two-thirds one site, so
    any unstratified figure on this page is largely that site. A bar makes the
    imbalance the first thing a reader sees. Labels sit in a legend rather than
    under their segments because the facility names are far wider than the
    smaller segments.
    """
    shades = ["#003D78", "#3D6C99", "#8CA6BF"]
    parts, keys, x = [], [], 0.0
    for index, site in enumerate(sites):
        width = 100 * site["share"]
        shade = shades[min(index, len(shades) - 1)]
        parts.append(
            f'<div class="seg" style="width:{width:.2f}%;background:{shade}">'
            + (f'{pct(site["patients"], total)}' if width > 12 else '')
            + '</div>'
        )
        keys.append(
            f'<div class="key"><i style="background:{shade}"></i>'
            f'<span><b>{esc(site["short"])}</b>, {esc(site["city"])}'
            f'<em>{site["patients"]} patients &#183; '
            f'{pct(site["patients"], total)}</em></span></div>'
        )
    return (f'<div class="sitebar">{"".join(parts)}</div>'
            f'<div class="keys">{"".join(keys)}</div>')


def hatch_defs() -> str:
    """One diagonal-hatch pattern per panel, for metabolite bars."""
    patterns = []
    for key, theme in THEME.items():
        patterns.append(
            f'<pattern id="h-{key}" width="5" height="5" '
            f'patternTransform="rotate(45)" patternUnits="userSpaceOnUse">'
            f'<rect width="5" height="5" fill="{theme["soft"]}"/>'
            f'<line x1="0" y1="0" x2="0" y2="5" stroke="{theme["bar"]}" '
            f'stroke-width="2.2"/></pattern>'
        )
    return (f'<svg width="0" height="0" style="position:absolute">'
            f'<defs>{"".join(patterns)}</defs></svg>')


CSS = f"""
@page {{ size: letter portrait; margin: 0.4in; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #EEF1F4; color: {INK};
  font: 400 12px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI",
  system-ui, sans-serif; -webkit-font-smoothing: antialiased; }}
.sheet {{ width: 8.5in; min-height: 11in; margin: 18px auto; padding: 0.42in;
  background: #FFF; box-shadow: 0 2px 14px rgba(0,0,0,.14);
  display: flex; flex-direction: column; gap: 13px; }}
h1 {{ margin: 0; font: 600 25px/1.1 Georgia, "Iowan Old Style", serif;
  letter-spacing: -.01em; }}
.eyebrow {{ font: 600 9px/1 system-ui, sans-serif; letter-spacing: .13em;
  text-transform: uppercase; color: {THEME['opioids']['bar']}; }}
header {{ border-bottom: 2.5px solid {INK}; padding-bottom: 11px;
  display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; }}
header .org {{ text-align: right; color: {MUTED}; font-size: 10.5px;
  line-height: 1.35; }}
.kpis {{ display: flex; gap: 0; border: 1px solid {RULE}; }}
.kpi {{ flex: 1; padding: 9px 12px; border-right: 1px solid {RULE}; }}
.kpi:last-child {{ border-right: 0; }}
.kpi b {{ display: block; font: 600 23px/1 Georgia, serif;
  font-variant-numeric: tabular-nums; }}
.kpi span {{ display: block; margin-top: 2px; font-size: 9.5px;
  color: {MUTED}; letter-spacing: .02em; }}
.where h2 {{ margin: 0 0 7px; font: 600 12px/1 system-ui, sans-serif;
  letter-spacing: .11em; text-transform: uppercase; }}
.sitebar {{ display: flex; height: 22px; overflow: hidden; border-radius: 2px; }}
.seg {{ display: flex; align-items: center; justify-content: center;
  color: #FFF; font: 600 10px system-ui, sans-serif;
  font-variant-numeric: tabular-nums; }}
.keys {{ display: flex; gap: 22px; margin-top: 7px; flex-wrap: wrap; }}
.key {{ display: flex; gap: 6px; align-items: flex-start; font-size: 9.5px;
  color: {MUTED}; }}
.key i {{ width: 8px; height: 8px; border-radius: 1px; margin-top: 2.5px;
  flex: none; }}
.key b {{ color: {INK}; font-weight: 600; }}
.key em {{ display: block; font-style: normal;
  font-variant-numeric: tabular-nums; }}
.panel {{ border-top: 1px solid {RULE}; padding-top: 11px; }}
.panel h2 {{ margin: 0 0 9px; font: 600 12px/1 system-ui, sans-serif;
  letter-spacing: .11em; text-transform: uppercase; }}
.panel-body {{ display: grid; grid-template-columns: 108px 1fr 158px;
  gap: 16px; align-items: start; }}
.big {{ font: 600 38px/1 Georgia, serif; letter-spacing: -.02em; }}
.big small {{ display: block; font: 400 9.5px/1.35 system-ui, sans-serif;
  color: {MUTED}; letter-spacing: .02em; margin-top: 3px; }}
.subtypes {{ margin: 9px 0 0; padding: 0; list-style: none; font-size: 10px;
  color: {INK}; display: flex; flex-direction: column; gap: 6px; }}
.subtypes b {{ font-variant-numeric: tabular-nums; }}
.subtypes span {{ display: block; font-size: 8.5px; color: {MUTED};
  font-variant-numeric: tabular-nums; margin-top: 1px; }}
.spotlight {{ border-left: 3px solid currentColor; padding: 2px 0 2px 9px;
  margin-bottom: 9px; }}
.spotlight .n {{ font: 600 17px/1 Georgia, serif;
  font-variant-numeric: tabular-nums; }}
.spotlight .nm {{ font-weight: 600; font-size: 11.5px; }}
.spotlight .no {{ display: block; color: {MUTED}; font-size: 9.5px;
  margin-top: 1px; }}
.aside {{ display: flex; flex-direction: column; gap: 10px; align-items: center; }}
.chart {{ display: flex; flex-direction: column; align-items: center; gap: 3px; }}
.legend {{ font-size: 8.5px; color: {MUTED}; text-align: center; line-height: 1.35; }}
.legend b {{ color: {INK}; font-weight: 600; }}
.tick {{ color: {INK}; }}
.bv {{ font: 600 9px system-ui, sans-serif; fill: {MUTED};
  font-variant-numeric: tabular-nums; }}
.bl {{ font: 400 10px system-ui, sans-serif; fill: {INK}; }}
.ax {{ font: 400 6.2px system-ui, sans-serif; fill: {MUTED};
  text-anchor: middle; letter-spacing: -.02em; }}
.sv {{ font: 600 10px system-ui, sans-serif; text-anchor: middle;
  font-variant-numeric: tabular-nums; }}
.cap {{ margin: 4px 0 0; font-size: 9.5px; color: {MUTED}; }}
.two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px;
  border-top: 1px solid {RULE}; padding-top: 11px; }}
.two h2 {{ margin: 0 0 8px; font: 600 12px/1 system-ui, sans-serif;
  letter-spacing: .11em; text-transform: uppercase; }}
.stat-line {{ display: flex; justify-content: space-between;
  font-size: 10.5px; padding: 2.5px 0; border-bottom: 1px dotted {RULE}; }}
.stat-line b {{ font-variant-numeric: tabular-nums; }}
.flag {{ color: {THEME['stimulants']['bar']}; font-weight: 600; }}
footer {{ margin-top: auto; border-top: 1px solid {RULE}; padding-top: 9px;
  font-size: 8.5px; line-height: 1.5; color: {MUTED}; }}
footer b {{ color: {INK}; }}
@media print {{ body {{ background: #FFF; }}
  .sheet {{ box-shadow: none; margin: 0; width: auto; padding: 0; }} }}
"""


def render_panel(panel: dict, total: int, cohort_male: float) -> str:
    theme = THEME[panel["key"]]
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


def render(stats: dict, period: str) -> str:
    total = stats["patients"]
    head = stats["header"]
    who = stats["who"]
    out = stats["outcome"]
    notes = stats["notes"]
    theme = THEME["opioids"]

    sites = sites_bar(head["sites"], total)
    cohort_male = who["sex"]["M"] / total
    panels = "".join(
        render_panel(p, total, cohort_male) for p in stats["panels"]
    )

    manner = who["od_manner"]["counts"]
    manner_lines = "".join(
        f'<div class="stat-line"><span>{esc(k)}</span>'
        f'<b>{v} &#183; {pct(v, who["od_manner"]["n"])}</b></div>'
        for k, v in sorted(manner.items(), key=lambda kv: -kv[1])
        if v >= SUPPRESS_BELOW
    )
    ps = out["polysubstance"]

    return f"""<title>Non-Fatal Overdose Biosurveillance</title>
<style>{CSS}</style>
{hatch_defs()}
<div class="sheet">
  <header>
    <div>
      <p class="eyebrow">Non-fatal overdose biosurveillance</p>
      <h1>What Wisconsin overdose patients tested positive for</h1>
    </div>
    <div class="org">Wisconsin State Laboratory of Hygiene<br>{esc(period)}</div>
  </header>

  <div class="kpis">
    <div class="kpi"><b>{head['patients']}</b><span>patients screened</span></div>
    <div class="kpi"><b>{head['facilities']}</b><span>hospitals</span></div>
    <div class="kpi"><b>{head['substances']}</b><span>substances detected</span></div>
    <div class="kpi"><b>{pct(out['discharge']['counts'].get('Admitted', 0),
        out['discharge']['n'])}</b><span>admitted to hospital</span></div>
  </div>
  <section class="where">
    <h2>Where</h2>
    {sites}
    <p class="cap">Two-thirds of the cohort presented at one hospital, so any
      figure on this page that is not split by site largely describes
      {esc(head['sites'][0]['city'])}.</p>
  </section>

  {panels}

  <div class="two">
    <div>
      <h2>Who</h2>
      <div class="stat-line"><span>Median age</span>
        <b>{who['age']['median']:.0f} years</b></div>
      <div class="stat-line"><span>Under 18</span>
        <b class="flag">{who['minors']} &#183; {pct(who['minors'], total)}</b></div>
      <div class="stat-line"><span>Male / Female</span>
        <b>{pct(who['sex']['M'], total)} / {pct(who['sex']['F'], total)}</b></div>
      {manner_lines}
      <p class="cap">Intent flips with age: no intentional overdose under 10,
        but a majority intentional at 10&#8211;17.</p>
    </div>
    <div>
      <h2>Outcome</h2>
      {stacked_row(out['discharge']['counts'], out['discharge']['n'],
                   theme, ['Admitted', 'Discharged', 'Transferred',
                           'Other', 'Unknown'])}
      <div class="stat-line"><span>Median hospital stay</span>
        <b>{out['stay']['median']:.0f} days</b></div>
      <div class="stat-line"><span>Both an opioid and a stimulant</span>
        <b class="flag">{ps['both']} &#183; {pct(ps['both'], total)}</b></div>
      <div class="stat-line"><span>Alcohol positive, of those tested</span>
        <b>{notes['bac']['positive']} of {notes['bac']['tested']}</b></div>
    </div>
  </div>

  <footer>
    <b>How to read this.</b> Every percentage counts <b>patients</b>, not
    detections &#8212; one exposure can produce several. Bars rank substances by
    how many patients they were found in; hatched bars are metabolites, so
    fentanyl and its breakdown products are largely the same people.
    Denominators differ: {total} patients for demographics,
    {out['stay']['n']} for hospital stay, {notes['bac']['tested']} for alcohol.<br>
    <b>Not shown, and why.</b> No time trend &#8212; the only date available is a
    data-entry stamp, not a collection date. No urine-vs-plasma comparison
    &#8212; sample type was unrecorded for most patients. No race or ethnicity
    breakdown &#8212; several categories are small enough to risk identifying a
    patient. Cannabinoids were found in {notes['cannabis']['any']} patients but
    almost entirely as metabolites ({notes['cannabis']['parent_only']} with a
    parent compound), indicating earlier use rather than acute intoxication.
    Naloxone, given in {notes['treatment']['naloxone']} cases, is treatment and
    is excluded from the charts.<br>
    <b>Cohort.</b> {total} patients presenting to three Wisconsin hospitals.
    {len(notes['no_analyte_patients'])} have no screen result on file and are
    counted in demographics but not in any detection figure.
  </footer>
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
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"File not found: {args.source}")
    stats = compute_stats(load(args.source))
    page = render(stats, args.period or "Period not stated")

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
