"""WYZE-022 G7: non-suggestive service and pattern wording.

Nothing on the dashboard may suggest that a recurring pattern is, or relates
to, garbage, recycling, mail, package, or any other service: no pattern string
names a service, and no layout places a pattern with a service term. The
household-service section keeps its meaning (explicit labels only; insufficient
evidence stays insufficient) under a heading that no longer claims learning, and
every unidentified pattern shows its non-identity statement. Synthetic data only.
"""
from __future__ import annotations

import pytest

from optimizer.patterns import presentation
from optimizer.patterns.settings import IDENTITY_LABEL_MAP, UNIDENTIFIED_TEXT

from .dashboard_helpers import (SERVICE_RE, WEB, build_db, client, findings, parse,
                                section)

SERVICE_HEADING = "Household-service windows (explicit labels only)"


def pattern_strings() -> list[str]:
    """Every string the recurring-pattern section can render from code."""
    copy = [v for v in presentation.COPY.values() if isinstance(v, str)]
    return (copy + list(presentation.OTHER_LIMITATIONS)
            + list(presentation.CONFIDENCE_PHRASES.values())
            + list(presentation.CADENCE_PHRASES.values())
            + [presentation.estimated_cadence(c) for c in presentation.CADENCE_PHRASES])


@pytest.fixture(scope="module")
def stored(tmp_path_factory):
    return build_db(tmp_path_factory.mktemp("g7") / "g7.db")


@pytest.fixture
def home(monkeypatch, tmp_path, stored):
    return parse(client(monkeypatch, tmp_path, stored).get("/").get_data(as_text=True))


def test_g7_the_service_scan_fires_on_the_copy_this_change_replaced():
    # Positive control: the former pattern statement and heading both trip the scan.
    assert SERVICE_RE.search("Unidentified recurring pattern - no service identity assigned")
    assert SERVICE_RE.search("Learned household-service windows")
    for term in ("garbage", "recycling", "mail", "package", "delivery", "service"):
        assert SERVICE_RE.search(f"a {term} pattern"), term
    assert not SERVICE_RE.search("a vehicle brief stop, cars and trucks")


def test_g7_no_pattern_string_names_a_service():
    strings = pattern_strings()
    assert UNIDENTIFIED_TEXT in strings and len(strings) > 15
    assert [s for s in strings if SERVICE_RE.search(s)] == []
    source = (WEB / "templates" / "_patterns.html").read_text()
    assert SERVICE_RE.findall(source) == []


def test_g7_every_unidentified_pattern_shows_its_non_identity_statement(home):
    assert IDENTITY_LABEL_MAP == {}
    assert UNIDENTIFIED_TEXT == "Unidentified recurring pattern (no identity)"
    patterns = section(home, "recurring-patterns")
    rows = [tr for tr in patterns.find_all("tr") if not tr.has_class("pattern-stats")
            and tr.find_all("td")]
    assert len(rows) > 0
    assert [tr.find_all("td")[0].text() for tr in rows] == [UNIDENTIFIED_TEXT] * len(rows)


def layout_couplings(root) -> list[str]:
    """Places where a pattern and a service term meet: a service term in the
    pattern section, pattern content in the service section, one section inside
    the other, the two sections side by side, or a row holding both."""
    problems = []
    patterns = section(root, "recurring-patterns")
    services = section(root, "service-windows")
    for term in SERVICE_RE.findall(patterns.text()):
        problems.append(f"service term in the pattern section: {term}")
    if UNIDENTIFIED_TEXT in services.text() or "Estimated cadence" in services.text():
        problems.append("pattern content in the service section")
    if patterns in services.ancestors() or services in patterns.ancestors():
        problems.append("one section contains the other")
    siblings = [c for c in patterns.parent.children if not isinstance(c, str)]
    if abs(siblings.index(patterns) - siblings.index(services)) == 1:
        problems.append("the pattern section sits directly beside the service section")
    for tr in root.find_all("tr"):
        text = tr.text()
        if UNIDENTIFIED_TEXT in text and SERVICE_RE.search(text):
            problems.append(f"a row holds a pattern and a service term: {text}")
    return problems


def test_g7_no_layout_couples_a_pattern_with_a_service_term(home, monkeypatch, tmp_path):
    assert layout_couplings(home) == []
    # The phantom page (synthetic generator) holds to the same rule.
    phantom = parse(client(monkeypatch, tmp_path, phantom=True).get("/").get_data(as_text=True))
    assert section(phantom, "recurring-patterns").text()
    assert layout_couplings(phantom) == []


def test_g7_layout_check_fires_on_a_coupled_page():
    # Positive control: a service word in a pattern row, the pattern section right
    # after the service section, and pattern copy inside the service section.
    planted = parse(
        f'<main><section id="service-windows"><h2>{SERVICE_HEADING}</h2>'
        f"<p>{UNIDENTIFIED_TEXT}</p></section>"
        f'<section id="recurring-patterns"><table><tr><td>{UNIDENTIFIED_TEXT}</td>'
        "<td>mail</td></tr></table></section></main>")
    found = layout_couplings(planted)
    assert any("service term in the pattern section" in p for p in found)
    assert any("pattern content in the service section" in p for p in found)
    assert any("directly beside" in p for p in found)
    assert any("a row holds" in p for p in found)


def test_g7_service_section_keeps_its_meaning_without_claiming_learning(home):
    services = section(home, "service-windows")
    assert services.find_all("h2")[0].text() == SERVICE_HEADING
    rows = [tr.find_all("td") for tr in services.find_all("tr") if tr.find_all("td")]
    assert len(rows) == 4
    assert {cells[1].text() for cells in rows} == {"insufficient-evidence"}
    assert [cells[4].text() for cells in rows] == ["not learned"] * 4
    assert "generic cars and trucks are never guessed into a service category" in services.text()
    assert "Learned household-service windows" not in home.text()


def test_g7_a_learned_schedule_still_shows_its_confidence(monkeypatch, tmp_path):
    # Control for "not learned": a learned row keeps its percentage.
    data = findings()
    rows = [dict(r) for r in data["service_schedule"]["schedules"]]
    rows[0].update(status="learned", confidence=0.8, typical_weekday="Tuesday",
                   typical_local_time="10:00", visits=5, distinct_weeks=4)
    data["service_schedule"] = dict(data["service_schedule"], schedules=rows)
    path = build_db(tmp_path / "learned.db", findings_override=data)
    page = parse(client(monkeypatch, tmp_path, path).get("/").get_data(as_text=True))
    cells = [tr.find_all("td") for tr in section(page, "service-windows").find_all("tr")
             if tr.find_all("td")]
    assert [c[4].text() for c in cells] == ["80%", "not learned", "not learned", "not learned"]
