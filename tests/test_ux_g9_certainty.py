"""WYZE-022 G9: plain-language pattern certainty.

Every confidence tier and cadence estimate maps to one plain phrase, and each
row says how often it was seen, from support k/n that already exists. No new
statistic is computed; the statistics move into a collapsed details row; the
documented limitations render as page text. No string pairs the synthetic
S-9a figure with real-world language. Synthetic data only.
"""
from __future__ import annotations

import pytest

from optimizer.patterns import presentation
from optimizer.web.patterns import CONFIDENCE_LEVELS

from .dashboard_helpers import build_db, client, parse, section
from .pattern_helpers import positive_run
from .test_estimate_language import FIGURE, offending, sentences

CADENCES = ("weekly", "biweekly", "weekday_set", "recurring")   # ARCHITECTURE.md, Cadence


@pytest.fixture(scope="module")
def stored(tmp_path_factory):
    return build_db(tmp_path_factory.mktemp("g9") / "g9.db")


def _page(monkeypatch, tmp_path, stored=None, **env):
    return parse(client(monkeypatch, tmp_path, stored, **env).get("/").get_data(as_text=True))


def _rows(page):
    """(main row, details row) pairs of the pattern table."""
    trs = [tr for tr in section(page, "recurring-patterns").find_all("tr") if tr.find_all("td")]
    return list(zip(trs[0::2], trs[1::2]))


def test_g9_every_confidence_and_cadence_value_maps_to_one_plain_phrase():
    assert set(presentation.CONFIDENCE_PHRASES) == set(CONFIDENCE_LEVELS)
    assert set(presentation.CADENCE_PHRASES) == set(CADENCES)
    for mapping in (presentation.CONFIDENCE_PHRASES, presentation.CADENCE_PHRASES):
        phrases = list(mapping.values())
        assert all(p.strip() for p in phrases) and len(set(phrases)) == len(phrases)
    for tier in CONFIDENCE_LEVELS:
        assert presentation.plain_confidence(tier) == presentation.CONFIDENCE_PHRASES[tier]
    for cadence in CADENCES:
        assert presentation.plain_cadence(cadence) == presentation.CADENCE_PHRASES[cadence]
    # Everything the synthetic scenario produces is covered by the mapping.
    run = positive_run()
    assert {p["confidence"] for p in run.patterns} <= set(presentation.CONFIDENCE_PHRASES)
    assert {p["cadence"] for p in run.patterns} <= set(presentation.CADENCE_PHRASES)
    # A value outside the mapping shows as itself rather than breaking the page.
    assert presentation.plain_cadence("unlisted") == "unlisted"


def test_g9_seen_summary_restates_support_only():
    one = {"support": {"k": 11, "n": 16}, "weekdays": [1]}
    many = {"support": {"k": 30, "n": 48}, "weekdays": [0, 1, 2]}
    assert presentation.seen_summary(one) == "Seen in 11 of 16 covered weeks"
    assert presentation.seen_summary(many) == "Seen on 30 of 48 covered days"


def test_g9_rows_speak_plainly_and_keep_statistics_in_a_details_row(
        monkeypatch, tmp_path, stored):
    page = _page(monkeypatch, tmp_path, stored)
    patterns = section(page, "recurring-patterns")
    rows = _rows(page)
    run = positive_run()
    visible = [p for p in run.patterns if p["default_visible"]]
    assert len(rows) == len(visible) > 0
    by_key = {(p["site"], p["label_group"], p["behavior"], ", ".join(p["weekday_names"]),
               p["window"]["median"]): p for p in visible}
    for main, details in rows:
        cells = [td.text() for td in main.find_all("td")]
        assert len(cells) == 8, cells
        site, group, behavior = cells[1].split(" / ")
        p = by_key[(site, group, behavior, cells[2], cells[3].split("(")[1].split(")")[0])]
        assert cells[4] == presentation.estimated_cadence(p["cadence"])
        assert cells[5] == (f"{presentation.plain_cadence(p['cadence'])} (estimate). "
                            f"{presentation.seen_summary(p)}.")
        assert cells[6] == presentation.plain_confidence(p["confidence"])
        # The statistics live in the details row, collapsed by default.
        assert details.has_class("pattern-stats")
        box = details.find_all("details")
        assert len(box) == 1 and "open" not in box[0].attrs
        stats = box[0].text()
        for label in ("Periodicity", "Parity", "Support k/n", "Hit rate", "Lift", "q",
                      "Confidence tier", "Basis mix", "Descriptors"):
            assert label in stats, label
        assert f"{p['support']['k']} / {p['support']['n']}" in stats
    headers = [th.text() for th in patterns.find_all("th")]
    assert "q" not in headers and "Lift (time / weekday)" not in headers
    assert patterns.text().count(presentation.CERTAINTY_LEGEND) == 1


@pytest.mark.parametrize("env", [{}, {"OPTIMIZER_PATTERNS_ENABLED": "0"}])
def test_g9_limitations_render_as_page_text(monkeypatch, tmp_path, stored, env):
    text = section(_page(monkeypatch, tmp_path, stored, **env), "recurring-patterns").text()
    assert presentation.DENSE_BACKGROUND_TEXT in text
    for line in presentation.OTHER_LIMITATIONS:
        assert line in text
        assert not any(ch.isdigit() for ch in line), line   # no numbers, so no rate


def page_text_hits(text: str) -> list[str]:
    """Sentences that pair the synthetic figure with real-world language, and any
    appearance of the figure at all (the dashboard never shows it)."""
    return offending(text) + [s for s in sentences(text) if FIGURE.search(s)]


def test_g9_no_string_pairs_the_synthetic_figure_with_real_world_language(monkeypatch,
                                                                          tmp_path, stored):
    # Positive control, built at runtime so this file never pairs them itself.
    count = "1 " + "of 20"
    assert page_text_hits(f"Weekly was read in {count} seeds in production.")
    assert page_text_hits(f"S-9a read weekly in {count} synthetic seeds.")
    copy = [v for v in presentation.COPY.values() if isinstance(v, str)]
    copy += list(presentation.OTHER_LIMITATIONS) + list(presentation.CONFIDENCE_PHRASES.values())
    copy += list(presentation.CADENCE_PHRASES.values()) + [presentation.CERTAINTY_LEGEND]
    assert [s for s in copy if page_text_hits(s)] == []
    pages = [_page(monkeypatch, tmp_path, stored),
             parse(client(monkeypatch, tmp_path, phantom=True).get("/").get_data(as_text=True))]
    for page in pages:
        assert section(page, "recurring-patterns").text()
        assert page_text_hits(page.text()) == []
