"""WYZE-022 G8: an explicit clock and timezone label on pattern times.

The label is read at runtime from the scheduler's local-time source (the
process-local clock), never written into code or configuration. These tests
inject that source through TZ with synthetic POSIX zones ("SYN", "ALT") that
are not real zones, and a static test checks that no committed file hardcodes
a real zone. Synthetic data only.
"""
from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from optimizer import config
from optimizer.patterns import localtime, synthetic
from optimizer.patterns.localtime import clock_label, process_clock_label, process_local_time
from optimizer.web import patterns as pattern_views

from .dashboard_helpers import ROOT, build_db, client, parse, section

SYN = "SYN+03:30"          # POSIX: abbreviation SYN, 3 h 30 min behind UTC
SYN_LABEL = "SYN (UTC-03:30)"
ALT = "ALT-02:00"          # POSIX: abbreviation ALT, 2 h ahead of UTC
ALT_LABEL = "ALT (UTC+02:00)"


@contextmanager
def process_tz(value: str):
    """Point the process-local clock at a synthetic POSIX zone, then restore it."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = value
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


@pytest.fixture(scope="module")
def stored(tmp_path_factory):
    return build_db(tmp_path_factory.mktemp("g8") / "g8.db")


def test_g8_clock_label_names_the_zone_and_offset():
    assert clock_label(datetime(2031, 3, 4, 12, tzinfo=timezone.utc)) == "UTC"
    syn = timezone(-timedelta(hours=3, minutes=30), "SYN")
    assert clock_label(datetime(2031, 3, 4, 12, tzinfo=syn)) == SYN_LABEL
    assert clock_label(datetime(2031, 3, 4, 12, tzinfo=timezone(timedelta(hours=5, minutes=45)))) \
        == "UTC+05:45"
    zero = timezone(timedelta(0), "ZZZ")
    assert clock_label(datetime(2031, 3, 4, 12, tzinfo=zero)) == "ZZZ (UTC+00:00)"


def test_g8_the_label_comes_from_the_same_source_as_process_local_time():
    ts = datetime(2031, 3, 4, 12, 0, tzinfo=timezone.utc).timestamp()
    with process_tz(SYN):
        assert process_clock_label(ts) == SYN_LABEL
        # The same source: 12:00 UTC is 08:30 on the SYN clock the stage uses.
        assert process_local_time(ts)[2] == 8 * 60 + 30
    with process_tz(ALT):   # control: a different zone gives a different label
        assert process_clock_label(ts) == ALT_LABEL
        assert process_local_time(ts)[2] == 14 * 60


def _window_cells(page) -> list[str]:
    rows = [tr for tr in section(page, "recurring-patterns").find_all("tr")
            if not tr.has_class("pattern-stats") and tr.find_all("td")]
    return [tr.find_all("td")[3].text() for tr in rows]


@pytest.mark.parametrize("zone,label", [(SYN, SYN_LABEL), (ALT, ALT_LABEL)])
def test_g8_pattern_times_render_with_the_injected_clock(monkeypatch, tmp_path, stored, zone,
                                                         label):
    with process_tz(zone):
        page = parse(client(monkeypatch, tmp_path, stored).get("/").get_data(as_text=True))
    patterns = section(page, "recurring-patterns")
    cells = _window_cells(page)
    assert cells, "the stored synthetic patterns render"
    for cell in cells:
        assert re.fullmatch(r"\d\d:\d\d to \d\d:\d\d \(\d\d:\d\d\) " + re.escape(label), cell), cell
    headers = [th.text() for th in patterns.find_all("th")]
    assert f"Window, {label} (p10 to p90, median)" in headers
    assert f"Pattern times are shown in {label}, the clock the analysis runs on." \
        in patterns.text()


def test_g8_dashboard_view_takes_an_injected_clock_source(monkeypatch, tmp_path, stored):
    monkeypatch.setenv("PHANTOM_MODE", "0")
    monkeypatch.setenv("OPTIMIZER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPTIMIZER_DB_PATH", str(stored))
    view = pattern_views.dashboard_view(config.load(), clock=lambda ts: "SYN (UTC-03:30)")
    assert view["clock"] == SYN_LABEL and view["rows"]


def test_g8_phantom_labels_its_synthetic_clock_at_runtime(monkeypatch, tmp_path):
    page = parse(client(monkeypatch, tmp_path, phantom=True).get("/").get_data(as_text=True))
    expected = localtime.zoneinfo_clock_label(synthetic.TZ_NAME)(synthetic.span_now())
    cells = _window_cells(page)
    assert cells and all(cell.endswith(" " + expected) for cell in cells)


# ---- no committed file hardcodes a real zone -------------------------------------

# IANA Area/Location names, plus the four legacy US rule zones (assembled here so
# this file never spells one out and trips its own scan).
LEGACY = "|".join(std + str(hours) + dst for std, hours, dst in
                  (("EST", 5, "EDT"), ("CST", 6, "CDT"), ("MST", 7, "MDT"), ("PST", 8, "PDT")))
ZONE = re.compile(
    r"\b(?:Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Brazil|Canada|Chile|"
    r"Europe|Indian|Mexico|Pacific|US|Etc)/[A-Za-z][A-Za-z0-9_+\-]*"
    r"|\b(?:" + LEGACY + r")\b")
TZ_SETTING = re.compile(r"^\s*(?:-\s*)?TZ\s*[:=]", re.M)
# The one exception: the synthetic generator's fixed scenario clock (a DST zone
# chosen for the WYZE-020 synthetic controls, which depend on it). Its module
# docstring states that no production-derived timing appears there.
SYNTHETIC_MODULE = "src/optimizer/patterns/synthetic.py"
DISPLAY_PATH = ("src/optimizer/web", "src/optimizer/patterns/presentation.py",
                "src/optimizer/patterns/localtime.py")


def committed_text_files() -> list:
    """Committed text: docs, deployment files, source, templates, CSS, tests,
    scripts, and CI. Listed explicitly, so untracked files (uv.lock) are never
    opened."""
    names = ["Dockerfile", "docker-compose.yml", "pyproject.toml", "requirements.txt",
             ".env.example", ".phantom.yml", ".pre-commit-config.yaml"]
    files = sorted(ROOT.glob("*.md")) + [ROOT / n for n in names]
    files += sorted((ROOT / ".github").rglob("*.yml"))
    for pattern in ("*.py", "*.html", "*.css"):
        files += sorted((ROOT / "src").rglob(pattern))
    files += sorted((ROOT / "tests").rglob("*.py")) + sorted((ROOT / "scripts").glob("*.sh"))
    return [f for f in files if f.is_file()]


def zone_hits(text: str) -> list[str]:
    return ZONE.findall(text)


def test_g8_zone_scan_fires_on_a_planted_real_zone():
    # Built at runtime, so this file never contains a zone itself.
    assert zone_hits("TZ=" + "Europe" + "/" + "Paris") == ["Europe" + "/" + "Paris"]
    assert zone_hits("a " + "CST6" + "CDT" + " clock") and not zone_hits("UTC and SYN+03:30")
    assert TZ_SETTING.search("  TZ: example") and TZ_SETTING.search("TZ=x")


def test_g8_no_committed_file_hardcodes_a_real_zone():
    files = committed_text_files()
    rel = {str(f.relative_to(ROOT)): f for f in files}
    assert SYNTHETIC_MODULE in rel and "README.md" in rel and len(files) > 60
    assert not any(f.name == "uv.lock" for f in files)
    hits = {name: zone_hits(f.read_text(errors="replace")) for name, f in rel.items()}
    hits = {name: found for name, found in hits.items() if found}
    assert set(hits) <= {SYNTHETIC_MODULE}, hits
    assert set(hits.get(SYNTHETIC_MODULE, [])) <= {synthetic.TZ_NAME}
    # Nothing on the display path names a zone, and no deployment file sets TZ.
    for name, f in rel.items():
        if name.startswith(DISPLAY_PATH):
            assert not zone_hits(f.read_text()), name
    for name in ("Dockerfile", "docker-compose.yml", ".env.example"):
        assert not TZ_SETTING.search(rel[name].read_text()), name
