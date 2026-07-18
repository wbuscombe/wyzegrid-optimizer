"""Tests for the optional ntfy failure-alert poster (Deferred #2 delivery path)."""
from __future__ import annotations

from optimizer import ntfy


def test_post_alert_noop_when_unconfigured(monkeypatch):
    called = []
    monkeypatch.setattr("optimizer.ntfy.requests.post",
                        lambda *a, **k: called.append(1))
    # any missing field → no-op, no HTTP call
    assert ntfy.post_alert("", "t", "u", "p", "m") is False
    assert ntfy.post_alert("http://x", "", "u", "p", "m") is False
    assert ntfy.post_alert("http://x", "t", "", "p", "m") is False
    assert ntfy.post_alert("http://x", "t", "u", "", "m") is False
    assert called == []


def test_post_alert_publishes_with_basic_auth(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200

    def _post(url, data=None, headers=None, auth=None, timeout=None):
        captured.update(url=url, data=data, headers=headers, auth=auth)
        return _Resp()

    monkeypatch.setattr("optimizer.ntfy.requests.post", _post)

    ok = ntfy.post_alert("http://192.168.50.7/", "wyzegrid-optimizer",
                         "will", "sekret", "boom", title="t", priority="high")
    assert ok is True
    assert captured["url"] == "http://192.168.50.7/wyzegrid-optimizer"  # trailing slash trimmed
    assert captured["auth"] == ("will", "sekret")
    assert captured["data"] == b"boom"
    assert captured["headers"]["Title"] == "t"


def test_post_alert_false_on_non_2xx(monkeypatch):
    class _Resp:
        status_code = 401  # deny-all server, bad/absent creds

    monkeypatch.setattr("optimizer.ntfy.requests.post", lambda *a, **k: _Resp())
    assert ntfy.post_alert("http://x", "t", "u", "p", "m") is False


def test_post_alert_never_raises_on_network_error(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("no route")

    monkeypatch.setattr("optimizer.ntfy.requests.post", _boom)
    assert ntfy.post_alert("http://x", "t", "u", "p", "m") is False
