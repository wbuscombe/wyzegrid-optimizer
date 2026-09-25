"""
Recurring time-locked window discovery (contract sections E, F, G).

For every stratum (site, label group, behavior) and weekday, a grid of 30-min
windows (15-min step) is tested against the window's OWN local base rate: the
same stratum's onsets in the 120 minutes before and after it, across covered
weeks. k (covered weeks with an onset in the window) is compared with an exact
binomial tail, and Benjamini-Hochberg runs across the ENTIRE family. Only then
are the surfacing thresholds applied; the family is never pre-filtered.

A lift floor of 3 guards step edges: at a pure step in activity the surround
averages one quiet and one busy side, so lift cannot exceed about 2 there.

The local null is used ONLY for discovery and surfacing. Cadence (G-PRIME-R)
measures background with b_ToD, the median over the other weekdays of each
weekday's hit rate in the same clock-time interval. The same clock time on
other days removes the activity-edge bias of a surround that straddles an
onset, and the median ignores up to two weekdays that carry their own
same-time pattern. A biweekly candidate's weekday specificity is measured on
its active parity, because a biweekly pattern halves its all-weeks hit rate
by construction.
"""
from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from fractions import Fraction
from statistics import median
from typing import Iterable, Optional

from . import settings as S
from .localtime import week_index, week_start
from .settings import BEHAVIORS, DEFAULT_BEHAVIORS, DEFAULT_PARAMS, PatternParams
from .stats import (bh_qvalues, binom_sf, circular_mean_deg, fisher_one_sided, median_of,
                    nearest_rank)
from .visits import Visit

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DAY_MINUTES = 1440
SIDE_ORDER = ("left", "right", "top", "bottom", "interior")
# The G-PRIME-R record every pattern carries (per weekday for a weekday_set).
CADENCE_TEST_FIELDS = ("n_A", "n_B", "h_A", "h_B", "high_parity", "p_parity", "p_bgL", "b_tod",
                       "b_tod_rates", "weekday_specificity", "periodicity")

Stratum = tuple[str, str, str]  # (site, label_group, behavior)


@dataclass
class Discovery:
    patterns: list[dict]
    suppressed: Counter = field(default_factory=Counter)
    family_size: int = 0
    significant_windows: int = 0
    candidate_intervals: int = 0


def hhmm(minute: float) -> str:
    m = int(round(minute)) % DAY_MINUTES
    return f"{m // 60:02d}:{m % 60:02d}"


def _round_to(value: float, step: int) -> int:
    return int(math.floor(value / step + 0.5)) * step


def pattern_key(stratum: Stratum, cadence: str, weekdays: Iterable[int],
                parity: Optional[int], median_min: float, step: int) -> str:
    payload = json.dumps(
        ["v1", list(stratum), cadence, sorted(weekdays), parity, _round_to(median_min, step)],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class _Index:
    """Onset minutes per (stratum, weekday, week) and covered weeks per (site, weekday)."""

    def __init__(self, visits: list[Visit], coverage: dict[str, set], dates: list[date],
                 params: PatternParams):
        self.params = params
        self.weeks = sorted({week_index(d, params.epoch_monday) for d in dates})
        self.covered: dict[tuple[str, int], list[int]] = {}
        for site in sorted(coverage):
            for d in dates:
                if d in coverage[site]:
                    key = (site, d.weekday())
                    self.covered.setdefault(key, []).append(week_index(d, params.epoch_monday))
        self.minutes: dict[tuple[Stratum, int], dict[int, list[int]]] = {}
        self.members: dict[tuple[Stratum, int], list[Visit]] = {}
        first, last = dates[0], dates[-1]
        for v in visits:
            if v.local_date is None or not (first <= v.local_date <= last):
                continue
            if v.local_date not in coverage.get(v.site, ()):
                continue
            key = ((v.site, v.label_group, v.behavior), v.weekday)
            wk = week_index(v.local_date, params.epoch_monday)
            self.minutes.setdefault(key, {}).setdefault(wk, []).append(v.minute)
            self.members.setdefault(key, []).append(v)
        for by_week in self.minutes.values():
            for mins in by_week.values():
                mins.sort()

    def covered_weeks(self, site: str, weekday: int) -> list[int]:
        return self.covered.get((site, weekday), [])

    def weeks_with_onset(self, stratum: Stratum, weekday: int, lo: int, hi: int) -> set[int]:
        out = set()
        for wk, mins in self.minutes.get((stratum, weekday), {}).items():
            i = bisect_left(mins, lo)
            if i < len(mins) and mins[i] < hi:
                out.add(wk)
        return out


def _prefix_counts(by_week: dict[int, list[int]]) -> list[int]:
    counts = [0] * (DAY_MINUTES + 1)
    for mins in by_week.values():
        for m in mins:
            counts[m + 1] += 1
    for i in range(1, DAY_MINUTES + 1):
        counts[i] += counts[i - 1]
    return counts


def _local_null(prefix: list[int], lo: int, hi: int, n: int,
                params: PatternParams) -> tuple[float, float]:
    """(lambda, p0) for [lo, hi) from the clipped surround on both sides."""
    width = hi - lo
    s_lo = max(0, lo - params.surround_min)
    s_hi = min(DAY_MINUTES, hi + params.surround_min)
    minutes = (lo - s_lo) + (s_hi - hi)
    onsets = (prefix[lo] - prefix[s_lo]) + (prefix[s_hi] - prefix[hi])
    lam = (onsets + 0.5) / (max(1, minutes) * n)
    p0 = 1.0 - math.exp(-lam * width)
    p0 = min(1.0 - params.p0_floor, max(params.p0_floor, p0))
    return lam, p0


def _tier(q: float, support: int, hit_rate: float, spread: float) -> Optional[str]:
    if (q <= S.STRONG_MAX_Q and support >= S.STRONG_MIN_SUPPORT
            and hit_rate >= S.STRONG_MIN_HIT_RATE and spread <= S.STRONG_MAX_SPREAD_MIN):
        return "strong"
    if (q <= S.MODERATE_MAX_Q and support >= S.MODERATE_MIN_SUPPORT
            and hit_rate >= S.MODERATE_MIN_HIT_RATE and spread <= S.MODERATE_MAX_SPREAD_MIN):
        return "moderate"
    return None


# ---- G-PRIME-R cadence ------------------------------------------------------

def other_weekday_counts(idx: _Index, stratum: Stratum, wd: int, lo: int,
                         hi: int, params: PatternParams) -> dict[int, tuple[int, int]]:
    """(hit weeks, covered weeks) in [lo, hi) for every OTHER weekday of the
    stratum's site with at least MIN_COVERED_WEEKS covered weeks. [lo, hi) is
    half-open, the same membership rule the interval's own members use."""
    out: dict[int, tuple[int, int]] = {}
    for other in range(7):
        if other == wd:
            continue
        weeks = idx.covered_weeks(stratum[0], other)
        if len(weeks) < params.min_covered_weeks:
            continue
        with_onset = idx.weeks_with_onset(stratum, other, lo, hi)
        out[other] = (sum(1 for w in weeks if w in with_onset), len(weeks))
    return out


def weekday_rate(hits: int, covered: int) -> float:
    """r(d') = (hit weeks + 0.5) / (covered weeks + 1)."""
    return (hits + 0.5) / (covered + 1)


def time_of_day_background(rates: Iterable[float],
                           params: PatternParams = DEFAULT_PARAMS) -> Optional[float]:
    """b_ToD: the median of the qualifying r(d') (the mean of the two middle
    values when their count is even), clipped. None means "unknown": fewer
    than BTOD_MIN_WEEKDAYS other weekdays qualify."""
    values = list(rates)
    if len(values) < params.btod_min_weekdays:
        return None
    return min(1.0 - params.p0_floor, max(params.p0_floor, median_of(values)))


def cadence_test(weeks: list[int], hit_weeks: list[int], counts: dict[int, tuple[int, int]],
                 params: PatternParams = DEFAULT_PARAMS) -> dict:
    """G-PRIME-R for one surfaced single-weekday interval (contract section G).

    `weeks` are the weekday's covered weeks, `hit_weeks` those with a member,
    and `counts` the other weekdays' (hit weeks, covered weeks). Returns the
    recorded fields plus `cadence` (weekly, biweekly, recurring, or None when
    the candidate may still join a weekday_set) and `active_hit_rate`.
    """
    n, k = len(weeks), len(hit_weeks)
    covered_by = Counter(w % 2 for w in weeks)
    hits_by = Counter(w % 2 for w in hit_weeks)
    n_by, h_by = (covered_by[0], covered_by[1]), (hits_by[0], hits_by[1])
    rates = {d: weekday_rate(h, c) for d, (h, c) in sorted(counts.items())}
    b_tod = time_of_day_background(rates.values(), params)

    # H = the parity with the higher h/n, compared exactly; on a tie H is
    # recorded as parity A and periodicity is weekly.
    share = [Fraction(h_by[p], n_by[p]) if n_by[p] else Fraction(0) for p in (0, 1)]
    tie = share[0] == share[1]
    high = 0 if share[0] >= share[1] else 1
    low = 1 - high
    n_h, h_h, n_l, h_l = n_by[high], h_by[high], n_by[low], h_by[low]
    p_parity = fisher_one_sided(h_h, n_h, h_l, n_l)
    p_bgl = binom_sf(h_l, n_l, b_tod) if b_tod is not None else None

    # Weekly asserts recurrence on BOTH parities, so it needs evidence on both
    # (WYZE-020-R4 W1). No parity contrast (a tie, or p_parity above
    # PARITY_ALPHA) is only absence of evidence that the parities differ, and
    # low power makes that easy to meet. Each parity must also reach
    # MIN_HIT_RATE on its own, over at least MIN_LOW_PARITY_WEEKS covered weeks;
    # without that, an interval with no parity contrast is indeterminate.
    both_parities = all(n_by[p] >= params.min_low_parity_weeks
                        and h_by[p] / n_by[p] >= params.min_hit_rate for p in (0, 1))
    if tie or p_parity > params.parity_alpha:
        periodicity = "weekly" if both_parities else "indeterminate"
    elif not (h_h >= params.min_parity_support and n_h and h_h / n_h >= params.min_hit_rate
              and n_l >= params.min_low_parity_weeks):
        periodicity = "indeterminate"
    elif p_bgl is None:
        periodicity = "unknown"
    elif p_bgl > params.background_consistency_alpha:
        periodicity = "biweekly"
    else:
        periodicity = "indeterminate"

    # Weekday specificity: a biweekly candidate is measured on its active parity.
    form, hits, trials = ("biweekly", h_h, n_h) if periodicity == "biweekly" else ("weekly", k, n)
    if b_tod is None:
        spec = {"form": form, "result": "unknown", "lift_w": None, "p_w": None}
    else:
        lift_w = (hits / trials) / b_tod
        p_w = binom_sf(hits, trials, b_tod)
        passed = lift_w >= params.min_lift_w and p_w <= params.weekday_alpha
        spec = {"form": form, "result": "pass" if passed else "fail",
                "lift_w": lift_w, "p_w": p_w}

    if b_tod is None:
        cadence = "recurring"   # unknown background: never weekly, biweekly, or grouped
    elif periodicity == "biweekly" and spec["result"] == "pass":
        cadence = "biweekly"
    elif periodicity == "weekly" and spec["result"] == "pass":
        cadence = "weekly"
    else:
        cadence = None
    return {
        "n_A": n_by[0], "n_B": n_by[1], "h_A": h_by[0], "h_B": h_by[1],
        "high_parity": None if tie else high,
        "p_parity": p_parity, "p_bgL": p_bgl, "b_tod": b_tod,
        "b_tod_rates": {WEEKDAY_NAMES[d]: round(r, 6) for d, r in rates.items()},
        "weekday_specificity": spec, "periodicity": periodicity,
        "cadence": cadence,
        "active_hit_rate": h_h / n_h if cadence == "biweekly" else (k / n if n else 0.0),
    }


def _edge_side(point, margin: float) -> str:
    x, y = point
    dists = {"left": x, "right": 1.0 - x, "top": y, "bottom": 1.0 - y}
    side = min(SIDE_ORDER[:4], key=lambda k: (dists[k], SIDE_ORDER.index(k)))
    return side if dists[side] <= margin else "interior"


def _mode(values: list[str]) -> Optional[str]:
    if not values:
        return None
    counts = Counter(values)
    return max(counts, key=lambda k: (counts[k], -SIDE_ORDER.index(k)))


def descriptors(members: list[Visit], params: PatternParams) -> dict:
    """Descriptive only, never identity."""
    areas, speeds, angles, entries, exits = [], [], [], [], []
    for v in members:
        path_tracks = [t for t in v.tracks if t.path is not None]
        if path_tracks:
            entries.append(_edge_side(path_tracks[0].path.first, params.edge_margin))
            exits.append(_edge_side(path_tracks[-1].path.last, params.edge_margin))
        for t in v.tracks:
            if t.box is not None:
                areas.append(t.box[2] * t.box[3])
            # Frigate reports speed/angle only inside a speed zone; 0 means "not estimated".
            if t.speed is not None and t.speed > 0:
                speeds.append(t.speed)
                if t.velocity_angle is not None:
                    angles.append(t.velocity_angle)
    return {
        "dwell_median_s": round(median(v.dwell for v in members), 1) if members else None,
        "box_area_median": round(median(areas), 4) if areas else None,
        "entry_side": _mode(entries),
        "exit_side": _mode(exits),
        "speed_median": round(median(speeds), 2) if speeds else None,
        "velocity_angle_mean_deg": circular_mean_deg(angles),
    }


def discover(visits: list[Visit], coverage: dict[str, set], first_date: date, last_date: date,
             params: PatternParams = DEFAULT_PARAMS) -> Discovery:
    dates = [first_date + timedelta(days=i) for i in range((last_date - first_date).days + 1)]
    idx = _Index(visits, coverage, dates, params)
    width, step = params.grid_width_min, params.grid_step_min
    grid = list(range(0, DAY_MINUTES - width + 1, step))
    sites = sorted(coverage)
    groups = list(params.label_groups)

    # ---- F. the discovery family -------------------------------------------
    family: list[tuple[Stratum, int, int, int, int, float, float]] = []
    prefixes: dict[tuple[Stratum, int], list[int]] = {}
    for site in sites:
        for group in groups:
            for behavior in BEHAVIORS:
                stratum = (site, group, behavior)
                for wd in range(7):
                    n = len(idx.covered_weeks(site, wd))
                    if n < params.min_covered_weeks:
                        continue
                    by_week = idx.minutes.get((stratum, wd), {})
                    prefix = _prefix_counts(by_week)
                    prefixes[(stratum, wd)] = prefix
                    hit_weeks: list[set[int]] = [set() for _ in grid]
                    for wk, mins in by_week.items():
                        for m in mins:
                            lo_i = max(0, -(-(m - width + 1) // step))
                            hi_i = min(len(grid) - 1, m // step)
                            for gi in range(lo_i, hi_i + 1):
                                hit_weeks[gi].add(wk)
                    for gi, s in enumerate(grid):
                        k = len(hit_weeks[gi])
                        _, p0 = _local_null(prefix, s, s + width, n, params)
                        family.append((stratum, wd, s, k, n, p0, binom_sf(k, n, p0)))

    qvals = bh_qvalues([w[6] for w in family])
    result = Discovery(patterns=[], family_size=len(family))

    significant: dict[tuple[Stratum, int], list[tuple[int, float]]] = {}
    for (stratum, wd, s, *_), q in zip(family, qvals):
        if q <= params.fdr_q:
            significant.setdefault((stratum, wd), []).append((s, q))
    result.significant_windows = sum(len(v) for v in significant.values())

    # ---- G. intervals, surfacing, weekday null, cadence --------------------
    per_weekday: dict[Stratum, list[dict]] = {}
    for (stratum, wd), wins in sorted(significant.items()):
        wins.sort()
        intervals: list[list] = []  # [lo, hi, min_q]
        for s, q in wins:
            if intervals and s < intervals[-1][1]:
                intervals[-1][1] = max(intervals[-1][1], s + width)
                intervals[-1][2] = min(intervals[-1][2], q)
            else:
                intervals.append([s, s + width, q])
        for lo, hi, q_min in intervals:
            result.candidate_intervals += 1
            cand = _evaluate_interval(idx, prefixes[(stratum, wd)], stratum, wd, lo, hi,
                                      q_min, params)
            if cand.get("suppressed"):
                result.suppressed[cand["suppressed"]] += 1
                continue
            per_weekday.setdefault(stratum, []).append(cand)

    surfaced: list[dict] = []
    for stratum in sorted(per_weekday):
        cands = per_weekday[stratum]
        fixed = [c for c in cands if c["cadence"] in ("weekly", "biweekly")]
        pinned = [c for c in cands if c["cadence"] == "recurring"]   # b_ToD unknown
        loose = [c for c in cands if c["cadence"] is None]
        sets, singles = _weekday_sets(loose, params)
        for c in fixed + pinned + singles:
            surfaced.append(_single_pattern(idx, stratum, c, params))
        for grp in sets:
            surfaced.append(_group_pattern(idx, stratum, grp, params))

    seen: dict[str, dict] = {}
    for p in sorted(surfaced, key=lambda p: (p["q"], p["pattern_key"])):
        if p["pattern_key"] in seen:
            result.suppressed["duplicate pattern_key"] += 1
            continue
        seen[p["pattern_key"]] = p
    result.patterns = sorted(seen.values(), key=lambda p: (
        p["site"], p["label_group"], p["behavior"], p["weekdays"], p["window"]["median_min"]))
    return result


def _evaluate_interval(idx: _Index, prefix: list[int], stratum: Stratum, wd: int, lo: int,
                       hi: int, q_min: float, params: PatternParams) -> dict:
    site = stratum[0]
    weeks = idx.covered_weeks(site, wd)
    n = len(weeks)
    members = [v for v in idx.members.get((stratum, wd), []) if lo <= v.minute < hi]
    hit_weeks = sorted({week_index(v.local_date, params.epoch_monday) for v in members})
    k = len(hit_weeks)
    if not members:
        return {"suppressed": "support"}
    mins = sorted(v.minute for v in members)
    p10, p90 = nearest_rank(mins, 10), nearest_rank(mins, 90)
    med = float(median(mins))
    spread = p90 - p10
    _, p0 = _local_null(prefix, lo, hi, n, params)
    hit_rate = k / n
    lift_t = hit_rate / p0
    if k < params.min_support_weeks:
        return {"suppressed": "support"}
    if hit_rate < params.min_hit_rate:
        return {"suppressed": "hit_rate"}
    if lift_t < params.min_lift_t:
        return {"suppressed": "lift"}
    if spread > params.max_window_spread_min:
        return {"suppressed": "spread"}

    # Cadence (G-PRIME-R): b_ToD from the same clock time on the other weekdays.
    test = cadence_test(weeks, hit_weeks,
                        other_weekday_counts(idx, stratum, wd, lo, hi, params), params)
    cadence = test.pop("cadence")
    reported_rate = test.pop("active_hit_rate")
    parity = test["high_parity"] if cadence == "biweekly" else None
    tier = _tier(q_min, k, reported_rate, spread)
    if tier is None:
        return {"suppressed": "below moderate"}
    spec = test["weekday_specificity"]
    return {
        "weekday": wd, "lo": lo, "hi": hi, "members": members, "hit_weeks": hit_weeks,
        "k": k, "n": n, "p10": p10, "p90": p90, "median": med, "spread": spread,
        "p0_t": p0, "hit_rate": reported_rate, "overall_hit_rate": hit_rate,
        "lift_t": lift_t, "p_w": spec["p_w"], "lift_w": spec["lift_w"], "q": q_min,
        "cadence": cadence, "parity": parity, "confidence": tier, "test": test,
    }


def _weekday_sets(cands: list[dict], params: PatternParams) -> tuple[list[list[dict]], list[dict]]:
    """Group per-weekday candidates whose medians sit within the tolerance on at
    least WEEKDAY_SET_MIN_DAYS distinct weekdays; the rest stay 'recurring'."""
    remaining = sorted(cands, key=lambda c: (c["median"], c["weekday"]))
    groups: list[list[dict]] = []
    while True:
        best: Optional[dict[int, dict]] = None
        for i, anchor in enumerate(remaining):
            chosen: dict[int, dict] = {}
            for c in remaining[i:]:
                if c["median"] - anchor["median"] > params.weekday_set_median_tol_min:
                    break
                chosen.setdefault(c["weekday"], c)
            if len(chosen) >= params.weekday_set_min_days and (
                    best is None or len(chosen) > len(best)):
                best = chosen
        if best is None:
            break
        picked = sorted(best.values(), key=lambda c: c["weekday"])
        groups.append(picked)
        remaining = [c for c in remaining if all(c is not p for p in picked)]
    for c in remaining:
        c["cadence"] = "recurring"
    return groups, remaining


def _grid(idx: _Index, stratum: Stratum, weekdays: list[int],
          hits: dict[int, set[int]], params: PatternParams) -> list[dict]:
    rows = []
    for wk in idx.weeks:
        for wd in weekdays:
            covered = wk in set(idx.covered_weeks(stratum[0], wd))
            rows.append({"week_start": week_start(wk, params.epoch_monday).isoformat(),
                         "weekday": wd, "covered": covered,
                         "hit": covered and wk in hits.get(wd, set())})
    return rows


def _recent(idx: _Index, site: str, wd: int, hit_weeks: list[int],
            params: PatternParams) -> bool:
    last = idx.covered_weeks(site, wd)[-params.recent_weeks:]
    return any(w in set(hit_weeks) for w in last)


def _window_dict(lo: int, hi: int, p10: float, med: float, p90: float) -> dict:
    return {"start_min": lo, "end_min": hi, "p10_min": p10, "median_min": med,
            "p90_min": p90, "p10": hhmm(p10), "median": hhmm(med), "p90": hhmm(p90)}


def _round(value: Optional[float], digits: int) -> Optional[float]:
    return None if value is None else round(value, digits)


def _cadence_record(c: dict) -> dict:
    """The G-PRIME-R fields for one weekday, in CADENCE_TEST_FIELDS order."""
    test = dict(c["test"])
    spec = dict(test["weekday_specificity"])
    spec["lift_w"] = _round(spec["lift_w"], 3)
    test["weekday_specificity"] = spec
    return {name: test[name] for name in CADENCE_TEST_FIELDS}


def _weekday_stats(c: dict) -> dict:
    out = {
        "weekday": c["weekday"], "weekday_name": WEEKDAY_NAMES[c["weekday"]],
        "window": _window_dict(c["lo"], c["hi"], c["p10"], c["median"], c["p90"]),
        "spread_min": c["spread"], "support": {"k": c["k"], "n": c["n"]},
        "hit_rate": round(c["hit_rate"], 4), "lift_t": round(c["lift_t"], 3),
        "p0_t": c["p0_t"], "lift_w": _round(c["lift_w"], 3), "p_w": c["p_w"], "q": c["q"],
        "confidence": c["confidence"],
    }
    out.update(_cadence_record(c))
    return out


def _base(idx: _Index, stratum: Stratum, members: list[Visit], params: PatternParams) -> dict:
    basis = Counter(v.basis for v in members)
    dates = sorted(v.local_date for v in members)
    return {
        "site": stratum[0], "label_group": stratum[1], "behavior": stratum[2],
        "default_visible": stratum[2] in DEFAULT_BEHAVIORS,
        "descriptors": descriptors(members, params),
        "basis_mix": {b: basis.get(b, 0) for b in ("path", "box", "dwell_only")},
        "first_seen": dates[0].isoformat(), "last_seen": dates[-1].isoformat(),
        "_members": members,
    }


def _single_pattern(idx: _Index, stratum: Stratum, c: dict, params: PatternParams) -> dict:
    out = _base(idx, stratum, c["members"], params)
    out.update({
        "pattern_key": pattern_key(stratum, c["cadence"], [c["weekday"]], c["parity"],
                                   c["median"], params.key_round_min),
        "cadence": c["cadence"], "weekdays": [c["weekday"]],
        "weekday_names": [WEEKDAY_NAMES[c["weekday"]]], "parity": c["parity"],
        "window": _window_dict(c["lo"], c["hi"], c["p10"], c["median"], c["p90"]),
        "spread_min": c["spread"], "support": {"k": c["k"], "n": c["n"]},
        "hit_rate": round(c["hit_rate"], 4), "lift_t": round(c["lift_t"], 3),
        "lift_w": _round(c["lift_w"], 3), "p_w": c["p_w"], "q": c["q"],
        "confidence": c["confidence"],
        "recent": _recent(idx, stratum[0], c["weekday"], c["hit_weeks"], params),
        "per_weekday": [_weekday_stats(c)],
        "hit_grid": _grid(idx, stratum, [c["weekday"]], {c["weekday"]: set(c["hit_weeks"])},
                          params),
        "cadence_test_scope": "pattern",
    })
    out.update(_cadence_record(c))
    return out


def _group_pattern(idx: _Index, stratum: Stratum, grp: list[dict], params: PatternParams) -> dict:
    members = [v for c in grp for v in c["members"]]
    mins = sorted(v.minute for v in members)
    p10, p90 = nearest_rank(mins, 10), nearest_rank(mins, 90)
    med = float(median(c["median"] for c in grp))
    weekdays = [c["weekday"] for c in grp]
    k_sum, n_sum = sum(c["k"] for c in grp), sum(c["n"] for c in grp)
    tiers = [c["confidence"] for c in grp]
    out = _base(idx, stratum, members, params)
    out.update({
        "pattern_key": pattern_key(stratum, "weekday_set", weekdays, None, med,
                                   params.key_round_min),
        "cadence": "weekday_set", "weekdays": weekdays,
        "weekday_names": [WEEKDAY_NAMES[w] for w in weekdays], "parity": None,
        "window": _window_dict(min(c["lo"] for c in grp), max(c["hi"] for c in grp),
                               p10, med, p90),
        "spread_min": p90 - p10, "support": {"k": k_sum, "n": n_sum},
        "hit_rate": round(k_sum / n_sum, 4),
        "lift_t": round(min(c["lift_t"] for c in grp), 3),
        # Grouped members always have a known b_ToD (unknown ones stay recurring).
        "lift_w": round(min(c["lift_w"] for c in grp), 3),
        "p_w": max(c["p_w"] for c in grp), "q": max(c["q"] for c in grp),
        "confidence": "moderate" if "moderate" in tiers else "strong",
        "recent": any(_recent(idx, stratum[0], c["weekday"], c["hit_weeks"], params)
                      for c in grp),
        "per_weekday": [_weekday_stats(c) for c in grp],
        "hit_grid": _grid(idx, stratum, weekdays,
                          {c["weekday"]: set(c["hit_weeks"]) for c in grp}, params),
        # G-PRIME-R is a per-weekday test: each per_weekday entry carries it.
        "cadence_test_scope": "per_weekday",
    })
    out.update({name: None for name in CADENCE_TEST_FIELDS})
    return out
