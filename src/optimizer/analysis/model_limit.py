"""
Model-limit candidate detector.

The honest-classification feature. A label that:
  - is tracked by the camera (in config)
  - has motion firing on the camera (we observe many detections of OTHER labels)
  - but produces essentially zero detections of that specific label

…is almost certainly something the model can't classify reliably at this
resolution. Threshold/min_area tweaks won't fix it; only a better model or
better source frames would.

CLASS-SWAP HARDENING (added 2026-06-11):
A previous version of this detector un-flagged a (camera, label) the moment
ANY events of that label appeared, even if those events were class-swap
misclassifications (e.g. a visiting cat being labeled "dog"). That's
self-reinforcing in an auto-tune loop — the false improvement signal would
nudge the threshold further in the wrong direction.

To prevent that, `detect` accepts `suspect_event_ids` (the ids returned by
`class_swap.detect`). Events in that set are treated as if they didn't exist
when counting per-label events on a camera. So a camera whose only "dog"
events are cat misclassifications is correctly re-flagged as model-limit
for dog.
"""
from __future__ import annotations

from collections import Counter, defaultdict


def detect(
    events: list[dict],
    config_map: dict,
    suspect_event_ids: list[str] | set[str] | None = None,
) -> dict:
    suspect = set(suspect_event_ids or [])

    cam_label_counts: dict[str, Counter] = defaultdict(Counter)
    for e in events:
        if e.get("id") in suspect:
            continue
        if e.get("camera") and e.get("label"):
            cam_label_counts[e["camera"]][e["label"]] += 1

    cam_totals: dict[str, int] = {
        cam: sum(c.values()) for cam, c in cam_label_counts.items()
    }

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
                findings.append({
                    "camera": cam,
                    "label": label,
                    "camera_total_events": total,
                    "this_label_events": 0,
                    "evidence": (
                        f"{cam} produced {total} detections of other labels in this window "
                        f"but ZERO of '{label}' (after excluding class-swap suspects). "
                        f"Almost certainly a model-limit on the current CPU model — NOT "
                        f"config-fixable. Do not recommend a threshold or min_area change."
                    ),
                    "recommended_action": "model-limit; no config change",
                })
    return {"candidates": findings}
