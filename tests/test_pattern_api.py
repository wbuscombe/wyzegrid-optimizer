"""F6 read-only API and dashboard (R-1..R-7) and estimate semantics (E2, E3).

The API reads persisted results from a temp database built from the
synthetic positive scenario. Synthetic data only; nothing here touches a
network, a camera, or an LLM.
"""
from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

import pytest

from optimizer import db
from optimizer.analysis import normalize_events, run_all
from optimizer.patterns import presentation, store, synthetic
from optimizer.patterns.discovery import CADENCE_TEST_FIELDS
from optimizer.patterns.settings import DISCLAIMER_TEXT, UNIDENTIFIED_TEXT

from .pattern_helpers import positive_payloads, positive_run

ROOT = Path(__file__).resolve().parents[1]
NEW_ROUTES = ("/api/patterns", "/api/patterns/status", "/api/patterns/visits")
PASS_KEY = "f" * 16   # a stored pass_through pattern added by the fixture
MEDIA_TOKENS = ("thumbnail", "snapshot", "clip", "recording", "preview", ".jpg", ".jpeg",
                ".png", ".mp4", ".m3u8", "/vod/")
TABLES = ("events", "config_snapshots", "analysis_runs", "recommendations", "event_metadata",
          "pattern_runs", "pattern_results")
# Exact copy (E3). Pinned here so a copy change is a deliberate test change.
ESTIMATE_HEADER = ("Cadences are statistical estimates from detection metadata. On busy "
                   "scenes an every-other-week pattern can occasionally read as weekly or "
                   "fail to surface.")
UNIDENTIFIED = "Unidentified recurring pattern (no identity)"
NOT_IDENTIFICATIONS = ("Patterns are statistical regularities in detection metadata, "
                       "not identifications.")


@lru_cache(maxsize=None)
def _findings_json() -> str:
    """Real detector findings for the synthetic scenario (the index renders
    them), derived as run_once does: stored events -> normalize -> run_all."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    for e in positive_payloads():
        db.upsert_event(conn, e)
    events = normalize_events(db.events_in_window(conn, 0.0, synthetic.span_now()))
    conn.close()
    return json.dumps(run_all(events, {}, schedule_events=events,
                              timezone_name=synthetic.TZ_NAME))


def _findings() -> dict:
    return json.loads(_findings_json())


@pytest.fixture(scope="module")
def stored_db(tmp_path_factory):
    """One ok analysis run (with service schedules) and its stored patterns,
    plus one stored pass_through pattern so hiding can be proven."""
    path = tmp_path_factory.mktemp("api") / "api.db"
    db.init_db(path)
    conn = db.connect(path)
    store.apply_schema(conn)
    run = positive_run()
    run_id = db.start_run(conn, 0.0, 1.0)
    db.finish_run(conn, run_id, status="ok", findings=_findings(), claude_used=False)
    store.insert_pattern_run(conn, run_id, "ok", 1.0, 2.0,
                             window_first_date=run.first_date.isoformat(),
                             window_last_date=run.last_date.isoformat(), summary=run.summary)
    extra = dict(run.patterns[0], pattern_key=PASS_KEY, behavior="pass_through",
                 default_visible=False)
    store.insert_pattern_results(conn, run_id, list(run.patterns) + [extra])
    conn.close()
    return path


def _client(monkeypatch, tmp_path, db_path, **env):
    monkeypatch.setenv("PHANTOM_MODE", "0")
    monkeypatch.setenv("OPTIMIZER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPTIMIZER_DB_PATH", str(db_path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from optimizer.web.dashboard import create_app
    return create_app().test_client()


@pytest.fixture
def client(monkeypatch, tmp_path, stored_db):
    return _client(monkeypatch, tmp_path, stored_db)


def _all_routes(client):
    keys = [p["pattern_key"] for p in client.get("/api/patterns").get_json()["patterns"]]
    assert keys
    return NEW_ROUTES + tuple(f"/api/patterns/{k}" for k in keys) + (f"/api/patterns/{PASS_KEY}",)


def _walk(value, path="$"):
    """Every (path, key-or-None, scalar) in a JSON value."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield path, k, None
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, None, value


def media_hits(value) -> list[str]:
    hits = []
    for path, key, scalar in _walk(value):
        for text in (key, scalar if isinstance(scalar, str) else None):
            if text and any(tok in text.lower() for tok in MEDIA_TOKENS):
                hits.append(f"{path}: {text}")
    return hits


def _row_counts(path) -> dict:
    conn = db.connect(path)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    finally:
        conn.close()


# ---- R-1 ------------------------------------------------------------------------

def test_r1_every_get_endpoint_returns_the_documented_schema(client):
    listing = client.get("/api/patterns").get_json()
    assert set(listing) == {"enabled", "status", "run_id", "analysis_window", "filters",
                            "count", "patterns"}
    assert listing["enabled"] is True and listing["status"] == "ok"
    assert listing["count"] == len(listing["patterns"]) == len(positive_run().patterns)
    for p in listing["patterns"]:
        for field in ("pattern_key", "site", "label_group", "behavior", "cadence", "weekdays",
                      "parity", "window", "support", "hit_rate", "lift_t", "lift_w", "q",
                      "confidence", "identity", "identity_reason", "label", "descriptors",
                      "basis_mix", "recent", "hit_grid", "per_weekday",
                      "cadence_is_estimate", "limitations") + CADENCE_TEST_FIELDS:
            assert field in p, field
        for wd in p["per_weekday"]:
            assert all(field in wd for field in CADENCE_TEST_FIELDS)

    status = client.get("/api/patterns/status").get_json()
    assert set(status) == {"enabled", "status", "run_id", "started_at", "finished_at",
                           "message", "latest_ok_run_id", "pattern_count", "pattern_count_all"}
    assert status["enabled"] is True and status["status"] == "ok"
    assert status["pattern_count"] == listing["count"]
    assert status["pattern_count_all"] == listing["count"] + 1

    visits = client.get("/api/patterns/visits").get_json()
    assert set(visits) == {"enabled", "status", "run_id", "summary"}
    assert visits["summary"]["visits"] and "family_size" in visits["summary"]

    key = listing["patterns"][0]["pattern_key"]
    detail = client.get(f"/api/patterns/{key}").get_json()
    assert set(detail) == {"enabled", "pattern"}
    assert detail["pattern"]["pattern_key"] == key
    assert detail["pattern"]["run_id"] == listing["run_id"]
    assert detail["pattern"]["hit_grid"] and "periodicity" in detail["pattern"]
    assert client.get("/api/patterns/0123456789abcdef").status_code == 404


def test_r1_status_reports_the_latest_run_and_serves_the_last_ok_one(monkeypatch, tmp_path):
    path = tmp_path / "err.db"
    db.init_db(path)
    conn = db.connect(path)
    store.apply_schema(conn)
    db.finish_run(conn, db.start_run(conn, 0.0, 1.0), status="ok", findings=_findings(),
                  claude_used=False)
    store.insert_pattern_run(conn, 7, "ok", 1.0, 2.0, window_first_date="2031-02-10",
                             window_last_date="2031-06-01", summary={"visits": []})
    store.insert_pattern_results(conn, 7, list(positive_run().patterns))
    store.insert_pattern_run(conn, 8, "error", 3.0, 4.0, message="RuntimeError: forced")
    conn.close()
    c = _client(monkeypatch, tmp_path, path)
    status = c.get("/api/patterns/status").get_json()
    assert (status["status"], status["run_id"], status["latest_ok_run_id"]) == ("error", 8, 7)
    assert status["message"] == "RuntimeError: forced"
    listing = c.get("/api/patterns").get_json()
    assert listing["status"] == "error" and listing["run_id"] == 7 and listing["count"] > 0
    html = c.get("/").get_data(as_text=True)
    assert "The latest pattern stage recorded an error (RuntimeError: forced)" in html
    assert html.count("Estimated cadence: ") == listing["count"]


def test_r1_uninitialised_database_reports_not_initialized(monkeypatch, tmp_path):
    path = tmp_path / "fresh.db"
    db.init_db(path)   # pre-existing tables only; the pattern tables do not exist yet
    c = _client(monkeypatch, tmp_path, path)
    assert c.get("/api/patterns/status").get_json()["status"] == "not_initialized"
    assert c.get("/api/patterns").get_json()["count"] == 0
    conn = db.connect(path)
    try:
        assert not store.tables_present(conn)   # a GET never creates them
    finally:
        conn.close()


# ---- R-2 ------------------------------------------------------------------------

def test_r2_filters_behave_and_pass_through_is_hidden_unless_requested(client):
    everything = client.get("/api/patterns").get_json()["patterns"]
    sites = {p["site"] for p in everything}
    assert {"cam_street", "cam_door", "cam_back"} <= sites
    for site in sites:
        got = client.get(f"/api/patterns?camera={site}").get_json()["patterns"]
        assert got and {p["site"] for p in got} == {site}
    people = client.get("/api/patterns?label_group=person").get_json()["patterns"]
    assert people and all(p["label_group"] == "person" for p in people)
    stays = client.get("/api/patterns?behavior=long_stay").get_json()["patterns"]
    assert stays and all(p["behavior"] == "long_stay" for p in stays)
    strong = client.get("/api/patterns?min_confidence=strong").get_json()["patterns"]
    assert all(p["confidence"] == "strong" for p in strong)
    assert len(strong) == sum(1 for p in everything if p["confidence"] == "strong")

    assert PASS_KEY not in {p["pattern_key"] for p in everything}
    shown = client.get("/api/patterns?include_pass_through=1").get_json()["patterns"]
    assert PASS_KEY in {p["pattern_key"] for p in shown} and len(shown) == len(everything) + 1
    only = client.get("/api/patterns?behavior=pass_through").get_json()["patterns"]
    assert [p["pattern_key"] for p in only] == [PASS_KEY]

    assert client.get("/api/patterns?behavior=teleport").status_code == 400
    assert client.get("/api/patterns?min_confidence=weak").status_code == 400


# ---- R-3 ------------------------------------------------------------------------

def test_r3_every_mutating_verb_on_every_new_route_returns_405(client):
    routes = _all_routes(client)
    for route in routes:
        assert client.get(route).status_code == 200, route
        for verb in ("post", "put", "patch", "delete"):
            assert getattr(client, verb)(route).status_code == 405, (verb, route)


# ---- R-4 ------------------------------------------------------------------------

def test_r4_no_media_field_value_or_link_in_any_response(client):
    # Positive control: the scan fires on a planted key and a planted value.
    planted = {"a": [{"thumb_url": "x/y.JPG"}], "snapshot": 1, "b": "/vod/cam"}
    assert len(media_hits(planted)) == 3
    for route in _all_routes(client) + ("/api/patterns?include_pass_through=1",):
        body = client.get(route).get_json()
        assert media_hits(body) == [], route


# ---- R-5 ------------------------------------------------------------------------

def test_r5_row_counts_unchanged_after_every_get(client, stored_db):
    before = _row_counts(stored_db)
    assert before["pattern_results"] > 0
    for route in _all_routes(client) + ("/", "/api/patterns?include_pass_through=1"):
        client.get(route)
        assert _row_counts(stored_db) == before, route


# ---- R-6 and E3 -------------------------------------------------------------------

def test_r6_e3_dashboard_renders_estimate_copy_unidentified_rows_and_service_schedules(client):
    html = client.get("/").get_data(as_text=True)
    patterns = client.get("/api/patterns").get_json()["patterns"]
    # Exact header copy: estimates, the dense-background case, not identifications.
    assert presentation.ESTIMATE_HEADER_TEXT == ESTIMATE_HEADER
    assert DISCLAIMER_TEXT == NOT_IDENTIFICATIONS
    assert ESTIMATE_HEADER in html and NOT_IDENTIFICATIONS in html
    # Every row is unidentified and reads exactly the unchanged text.
    assert UNIDENTIFIED_TEXT == UNIDENTIFIED
    assert html.count(f"<td>{UNIDENTIFIED}</td>") == len(patterns)
    # Cadence renders as "Estimated cadence: <label>", never as a bare label.
    for cadence in {p["cadence"] for p in patterns}:
        expected = sum(1 for p in patterns if p["cadence"] == cadence)
        assert html.count(f"<td>Estimated cadence: {cadence}</td>") == expected
    assert not re.search(r"<td>\s*(weekly|biweekly|weekday_set|recurring)\s*</td>", html)
    # Existing service schedules still render, and still say insufficient-evidence,
    # under a heading that no longer claims learning (WYZE-022 G7).
    assert "Household-service windows (explicit labels only)" in html
    assert "Learned household-service windows" not in html
    assert html.count("<td>insufficient-evidence</td>") == 4
    assert html.count("<td>not learned</td>") == 4


# ---- R-7 ------------------------------------------------------------------------

def test_r7_phantom_mode_renders_synthetic_data_only(monkeypatch, tmp_path):
    monkeypatch.setenv("PHANTOM_MODE", "1")
    monkeypatch.setenv("OPTIMIZER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPTIMIZER_DB_PATH", str(tmp_path / "must-not-exist.db"))
    from optimizer.web.dashboard import create_app
    c = create_app().test_client()
    listing = c.get("/api/patterns").get_json()
    assert listing["count"] > 0
    assert {p["site"] for p in listing["patterns"]} <= {"cam_street", "cam_door", "cam_back"}
    assert all(p["first_seen"].startswith("2031-") for p in listing["patterns"])
    html = c.get("/").get_data(as_text=True)
    assert ESTIMATE_HEADER in html and UNIDENTIFIED in html
    assert not (tmp_path / "must-not-exist.db").exists()


# ---- E2 ---------------------------------------------------------------------------

ERROR_RATE_KEY = re.compile(r"accuracy|error_rate|misclass|precision|recall|false_(pos|neg)")


def test_e2_every_pattern_is_an_estimate_with_a_limitations_object(client):
    listing = client.get("/api/patterns?include_pass_through=1").get_json()
    details = [client.get(f"/api/patterns/{p['pattern_key']}").get_json()["pattern"]
               for p in listing["patterns"]]
    for p in listing["patterns"] + details:
        assert p["cadence_is_estimate"] is True
        assert p["limitations"] == {
            "dense_background": ("On busy scenes an every-other-week pattern can "
                                 "occasionally read as weekly or fail to surface."),
            "documentation": "ARCHITECTURE.md#cadence-estimates-and-limitations",
        }
        # The limitations object carries no number at all, so no rate.
        assert not re.search(r"\d", json.dumps(p["limitations"]))
    # No field anywhere expresses an error rate. Positive control first.
    assert [k for _, k, _ in _walk({"weekly_error_rate": 0.1}) if k and ERROR_RATE_KEY.search(k)]
    for route in _all_routes(client):
        keys = [k for _, k, _ in _walk(client.get(route).get_json()) if k]
        assert not [k for k in keys if ERROR_RATE_KEY.search(k)], route


def test_e2_limitations_reference_resolves_to_an_architecture_section():
    doc, anchor = presentation.LIMITATIONS_DOC.split("#")
    headings = [line.lstrip("#").strip() for line in (ROOT / doc).read_text().splitlines()
                if line.startswith("#")]
    slugs = {re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-") for h in headings}
    assert anchor in slugs
