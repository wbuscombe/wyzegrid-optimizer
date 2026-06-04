"""
Tests for the deterministic analysis detectors.

Each detector is exercised against a canonical scenario from conftest.py.
The tests assert on specific outputs that encode the detector's contract —
not just "ran without error."
"""
from __future__ import annotations

from optimizer.analysis import (
    score_distribution,
    threshold_proximity,
    fp_patterns,
    regression,
    per_camera_profile,
    stationary,
    model_limit,
    run_all,
)


def test_score_distribution_basic(porch_no_dog_events, config_map):
    result = score_distribution.detect(porch_no_dog_events, config_map)
    assert "distributions" in result
    pairs = {(d["camera"], d["label"]): d for d in result["distributions"]}
    yd_dog = pairs[("front_yard_cam", "dog")]
    assert yd_dog["count"] == 8
    assert 0.70 <= yd_dog["mean"] <= 0.85
    assert yd_dog["current_threshold"] == 0.55
    # The yard dogs are well above threshold — nothing pathological
    assert yd_dog["min"] >= 0.70


def test_threshold_proximity_flags_clustered_scores(threshold_band_events, config_map):
    result = threshold_proximity.detect(threshold_band_events, config_map)
    cand = [c for c in result["candidates"] if c["camera"] == "front_porch_cam" and c["label"] == "person"]
    assert len(cand) == 1
    c = cand[0]
    assert c["total"] == 20
    assert c["in_band"] == 20  # all 20 sit within ±0.05 of 0.65
    assert c["is_tuning_candidate"] is True


def test_threshold_proximity_clean_data_doesnt_flag(porch_no_dog_events, config_map):
    """Yard dog scores (~0.78) are well away from 0.55 threshold — not a candidate."""
    result = threshold_proximity.detect(porch_no_dog_events, config_map)
    yd_dog = next(
        (c for c in result["candidates"] if c["camera"] == "front_yard_cam" and c["label"] == "dog"),
        None,
    )
    assert yd_dog is not None
    assert yd_dog["is_tuning_candidate"] is False


def test_fp_patterns_spatial_hotspot(stationary_flicker_events, config_map):
    result = fp_patterns.detect(stationary_flicker_events, config_map)
    hotspots = result["spatial_hotspots"]
    car_hotspot = [h for h in hotspots if h["camera"] == "back_yard_cam" and h["label"] == "car"]
    assert len(car_hotspot) == 1
    assert car_hotspot[0]["events_in_hotspot"] >= 10
    assert car_hotspot[0]["share"] >= 0.99  # all 12 events at same position


def test_regression_detects_drop():
    # baseline: 24 events over a day = 1/hour
    history = [
        {"id": f"h{i}", "camera": "front_yard_cam", "label": "dog",
         "score": 0.78, "start_time": 1717400000.0 + i * 3600,
         "end_time": None, "box": None, "false_positive": False}
        for i in range(24)
    ]
    # recent: 1 event over a day = 0.04/hour → big drop
    recent = [{
        "id": "r0", "camera": "front_yard_cam", "label": "dog",
        "score": 0.75, "start_time": 1717500000.0,
        "end_time": None, "box": None, "false_positive": False,
    }, {
        "id": "r1", "camera": "front_yard_cam", "label": "dog",
        "score": 0.75, "start_time": 1717586000.0,
        "end_time": None, "box": None, "false_positive": False,
    }]
    result = regression.detect(recent, history)
    drops = result["drops"]
    assert len(drops) == 1
    assert drops[0]["camera"] == "front_yard_cam"
    assert drops[0]["label"] == "dog"
    assert drops[0]["drop_pct"] >= 0.5


def test_per_camera_profile_flags_porch_no_dog(porch_no_dog_events, config_map):
    result = per_camera_profile.detect(porch_no_dog_events, config_map)
    asymmetries = result["asymmetries"]
    porch_dog = [a for a in asymmetries if a["camera"] == "front_porch_cam" and a["label"] == "dog"]
    assert len(porch_dog) == 1
    assert porch_dog[0]["events_on_this_camera"] == 0
    assert porch_dog[0]["events_on_other_cameras"] == 8


def test_stationary_detects_parked_car(stationary_flicker_events, config_map):
    result = stationary.detect(stationary_flicker_events, config_map)
    findings = result["flicker_hotspots"]
    car = [f for f in findings if f["camera"] == "back_yard_cam" and f["label"] == "car"]
    assert len(car) == 1
    assert car[0]["flicker_event_count"] == 12
    assert car[0]["current_stationary_max_frames"] == 100  # comes through from config


def test_model_limit_flags_porch_dog_correctly(porch_no_dog_events, config_map):
    """
    Critical honesty test: the porch-dog absence must be flagged as model-limit,
    NOT as a tunable threshold candidate. This is the whole point of having a
    model-limit detector at all.
    """
    result = model_limit.detect(porch_no_dog_events, config_map)
    findings = result["candidates"]
    porch_dog = [f for f in findings if f["camera"] == "front_porch_cam" and f["label"] == "dog"]
    assert len(porch_dog) == 1
    assert porch_dog[0]["this_label_events"] == 0
    assert porch_dog[0]["camera_total_events"] >= 50
    assert porch_dog[0]["recommended_action"] == "model-limit; no config change"


def test_model_limit_does_not_flag_quiet_cameras():
    """A camera with no detections at all is NOT a model-limit case (it's silent)."""
    events = [
        {"id": "x", "camera": "front_yard_cam", "label": "person", "score": 0.7,
         "start_time": 1717500000.0, "end_time": None, "box": None, "false_positive": False}
    ]
    config_map = {("back_yard_cam", "car"): {"min_score": 0.6, "threshold": 0.7,
                                              "min_area": 8000, "stationary_max_frames": 100,
                                              "zones": []}}
    result = model_limit.detect(events, config_map)
    # back_yard_cam has no events at all — must NOT be flagged
    bk = [f for f in result["candidates"] if f["camera"] == "back_yard_cam"]
    assert bk == []


def test_run_all_returns_full_findings(porch_no_dog_events, config_map):
    findings = run_all(porch_no_dog_events, config_map, history_events=[])
    for key in (
        "score_distribution", "threshold_proximity", "fp_patterns",
        "regression", "per_camera_profile", "stationary", "model_limit",
    ):
        assert key in findings


def test_run_all_with_no_events_is_clean(config_map):
    findings = run_all([], config_map, history_events=[])
    # Every detector should produce its top-level container; values may be empty
    assert findings["score_distribution"]["distributions"] == []
    assert findings["model_limit"]["candidates"] == []
    assert findings["per_camera_profile"]["asymmetries"] == []
