"""Shared helpers for the recurring-pattern tests (synthetic data only)."""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from optimizer.patterns import pipeline, synthetic, visits
from optimizer.patterns.localtime import week_index, zoneinfo_local_time
from optimizer.patterns.settings import DEFAULT_BEHAVIORS, DEFAULT_PARAMS, PatternParams

TZ = ZoneInfo(synthetic.TZ_NAME)
LOCAL = zoneinfo_local_time(synthetic.TZ_NAME)
POSITIVE_SEED = 2031
BUSY_NULL_SEEDS = tuple(range(1001, 1051))    # 50 fixed busy-street seeds
QUIET_NULL_SEEDS = tuple(range(2001, 2011))   # 10 fixed quiet-street seeds
# Multi-seed positive control (WYZE-020-R3): the unchanged positive scenario,
# seeds 2031-2050. 2031 is the original single S-3 seed, included on purpose.
S3M_SEEDS = tuple(range(2031, 2051))
# A surfaced pattern derives from a planted one when it is in that plant's
# stratum, covers only the plant's weekdays, and sits within this many
# minutes of the planted center, whatever cadence it was given.
PLANTED_MATCH_TOL_MIN = 30
PLANTED_STRATA = {
    "P1": (("cam_street", "vehicle", "brief_stop"), frozenset({1})),
    "P2": (("cam_street", "vehicle", "brief_stop"), frozenset({3})),
    "P3": (("cam_door", "person", "brief_stop"), frozenset({0, 1, 2, 3, 4, 5})),
    "P4": (("cam_back", "vehicle", "long_stay"), frozenset({0, 1, 2, 3, 4})),
}
STREET = ("cam_street", "vehicle", "brief_stop")


def epoch(day: date, hh: int, mm: int, ss: float = 0.0) -> float:
    return datetime.combine(day, dtime(hh, mm), tzinfo=TZ).timestamp() + ss


def track(tid: str, start: float, end, *, camera="cam_street", label="car", box=None,
          path=None, speed=None, angle=None, labels=()) -> visits.Track:
    return visits.Track(
        id=tid, camera=camera, label=label, start=start, end=end,
        box=tuple(box) if box else None,
        path=visits.summarize_path(path, end) if path else None,
        speed=speed, velocity_angle=angle, explicit_labels=tuple(labels),
    )


def tracks_of(payloads, params: PatternParams = DEFAULT_PARAMS):
    return [t for t in (visits.track_from_payload(p, params) for p in payloads) if t]


def run_payloads(payloads, params: PatternParams = DEFAULT_PARAMS):
    return pipeline.compute(tracks_of(payloads, params), synthetic.span_now(), LOCAL, params)


@lru_cache(maxsize=None)
def positive_payloads():
    return tuple(synthetic.generate(POSITIVE_SEED))


@lru_cache(maxsize=None)
def positive_run():
    return run_payloads(positive_payloads())


def find(patterns, *, site, group, behavior, cadence=None, weekdays=None):
    out = [p for p in patterns
           if p["site"] == site and p["label_group"] == group and p["behavior"] == behavior
           and (cadence is None or p["cadence"] == cadence)
           and (weekdays is None or p["weekdays"] == list(weekdays))]
    return out


def visible(patterns):
    return [p for p in patterns if p["behavior"] in DEFAULT_BEHAVIORS]


def planted_origin(pattern):
    """The planted pattern (P1..P4) a surfaced pattern derives from, or None."""
    for name, (stratum, weekdays) in PLANTED_STRATA.items():
        if ((pattern["site"], pattern["label_group"], pattern["behavior"]) == stratum
                and set(pattern["weekdays"]) <= weekdays
                and abs(pattern["window"]["median_min"] - synthetic.PLANTED_CENTERS[name])
                <= PLANTED_MATCH_TOL_MIN):
            return name
    return None


@lru_cache(maxsize=None)
def s3m_runs():
    return tuple((seed, run_payloads(synthetic.generate(seed)).patterns) for seed in S3M_SEEDS)


def street_patterns(seed, plants, origins=None):
    """Default-visible patterns for a generate_street scenario."""
    return visible(run_payloads(synthetic.generate_street(seed, plants, origins=origins)).patterns)


def near(patterns, weekday, center_min, stratum=STREET, tol=PLANTED_MATCH_TOL_MIN):
    """Patterns of `stratum` that include `weekday` within `tol` minutes of a center."""
    return [p for p in patterns
            if (p["site"], p["label_group"], p["behavior"]) == stratum
            and weekday in p["weekdays"] and abs(p["window"]["median_min"] - center_min) <= tol]


def pooled_other_weekday_rate(payloads, stratum, weekday, lo, hi):
    """WYZE-020-R1's pooled weekday null for [lo, hi), recomputed from visits
    independently of discovery (comparison only; the pipeline no longer uses
    it): (hit cells + 0.5) / (covered cells + 1) over the six other weekdays."""
    now = synthetic.span_now()
    tracks = tracks_of(payloads)
    first, last = pipeline.analysis_dates(now, LOCAL)
    coverage = pipeline.coverage_by_site(tracks, LOCAL, first, last)
    hits: dict[int, set] = {}
    for v in visits.build_visits(tracks):
        d, wd, minute = LOCAL(v.onset)
        if ((v.site, v.label_group, v.behavior) == stratum and first <= d <= last
                and d in coverage.get(v.site, ()) and lo <= minute < hi):
            hits.setdefault(wd, set()).add(week_index(d))
    cells = hit_cells = 0
    day = first
    while day <= last:
        if day.weekday() != weekday and day in coverage.get(stratum[0], ()):
            cells += 1
            hit_cells += week_index(day) in hits.get(day.weekday(), set())
        day += timedelta(days=1)
    return (hit_cells + 0.5) / (cells + 1)
