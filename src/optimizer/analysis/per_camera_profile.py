"""
Per-camera label profile — the "porch-never-sees-dogs" detector.

For each camera, list the tracked labels and what fraction of the window's
events for that label came from this camera. Surfaces asymmetries: a camera
that NEVER detects a label that other cameras detect frequently is a candidate
for either a model-limit issue (porch dog), a geometry issue (cat zone wrong),
or simply low activity (not a defect).
"""
from __future__ import annotations

from collections import Counter, defaultdict


def detect(events: list[dict], config_map: dict) -> dict:
    # Total events per label across all cameras
    label_totals: Counter = Counter()
    # Per-camera label counts
    per_cam: dict[str, Counter] = defaultdict(Counter)

    for e in events:
        if e["label"] and e["camera"]:
            label_totals[e["label"]] += 1
            per_cam[e["camera"]][e["label"]] += 1

    # Tracked labels per camera, from config
    tracked: dict[str, set[str]] = defaultdict(set)
    for (cam, label), _ in config_map.items():
        tracked[cam].add(label)

    asymmetries = []
    profile = []
    for cam in sorted(set(per_cam.keys()) | set(tracked.keys())):
        cam_counts = per_cam.get(cam, Counter())
        cam_total = sum(cam_counts.values())
        labels_seen = sorted(cam_counts.keys())
        labels_tracked = sorted(tracked.get(cam, set()))
        labels_never_seen = sorted(set(labels_tracked) - set(labels_seen))

        for label in labels_never_seen:
            global_count = label_totals.get(label, 0)
            other_cam_count = global_count - cam_counts.get(label, 0)
            if other_cam_count >= 5:
                asymmetries.append({
                    "camera": cam,
                    "label": label,
                    "events_on_this_camera": 0,
                    "events_on_other_cameras": other_cam_count,
                    "evidence": (
                        f"{cam} tracks '{label}' but produced zero events while "
                        f"other cameras produced {other_cam_count}. Likely model-limit, "
                        f"camera-angle, or low-activity issue (NOT a config-fixable threshold problem)."
                    ),
                })

        profile.append({
            "camera": cam,
            "total_events": cam_total,
            "by_label": dict(cam_counts),
            "tracked_labels": labels_tracked,
            "never_seen_labels": labels_never_seen,
        })

    return {"profiles": profile, "asymmetries": asymmetries}
