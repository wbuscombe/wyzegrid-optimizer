"""
Shared test fixtures.

Builds canonical, hand-tuned event datasets that exercise each detector's
core decisions. Tests reach for these instead of repeating the boilerplate.
"""
from __future__ import annotations

import time

import pytest


def _ev(
    eid: str,
    camera: str,
    label: str,
    score: float = 0.7,
    start_offset: float = 0.0,
    duration: float = 5.0,
    box: list | None = None,
) -> dict:
    now = 1717500000.0  # fixed epoch so tests are deterministic
    return {
        "id": eid,
        "camera": camera,
        "label": label,
        "score": score,
        "start_time": now + start_offset,
        "end_time": (now + start_offset + duration) if duration is not None else None,
        "box": box,
        "false_positive": False,
    }


@pytest.fixture
def config_map() -> dict:
    """Mirrors the actual production config — thresholds the detectors check against."""
    return {
        ("front_yard_cam", "person"): {
            "min_score": 0.55, "threshold": 0.65, "min_area": 3000,
            "stationary_max_frames": None, "zones": [],
        },
        ("front_yard_cam", "car"): {
            "min_score": 0.60, "threshold": 0.70, "min_area": 8000,
            "stationary_max_frames": 100, "zones": [],
        },
        ("front_yard_cam", "dog"): {
            "min_score": 0.45, "threshold": 0.55, "min_area": 1500,
            "stationary_max_frames": None, "zones": [],
        },
        ("front_yard_cam", "cat"): {
            "min_score": 0.45, "threshold": 0.55, "min_area": 1000,
            "stationary_max_frames": None, "zones": [],
        },
        ("front_porch_cam", "person"): {
            "min_score": 0.55, "threshold": 0.65, "min_area": 3000,
            "stationary_max_frames": None, "zones": [],
        },
        ("front_porch_cam", "dog"): {
            "min_score": 0.45, "threshold": 0.55, "min_area": 1500,
            "stationary_max_frames": None, "zones": [],
        },
        ("front_porch_cam", "cat"): {
            "min_score": 0.45, "threshold": 0.55, "min_area": 1000,
            "stationary_max_frames": None, "zones": [],
        },
        ("back_yard_cam", "car"): {
            "min_score": 0.60, "threshold": 0.70, "min_area": 8000,
            "stationary_max_frames": 100, "zones": [],
        },
        ("back_deck_cam", "person"): {
            "min_score": 0.55, "threshold": 0.65, "min_area": 3000,
            "stationary_max_frames": None, "zones": [],
        },
        ("back_deck_cam", "cat"): {
            "min_score": 0.45, "threshold": 0.55, "min_area": 1000,
            "stationary_max_frames": None, "zones": [],
        },
    }


@pytest.fixture
def porch_no_dog_events() -> list[dict]:
    """
    The signature scenario: front_porch_cam tracks dogs but never produces a dog
    event, while front_yard_cam does. Per-camera-profile should flag the asymmetry
    AND model-limit should flag the porch dog (porch has many other detections,
    just no dogs).
    """
    events = []
    # Yard: 8 dog detections, 30 person, 40 car (active camera, dog detection works)
    for i in range(8):
        events.append(_ev(f"yd_dog_{i}", "front_yard_cam", "dog", score=0.78, start_offset=i * 100))
    for i in range(30):
        events.append(_ev(f"yp_{i}", "front_yard_cam", "person", score=0.72, start_offset=1000 + i * 50))
    for i in range(40):
        events.append(_ev(f"yc_{i}", "front_yard_cam", "car", score=0.81, start_offset=3000 + i * 30))
    # Porch: 0 dog detections, 30 person, 40 car (camera busy, but never a dog)
    for i in range(30):
        events.append(_ev(f"pp_{i}", "front_porch_cam", "person", score=0.71, start_offset=i * 50))
    for i in range(40):
        events.append(_ev(f"pc_{i}", "front_porch_cam", "car", score=0.74, start_offset=2000 + i * 30))
    return events


@pytest.fixture
def threshold_band_events() -> list[dict]:
    """
    front_porch_cam person detections clustering near the 0.65 threshold —
    threshold_proximity should flag this as a tuning candidate.
    """
    events = []
    # 20 events tightly around 0.65: 10 just above, 10 just below
    for i in range(10):
        events.append(_ev(f"high_{i}", "front_porch_cam", "person", score=0.66 + i * 0.001))
    for i in range(10):
        events.append(_ev(f"low_{i}", "front_porch_cam", "person", score=0.64 - i * 0.001))
    return events


@pytest.fixture
def stationary_flicker_events() -> list[dict]:
    """
    back_yard_cam car: 12 brief detections at the same approximate position —
    classic parked-car re-trigger.
    """
    events = []
    box = [0.30, 0.40, 0.10, 0.08]  # same position
    for i in range(12):
        events.append(_ev(f"park_{i}", "back_yard_cam", "car",
                          score=0.72, start_offset=i * 200, duration=1.5, box=box))
    return events
