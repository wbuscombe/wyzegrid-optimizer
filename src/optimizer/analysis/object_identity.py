"""Summarize the object identities Frigate actually supplied.

This detector never guesses from imagery.  It reports the base label and any
upstream ``sub_label`` already attached to an event, so the dashboard can show
the difference between "vehicle" and a genuinely identified service vehicle.
"""
from __future__ import annotations

from collections import Counter, defaultdict


def detect(events: list[dict], _config_map: dict | None = None) -> dict:
    per_camera: dict[str, Counter[tuple[str, str | None]]] = defaultdict(Counter)
    base_labels: Counter[str] = Counter()
    sub_labels: Counter[str] = Counter()
    unresolved_vehicle_events = 0

    for event in events:
        camera = str(event.get("camera") or "unknown")
        label = str(event.get("label") or "unknown")
        sub_label = event.get("sub_label")
        sub_label = str(sub_label).strip() if sub_label else None
        per_camera[camera][(label, sub_label)] += 1
        base_labels[label] += 1
        if sub_label:
            sub_labels[sub_label] += 1
        elif label.lower() in {"car", "truck", "bus", "motorcycle"}:
            unresolved_vehicle_events += 1

    rows = []
    for camera in sorted(per_camera):
        for (label, sub_label), count in sorted(
            per_camera[camera].items(), key=lambda item: (item[0][0], item[0][1] or "")
        ):
            rows.append({
                "camera": camera,
                "label": label,
                "sub_label": sub_label,
                "count": count,
                "identity": sub_label or label,
                "granularity": "sub-label" if sub_label else "base-label",
            })

    return {
        "events": sum(base_labels.values()),
        "base_labels": dict(sorted(base_labels.items())),
        "sub_labels": dict(sorted(sub_labels.items())),
        "sub_labeled_events": sum(sub_labels.values()),
        "unresolved_vehicle_events": unresolved_vehicle_events,
        "rows": rows,
    }
