"""
Tests for the confidence field populated by the rule-layer fallback.

Phase 2's auto-apply gate will read `recommendation.confidence`. These tests
pin the contract: every recommendation has confidence ∈ (0, 1], and stronger
data produces higher confidence than thinner data.
"""
from __future__ import annotations

from optimizer.analysis import run_all
from optimizer.claude_layer import interpret


def _ev(eid, camera, label, score, t, dur=5.0, box=None):
    return {
        "id": eid, "camera": camera, "label": label, "score": score,
        "start_time": t, "end_time": t + dur, "box": box, "false_positive": False,
    }


def test_every_recommendation_has_nonnull_confidence(
        porch_no_dog_events, config_map):
    findings = run_all(porch_no_dog_events, config_map, history_events=[])
    result = interpret(findings, config_map, api_key="")
    assert result.recommendations  # we should get at least one
    for r in result.recommendations:
        assert "confidence" in r
        assert r["confidence"] is not None
        assert 0.0 < r["confidence"] <= 1.0


def test_threshold_confidence_scales_with_sample_size():
    """Same in-band-pct but bigger dataset → higher confidence."""
    from optimizer.claude_layer import _rule_layer_fallback_recs
    config_map = {("front_porch_cam", "person"): {"min_score": 0.55, "threshold": 0.65,
                                                   "min_area": 3000,
                                                   "stationary_max_frames": None,
                                                   "zones": []}}
    # Small dataset, 50% in-band
    small_findings = {
        "threshold_proximity": {"candidates": [{
            "camera": "front_porch_cam", "label": "person", "threshold": 0.65,
            "band": 0.05, "total": 20, "in_band": 10, "in_band_pct": 0.50,
            "is_tuning_candidate": True,
        }]},
        "class_swap": {"candidates": []}, "shape_mismatch": {"candidates": []},
        "model_limit": {"candidates": []},
    }
    # Big dataset, 50% in-band
    big_findings = {
        "threshold_proximity": {"candidates": [{
            "camera": "front_porch_cam", "label": "person", "threshold": 0.65,
            "band": 0.05, "total": 200, "in_band": 100, "in_band_pct": 0.50,
            "is_tuning_candidate": True,
        }]},
        "class_swap": {"candidates": []}, "shape_mismatch": {"candidates": []},
        "model_limit": {"candidates": []},
    }
    small_rec = _rule_layer_fallback_recs(small_findings, config_map)[0]
    big_rec = _rule_layer_fallback_recs(big_findings, config_map)[0]
    assert big_rec["confidence"] > small_rec["confidence"]


def test_model_limit_confidence_scales_with_other_label_activity():
    """A model-limit on a busy camera is more confident than on a quiet one."""
    from optimizer.claude_layer import _rule_layer_fallback_recs
    quiet = {"class_swap": {"candidates": []}, "shape_mismatch": {"candidates": []},
             "model_limit": {"candidates": [{
                 "camera": "back_deck_cam", "label": "cat",
                 "camera_total_events": 60, "this_label_events": 0,
                 "evidence": "", "recommended_action": "model-limit; no config change",
             }]}}
    busy = {"class_swap": {"candidates": []}, "shape_mismatch": {"candidates": []},
            "model_limit": {"candidates": [{
                "camera": "back_yard_cam", "label": "cat",
                "camera_total_events": 500, "this_label_events": 0,
                "evidence": "", "recommended_action": "model-limit; no config change",
            }]}}
    q_rec = _rule_layer_fallback_recs(quiet, {})[0]
    b_rec = _rule_layer_fallback_recs(busy, {})[0]
    assert b_rec["confidence"] > q_rec["confidence"]


def test_class_swap_recommendation_suppresses_duplicate_model_limit():
    """If a camera+label is flagged by class_swap, the zero-detection model-limit
    rec for the same pair must NOT also appear (single source of truth)."""
    from optimizer.claude_layer import _rule_layer_fallback_recs
    findings = {
        "class_swap": {"candidates": [{
            "camera": "back_deck_cam", "label": "dog", "confused_with": "cat",
            "mean_score": 0.58, "label_confident_low": 0.70,
            "overlap_count": 6, "suspect_event_count": 6,
            "evidence": "",
        }]},
        "shape_mismatch": {"candidates": []},
        # model_limit ALSO flags back_deck/dog (because class_swap fed it suspect ids)
        "model_limit": {"candidates": [{
            "camera": "back_deck_cam", "label": "dog",
            "camera_total_events": 100, "this_label_events": 0,
            "evidence": "", "recommended_action": "model-limit; no config change",
        }]},
    }
    recs = _rule_layer_fallback_recs(findings, {})
    deck_dog_recs = [r for r in recs
                     if r["camera"] == "back_deck_cam" and r["label"] == "dog"]
    # Exactly one — the class-swap version (more specific than zero-detection)
    assert len(deck_dog_recs) == 1
    assert deck_dog_recs[0]["risk_class"] == "model-limit"
    assert "cat" in deck_dog_recs[0]["rationale"]


def test_class_swap_recommendation_uses_model_limit_risk_class():
    """Hard rule: class-swap recommendations are ALWAYS risk_class=model-limit
    with proposed_value=null. Phase 2 must never tune a class-swap."""
    from optimizer.claude_layer import _rule_layer_fallback_recs
    findings = {
        "class_swap": {"candidates": [{
            "camera": "back_deck_cam", "label": "dog", "confused_with": "cat",
            "mean_score": 0.55, "label_confident_low": 0.70,
            "overlap_count": 6, "suspect_event_count": 6, "evidence": "",
        }]},
        "shape_mismatch": {"candidates": []},
        "model_limit": {"candidates": []},
    }
    recs = _rule_layer_fallback_recs(findings, {})
    assert recs[0]["risk_class"] == "model-limit"
    assert recs[0]["proposed_value"] is None
    assert recs[0]["param"] is None


def test_shape_mismatch_recommendation_uses_model_limit_risk_class():
    from optimizer.claude_layer import _rule_layer_fallback_recs
    findings = {
        "class_swap": {"candidates": []},
        "shape_mismatch": {"candidates": [{
            "camera": "front_yard_cam", "label": "person", "count": 4,
            "person_confident_low": 0.65, "sample_event_ids": [],
            "evidence": "",
        }]},
        "model_limit": {"candidates": []},
    }
    recs = _rule_layer_fallback_recs(findings, {})
    assert recs[0]["risk_class"] == "model-limit"
    assert recs[0]["proposed_value"] is None
