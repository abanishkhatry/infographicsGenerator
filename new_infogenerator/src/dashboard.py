"""A local picker for assembling the one-pager.

Serves a small form on ``127.0.0.1``: choose a body and a reporting period, and
the sheet renders beside the controls. The masthead and the footnotes are not
selectable -- the title and the caveats follow the body, because a sheet whose
footnotes do not match its charts is worse than one with fewer options. Start it
with::

    python3 src/dashboard.py            # then open http://127.0.0.1:8000

Why a local server rather than a browser-side builder. The suppression rule and
patient-level counting live in ``onepager_stats``; a picker that re-rendered in
JavaScript would fork that logic, and a fork is exactly what put a withheld cell
on the page once already. Here the browser only chooses; every number is still
computed and adjudicated in Python, once.

Binds to 127.0.0.1 deliberately. The rendered page carries cohort counts derived
from PHI, so it must not be reachable from the network. Nothing is written to
disk unless you ask for a download.

Not built yet: two of the three body variants. Choosing one renders a panel
describing what it will contain, above the current body, so the selection is
visibly doing something and a placeholder is never mistaken for a finished
panel.
"""

from __future__ import annotations

import argparse
import html
import io
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from build_onepager import BODIES, render
from onepager_stats import compute_stats, load

FORMATS = {
    "preview": "Preview in the browser",
    "html": "Download HTML (self-contained, opens offline)",
    "pdf": "Download PDF (letter, print-ready)",
}

DEFAULTS = {
    "body": "classes",
    "format": "preview",
    "period": "July 2025 – March 2026",
}


def esc(text: str) -> str:
    return html.escape(str(text))


FORM_CSS = """
* { box-sizing: border-box; }
html, body { height: 100%; }
body { margin: 0; background: #EEF1F4; color: #16202B;
  font: 400 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui,
  sans-serif; -webkit-font-smoothing: antialiased;
  display: grid; grid-template-columns: 372px minmax(0, 1fr);
  grid-template-areas: "controls stage"; height: 100vh; overflow: hidden; }
body > script { display: none; }
.panel { grid-area: controls; overflow-y: auto; padding: 22px 20px 30px;
  background: #EEF1F4; border-right: 1px solid #D5DCE3; }
.stage { grid-area: stage; background: #E2E7EC; overflow: hidden;
  position: relative; min-width: 0; }
.stage iframe { width: 100%; height: 100%; border: 0; display: block; }
.stage .badge { position: absolute; top: 10px; right: 14px; z-index: 2;
  background: rgba(255,255,255,.9); border: 1px solid #D5DCE3; border-radius: 3px;
  padding: 3px 9px; font: 600 10px/1.6 system-ui, sans-serif;
  letter-spacing: .06em; text-transform: uppercase; color: #5E6E7E; }
h1 { margin: 0 0 4px; font: 600 20px/1.2 Georgia, serif; }
.sub { margin: 0 0 18px; color: #5E6E7E; font-size: 12px; }
fieldset { border: 1px solid #D5DCE3; background: #FFF; border-radius: 3px;
  margin: 0 0 14px; padding: 11px 13px 13px; }
legend { font: 600 10px/1 system-ui, sans-serif; letter-spacing: .12em;
  text-transform: uppercase; color: #003D78; padding: 0 6px; }
label.opt { display: flex; gap: 10px; align-items: flex-start; padding: 7px 0;
  border-bottom: 1px dotted #E4E9EE; cursor: pointer; }
label.opt:last-child { border-bottom: 0; }
label.opt input { margin-top: 3px; flex: none; }
label.opt b { font-weight: 600; }
label.opt span.desc { display: block; color: #5E6E7E; font-size: 12px;
  margin-top: 2px; }
label.opt span.tag { display: inline-block; margin-left: 6px; padding: 1px 6px;
  border-radius: 2px; font: 600 9px/1.6 system-ui, sans-serif;
  letter-spacing: .06em; text-transform: uppercase; }
label.opt span.tag-built { background: #DCE6F0; color: #003D78; }
label.opt span.tag-planned { background: #F2DFE4; color: #7B1E3C; }
input[type=text] { width: 100%; padding: 7px 9px; border: 1px solid #D5DCE3;
  border-radius: 3px; font: inherit; }
.actions { display: flex; gap: 8px; }
button { flex: 1; background: #003D78; color: #FFF; border: 0; border-radius: 3px;
  padding: 10px 12px; font: 600 12.5px system-ui, sans-serif; cursor: pointer; }
button.ghost { background: #FFF; color: #003D78; border: 1px solid #B9C6D4; }
button:hover { background: #002B56; }
button.ghost:hover { background: #F2F6FA; }
.warn { margin: 14px 0 0; padding: 9px 11px; background: #FFF7E6;
  border: 1px solid #E8D9B0; border-radius: 3px; font-size: 11.5px;
  line-height: 1.45; color: #6B5520; }
"""


def radio(name: str, key: str, title: str, note: str, checked: bool,
          tag: str = "") -> str:
    mark = " checked" if checked else ""
    tag_html = (f'<span class="tag tag-{tag}">{tag}</span>'
                if tag else "")
    note_html = f'<span class="desc">{esc(note)}</span>' if note else ""
    return (f'<label class="opt"><input type="radio" name="{name}" '
            f'value="{esc(key)}"{mark}>'
            f'<div><b>{esc(title)}</b>{tag_html}{note_html}</div></label>')


def form_page(selected: dict) -> str:
    bodies = "".join(
        radio("body", key, variant["label"].replace(" (built)", "")
              .replace(" (planned)", ""), variant["note"],
              selected["body"] == key,
              "built" if variant["built"] else "planned")
        for key, variant in BODIES.items()
    )
    formats = "".join(
        radio("format", key, label.split(" (")[0],
              label.split(" (")[1].rstrip(")") if " (" in label else "",
              selected["format"] == key)
        for key, label in FORMATS.items()
    )
    return f"""<!doctype html>
<meta charset="utf-8"><title>One-pager builder</title>
<style>{FORM_CSS}</style>
<div class="panel">
  <h1>One-pager builder</h1>
  <p class="sub">373 patients &#183; every figure computed in Python, so
    suppression is applied once. The title and the footnotes follow the body
    you pick.</p>
  <form id="f" method="post" action="/build" target="pv">
    <!-- The form submits to the iframe on every change, so it needs a format
         even when no button was pressed. The download buttons carry their own
         field name rather than overriding this one: a button sharing a name
         with a hidden input sends *both* values, and the hidden one wins. -->
    <input type="hidden" name="format" value="preview">
    <fieldset><legend>Body</legend>{bodies}</fieldset>
    <fieldset><legend>Reporting period</legend>
      <input type="text" name="period" value="{esc(selected['period'])}">
    </fieldset>
    <div class="actions">
      <button type="submit" name="download" value="pdf"
              formtarget="_blank">Download PDF</button>
      <button type="submit" name="download" value="html" class="ghost"
              formtarget="_blank">HTML</button>
    </div>
  </form>
  <p class="warn"><b>Local only.</b> This page and everything it generates carry
    cohort counts derived from PHI. The server is bound to 127.0.0.1 and nothing
    is written to disk unless you download.</p>
</div>
<div class="stage">
  <span class="badge">Live preview</span>
  <iframe name="pv" title="One-pager preview"></iframe>
</div>
<script>
  const f = document.getElementById('f');
  // Re-render on any change. The hidden field keeps the default action a
  // preview; the download buttons override it via their own name/value.
  f.addEventListener('change', () => f.submit());
  f.addEventListener('input', e => {{
    if (e.target.type === 'text') {{
      clearTimeout(window._t);
      window._t = setTimeout(() => f.submit(), 400);
    }}
  }});
  f.submit();
</script>
"""


def render_pdf(page: str) -> bytes:
    """The sheet as a print-ready PDF, checked to be exactly one page.

    A one-pager that quietly becomes two is the failure worth catching here: it
    still downloads, still looks right on screen, and the second page is usually
    the footnotes -- the part carrying the denominators and the caveats. The
    page count is asserted rather than trusted, because the sheet has run over
    before and the layout will change again as bodies are added.
    """
    from weasyprint import HTML

    document = HTML(string=page).render()
    if len(document.pages) != 1:
        raise SystemExit(
            f"the sheet renders as {len(document.pages)} pages, not one. "
            f"Something in the layout has grown past the printable area; "
            f"tighten it rather than shipping a two-page one-pager."
        )
    buf = io.BytesIO()
    document.write_pdf(buf)
    return buf.getvalue()


def preview_wrapper(page: str) -> str:
    """The sheet, scaled so the whole page is visible in the preview pane.

    The sheet is a fixed 8.5in x 11in-or-taller. Scaling by width alone is not
    enough: on a wide window the scale lands at 1, the sheet renders taller than
    the pane, and the footer is clipped. So it fits to whichever dimension binds.

    ``overflow: hidden`` on the body is what makes that safe -- the sheet's
    layout box keeps its full unscaled height even when transformed, so without
    it the pane would show a scrollbar for space the scaled page does not
    occupy.

    Nothing here touches the sheet's own styles, so what is downloaded is
    byte-identical to what is previewed.
    """
    return page + """
<style>
  body { background: #E2E7EC; margin: 0; overflow: hidden; }
  .sheet { transform-origin: top center; margin: 12px auto; }
</style>
<script>
  const PAD = 24;
  function fit() {
    const sheet = document.querySelector('.sheet');
    if (!sheet) return;
    sheet.style.transform = 'none';          // measure unscaled
    const w = sheet.offsetWidth, h = sheet.offsetHeight;
    const scale = Math.min(1,
      (window.innerWidth - PAD) / w,
      (window.innerHeight - PAD) / h);
    sheet.style.transform = 'scale(' + scale + ')';
    document.body.style.height = (h * scale + PAD) + 'px';
  }
  window.addEventListener('resize', fit);
  window.addEventListener('load', fit);     // re-fit once fonts have settled
  fit();
</script>
"""


class Handler(BaseHTTPRequestHandler):
    rows: list[dict[str, str]] = []

    def log_message(self, fmt, *args):  # quieter than the default
        print(f"  {self.command} {self.path}")

    def _send(self, body: bytes, content_type: str, filename: str = "") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Never cache. The builder re-renders on every change, so a stale copy
        # shows the wrong layout; and every response carries cohort counts
        # derived from PHI, which should not sit in the browser's disk cache.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        if filename:
            self.send_header(
                "Content-Disposition", f'attachment; filename="{filename}"'
            )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        self._send(form_page(DEFAULTS).encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != "/build":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        fields = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

        # Every selection is validated against the known variants. An unknown
        # key falls back to the default rather than reaching render().
        choice = {
            "body": fields.get("body") if fields.get("body") in BODIES
            else DEFAULTS["body"],
            # A pressed download button decides the format; otherwise this is
            # the iframe's own re-render and the hidden field applies.
            "format": (fields.get("download") or fields.get("format"))
            if (fields.get("download") or fields.get("format")) in FORMATS
            else DEFAULTS["format"],
            "period": fields.get("period", "").strip() or DEFAULTS["period"],
        }
        print(f"    body={choice['body']} format={choice['format']}")

        stats = compute_stats(self.rows)
        page = render(stats, choice["period"], choice["body"])

        if choice["format"] == "pdf":
            try:
                pdf = render_pdf(page)
            except ImportError:
                self._send(
                    b"weasyprint is not installed; choose HTML or Preview.",
                    "text/plain; charset=utf-8",
                )
                return
            self._send(pdf, "application/pdf", "onepager.pdf")
        elif choice["format"] == "html":
            self._send(page.encode("utf-8"), "text/html; charset=utf-8",
                       "onepager.html")
        else:
            self._send(preview_wrapper(page).encode("utf-8"),
                       "text/html; charset=utf-8")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=root / "versioned/validate_v1.csv"
    )
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"File not found: {args.source}")

    # Loaded once at startup: the data does not change while the server runs,
    # and re-reading per request would only add latency.
    Handler.rows = load(args.source)
    stats = compute_stats(Handler.rows)
    print(f"loaded {stats['patients']} patients from {args.source.name}")
    if stats["suppressed"]:
        print(f"suppressed (< 11): {len(stats['suppressed'])} cell(s)")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"\n  builder on http://127.0.0.1:{args.port}   (ctrl-c to stop)")
    print("  local only — the rendered page carries PHI-derived counts\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
