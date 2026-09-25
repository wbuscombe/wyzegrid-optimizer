"""
Tracks, visits, and behavior classes (contract sections A, B, C).

A track is one Frigate event reduced to what visits need. Paths are reduced
to a small summary at load time (usable, halted, first and last point), so a
long analysis window never holds every path point in memory.

Visits collapse repeated tracks per stratum (site, label group): a track joins
the open visit if it starts within VISIT_GAP_S of the visit's end, or, when
both carry a box, if its box overlaps the visit's last box with IoU at least
STATIONARY_IOU and it starts within STATIONARY_LINK_S (a parked object
re-triggering). Overlapping objects in one stratum merge into one activity
episode; that is a documented limitation.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from . import metadata
from .settings import DEFAULT_PARAMS, PatternParams

Point = tuple[float, float, float]  # (x, y, t), normalized image coordinates


@dataclass(frozen=True)
class PathSummary:
    usable: bool
    halted: bool
    first: Optional[tuple[float, float]]
    last: Optional[tuple[float, float]]


@dataclass
class Track:
    id: str
    camera: str
    label: str
    start: float
    end: Optional[float]
    box: Optional[tuple[float, float, float, float]] = None
    path: Optional[PathSummary] = None
    speed: Optional[float] = None
    velocity_angle: Optional[float] = None
    explicit_labels: tuple[str, ...] = ()


@dataclass
class Visit:
    site: str
    label_group: str
    onset: float
    end: float
    incomplete: bool = False
    tracks: list[Track] = field(default_factory=list)
    last_box: Optional[tuple[float, float, float, float]] = None
    behavior: str = ""
    basis: str = ""
    local_date: Any = None
    weekday: int = -1
    minute: int = -1

    @property
    def dwell(self) -> float:
        return max(0.0, self.end - self.onset)


# ---- geometry ---------------------------------------------------------------

def iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def centroid(box) -> tuple[float, float]:
    x, y, w, h = box
    return x + w / 2.0, y + h / 2.0


def has_halt(points: list[Point], span_end: float, halt_min_s: float, eps: float) -> bool:
    """True if some sub-interval of at least `halt_min_s`, anchored at a path
    point and inside the observed track span, holds only points within `eps`
    of that anchor.

    Frigate appends a path point only after the object moves a minimum
    distance (tracked_object.py), so a halted object emits no points: its halt
    shows as a stretch with no departing point. The observed span ends at the
    track's end time, which keeps a track's silent tail from counting unless
    the track was still observed for the whole sub-interval.
    """
    n = len(points)
    for i in range(n):
        x0, y0, t0 = points[i]
        if t0 + halt_min_s > span_end:
            break
        ok = True
        for j in range(i + 1, n):
            x, y, t = points[j]
            if t > t0 + halt_min_s:
                break
            if math.hypot(x - x0, y - y0) > eps:
                ok = False
                break
        if ok:
            return True
    return False


def summarize_path(points: Optional[list], end: Optional[float],
                   params: PatternParams = DEFAULT_PARAMS) -> Optional[PathSummary]:
    if not points:
        return None
    pts = sorted(((float(p[0]), float(p[1]), float(p[2])) for p in points), key=lambda p: p[2])
    usable = len(pts) >= 2
    span_end = end if end is not None else pts[-1][2]
    halted = usable and has_halt(pts, span_end, params.halt_min_s, params.halt_eps)
    return PathSummary(usable=usable, halted=halted,
                       first=(pts[0][0], pts[0][1]), last=(pts[-1][0], pts[-1][1]))


# ---- track construction -------------------------------------------------------

def _box_tuple(values) -> Optional[tuple[float, float, float, float]]:
    if values is None or any(v is None for v in values):
        return None
    try:
        return tuple(float(v) for v in values)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _labels(*sources: Iterable[Optional[str]]) -> tuple[str, ...]:
    seen: list[str] = []
    for source in sources:
        for label in source:
            if label and label not in seen:
                seen.append(label)
    return tuple(seen)


def track_from_payload(payload: dict, params: PatternParams = DEFAULT_PARAMS) -> Optional[Track]:
    """A Frigate `/api/events` payload -> Track (uses the same allowlisted
    extraction as ingestion)."""
    meta = metadata.extract(payload)
    if meta is None:
        return None
    try:
        start = float(payload["start_time"])
    except (KeyError, TypeError, ValueError):
        return None
    end_raw = payload.get("end_time")
    end = float(end_raw) if end_raw not in (None, "") else None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return Track(
        id=meta["event_id"],
        camera=str(payload.get("camera") or "unknown"),
        label=str(payload.get("label") or "unknown"),
        start=start,
        end=end,
        box=_box_tuple(metadata.parse_box(data.get("box"))),
        path=summarize_path(meta["path"], end, params),
        speed=meta["average_estimated_speed"],
        velocity_angle=meta["velocity_angle"],
        explicit_labels=_labels(
            [meta["sub_label"]],
            [a["label"] for a in (meta["attributes"] or [])],
        ),
    )


def track_from_row(row, params: PatternParams = DEFAULT_PARAMS) -> Track:
    """A joined events + event_metadata row -> Track."""
    end = float(row["end_time"]) if row["end_time"] else None
    path = None
    if row["path_json"]:
        try:
            path = summarize_path(json.loads(row["path_json"]), end, params)
        except (TypeError, ValueError):
            path = None
    attrs: list = []
    if row["attributes_json"]:
        try:
            attrs = [a.get("label") for a in json.loads(row["attributes_json"])
                     if isinstance(a, dict)]
        except (TypeError, ValueError):
            attrs = []
    return Track(
        id=row["id"],
        camera=row["camera"],
        label=row["label"],
        start=float(row["start_time"]),
        end=end,
        box=_box_tuple((row["box_x"], row["box_y"], row["box_w"], row["box_h"])),
        path=path,
        speed=row["average_estimated_speed"],
        velocity_angle=row["velocity_angle"],
        explicit_labels=_labels([row["meta_sub_label"], row["sub_label"]], attrs),
    )


# ---- visits -------------------------------------------------------------------

def label_group_of(label: str, params: PatternParams = DEFAULT_PARAMS) -> Optional[str]:
    low = (label or "").lower()
    for group, members in params.label_groups.items():
        if low in members:
            return group
    return None


def build_visits(tracks: Iterable[Track], params: PatternParams = DEFAULT_PARAMS) -> list[Visit]:
    """Collapse tracks into visits per (site, label_group). Other labels are ignored."""
    by_stratum: dict[tuple[str, str], list[Track]] = {}
    for track in tracks:
        group = label_group_of(track.label, params)
        if group is None:
            continue
        site = params.site_groups.get(track.camera, track.camera)
        by_stratum.setdefault((site, group), []).append(track)

    visits: list[Visit] = []
    for (site, group), members in sorted(by_stratum.items()):
        members.sort(key=lambda t: (t.start, t.id))
        current: Optional[Visit] = None
        for track in members:
            end = track.end if track.end is not None else track.start
            if current is not None and _joins(current, track, params):
                current.tracks.append(track)
                current.end = max(current.end, end)
                current.incomplete = current.incomplete or track.end is None
                if track.box is not None:
                    current.last_box = track.box
                continue
            current = Visit(site=site, label_group=group, onset=track.start, end=end,
                            incomplete=track.end is None, tracks=[track],
                            last_box=track.box)
            visits.append(current)
    for visit in visits:
        visit.behavior, visit.basis = classify(visit, params)
    return visits


def _joins(visit: Visit, track: Track, params: PatternParams) -> bool:
    gap = track.start - visit.end
    if gap <= params.visit_gap_s:
        return True
    return (
        track.box is not None
        and visit.last_box is not None
        and gap <= params.stationary_link_s
        and iou(track.box, visit.last_box) >= params.stationary_iou
    )


def classify(visit: Visit, params: PatternParams = DEFAULT_PARAMS) -> tuple[str, str]:
    """(behavior, behavior_basis) per contract section C."""
    if visit.dwell >= params.long_stay_s:
        return "long_stay", "dwell_only"
    path_tracks = [t for t in visit.tracks if t.path is not None and t.path.usable]
    if path_tracks:
        halted = any(t.path.halted for t in path_tracks)  # type: ignore[union-attr]
        return ("brief_stop" if halted else "pass_through"), "path"
    boxes = [t.box for t in visit.tracks if t.box is not None]
    long_enough = visit.dwell >= params.brief_stop_min_s
    if len(boxes) >= 2:
        (x0, y0), (x1, y1) = centroid(boxes[0]), centroid(boxes[-1])
        still = math.hypot(x1 - x0, y1 - y0) <= params.brief_stop_max_displacement
        return ("brief_stop" if long_enough and still else "pass_through"), "box"
    return ("brief_stop" if long_enough else "pass_through"), "dwell_only"
