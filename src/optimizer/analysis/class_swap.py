"""
Class-swap detector — finds (camera, label) pairs whose events are likely
misclassifications of a different class on the same camera.

The signature pattern (live case: back_deck_cam dogs that are actually a
visiting cat):
  1. ≥5 events of label X on the camera (enough to judge — not coincidence).
  2. Mean score of those events is BELOW the label's confident range, computed
     as the 25th percentile of label X's scores across all cameras (data-driven
     so the threshold adapts as the dataset grows).
  3. Another label Y exists on the same camera with events that overlap label
     X's events in BOTH box position (centroid y within 0.10 normalized) AND
     time (within 1h).

Returns:
  {
    "candidates": [{camera, label, confused_with, mean_score,
                    label_confident_low, overlap_count, suspect_event_count,
                    evidence}, ...],
    "suspect_event_ids": [str, ...]   # event ids flagged as likely swaps;
                                      # passed to model_limit so it doesn't
                                      # treat them as evidence the model can
                                      # detect that class.
  }

The suspect_event_ids set is the contract that hardens model_limit against
class-swap noise. The whole point of this detector.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean


DEFAULT_QUANTILE_LOW = 0.25
DEFAULT_POSITION_TOLERANCE = 0.10   # normalized box centroid Y distance
DEFAULT_HEIGHT_TOLERANCE = 0.20     # normalized box height delta — dogs and
                                    # cats have similar h, persons are clearly
                                    # taller. Required so dog↔person false
                                    # overlaps don't crowd out dog↔cat ones.
DEFAULT_TIME_WINDOW_SECONDS = 3600  # within 1h counts as co-temporal
DEFAULT_MIN_EVENTS = 5              # camera+label needs this many to judge
DEFAULT_MIN_OVERLAP_PAIRS = 3       # needs this many co-located+co-temporal pairs
DEFAULT_FALLBACK_CONFIDENT_LOW = 0.50


def _centroid_y(event: dict) -> float | None:
    box = event.get("box")
    if not box or len(box) != 4 or box[1] is None or box[3] is None:
        return None
    return box[1] + box[3] / 2


def _box_h(event: dict) -> float | None:
    box = event.get("box")
    if not box or len(box) != 4 or box[3] is None:
        return None
    return box[3]


def _label_confident_low(
    events: list[dict], label: str, quantile: float,
    exclude_camera: str | None = None,
) -> float:
    """Quantile of label X's scores across cameras OTHER than `exclude_camera`.

    Computing the threshold from all cameras (including the suspect one) is
    circular: when a single camera dominates the label's event count with
    misclassifications, those low scores pull the threshold down and the
    suspect camera's mean ends up "healthy" against its own contamination.

    Excluding the suspect camera fixes that — the threshold becomes "what does
    a confident detection of X look like on cameras that aren't being judged."
    Falls back to a fixed default when fewer than 10 events remain.
    """
    scores = [
        e["score"] for e in events
        if e.get("label") == label
        and e.get("score")
        and (exclude_camera is None or e.get("camera") != exclude_camera)
    ]
    if len(scores) < 10:
        return DEFAULT_FALLBACK_CONFIDENT_LOW
    s = sorted(scores)
    idx = int(len(s) * quantile)
    return s[idx]


def detect(
    events: list[dict],
    config_map: dict,
    *,
    quantile_low: float = DEFAULT_QUANTILE_LOW,
    position_tolerance: float = DEFAULT_POSITION_TOLERANCE,
    height_tolerance: float = DEFAULT_HEIGHT_TOLERANCE,
    time_window_seconds: float = DEFAULT_TIME_WINDOW_SECONDS,
    min_events: int = DEFAULT_MIN_EVENTS,
    min_overlap_pairs: int = DEFAULT_MIN_OVERLAP_PAIRS,
) -> dict:
    by_cam_label: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in events:
        if e.get("camera") and e.get("label"):
            by_cam_label[(e["camera"], e["label"])].append(e)

    candidates: list[dict] = []
    suspect_ids: set[str] = set()

    for (camera, label), cam_events in by_cam_label.items():
        if len(cam_events) < min_events:
            continue
        avg = mean(e["score"] for e in cam_events if e.get("score"))
        # Compute confident_low excluding THIS camera so the baseline isn't
        # contaminated by the misclassifications we're trying to detect.
        confident_low = _label_confident_low(
            events, label, quantile_low, exclude_camera=camera,
        )
        if avg >= confident_low:
            # Scores on this camera look healthy — not a class-swap candidate.
            continue

        # Look for another label Y on the same camera whose events overlap
        # in box centroid Y, box height, AND time. Rank candidates by
        # OTHER-CLASS SHARE (overlap_pairs / total_other_class_events_on_camera),
        # not by raw overlap count. Otherwise a high-traffic class like "person"
        # always wins simply because it's numerous, masking more-informative
        # rare-class overlaps like cat (the canonical cat↔dog confusion).
        best = None  # (share, raw_count, other_label, pairs)
        for (other_cam, other_label), other_events in by_cam_label.items():
            if other_cam != camera or other_label == label:
                continue
            overlap_pairs: list[tuple[dict, dict]] = []
            for e1 in cam_events:
                c1 = _centroid_y(e1)
                h1 = _box_h(e1)
                if c1 is None or h1 is None:
                    continue
                for e2 in other_events:
                    c2 = _centroid_y(e2)
                    h2 = _box_h(e2)
                    if c2 is None or h2 is None:
                        continue
                    if abs(c1 - c2) > position_tolerance:
                        continue
                    if abs(h1 - h2) > height_tolerance:
                        continue
                    if abs(e1["start_time"] - e2["start_time"]) > time_window_seconds:
                        continue
                    overlap_pairs.append((e1, e2))
            if len(overlap_pairs) < min_overlap_pairs:
                continue
            share = len(overlap_pairs) / max(1, len(other_events))
            # Tuple ranks (share desc, raw count desc) so rare-class overlaps
            # beat common-class noise while keeping a minimum raw threshold.
            score = (share, len(overlap_pairs))
            if best is None or score > (best[0], best[1]):
                best = (share, len(overlap_pairs), other_label, overlap_pairs)

        if best is None:
            continue
        _, _, other_label, pairs = best
        swap_event_ids = {e1["id"] for e1, _ in pairs if e1.get("id")}
        suspect_ids.update(swap_event_ids)
        candidates.append({
            "camera": camera,
            "label": label,
            "confused_with": other_label,
            "mean_score": avg,
            "label_confident_low": confident_low,
            "overlap_count": len(pairs),
            "suspect_event_count": len(swap_event_ids),
            "evidence": (
                f"{label} on {camera} appears to be {other_label} "
                f"misclassification — {len(pairs)} events share box "
                f"position/time with {other_label} events; mean score "
                f"{avg:.2f} below confident range ({confident_low:.2f}). "
                f"Config cannot fix class confusion."
            ),
        })

    return {
        "candidates": candidates,
        "suspect_event_ids": sorted(suspect_ids),
    }
