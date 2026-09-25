"""F5 run integration: failure isolation (I-7), kill switch (I-8), and
append-only results (I-9). Synthetic data only; no network, no LLM (the
Claude layer is off because no API key is configured)."""
from __future__ import annotations

import json

import pytest

from optimizer import config as cfg_module
from optimizer import db, ingest, run_once, zma
from optimizer.config import Config
from optimizer.patterns import metadata, stage, store, synthetic

from .pattern_helpers import LOCAL, positive_payloads

FIXED_NOW = synthetic.span_now()


def _cfg(tmp_path, *, enabled=True) -> Config:
    return Config(
        phantom_mode=False, frigate_url="http://frigate.invalid:5000", cameras_filter=[],
        anthropic_api_key="", model="claude-haiku-4-5", token_budget=20000,
        interval_seconds=86400, run_at_hour=2, run_at_minute=0,
        timezone_name=synthetic.TZ_NAME, zma_webhook_url="", ntfy_url="", ntfy_topic="",
        ntfy_user="", ntfy_pass="", dashboard_host="127.0.0.1", dashboard_port=5004,
        data_dir=tmp_path, db_path=tmp_path / "t.db", patterns_enabled=enabled,
    )


def _fake_ingest(conn, client, cameras=None, capture_metadata=False, **_):
    """Stands in for the Frigate pull: upserts the synthetic payloads."""
    with db.transaction(conn):
        for e in positive_payloads():
            db.upsert_event(conn, e)
            if capture_metadata:
                store.upsert_event_metadata(conn, metadata.extract(e))
    return len(positive_payloads())


def _cycle(monkeypatch, tmp_path, *, enabled=True, break_stage=False) -> dict:
    """One run_cycle against a fresh temp database with the clock pinned."""
    monkeypatch.setattr(cfg_module, "load", lambda: _cfg(tmp_path, enabled=enabled))
    monkeypatch.setattr(run_once, "FrigateClient", lambda url: object())
    monkeypatch.setattr(ingest, "ingest_events", _fake_ingest)
    monkeypatch.setattr(ingest, "snapshot_config", lambda conn, client: 0)
    monkeypatch.setattr(zma, "post_status", lambda *a, **k: True)
    monkeypatch.setattr(run_once.time, "time", lambda: FIXED_NOW)
    if break_stage:
        def _boom(*a, **k):
            raise RuntimeError("forced failure\nsecond line \x00 hidden")
        monkeypatch.setattr(stage, "compute", _boom)
    summary = run_once.run_cycle()
    monkeypatch.undo()
    conn = db.connect(tmp_path / "t.db")
    try:
        run = dict(db.latest_run(conn))
        recs = [{k: r[k] for k in r.keys() if k not in ("id", "created_at")}
                for r in db.recommendations_for_run(conn, run["id"])]
        pattern_runs = [dict(r) for r in conn.execute(
            "SELECT run_id, status, message FROM pattern_runs ORDER BY run_id")]
        results = conn.execute("SELECT COUNT(*) FROM pattern_results").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    return {"summary": summary, "run": run, "recs": recs, "pattern_runs": pattern_runs,
            "results": results, "events": events}


def _detector_view(out: dict) -> tuple:
    run = out["run"]
    return (run["status"], json.loads(run["findings_json"]), run["claude_used"], run["notes"],
            out["recs"], out["events"], out["summary"])


@pytest.fixture(scope="module")
def healthy(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        return _cycle(mp, tmp_path_factory.mktemp("healthy"))
    finally:
        mp.undo()


def test_healthy_cycle_records_an_ok_pattern_run(healthy):
    assert healthy["run"]["status"] == "ok"
    # The detectors saw the data, so the equality checks below are not vacuous.
    assert healthy["summary"]["ingested"] == healthy["events"] == len(positive_payloads())
    profiles = json.loads(healthy["run"]["findings_json"])["per_camera_profile"]["profiles"]
    assert "cam_street" in {p["camera"] for p in profiles}
    assert healthy["pattern_runs"] == [{"run_id": healthy["run"]["id"], "status": "ok",
                                        "message": None}]
    assert healthy["results"] > 0


def test_i7_forced_stage_exception_records_error_and_changes_nothing_else(
        monkeypatch, tmp_path, healthy):
    broken = _cycle(monkeypatch, tmp_path, break_stage=True)
    assert broken["pattern_runs"] == [{"run_id": broken["run"]["id"], "status": "error",
                                       "message": "RuntimeError: forced failure"}]
    assert broken["results"] == 0
    # The existing run succeeds and every detector output is byte-for-byte the same.
    assert broken["run"]["status"] == "ok"
    assert _detector_view(broken) == _detector_view(healthy)


def test_i8_kill_switch_skips_the_stage_and_the_api_reports_disabled(
        monkeypatch, tmp_path, healthy):
    off = _cycle(monkeypatch, tmp_path, enabled=False)
    assert off["pattern_runs"] == [] and off["results"] == 0
    assert _detector_view(off) == _detector_view(healthy)

    monkeypatch.setenv("PHANTOM_MODE", "0")
    monkeypatch.setenv("OPTIMIZER_PATTERNS_ENABLED", "0")
    monkeypatch.setenv("OPTIMIZER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPTIMIZER_DB_PATH", str(tmp_path / "t.db"))
    from optimizer.web.dashboard import create_app
    client = create_app().test_client()
    status = client.get("/api/patterns/status").get_json()
    assert status["enabled"] is False and status["status"] == "disabled"
    listing = client.get("/api/patterns").get_json()
    assert listing["enabled"] is False and listing["count"] == 0
    assert "Recurring-pattern analysis is turned off" in client.get("/").get_data(as_text=True)


def test_i9_a_second_run_adds_rows_and_removes_none(tmp_path):
    path = tmp_path / "i9.db"
    db.init_db(path)
    conn = db.connect(path)
    store.apply_schema(conn)
    with db.transaction(conn):
        for e in positive_payloads():
            db.upsert_event(conn, e)
            store.upsert_event_metadata(conn, metadata.extract(e))

    def rows(table):
        return sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {table}"))

    first = stage.run_stage(conn, 1, now=FIXED_NOW, local_time=LOCAL)
    before = {t: rows(t) for t in ("events", "event_metadata", "pattern_runs",
                                   "pattern_results")}
    second = stage.run_stage(conn, 2, now=FIXED_NOW, local_time=LOCAL)
    after = {t: rows(t) for t in before}
    conn.close()

    assert first["status"] == second["status"] == "ok" and first["patterns"] > 0
    assert after["events"] == before["events"]
    assert after["event_metadata"] == before["event_metadata"]
    for table in ("pattern_runs", "pattern_results"):
        assert set(before[table]) <= set(after[table])          # nothing removed or changed
    assert len(after["pattern_runs"]) == len(before["pattern_runs"]) + 1
    assert len(after["pattern_results"]) == len(before["pattern_results"]) + second["patterns"]
