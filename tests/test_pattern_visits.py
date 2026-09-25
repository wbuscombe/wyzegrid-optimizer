"""F2/F3 visits and behavior, local time, parity, coverage (V-1..V-7). Synthetic data only."""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone

from optimizer.patterns import discovery, pipeline, synthetic, visits
from optimizer.patterns.localtime import parity, process_local_time, week_index
from optimizer.patterns.settings import EPOCH_MONDAY

from .pattern_helpers import LOCAL, epoch, track, tracks_of

DAY = date(2031, 3, 4)  # a Tuesday
BOX = (0.40, 0.50, 0.20, 0.14)


def _moving(t0: float, secs: float, x0=0.02, x1=0.98, y=0.6):
    n = 16
    return [[x0 + (x1 - x0) * i / (n - 1), y, t0 + secs * i / (n - 1)] for i in range(n)]


def _halting(t0: float, dwell: float, x=0.5, y=0.6):
    # approach points, then silence while halted, then a departing point
    return [[0.2, y, t0], [0.35, y, t0 + 1.5], [x, y, t0 + 3.0],
            [x + 0.06, y, t0 + dwell - 2.0], [0.98, y, t0 + dwell]]


def test_v1_three_fragments_with_short_gaps_collapse_to_one_visit():
    t0 = epoch(DAY, 7, 10)
    frags = [track("f1", t0, t0 + 20), track("f2", t0 + 20 + 15, t0 + 60),
             track("f3", t0 + 60 + 45, t0 + 130)]
    vs = visits.build_visits(frags)
    assert len(vs) == 1
    assert [t.id for t in vs[0].tracks] == ["f1", "f2", "f3"]
    assert vs[0].onset == t0 and vs[0].end == t0 + 130
    # Control: a gap beyond VISIT_GAP_S (and no box link) starts a new visit.
    assert len(visits.build_visits([track("a", t0, t0 + 5), track("b", t0 + 96, t0 + 99)])) == 2


def test_v2_parked_retriggers_become_one_long_stay():
    t0 = epoch(DAY, 17, 30)
    parked = []
    for i in range(37):  # every 5 min for 3 h
        jitter = 0.002 * (i % 3)
        parked.append(track(f"p{i}", t0 + i * 300, t0 + i * 300 + 20, camera="cam_back",
                            box=(BOX[0] + jitter, BOX[1], BOX[2], BOX[3])))
    assert visits.iou(parked[0].box, parked[1].box) >= 0.9
    vs = visits.build_visits(parked)
    assert len(vs) == 1
    assert vs[0].behavior == "long_stay" and vs[0].dwell >= 3 * 3600
    # Control: the same cadence with non-overlapping boxes stays separate visits.
    moved = [track(f"m{i}", t0 + i * 300, t0 + i * 300 + 20, camera="cam_back",
                   box=((i % 4) * 0.25, 0.5, 0.2, 0.14)) for i in range(37)]
    separate = visits.build_visits(moved)
    assert len(separate) == 37
    assert all(v.behavior != "long_stay" for v in separate)


def _one(*tracks_):
    vs = visits.build_visits(tracks_)
    assert len(vs) == 1
    return vs[0].behavior, vs[0].basis


def test_v3_behavior_and_basis_for_path_box_and_dwell_only():
    t0 = epoch(DAY, 9, 0)
    # path basis
    assert _one(track("h", t0, t0 + 60, path=_halting(t0, 60))) == ("brief_stop", "path")
    assert _one(track("m", t0, t0 + 6, path=_moving(t0, 6))) == ("pass_through", "path")
    # a halt whose 10 s is not observed before the track ends is not a halt
    short = [[0.4, 0.6, t0], [0.41, 0.6, t0 + 0.2]]
    assert _one(track("s", t0, t0 + 5, path=short)) == ("pass_through", "path")
    # box basis (no usable path, two or more boxes)
    b1, b2_near, b2_far = (0.4, 0.5, 0.1, 0.1), (0.42, 0.5, 0.1, 0.1), (0.7, 0.5, 0.1, 0.1)
    assert _one(track("b1", t0, t0 + 10, box=b1),
                track("b2", t0 + 20, t0 + 40, box=b2_near)) == ("brief_stop", "box")
    assert _one(track("c1", t0, t0 + 10, box=b1),
                track("c2", t0 + 20, t0 + 40, box=b2_far)) == ("pass_through", "box")
    assert _one(track("d1", t0, t0 + 5, box=b1),
                track("d2", t0 + 6, t0 + 12, box=b2_near)) == ("pass_through", "box")
    # dwell_only basis (no path, fewer than two boxes)
    assert _one(track("w", t0, t0 + 25, box=b1)) == ("brief_stop", "dwell_only")
    assert _one(track("x", t0, t0 + 12)) == ("pass_through", "dwell_only")
    # long_stay is decided by dwell alone
    assert _one(track("l", t0, t0 + 1500, path=_moving(t0, 6))) == ("long_stay", "dwell_only")


def test_v4_null_speed_angle_path_and_box_are_handled():
    t0 = epoch(DAY, 9, 0)
    payload = {"id": "nulls", "camera": "cam_street", "label": "car", "start_time": t0,
               "end_time": None, "data": {"box": None, "path_data": None,
                                          "average_estimated_speed": None,
                                          "velocity_angle": None}}
    tr = tracks_of([payload])
    assert len(tr) == 1 and tr[0].box is None and tr[0].path is None
    assert tr[0].speed is None and tr[0].velocity_angle is None
    vs = visits.build_visits(tr)
    assert vs[0].incomplete is True and vs[0].end == t0
    assert vs[0].behavior == "pass_through" and vs[0].basis == "dwell_only"
    d = discovery.descriptors(vs, pipeline.DEFAULT_PARAMS)
    assert d["speed_median"] is None and d["velocity_angle_mean_deg"] is None
    assert d["entry_side"] is None and d["box_area_median"] is None


def test_v5_local_minute_survives_dst_change():
    before, after = date(2031, 3, 4), date(2031, 3, 11)  # DST begins 2031-03-09
    minutes = [LOCAL(epoch(d, 7, 10))[2] for d in (before, after)]
    assert all(abs(m - 430) <= 5 for m in minutes)
    # Control: a fixed UTC offset would move the wall clock by an hour across the change.
    fixed = timezone(timedelta(hours=-5))
    naive = [datetime.fromtimestamp(epoch(d, 7, 10), tz=fixed) for d in (before, after)]
    assert abs((naive[1].hour * 60 + naive[1].minute) - 430) == 60
    # The production source (process local time via TZ) gives the same DST-correct answer.
    old = os.environ.get("TZ")
    try:
        os.environ["TZ"] = synthetic.TZ_NAME
        time.tzset()
        assert [process_local_time(epoch(d, 7, 10))[2] for d in (before, after)] == [430, 430]
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_v6_parity_uses_fixed_epoch_monday_across_year_boundary():
    assert EPOCH_MONDAY.weekday() == 0
    mondays = [date(2031, 12, 15) + timedelta(weeks=i) for i in range(6)]  # into 2032
    weeks = [week_index(d) for d in mondays]
    assert weeks == list(range(weeks[0], weeks[0] + 6))
    pars = [parity(d) for d in mondays]
    assert all(a != b for a, b in zip(pars, pars[1:]))
    assert len({week_index(mondays[2] + timedelta(days=k)) for k in range(7)}) == 1
    # Control: ISO week numbers break alternation at a 53-week year (2032 -> 2033).
    iso = [(date(2032, 12, 27) + timedelta(weeks=i)).isocalendar().week % 2 for i in range(2)]
    assert iso[0] == iso[1]
    ours = [parity(date(2032, 12, 27) + timedelta(weeks=i)) for i in range(2)]
    assert ours[0] != ours[1]


def test_v7_camera_without_events_on_a_date_is_not_covered():
    first, last = date(2031, 3, 2), date(2031, 3, 5)
    tr = [track("a1", epoch(date(2031, 3, 2), 12, 0), None, camera="cam_door", label="person"),
          track("a2", epoch(date(2031, 3, 4), 12, 0), None, camera="cam_door", label="cat")]
    cov = pipeline.coverage_by_site(tr, LOCAL, first, last)
    assert cov == {"cam_door": {date(2031, 3, 2), date(2031, 3, 4)}}  # any label covers
    assert date(2031, 3, 3) not in cov["cam_door"]
    assert "cam_street" not in cov
