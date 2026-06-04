"""
Deterministic rule detectors.

Each detector is a pure function: takes a list of normalized event dicts (and
sometimes a config snapshot map), returns a JSON-serializable findings dict.

Event dict shape (post-normalization in `normalize_events`):
    {
      "id": str,
      "camera": str,
      "label": str,
      "score": float (0-1),
      "start_time": float,
      "end_time": float | None,
      "box": [x, y, w, h] of normalized 0-1 floats (or all None),
      "false_positive": bool,
    }

Config snapshot map shape (post-normalization in `normalize_config_rows`):
    {(camera, label): {"min_score": float|None, "threshold": float|None,
                       "min_area": int|None, "stationary_max_frames": int|None,
                       "zones": [str, ...]}}
"""
from __future__ import annotations

from . import (
    score_distribution,
    threshold_proximity,
    fp_patterns,
    regression,
    per_camera_profile,
    stationary,
    model_limit,
)
from .normalize import normalize_events, normalize_config_rows

DETECTORS = {
    "score_distribution": score_distribution.detect,
    "threshold_proximity": threshold_proximity.detect,
    "fp_patterns": fp_patterns.detect,
    "regression": regression.detect,
    "per_camera_profile": per_camera_profile.detect,
    "stationary": stationary.detect,
    "model_limit": model_limit.detect,
}


def run_all(events: list[dict], config_map: dict, history_events: list[dict] | None = None) -> dict:
    """
    Run every detector and return a single findings dict.

    `events` is the current window (e.g. last 24h or 7d).
    `history_events` is a longer trailing window used by `regression` to build a
    baseline; pass None to disable regression analysis.
    """
    return {
        "score_distribution": score_distribution.detect(events, config_map),
        "threshold_proximity": threshold_proximity.detect(events, config_map),
        "fp_patterns": fp_patterns.detect(events, config_map),
        "regression": regression.detect(events, history_events or []),
        "per_camera_profile": per_camera_profile.detect(events, config_map),
        "stationary": stationary.detect(events, config_map),
        "model_limit": model_limit.detect(events, config_map),
    }


__all__ = ["DETECTORS", "run_all", "normalize_events", "normalize_config_rows"]
