"""
Model-limit candidate detector.

The honest-classification feature. A label that:
  - is tracked by the camera (in config)
  - has motion firing on the camera (camera_fps ≈ process_fps in the stats, i.e.
    we can observe many process_fps for this camera in the *current* events)
  - but produces essentially zero detections of that specific label

…is almost certainly something the model can't classify reliably at this
resolution. Threshold/min_area tweaks won't fix it; only a better model or
better source frames would.

We can't read live process_fps from history events alone, so this detector
proxies "the camera was active" by counting the camera's TOTAL detection events
of OTHER labels. If other labels detect just fine but THIS label is zero, that's
the model-limit signal — distinct from a camera that's simply offline.
"""
from __future__ import annotations

from collections import Counter, defaultdict


def detect(events: list[dict], config_map: dict) -> dict:
    cam_label_counts: dict[str, Counter] = defaultdict(Counter)
    for e in events:
        if e["camera"] and e["label"]:
            cam_label_counts[e["camera"]][e["label"]] += 1

    cam_totals: dict[str, int] = {cam: sum(c.values()) for cam, c in cam_label_counts.items()}

    tracked: dict[str, set[str]] = defaultdict(set)
    for (cam, label), _ in config_map.items():
        tracked[cam].add(label)

    findings = []
    for cam, labels in tracked.items():
        total = cam_totals.get(cam, 0)
        if total < 50:
            # Camera not active enough in this window to conclude anything
            # about per-label failure. Don't muddy the signal.
            continue
        for label in sorted(labels):
            if cam_label_counts.get(cam, Counter()).get(label, 0) == 0:
                # Camera has many detections of OTHER labels but zero of this one
                findings.append({
                    "camera": cam,
                    "label": label,
                    "camera_total_events": total,
                    "this_label_events": 0,
                    "evidence": (
                        f"{cam} produced {total} detections of other labels in this window "
                        f"but ZERO of '{label}' despite tracking it. Almost certainly a "
                        f"model-limit on the current CPU model — NOT config-fixable. Do not "
                        f"recommend a threshold or min_area change."
                    ),
                    "recommended_action": "model-limit; no config change",
                })
    return {"candidates": findings}
