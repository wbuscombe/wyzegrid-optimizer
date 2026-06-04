"""
Tests for the rule-layer fallback path of the Claude interpretation layer.

These tests do NOT call the Anthropic API. They verify:
  - With no API key, the layer emits recommendations from the rule findings.
  - Model-limit findings are tagged correctly (the honesty requirement).
  - Threshold-tuning candidates become threshold-change recommendations.
  - Stationary findings become stationary.max_frames recommendations.
"""
from __future__ import annotations

from optimizer.claude_layer import interpret


def test_no_api_key_uses_fallback_and_returns_recs(porch_no_dog_events, config_map):
    from optimizer.analysis import run_all
    findings = run_all(porch_no_dog_events, config_map, history_events=[])
    result = interpret(findings, config_map, api_key="", model="claude-haiku-4-5")
    assert result.used is False
    assert result.error and "ANTHROPIC_API_KEY unset" in result.error
    assert isinstance(result.recommendations, list)
    # Model-limit finding for porch dog must surface as risk_class=model-limit
    porch_dog = [r for r in result.recommendations
                 if r.get("camera") == "front_porch_cam" and r.get("label") == "dog"]
    assert len(porch_dog) == 1
    assert porch_dog[0]["risk_class"] == "model-limit"
    assert porch_dog[0]["param"] is None
    assert porch_dog[0]["proposed_value"] is None


def test_threshold_candidate_becomes_threshold_recommendation(threshold_band_events, config_map):
    from optimizer.analysis import run_all
    findings = run_all(threshold_band_events, config_map, history_events=[])
    result = interpret(findings, config_map, api_key="")
    porch_person = [r for r in result.recommendations
                    if r.get("camera") == "front_porch_cam"
                    and r.get("label") == "person"
                    and r.get("param") == "threshold"]
    assert len(porch_person) == 1
    rec = porch_person[0]
    assert rec["current_value"] == 0.65
    assert isinstance(rec["proposed_value"], (int, float))
    assert rec["proposed_value"] < rec["current_value"]
    assert rec["risk_class"] in ("safe", "risky")


def test_stationary_finding_becomes_max_frames_recommendation(stationary_flicker_events, config_map):
    from optimizer.analysis import run_all
    findings = run_all(stationary_flicker_events, config_map, history_events=[])
    result = interpret(findings, config_map, api_key="")
    flicker = [r for r in result.recommendations
               if r.get("camera") == "back_yard_cam"
               and r.get("label") == "car"
               and r.get("param") == "stationary.max_frames"]
    assert len(flicker) == 1
    assert flicker[0]["current_value"] == 100
    assert flicker[0]["proposed_value"] == 50
    assert flicker[0]["risk_class"] == "safe"


def test_stationary_dedup_collapses_multiple_hotspots_into_one_rec():
    """Multiple flicker hotspots on the same (camera, label) must collapse
    to a single recommendation — otherwise the dashboard shows 10 dupes for
    a single street-parking pattern."""
    from optimizer.claude_layer import _rule_layer_fallback_recs
    # Three hotspots, same camera+label
    findings = {
        "stationary": {"flicker_hotspots": [
            {"camera": "back_yard_cam", "label": "car",
             "position_bucket": [3, 5], "flicker_event_count": 12,
             "current_stationary_max_frames": 100, "evidence": ""},
            {"camera": "back_yard_cam", "label": "car",
             "position_bucket": [7, 5], "flicker_event_count": 8,
             "current_stationary_max_frames": 100, "evidence": ""},
            {"camera": "back_yard_cam", "label": "car",
             "position_bucket": [11, 5], "flicker_event_count": 15,
             "current_stationary_max_frames": 100, "evidence": ""},
        ]},
        "model_limit": {"candidates": []},
        "threshold_proximity": {"candidates": []},
    }
    config_map = {("back_yard_cam", "car"): {"min_score": 0.6, "threshold": 0.7,
                                              "min_area": 8000, "stationary_max_frames": 100,
                                              "zones": []}}
    recs = _rule_layer_fallback_recs(findings, config_map)
    flicker_recs = [r for r in recs if r["param"] == "stationary.max_frames"]
    assert len(flicker_recs) == 1
    assert "35 brief detections" in flicker_recs[0]["rationale"]
    assert "3 fixed positions" in flicker_recs[0]["rationale"]


def test_empty_findings_yield_empty_recommendations(config_map):
    from optimizer.analysis import run_all
    findings = run_all([], config_map, history_events=[])
    result = interpret(findings, config_map, api_key="")
    assert result.used is False
    assert result.recommendations == []


def test_budget_exceeded_falls_back_without_calling(porch_no_dog_events, config_map):
    """If the estimated input exceeds the token budget, the layer falls back
    without ever calling Anthropic."""
    from optimizer.analysis import run_all
    findings = run_all(porch_no_dog_events, config_map, history_events=[])
    # Tiny budget — will trip the pre-call gate
    result = interpret(findings, config_map, api_key="sk-ant-fake", token_budget=10)
    assert result.used is False
    assert result.error and "budget" in result.error
    # Recommendations still produced via fallback
    assert len(result.recommendations) >= 1
