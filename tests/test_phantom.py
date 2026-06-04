"""
Phantom mode boot contract: with PHANTOM_MODE=1 and NO other env vars / .env / DB,
the dashboard must boot and serve every defined route with HTTP 200.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def phantom_app(monkeypatch, tmp_path):
    # Strip every optimizer-relevant env var so we prove no hard dependencies.
    for var in [
        "ANTHROPIC_API_KEY", "FRIGATE_URL", "OPTIMIZER_CAMERAS",
        "OPTIMIZER_MODEL", "OPTIMIZER_TOKEN_BUDGET",
        "OPTIMIZER_INTERVAL_SECONDS", "ZMA_WEBHOOK_URL",
        "DASHBOARD_HOST", "DASHBOARD_PORT", "OPTIMIZER_DB_PATH",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PHANTOM_MODE", "1")
    # Point data dir somewhere safe; phantom mode shouldn't write to it anyway
    monkeypatch.setenv("OPTIMIZER_DATA_DIR", str(tmp_path))
    from optimizer.web.dashboard import create_app
    return create_app()


def test_phantom_health(phantom_app):
    client = phantom_app.test_client()
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["phantom"] is True


def test_phantom_index_renders(phantom_app):
    client = phantom_app.test_client()
    r = client.get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "wyzegrid-optimizer" in body
    assert "PHANTOM" in body  # the phantom badge


def test_phantom_recommendations_renders(phantom_app):
    client = phantom_app.test_client()
    r = client.get("/recommendations")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # The canonical model-limit row should be present in the synthetic data
    assert "model-limit" in body.lower()
    assert "phantom_front_porch" in body  # porch dog asymmetry surfaces


def test_phantom_trends_renders(phantom_app):
    client = phantom_app.test_client()
    r = client.get("/trends")
    assert r.status_code == 200


def test_phantom_cost_renders(phantom_app):
    client = phantom_app.test_client()
    r = client.get("/cost")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Total cost" in body


def test_phantom_api_latest_returns_json(phantom_app):
    client = phantom_app.test_client()
    r = client.get("/api/latest")
    assert r.status_code == 200
    body = r.get_json()
    assert body["phantom"] is True
    assert isinstance(body["recommendations"], list)
    assert len(body["recommendations"]) >= 1
    assert isinstance(body["findings"], dict)
    # The model-limit detector should fire on porch dog in the synthetic dataset
    model_limit_recs = [r for r in body["recommendations"] if r["risk_class"] == "model-limit"]
    assert len(model_limit_recs) >= 1
