"""
Phantom demo-mode synthetic data.

Generates a believable analysis run from thin air — no Frigate, no API key.
The dashboard reads these structures when PHANTOM_MODE=1 is set.
"""
from __future__ import annotations

import time
from typing import Any


PHANTOM_CAMERAS = [
    ("phantom_front_yard", "Front Yard (demo)"),
    ("phantom_front_porch", "Front Porch (demo)"),
    ("phantom_back_yard", "Back Yard (demo)"),
    ("phantom_back_deck", "Back Deck (demo)"),
]


def phantom_config_map() -> dict[tuple[str, str], dict]:
    """Plausible mirror of the production filter set."""
    base = {
        "person": {"min_score": 0.55, "threshold": 0.65, "min_area": 3000,
                   "stationary_max_frames": None, "zones": []},
        "car": {"min_score": 0.60, "threshold": 0.70, "min_area": 8000,
                "stationary_max_frames": 100, "zones": []},
        "dog": {"min_score": 0.45, "threshold": 0.55, "min_area": 1500,
                "stationary_max_frames": None, "zones": []},
        "cat": {"min_score": 0.45, "threshold": 0.55, "min_area": 1000,
                "stationary_max_frames": None, "zones": []},
    }
    out = {}
    for cam, _ in PHANTOM_CAMERAS:
        for label, cfg in base.items():
            if cam.endswith("back_yard") and label not in ("person", "car", "dog", "cat"):
                continue
            if cam.endswith("back_deck") and label == "car":
                continue  # back deck doesn't track cars in production
            out[(cam, label)] = dict(cfg)
    return out


def phantom_events(now: float | None = None) -> list[dict]:
    """200ish synthetic events spread over the last week with believable signatures."""
    now = now if now is not None else time.time()
    events: list[dict] = []
    eid = 0

    def add(camera, label, score, hours_ago, duration=5.0, box=None):
        nonlocal eid
        eid += 1
        events.append({
            "id": f"phantom-{eid}",
            "camera": camera,
            "label": label,
            "score": score,
            "start_time": now - hours_ago * 3600,
            "end_time": (now - hours_ago * 3600 + duration) if duration is not None else None,
            "box": box,
            "false_positive": False,
        })

    # Yard: detects dogs reliably + lots of cars
    for i in range(15):
        add("phantom_front_yard", "dog", 0.74 + (i % 5) * 0.02, hours_ago=i * 10)
    for i in range(80):
        add("phantom_front_yard", "car", 0.72 + (i % 7) * 0.01, hours_ago=i * 2)
    for i in range(25):
        add("phantom_front_yard", "person", 0.71 + (i % 4) * 0.01, hours_ago=i * 5)

    # Porch: detects people + cars, NEVER dogs (the canonical model-limit case)
    for i in range(45):
        add("phantom_front_porch", "person", 0.69 + (i % 3) * 0.01, hours_ago=i * 3.5)
    for i in range(35):
        add("phantom_front_porch", "car", 0.74 + (i % 5) * 0.01, hours_ago=i * 4)

    # Back yard: heavy car traffic with parked-car signature
    parked_box = [0.30, 0.45, 0.10, 0.08]
    for i in range(15):
        add("phantom_back_yard", "car", 0.72, hours_ago=24 + i * 0.5,
            duration=1.5, box=parked_box)
    for i in range(100):
        add("phantom_back_yard", "car", 0.74 + (i % 6) * 0.01, hours_ago=i * 1.5,
            duration=8.0, box=[0.1 + (i % 5) * 0.1, 0.5, 0.15, 0.1])

    # Back deck: people-heavy, low cat detections (also signature for model-limit)
    for i in range(120):
        add("phantom_back_deck", "person", 0.68 + (i % 5) * 0.01, hours_ago=i * 1.2)

    # A few threshold-band-clustered person events on the porch to drive a tuning rec
    for i in range(12):
        add("phantom_front_porch", "person", 0.64 + i * 0.001, hours_ago=160 + i)

    return events


def phantom_findings_and_recommendations() -> tuple[dict, list[dict]]:
    """Build the full findings + recommendations payload for the dashboard demo."""
    from .analysis import run_all
    from .claude_layer import _rule_layer_fallback_recs

    config_map = phantom_config_map()
    events = phantom_events()
    findings = run_all(events, config_map, history_events=[])
    recs = _rule_layer_fallback_recs(findings, config_map)
    return findings, recs


def phantom_history() -> list[dict]:
    """A fake recent-run history for the dashboard's runs view."""
    now = time.time()
    return [
        {
            "id": 4, "started_at": now - 86400, "finished_at": now - 86400 + 35,
            "status": "ok", "events_window_start": now - 7 * 86400,
            "events_window_end": now - 86400, "claude_used": 1,
            "claude_input_tokens": 1840, "claude_output_tokens": 620,
            "claude_cost_usd": 0.0053, "notes": "phantom",
        },
        {
            "id": 3, "started_at": now - 172800, "finished_at": now - 172800 + 28,
            "status": "ok", "events_window_start": now - 8 * 86400,
            "events_window_end": now - 172800, "claude_used": 1,
            "claude_input_tokens": 1720, "claude_output_tokens": 580,
            "claude_cost_usd": 0.0049, "notes": "phantom",
        },
    ]
