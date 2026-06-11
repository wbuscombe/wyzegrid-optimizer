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
    class_swap,
    shape_mismatch,
)
from .normalize import normalize_events, normalize_config_rows

DETECTORS = {
    "score_distribution": score_distribution.detect,
    "threshold_proximity": threshold_proximity.detect,
    "fp_patterns": fp_patterns.detect,
    "regression": regression.detect,
    "per_camera_profile": per_camera_profile.detect,
    "stationary": stationary.detect,
    "class_swap": class_swap.detect,
    "shape_mismatch": shape_mismatch.detect,
    "model_limit": model_limit.detect,
}


def run_all(events: list[dict], config_map: dict, history_events: list[dict] | None = None) -> dict:
    """
    Run every detector and return a single findings dict.

    Order matters: class_swap runs BEFORE model_limit, and its suspect_event_ids
    are threaded into model_limit so that class-swap misclassifications do not
    count as evidence the model can detect that label. Without this, a camera
    whose only "dog" detections are actually mislabeled cats would be silently
    removed from the model-limit list — a self-reinforcing error if any
    downstream loop ever auto-acts on the recommendations.

    Window choice: class_swap operates on the UNION of `events` and
    `history_events` because class-swap is a slow-developing signal — back_deck
    cat misclassifications in real data were 5-10 events spread across days,
    not minutes. A 24h-only window would under-detect them. The suspect_event_ids
    produced over this wider window still filter model_limit's narrower window
    because event ids are stable.
    """
    swap_events = list(events) + list(history_events or [])
    class_swap_findings = class_swap.detect(swap_events, config_map)
    suspect_event_ids = class_swap_findings.get("suspect_event_ids", [])
    return {
        "score_distribution": score_distribution.detect(events, config_map),
        "threshold_proximity": threshold_proximity.detect(events, config_map),
        "fp_patterns": fp_patterns.detect(events, config_map),
        "regression": regression.detect(events, history_events or []),
        "per_camera_profile": per_camera_profile.detect(events, config_map),
        "stationary": stationary.detect(events, config_map),
        "class_swap": class_swap_findings,
        "shape_mismatch": shape_mismatch.detect(events, config_map),
        "model_limit": model_limit.detect(events, config_map, suspect_event_ids),
    }


__all__ = ["DETECTORS", "run_all", "normalize_events", "normalize_config_rows"]
