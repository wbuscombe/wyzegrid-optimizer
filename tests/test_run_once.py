"""Tests for run_once.run_cycle failure alerting (Deferred #2).

The 2026-07-05 outage was silent because ZMA_WEBHOOK_URL was unset in prod AND a
failure during setup would have escaped the alert path entirely (setup used to
live outside the try/except). These pin that ANY failure — setup or in-run —
fires zma.post_status("run-error", ...).
"""
from __future__ import annotations

import pytest

from optimizer import config as cfg_module
from optimizer import db, ingest, run_once, zma
from optimizer.config import Config


def _cfg(tmp_path, webhook="http://zma.local/hook", ntfy=("", "", "", "")):
    nu, nt, nusr, npw = ntfy
    return Config(
        phantom_mode=False,
        frigate_url="http://frigate.invalid:5000",
        cameras_filter=[],
        anthropic_api_key="",
        model="claude-haiku-4-5",
        token_budget=20000,
        interval_seconds=86400,
        run_at_hour=2,
        run_at_minute=0,
        zma_webhook_url=webhook,
        ntfy_url=nu,
        ntfy_topic=nt,
        ntfy_user=nusr,
        ntfy_pass=npw,
        dashboard_host="0.0.0.0",
        dashboard_port=5004,
        data_dir=tmp_path,
        db_path=tmp_path / "t.db",
    )


def _spy_post(monkeypatch):
    calls = []

    def _fake(url, event, payload=None, timeout=3.0):
        calls.append((url, event, payload))
        return True

    monkeypatch.setattr(zma, "post_status", _fake)
    return calls


def test_setup_failure_still_alerts(monkeypatch, tmp_path):
    """A failure DURING setup (db.init_db raises) used to escape the try and go
    silent. It must now fire the run-error alert even though conn/run_id are
    still None."""
    monkeypatch.setattr(cfg_module, "load", lambda: _cfg(tmp_path))
    calls = _spy_post(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "init_db", _boom)

    with pytest.raises(RuntimeError):
        run_once.run_cycle()

    assert any(event == "run-error" for _, event, _ in calls), \
        "setup failure must still post run-error"


def test_in_run_failure_alerts(monkeypatch, tmp_path):
    """A failure after the run has started (ingest raises) also alerts and
    records the error row."""
    monkeypatch.setattr(cfg_module, "load", lambda: _cfg(tmp_path))
    calls = _spy_post(monkeypatch)

    # Let setup succeed against a real temp DB, then blow up inside ingest.
    monkeypatch.setattr("optimizer.run_once.FrigateClient", lambda url: object())

    def _boom(*a, **k):
        raise RuntimeError("frigate refused")

    monkeypatch.setattr(ingest, "ingest_events", _boom)

    with pytest.raises(RuntimeError):
        run_once.run_cycle()

    assert any(event == "run-error" for _, event, _ in calls)


def test_post_status_is_noop_when_webhook_unset(monkeypatch, tmp_path):
    """Sanity: with the webhook unset the alert path runs but no-ops — this is
    exactly the prod state that made 2026-07-05 silent, reproduced as a test so
    the fix (set the URL) is unambiguous."""
    monkeypatch.setattr(cfg_module, "load", lambda: _cfg(tmp_path, webhook=""))

    posted = []
    real_post = zma.post_status

    def _wrapped(url, event, payload=None, timeout=3.0):
        posted.append((url, event))
        return real_post(url, event, payload, timeout)  # url="" -> returns False, no HTTP

    monkeypatch.setattr(zma, "post_status", _wrapped)

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "init_db", _boom)

    with pytest.raises(RuntimeError):
        run_once.run_cycle()

    # The call IS made (event == run-error) but with an empty URL it no-ops.
    assert ("", "run-error") in posted


def test_failure_alerts_via_ntfy_when_configured(monkeypatch, tmp_path):
    """With ntfy configured, a run failure publishes to {url}/{topic} with the
    Basic-auth credential — the real delivery path (ZMA was never deployed)."""
    cfg = _cfg(tmp_path, ntfy=("http://ntfy.local", "wyzegrid-optimizer", "will", "sekret"))
    monkeypatch.setattr(cfg_module, "load", lambda: cfg)
    _spy_post(monkeypatch)  # silence the ZMA path

    captured = {}

    class _Resp:
        status_code = 200

    def _fake_post(url, data=None, headers=None, auth=None, timeout=None):
        captured.update(url=url, data=data, auth=auth)
        return _Resp()

    monkeypatch.setattr("optimizer.ntfy.requests.post", _fake_post)

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "init_db", _boom)

    with pytest.raises(RuntimeError):
        run_once.run_cycle()

    assert captured["url"] == "http://ntfy.local/wyzegrid-optimizer"
    assert captured["auth"] == ("will", "sekret")
    assert b"run failed" in captured["data"]
