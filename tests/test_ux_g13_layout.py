"""WYZE-022 G13: a phone-width dashboard layout.

The viewport meta is present, no fixed layout width exceeds 360 px, and wide
content (every table) scrolls inside its own container, so the page itself
never scrolls sideways. When a local Chrome is available (CHROME_BIN, or the
standard macOS install), the synthetic PHANTOM_MODE pages are also rendered at
390 px wide with the network blocked, and the layout is measured from the DOM.
No screenshot is taken and no media exists on these pages. Synthetic data only.
"""
from __future__ import annotations

import html as htmlmod
import json
import os
import re
import select
import subprocess
import time
from pathlib import Path

import pytest

from .dashboard_helpers import PAGES, WEB, build_db, client, parse

MAX_FIXED_PX = 360
PHONE_WIDTH = 390
CSS = WEB / "static" / "app.css"
TEMPLATES = sorted((WEB / "templates").glob("*.html"))
FIXED_WIDTH_PROPS = ("width", "min-width", "flex-basis", "grid-template-columns", "column-width")


def css_declarations(text: str) -> list[tuple[str, str, str]]:
    """(selector, property, value) for every declaration, media blocks included."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    out = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
        for decl in body.split(";"):
            if ":" in decl:
                prop, value = decl.split(":", 1)
                out.append((selector.strip(), prop.strip().lower(), value.strip()))
    return out


def oversized(declarations) -> list[str]:
    """Fixed widths above the phone budget. max-width only caps, so it is exempt."""
    found = []
    for selector, prop, value in declarations:
        if prop in FIXED_WIDTH_PROPS:
            for px in re.findall(r"(\d+(?:\.\d+)?)px", value):
                if float(px) > MAX_FIXED_PX:
                    found.append(f"{selector} {{ {prop}: {value} }}")
    return found


def test_g13_viewport_meta_is_present(monkeypatch, tmp_path):
    base = (WEB / "templates" / "_base.html").read_text()
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in base
    c = client(monkeypatch, tmp_path, phantom=True)
    for page in PAGES:
        metas = parse(c.get(page).get_data(as_text=True)).find_all("meta", name="viewport")
        assert [m.attrs["content"] for m in metas] == ["width=device-width, initial-scale=1"]


def test_g13_no_fixed_layout_width_exceeds_360px():
    # Positive control: the check fires on a fixed wide table.
    assert oversized(css_declarations("table { min-width: 800px; } main { max-width: 1280px; }")) \
        == ["table { min-width: 800px }"]
    assert oversized(css_declarations(CSS.read_text())) == []
    for template in TEMPLATES:
        text = template.read_text()
        styles = re.findall(r'style="([^"]*)"', text)
        assert oversized(css_declarations("".join(f"x {{ {s} }}" for s in styles))) == [], \
            template.name
        widths = [int(w) for w in re.findall(r'\swidth="(\d+)"', text)]
        assert all(w <= MAX_FIXED_PX for w in widths), template.name


def tables_outside_scrollers(root) -> int:
    return sum(1 for t in root.find_all("table")
               if not any(a.tag == "div" and a.has_class("table-scroll") for a in t.ancestors()))


def test_g13_every_table_scrolls_in_its_own_named_focusable_container(monkeypatch, tmp_path):
    rules = {(sel, prop): value for sel, prop, value in css_declarations(CSS.read_text())}
    assert rules[(".table-scroll", "overflow-x")] == "auto"
    assert rules[("header.topbar", "flex-wrap")] == "wrap"
    assert rules[("code", "overflow-wrap")] == "anywhere"
    assert "@media (max-width: 600px)" in CSS.read_text()
    # Positive control: a bare table is counted.
    assert tables_outside_scrollers(parse("<main><table><tr><td>x</td></tr></table></main>")) == 1
    stored = build_db(tmp_path / "g13.db")
    for phantom in (True, False):
        c = client(monkeypatch, tmp_path, None if phantom else stored, phantom=phantom)
        for page in PAGES:
            response = c.get(page)
            assert response.status_code == 200, (page, phantom)
            root = parse(response.get_data(as_text=True))
            if phantom:   # control: the synthetic pages do render tables
                assert root.find_all("table"), page
            assert tables_outside_scrollers(root) == 0, (page, phantom)
            for box in [n for n in root.find_all("div") if n.has_class("table-scroll")]:
                assert box.attrs.get("tabindex") == "0" and box.attrs.get("role") == "region"
                assert box.attrs.get("aria-label"), (page, phantom)
    home = parse(client(monkeypatch, tmp_path, stored).get("/").get_data(as_text=True))
    assert len([n for n in home.find_all("div") if n.has_class("table-scroll")]) >= 4


# ---- rendered at 390 px (needs a local Chrome; skipped otherwise) ----------------

MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# Desktop Chrome will not open a window narrower than about 500 px, so the page
# is laid out in a 390 px iframe (a true 390 px viewport, media queries included)
# and reports its measurements to the wrapper, whose DOM Chrome prints.
WRAPPER = """<!doctype html><html><body style="margin:0">
<iframe src="page.html" width="{width}" height="844" style="border:0"></iframe>
<script>window.addEventListener('message', function (e) {{
  document.body.setAttribute('data-measure', e.data); }});</script></body></html>"""
MEASURE = """<script>
window.addEventListener('load', function () {
  var root = document.documentElement;
  var scrollers = Array.prototype.slice.call(document.querySelectorAll('.table-scroll'));
  var outside = Array.prototype.slice.call(document.body.querySelectorAll('*')).filter(
    function (el) { return !el.closest('.table-scroll'); });
  parent.postMessage(JSON.stringify({
    scrollWidth: root.scrollWidth, clientWidth: root.clientWidth,
    scrollers: scrollers.map(function (el) { return el.getBoundingClientRect().right; }),
    widestOutside: Math.max.apply(null, outside.map(
      function (el) { return el.getBoundingClientRect().right; }))
  }), '*');
});
</script>"""


def _chrome() -> str | None:
    for candidate in (os.environ.get("CHROME_BIN"), MAC_CHROME):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def render_at_phone_width(chrome: str, page_html: str, work: Path) -> dict:
    """Lay out one page at 390 px in headless Chrome, network blocked, and return
    the measurements its DOM reports (no screenshot)."""
    work.mkdir(parents=True, exist_ok=True)
    (work / "app.css").write_text(CSS.read_text())
    page_html = re.sub(r'<script src="[^"]*"></script>', "", page_html)   # external scripts
    page_html = page_html.replace('href="/static/app.css"', 'href="app.css"')
    page_html = page_html.replace("</body>", MEASURE + "</body>")
    (work / "page.html").write_text(page_html)
    target = work / "wrapper.html"
    target.write_text(WRAPPER.format(width=PHONE_WIDTH))
    args = [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--use-mock-keychain", "--disable-extensions",
            "--disable-sync", "--disable-background-networking", "--disable-component-update",
            "--host-resolver-rules=MAP * ~NOTFOUND", f"--user-data-dir={work / 'profile'}",
            "--window-size=800,900",
            "--virtual-time-budget=5000", "--dump-dom", target.as_uri()]
    # Chrome can linger after printing the DOM, so read until the document ends,
    # then stop it.
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    dom, deadline = b"", time.monotonic() + 60
    try:
        while b"</html>" not in dom and time.monotonic() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], deadline - time.monotonic())
            chunk = proc.stdout.read1(65536) if ready else b""
            if not chunk:
                break
            dom += chunk
    finally:
        proc.kill()
        proc.wait()
    found = re.search(r'data-measure="([^"]*)"', dom.decode(errors="replace"))
    assert found, "the measurement script ran"
    return json.loads(htmlmod.unescape(found.group(1)))


@pytest.mark.skipif(_chrome() is None, reason="no local Chrome (set CHROME_BIN to run)")
def test_g13_synthetic_pages_render_at_390px_without_sideways_page_scroll(monkeypatch, tmp_path):
    chrome = _chrome()
    # Positive control: a bare wide table does push the page past 390 px.
    control = render_at_phone_width(
        chrome, '<html><head><link rel="stylesheet" href="/static/app.css"></head><body>'
        '<table style="width:1200px"><tr><td>wide</td></tr></table></body></html>',
        tmp_path / "control")
    assert control["scrollWidth"] > PHONE_WIDTH
    assert MAX_FIXED_PX <= control["clientWidth"] <= PHONE_WIDTH, control   # a phone viewport
    c = client(monkeypatch, tmp_path, phantom=True)
    for i, page in enumerate(PAGES):
        m = render_at_phone_width(chrome, c.get(page).get_data(as_text=True), tmp_path / f"p{i}")
        assert MAX_FIXED_PX <= m["clientWidth"] <= PHONE_WIDTH, (page, m)
        assert m["scrollWidth"] <= m["clientWidth"], (page, m)
        assert m["widestOutside"] <= PHONE_WIDTH + 0.5, (page, m)
        assert all(right <= PHONE_WIDTH + 0.5 for right in m["scrollers"]), (page, m)
