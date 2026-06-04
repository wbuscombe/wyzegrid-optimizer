"""
False-positive pattern detector.

Two signals:
  1. Recurring same-position events: many events of the same (camera, label) with
     box centers within a small spatial epsilon. Suggests a stationary object
     being mis-classified or re-triggered.
  2. Time-of-day clustering: events of the same (camera, label) tightly grouped
     into 1–3 hour windows day after day. Suggests light/shadow/wind artifacts.

These don't claim FP status — they surface a *candidate* with evidence. The
Claude layer (or human reviewer) decides.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone


SPATIAL_BUCKETS = 10  # 10x10 grid over the normalized frame
HOUR_BUCKETS = 24


def _box_center_bucket(box: list | None) -> tuple[int, int] | None:
    if not box or any(v is None for v in box):
        return None
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    return (
        max(0, min(SPATIAL_BUCKETS - 1, int(cx * SPATIAL_BUCKETS))),
        max(0, min(SPATIAL_BUCKETS - 1, int(cy * SPATIAL_BUCKETS))),
    )


def detect(events: list[dict], config_map: dict) -> dict:
    spatial: dict[tuple[str, str], Counter] = defaultdict(Counter)
    temporal: dict[tuple[str, str], Counter] = defaultdict(Counter)

    for e in events:
        if not e["camera"] or not e["label"]:
            continue
        bucket = _box_center_bucket(e["box"])
        if bucket is not None:
            spatial[(e["camera"], e["label"])][bucket] += 1
        ts = e["start_time"]
        if ts:
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            temporal[(e["camera"], e["label"])][hour] += 1

    spatial_findings = []
    for (cam, label), counts in spatial.items():
        total = sum(counts.values())
        if total < 10:
            continue
        most_bucket, most_count = counts.most_common(1)[0]
        share = most_count / total
        if share >= 0.4 and most_count >= 10:
            spatial_findings.append({
                "camera": cam,
                "label": label,
                "hotspot_bucket": list(most_bucket),
                "events_in_hotspot": most_count,
                "total_events": total,
                "share": share,
                "evidence": "≥40% of detections concentrated in a single 10x10 grid cell — likely stationary object",
            })

    temporal_findings = []
    for (cam, label), counts in temporal.items():
        total = sum(counts.values())
        if total < 10:
            continue
        # Find a 3-hour window holding the majority of detections
        best_window = None
        best_share = 0.0
        for start in range(HOUR_BUCKETS):
            window_count = sum(counts.get((start + i) % HOUR_BUCKETS, 0) for i in range(3))
            share = window_count / total
            if share > best_share:
                best_share, best_window = share, (start, (start + 2) % HOUR_BUCKETS, window_count)
        if best_window and best_share >= 0.5 and best_window[2] >= 10:
            temporal_findings.append({
                "camera": cam,
                "label": label,
                "window_hours_utc": [best_window[0], best_window[1]],
                "events_in_window": best_window[2],
                "total_events": total,
                "share": best_share,
                "evidence": "≥50% of detections fall in a single 3h UTC window — possible light/shadow/wind artifact",
            })

    return {"spatial_hotspots": spatial_findings, "temporal_clusters": temporal_findings}
