"""
Stationary re-trigger detector.

Distinct from `fp_patterns.spatial_hotspots` (which looks at all events): this
detector looks at *short-duration* events (likely flickers) clustered at one
position, suggesting an object is generating burst-detections from a fixed
spot. Classic case: the parked-car re-trigger.
"""
from __future__ import annotations

from collections import Counter, defaultdict


FLICKER_MAX_SECONDS = 5.0  # events shorter than this are flickers
SPATIAL_BUCKETS = 20  # finer grid than fp_patterns to isolate single objects


def _center_bucket(box: list | None) -> tuple[int, int] | None:
    if not box or any(v is None for v in box):
        return None
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    return (
        max(0, min(SPATIAL_BUCKETS - 1, int(cx * SPATIAL_BUCKETS))),
        max(0, min(SPATIAL_BUCKETS - 1, int(cy * SPATIAL_BUCKETS))),
    )


def detect(events: list[dict], config_map: dict) -> dict:
    flickers_by_pos: dict[tuple[str, str, tuple[int, int]], int] = Counter()
    for e in events:
        if not e["camera"] or not e["label"]:
            continue
        end = e.get("end_time")
        if end is None:
            continue
        if end - e["start_time"] > FLICKER_MAX_SECONDS:
            continue
        bucket = _center_bucket(e.get("box"))
        if bucket is None:
            continue
        flickers_by_pos[(e["camera"], e["label"], bucket)] += 1

    findings = []
    for (cam, label, bucket), count in flickers_by_pos.items():
        if count >= 8:
            cfg = config_map.get((cam, label), {}) or {}
            findings.append({
                "camera": cam,
                "label": label,
                "position_bucket": list(bucket),
                "flicker_event_count": count,
                "current_stationary_max_frames": cfg.get("stationary_max_frames"),
                "evidence": (
                    f"{count} short (<{FLICKER_MAX_SECONDS:.0f}s) {label} events from "
                    f"the same position — parked-object / fixed-misclassification pattern. "
                    f"Candidate for a stationary.max_frames tweak."
                ),
            })
    return {"flicker_hotspots": findings}
