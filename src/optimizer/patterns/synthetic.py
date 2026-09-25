"""
Deterministic synthetic event generator for tests and the Phantom demo.

Emits Frigate-shaped `/api/events` payloads for three synthetic cameras
(cam_street, cam_door, cam_back) over 16 weeks from Monday 2031-02-10 in
America/New_York, a span that includes a DST change. No real camera names,
dates, or production-derived timings appear here.

Paths follow Frigate's sampling rule (tracked_object.py): a first point, an
unconditional second point on the next update, then a point only after the
object has moved about 5% of the frame diagonal. A halted object therefore
emits no points while it stands still.

Background (always present unless disabled):
  - cam_street pass-through vehicles, about 344 per day, quiet overnight, a
    sharp ramp at 05:45, peaks 07:00-09:00 and 15:30-18:30
  - cam_street random brief stops, about 3 per day, uniform 07:00-19:00
  - cam_door random person visits, about 6 per day, uniform 07:00-21:00;
    half pause at the door (brief stop), half walk past (pass-through)
Planted (when `planted=True`):
  P1 weekly vehicle brief stop, cam_street, Tuesday onsets 07:05-07:25,
     dwell 40-90 s, 1-3 fragments per visit
  P2 biweekly vehicle brief stop, cam_street, Thursday onsets 06:50-07:10,
     parity A (week_index % 2 == 0) weeks only, same visit model as P1
  P3 person brief stop, cam_door, Monday-Saturday onsets 10:40-11:20
  P4 vehicle long stay, cam_back, Monday-Friday onsets 17:20-17:50, dwell
     3-10 h, stationary re-triggers every 5 min

Cadence controls (`generate_street`): the same declared cam_street background
and nothing else, plus `StreetPlant` vehicle brief stops with P1's visit model.

Ground truth for tests: pass a dict as `origins` and it is filled with
event id -> (origin, declared behavior), origin being one of ORIGINS. The
payloads themselves never carry it, tagging draws no random numbers, and the
pattern pipeline never reads it.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from .localtime import parity as week_parity

TZ_NAME = "America/New_York"
SPAN_START = date(2031, 2, 10)   # a Monday
SPAN_WEEKS = 16
PARITY_A = 0
CAMERAS = ("cam_street", "cam_door", "cam_back")
# Frigate's path threshold for a 16:9 detect stream: 0.05 * diag / max(w, h).
PATH_STEP = 0.05 * math.sqrt(1 + (9 / 16) ** 2)

# (start minute, end minute, vehicles per hour); about 344 per day in total.
STREET_PROFILE = (
    (0, 345, 1.0),
    (345, 420, 18.0),
    (420, 540, 32.0),
    (540, 930, 16.0),
    (930, 1110, 30.0),
    (1110, 1260, 16.0),
    (1260, 1440, 6.0),
)
STREET_DAILY = sum((b - a) / 60.0 * r for a, b, r in STREET_PROFILE)
QUIET_STREET_DAILY = 40.0

PLANTED_CENTERS = {"P1": 7 * 60 + 15, "P2": 7 * 60, "P3": 11 * 60, "P4": 17 * 60 + 35}

ORIGINS = ("background_pass_through", "background_brief_stop", "background_person",
           "P1", "P2", "P3", "P4")


def span_now() -> float:
    """An injected 'now' just after the span: 02:00 local on the following Monday."""
    end = SPAN_START + timedelta(weeks=SPAN_WEEKS)
    return datetime.combine(end, dtime(2, 0), tzinfo=ZoneInfo(TZ_NAME)).timestamp()


class _Gen:
    def __init__(self, seed: int, tz: ZoneInfo, origins: Optional[dict] = None):
        self.rng = random.Random(seed)
        self.tz = tz
        self.seed = seed
        self.count = 0
        self.events: list[dict] = []
        self.origins = origins
        self.tag: Optional[tuple[str, str]] = None   # (origin, declared behavior)

    def epoch(self, day: date, minute: float) -> float:
        base = datetime.combine(day, dtime(0, 0))
        local = base + timedelta(seconds=minute * 60.0)
        return local.replace(tzinfo=self.tz).timestamp()

    def poisson(self, lam: float) -> int:
        # Knuth for small lambda; normal approximation for large ones.
        if lam > 60:
            return max(0, int(round(self.rng.gauss(lam, math.sqrt(lam)))))
        limit, k, prod = math.exp(-lam), 0, 1.0
        while True:
            prod *= self.rng.random()
            if prod <= limit:
                return k
            k += 1

    def emit(self, camera: str, label: str, start: float, end: Optional[float],
             path: list, box: list, sub_label: Optional[str] = None,
             speed: float = 0.0, angle: float = 0.0) -> None:
        self.count += 1
        if self.origins is not None:
            self.origins[f"syn-{self.seed}-{self.count:07d}"] = self.tag
        self.events.append({
            "id": f"syn-{self.seed}-{self.count:07d}",
            "camera": camera,
            "label": label,
            "sub_label": sub_label,
            "start_time": round(start, 3),
            "end_time": round(end, 3) if end is not None else None,
            "zones": [],
            "false_positive": False,
            "top_score": None,
            "data": {
                "box": box,
                "region": [round(max(0.0, box[0] - 0.05), 4), round(max(0.0, box[1] - 0.05), 4),
                           0.3, 0.3],
                "score": round(self.rng.uniform(0.7, 0.9), 3),
                "top_score": round(self.rng.uniform(0.75, 0.95), 3),
                "attributes": [],
                "average_estimated_speed": speed,
                "velocity_angle": angle,
                "type": "object",
                "path_data": [[[x, y], t] for x, y, t in path],
            },
        })


def _straight_path(x0: float, y0: float, x1: float, y1: float, t_move0: float,
                   t_move1: float, halt: Optional[tuple[float, float]] = None,
                   step: float = PATH_STEP) -> list[tuple[float, float, float]]:
    """Frigate-style sampled path along one straight line, optionally pausing
    at arc fraction `halt[0]` for `halt[1]` seconds (no points while halted)."""
    length = math.hypot(x1 - x0, y1 - y0)
    moving = max(0.1, (t_move1 - t_move0) - (halt[1] if halt else 0.0))
    speed = length / moving
    h_at = halt[0] * length if halt else None
    h_dur = halt[1] if halt else 0.0

    def time_at(s: float) -> float:
        t = t_move0 + s / speed
        if h_at is not None and s > h_at:
            t += h_dur
        return t

    def point(s: float) -> tuple[float, float, float]:
        f = s / length if length > 0 else 0.0
        return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, time_at(s)

    second = min(length, speed * 0.2)
    pts = [point(0.0), point(second)]
    s = second + step
    while s <= length:
        pts.append(point(s))
        s += step
    return pts


def _box_at(x: float, y: float, w: float, h: float) -> list[float]:
    bx = min(max(0.0, x - w / 2), 1.0 - w)
    by = min(max(0.0, y - h), 1.0 - h)
    return [round(bx, 4), round(by, 4), w, h]


def _pass_through(g: _Gen, camera: str, label: str, start: float, duration: float,
                  y: float, left_to_right: bool) -> None:
    xa, xb = (0.02, 0.98) if left_to_right else (0.98, 0.02)
    path = _straight_path(xa, y, xb, y + g.rng.uniform(-0.03, 0.03), start, start + duration)
    mid = path[len(path) // 2]
    w, h = (0.12, 0.10) if label != "person" else (0.05, 0.18)
    g.emit(camera, label, start, start + duration, path, _box_at(mid[0], mid[1], w, h))


def _halting_visit(g: _Gen, camera: str, label: str, onset: float, dwell: float,
                   fragments: int, sub_label: Optional[str] = None,
                   speed: float = 0.0, angle: float = 0.0) -> None:
    """One physical stop: approach, halt, depart, emitted as 1-3 tracks. Breaks
    fall at the approach->halt and halt->depart boundaries with 1-3 s gaps."""
    rng = g.rng
    person = label == "person"
    if person:
        xa, ya, xb, yb = rng.uniform(0.3, 0.7), 0.97, 0.5, 0.40
        stop_f = 0.85
    else:
        ltr = rng.random() < 0.5
        y = rng.uniform(0.55, 0.7)
        xa, ya, xb, yb = (0.02, y, 0.98, y) if ltr else (0.98, y, 0.02, y)
        stop_f = rng.uniform(0.35, 0.65)
    moving = rng.uniform(6, 10)             # approach + depart time, split by stop_f
    halt = max(1.0, dwell - moving)
    path = _straight_path(xa, ya, xb, yb, onset, onset + dwell, halt=(stop_f, halt))
    sx, sy = xa + (xb - xa) * stop_f, ya + (yb - ya) * stop_f
    w, h = (0.05, 0.18) if person else (0.14, 0.11)
    box = _box_at(sx, sy, w, h)
    end = onset + dwell
    if fragments <= 1:
        g.emit(camera, label, onset, end, path, box, sub_label, speed, angle)
        return
    # The path's own arrival and departure instants, so breaks land exactly on
    # the approach->halt and halt->depart boundaries.
    arrive = onset + stop_f * (dwell - halt)
    leave = arrive + halt
    gaps = [rng.uniform(1, 3), min(rng.uniform(1, 3), 0.5 * (end - leave))]
    if fragments == 2:
        cuts = [(onset, leave), (leave + gaps[1], end)]
    else:
        cuts = [(onset, arrive), (arrive + gaps[0], leave), (leave + gaps[1], end)]
    for i, (a, b) in enumerate(cuts):
        piece = [p for p in path if a <= p[2] <= b]
        if len(piece) < 2:  # a re-acquired track restarts with its own first two points
            anchor = piece[0] if piece else (sx, sy, a)
            piece = [(anchor[0], anchor[1], a), (anchor[0], anchor[1], a + 0.2)] + piece[1:]
        g.emit(camera, label, a, b, piece, box, sub_label if i == 0 else None, speed, angle)


def _parked_stay(g: _Gen, onset: float, dwell: float) -> None:
    rng = g.rng
    px, py = rng.uniform(0.35, 0.55), rng.uniform(0.5, 0.6)
    base = _box_at(px, py, 0.2, 0.14)
    arrive = _straight_path(0.98, py, px, py, onset, onset + 6)
    g.emit("cam_back", "car", onset, onset + 60, arrive, base)
    t = onset + 300 + rng.uniform(-10, 10)
    while t < onset + dwell - 90:
        jitter = [round(v + rng.uniform(-0.003, 0.003), 4) for v in base[:2]] + base[2:]
        dur = rng.uniform(10, 40)
        g.emit("cam_back", "car", t, t + dur, [(px, py, t), (px, py, t + 0.2)], jitter)
        t += 300 + rng.uniform(-10, 10)
    leave = onset + dwell - 60
    depart = [(px, py, leave)] + _straight_path(px, py, 0.02, py, leave + 50, onset + dwell)
    g.emit("cam_back", "car", leave, onset + dwell, depart, base)


def generate(seed: int, *, planted: bool = True, street_daily: float = STREET_DAILY,
             offline_street_weeks: Sequence[int] = (), weeks: int = SPAN_WEEKS,
             p1_labels: Sequence[tuple[str, float]] = (),
             origins: Optional[dict] = None) -> list[dict]:
    """Payloads sorted by start time. `offline_street_weeks` are 0-based week
    offsets in the span during which cam_street emits nothing at all; the rest
    of the world is generated identically, so a coverage variant differs from
    its base dataset only by the outage. `p1_labels` = [(explicit_label,
    fraction), ...] attaches a test-local upstream sub_label to that fraction
    of P1 visits, in order. `origins`, when given, receives the ground truth of
    every returned event (see the module docstring)."""
    g = _Gen(seed, ZoneInfo(TZ_NAME), origins)
    rng = g.rng
    scale = street_daily / STREET_DAILY
    p1_index = 0
    for day_i in range(weeks * 7):
        day = SPAN_START + timedelta(days=day_i)
        wd = day.weekday()
        # cam_street background (dropped afterwards for offline weeks)
        g.tag = ("background_pass_through", "pass_through")
        for a, b, rate in STREET_PROFILE:
            for _ in range(g.poisson(rate * scale * (b - a) / 60.0)):
                minute = rng.uniform(a, b)
                label = rng.choices(("car", "truck", "bus", "motorcycle"),
                                    weights=(85, 11, 2, 2))[0]
                _pass_through(g, "cam_street", label, g.epoch(day, minute),
                              rng.uniform(3, 8), rng.uniform(0.55, 0.7), rng.random() < 0.5)
        g.tag = ("background_brief_stop", "brief_stop")
        for _ in range(g.poisson(3.0)):
            minute = rng.uniform(7 * 60, 19 * 60)
            _halting_visit(g, "cam_street", rng.choice(("car", "truck")),
                           g.epoch(day, minute), rng.uniform(30, 120), 1)

        for _ in range(g.poisson(6.0)):
            minute = rng.uniform(7 * 60, 21 * 60)
            if rng.random() < 0.5:
                g.tag = ("background_person", "brief_stop")
                _halting_visit(g, "cam_door", "person", g.epoch(day, minute),
                               rng.uniform(15, 90), 1)
            else:
                g.tag = ("background_person", "pass_through")
                _pass_through(g, "cam_door", "person", g.epoch(day, minute),
                              rng.uniform(5, 15), rng.uniform(0.75, 0.85), rng.random() < 0.5)

        if not planted:
            continue
        if wd == 1:  # P1
            g.tag = ("P1", "brief_stop")
            label = None
            cum = 0.0
            for name, frac in p1_labels:
                if p1_index < round((cum + frac) * weeks) and p1_index >= round(cum * weeks):
                    label = name
                cum += frac
            _halting_visit(g, "cam_street", "car", g.epoch(day, rng.uniform(425, 445)),
                           rng.uniform(40, 90), rng.randint(1, 3), sub_label=label,
                           speed=round(rng.uniform(8, 15), 1), angle=round(rng.uniform(0, 360), 1))
            p1_index += 1
        if wd == 3 and week_parity(day) == PARITY_A:  # P2
            g.tag = ("P2", "brief_stop")
            _halting_visit(g, "cam_street", "car", g.epoch(day, rng.uniform(410, 430)),
                           rng.uniform(40, 90), rng.randint(1, 3))
        if wd <= 5:  # P3
            g.tag = ("P3", "brief_stop")
            _halting_visit(g, "cam_door", "person", g.epoch(day, rng.uniform(640, 680)),
                           rng.uniform(30, 120), 1)
        if wd <= 4:  # P4
            g.tag = ("P4", "long_stay")
            _parked_stay(g, g.epoch(day, rng.uniform(1040, 1070)), rng.uniform(3, 10) * 3600)

    if offline_street_weeks:
        tz = ZoneInfo(TZ_NAME)
        offline = set(offline_street_weeks)
        g.events = [
            e for e in g.events
            if not (e["camera"] == "cam_street"
                    and (datetime.fromtimestamp(e["start_time"], tz).date()
                         - SPAN_START).days // 7 in offline)
        ]
    if origins is not None:
        kept = {e["id"] for e in g.events}
        for eid in [k for k in origins if k not in kept]:
            del origins[eid]
    g.events.sort(key=lambda e: (e["start_time"], e["id"]))
    return g.events


@dataclass(frozen=True)
class StreetPlant:
    """One planted cam_street vehicle brief stop for the cadence controls.

    Onsets are uniform on [start_min, end_min) local minutes on `weekday`,
    with P1's visit model (car, dwell 40-90 s, 1-3 fragments). `parity`
    None plants every week, otherwise only weeks of that parity.
    `skip_weeks` are 0-based week offsets in the span with no visit, and
    `miss_rate` is an independent per-week probability of no visit.
    """
    name: str
    weekday: int
    start_min: float
    end_min: float
    parity: Optional[int] = None
    skip_weeks: tuple[int, ...] = ()
    miss_rate: float = 0.0


def generate_street(seed: int, plants: Sequence[StreetPlant], *, weeks: int = SPAN_WEEKS,
                    origins: Optional[dict] = None) -> list[dict]:
    """The declared cam_street background (pass-throughs and random brief stops,
    exactly as in `generate`) plus `plants`, and nothing on any other camera.
    Payloads sorted by start time; `origins` as in `generate`, with each
    plant's name as its origin."""
    g = _Gen(seed, ZoneInfo(TZ_NAME), origins)
    rng = g.rng
    for day_i in range(weeks * 7):
        day = SPAN_START + timedelta(days=day_i)
        wd = day.weekday()
        g.tag = ("background_pass_through", "pass_through")
        for a, b, rate in STREET_PROFILE:
            for _ in range(g.poisson(rate * (b - a) / 60.0)):
                minute = rng.uniform(a, b)
                label = rng.choices(("car", "truck", "bus", "motorcycle"),
                                    weights=(85, 11, 2, 2))[0]
                _pass_through(g, "cam_street", label, g.epoch(day, minute),
                              rng.uniform(3, 8), rng.uniform(0.55, 0.7), rng.random() < 0.5)
        g.tag = ("background_brief_stop", "brief_stop")
        for _ in range(g.poisson(3.0)):
            minute = rng.uniform(7 * 60, 19 * 60)
            _halting_visit(g, "cam_street", rng.choice(("car", "truck")),
                           g.epoch(day, minute), rng.uniform(30, 120), 1)
        for plant in plants:
            if wd != plant.weekday or day_i // 7 in plant.skip_weeks:
                continue
            if plant.parity is not None and week_parity(day) != plant.parity:
                continue
            if plant.miss_rate and rng.random() < plant.miss_rate:
                continue
            g.tag = (plant.name, "brief_stop")
            _halting_visit(g, "cam_street", "car",
                           g.epoch(day, rng.uniform(plant.start_min, plant.end_min)),
                           rng.uniform(40, 90), rng.randint(1, 3))
    g.events.sort(key=lambda e: (e["start_time"], e["id"]))
    return g.events
