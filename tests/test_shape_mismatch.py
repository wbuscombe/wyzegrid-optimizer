"""
Tests for the shape-mismatch detector.

The detector surfaces person events whose box geometry is animal-shaped
(low + short) AND whose score is below the person confident-low.
"""
from __future__ import annotations

from optimizer.analysis import shape_mismatch


def _ev(eid, camera, label, score, box):
    return {
        "id": eid, "camera": camera, "label": label, "score": score,
        "start_time": 1717500000.0, "end_time": 1717500005.0, "box": box,
        "false_positive": False,
    }


def test_upright_person_is_not_flagged():
    """Healthy standing-person geometry (tall box, top of frame)."""
    events = []
    for i in range(20):
        events.append(_ev(f"p_{i}", "front_yard_cam", "person",
                          score=0.78, box=[0.30, 0.15, 0.20, 0.65]))
    result = shape_mismatch.detect(events, {})
    assert result["candidates"] == []


def test_low_short_lowscore_person_is_flagged():
    """Cluster of person events with box_y > 0.5, box_h < 0.35, below-low score."""
    events = []
    # Healthy baseline so confident_low is meaningful
    for i in range(20):
        events.append(_ev(f"good_{i}", "front_yard_cam", "person",
                          score=0.80, box=[0.30, 0.15, 0.20, 0.65]))
    # Five animal-shaped suspects
    for i in range(5):
        events.append(_ev(f"bad_{i}", "front_yard_cam", "person",
                          score=0.62, box=[0.40, 0.60, 0.15, 0.25]))
    result = shape_mismatch.detect(events, {})
    yard = [c for c in result["candidates"] if c["camera"] == "front_yard_cam"]
    assert len(yard) == 1
    assert yard[0]["count"] == 5
    assert yard[0]["label"] == "person"
    assert len(yard[0]["sample_event_ids"]) == 5


def test_below_min_count_not_flagged():
    """A single suspect isn't enough to flag a pattern."""
    events = []
    for i in range(15):
        events.append(_ev(f"good_{i}", "front_yard_cam", "person",
                          score=0.78, box=[0.30, 0.15, 0.20, 0.65]))
    events.append(_ev("solo", "front_yard_cam", "person",
                      score=0.55, box=[0.40, 0.60, 0.15, 0.25]))
    result = shape_mismatch.detect(events, {})
    assert result["candidates"] == []


def test_high_score_animal_shape_not_flagged():
    """Animal geometry but HIGH score: don't flag — likely a real dog event
    on a camera where the model can correctly classify it. The shape-mismatch
    signal requires the score to be below the person confident-low."""
    events = []
    for i in range(20):
        events.append(_ev(f"good_{i}", "front_yard_cam", "person",
                          score=0.80, box=[0.30, 0.15, 0.20, 0.65]))
    # 5 animal-shaped events but high confidence — score above confident_low
    for i in range(5):
        events.append(_ev(f"hi_{i}", "front_yard_cam", "person",
                          score=0.90, box=[0.40, 0.60, 0.15, 0.25]))
    result = shape_mismatch.detect(events, {})
    assert result["candidates"] == []
