"""
Shape-mismatch detector — person events whose box geometry is inconsistent
with an upright human.

A standing person's bounding box, normalized to [0, 1] frame coordinates,
typically has its TOP near box_y ≈ 0.1–0.4 and a tall box_h ≥ 0.30. A dog
or other small animal at the same distance produces a box that:
  - sits LOWER in the frame (box_y > 0.50 — animal's top is near or below
    the frame's vertical midpoint).
  - is SHORTER (box_h < 0.35 — animal-tall, not human-tall).

Combined with a below-confident-range person score, this is a strong signal
that the "person" detection is a misclassified animal.

Tagged model-limit — the model is confusing classes it partially detects, and
no config knob fixes that. Phase 2's auto-apply must never try to tune these.

Output:
  {"candidates": [{camera, count, sample_event_ids, person_confident_low, evidence}, ...]}

Tuning note: the (box_y > 0.50, box_h < 0.35, score < person_confident_low)
band was validated against the live data review on 2026-06-11: 8 candidates
surfaced on front_yard_cam with the documented review criteria — these
thresholds reproduce that count.
"""
from __future__ import annotations

from collections import defaultdict


DEFAULT_BOX_Y_MIN = 0.50
DEFAULT_BOX_H_MAX = 0.35
DEFAULT_QUANTILE_LOW = 0.25
DEFAULT_MIN_CANDIDATES_PER_CAMERA = 3
DEFAULT_FALLBACK_CONFIDENT_LOW = 0.60


def _person_confident_low(events: list[dict], quantile: float) -> float:
    scores = sorted(
        e["score"] for e in events if e.get("label") == "person" and e.get("score")
    )
    if len(scores) < 10:
        return DEFAULT_FALLBACK_CONFIDENT_LOW
    return scores[int(len(scores) * quantile)]


def detect(
    events: list[dict],
    config_map: dict,
    *,
    box_y_min: float = DEFAULT_BOX_Y_MIN,
    box_h_max: float = DEFAULT_BOX_H_MAX,
    quantile_low: float = DEFAULT_QUANTILE_LOW,
    min_candidates: int = DEFAULT_MIN_CANDIDATES_PER_CAMERA,
) -> dict:
    person_low = _person_confident_low(events, quantile_low)

    by_camera: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("label") != "person":
            continue
        box = e.get("box")
        if not box or len(box) != 4:
            continue
        bx, by, bw, bh = box
        if by is None or bh is None:
            continue
        if by > box_y_min and bh < box_h_max and (e.get("score") or 1.0) < person_low:
            by_camera[e["camera"]].append(e)

    candidates = []
    for camera, evs in by_camera.items():
        if len(evs) < min_candidates:
            continue
        candidates.append({
            "camera": camera,
            "label": "person",
            "count": len(evs),
            "person_confident_low": person_low,
            "sample_event_ids": [e["id"] for e in evs[:5] if e.get("id")],
            "evidence": (
                f"{len(evs)} person events on {camera} have animal-like geometry "
                f"(box_y > {box_y_min:.2f}, box_h < {box_h_max:.2f}) and score below "
                f"the person confident-range floor ({person_low:.2f}) — possible "
                f"misclassified animal. Surfaced for review; not config-fixable."
            ),
        })
    return {"candidates": candidates}
