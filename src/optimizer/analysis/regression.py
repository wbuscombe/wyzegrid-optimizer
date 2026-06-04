"""
Regression detector.

For each (camera, label), compare the recent-window detection rate to a trailing
baseline. A meaningful drop suggests something changed: a config tweak, a camera
issue, weather change, or model drift. The detector flags the drop; it does not
claim which cause.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional


def _rate_per_hour(events: list[dict], camera: str, label: str) -> float:
    if not events:
        return 0.0
    relevant = [e for e in events if e["camera"] == camera and e["label"] == label]
    if not relevant:
        return 0.0
    span_seconds = max(1.0, max(e["start_time"] for e in events) - min(e["start_time"] for e in events))
    return len(relevant) / (span_seconds / 3600.0)


def detect(events: list[dict], history_events: list[dict]) -> dict:
    """
    `events` = recent window (e.g. last 24h).
    `history_events` = older trailing window (e.g. prior 7d) — baseline.
    """
    pairs = {(e["camera"], e["label"]) for e in events} | {(e["camera"], e["label"]) for e in history_events}
    drops = []
    for cam, label in sorted(pairs):
        recent = _rate_per_hour(events, cam, label)
        baseline = _rate_per_hour(history_events, cam, label) if history_events else 0.0
        if baseline <= 0.05:  # too small to be meaningful (~1 event per 20h)
            continue
        drop_pct = 1.0 - (recent / baseline) if baseline > 0 else 0.0
        if drop_pct >= 0.5 and (baseline * 24 >= 2):  # ≥50% drop, baseline at least ~2/day
            drops.append({
                "camera": cam,
                "label": label,
                "recent_rate_per_hour": recent,
                "baseline_rate_per_hour": baseline,
                "drop_pct": drop_pct,
                "evidence": (
                    f"recent rate {recent:.2f}/h vs baseline {baseline:.2f}/h "
                    f"— {drop_pct:.0%} drop"
                ),
            })
    return {"drops": drops}
