"""
The in-memory pattern pipeline: tracks -> coverage -> visits -> behavior ->
discovery -> identity -> JSON-ready patterns plus an aggregate visit summary.

Visits live only in memory for one run; only aggregates and patterns persist.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Iterable

from . import identity
from .discovery import discover
from .localtime import LocalTimeFn
from .settings import BEHAVIORS, DEFAULT_PARAMS, UNIDENTIFIED_TEXT, PatternParams
from .visits import Track, build_visits, label_group_of

# Events are loaded with a margin before the first analysis date so visits and
# coverage near the window edge are built from whole days in any time zone.
LOAD_MARGIN_DAYS = 2


@dataclass
class PatternRun:
    patterns: list[dict]
    summary: dict
    first_date: date
    last_date: date


def analysis_dates(now: float, local_time: LocalTimeFn,
                   params: PatternParams = DEFAULT_PARAMS) -> tuple[date, date]:
    """The ANALYSIS_WINDOW_DAYS complete local days before the local date of `now`."""
    today = local_time(now)[0]
    return today - timedelta(days=params.analysis_window_days), today - timedelta(days=1)


def load_start_epoch(now: float, params: PatternParams = DEFAULT_PARAMS) -> float:
    return now - (params.analysis_window_days + LOAD_MARGIN_DAYS) * 86400.0


def coverage_by_site(tracks: Iterable[Track], local_time: LocalTimeFn, first: date, last: date,
                     params: PatternParams = DEFAULT_PARAMS) -> dict[str, set]:
    """Local dates in [first, last] on which each site has one or more events
    of ANY label. A site group is covered when any member camera is."""
    coverage: dict[str, set] = {}
    for t in tracks:
        day = local_time(t.start)[0]
        if first <= day <= last:
            coverage.setdefault(params.site_groups.get(t.camera, t.camera), set()).add(day)
    return coverage


def compute(tracks: Iterable[Track], now: float, local_time: LocalTimeFn,
            params: PatternParams = DEFAULT_PARAMS) -> PatternRun:
    first, last = analysis_dates(now, local_time, params)
    tracks = [t for t in tracks if t.start <= now]

    coverage = coverage_by_site(tracks, local_time, first, last, params)
    labels_seen = Counter(
        "grouped" if label_group_of(t.label, params) else "ignored"
        for t in tracks if first <= local_time(t.start)[0] <= last
    )

    visits = build_visits(tracks, params)
    in_window = []
    for v in visits:
        v.local_date, v.weekday, v.minute = local_time(v.onset)
        if first <= v.local_date <= last and v.local_date in coverage.get(v.site, ()):
            in_window.append(v)

    found = discover(in_window, coverage, first, last, params)
    patterns = []
    for p in found.patterns:
        members = p.pop("_members")
        ident, reason = identity.assign(members, params)
        p["identity"] = ident
        p["identity_reason"] = reason
        p["label"] = ident if ident else UNIDENTIFIED_TEXT
        patterns.append(p)

    summary = {
        "analysis_window": {"first_date": first.isoformat(), "last_date": last.isoformat(),
                            "days": params.analysis_window_days},
        "events_in_window": labels_seen["grouped"] + labels_seen["ignored"],
        "events_in_label_groups": labels_seen["grouped"],
        "events_other_labels": labels_seen["ignored"],
        "covered_days": {site: len(days) for site, days in sorted(coverage.items())},
        "visits": _visit_summary(in_window),
        "family_size": found.family_size,
        "significant_windows": found.significant_windows,
        "candidate_intervals": found.candidate_intervals,
        "surfaced": dict(Counter(p["behavior"] for p in patterns)),
        "suppressed": dict(found.suppressed),
        "params": params.as_record(),
    }
    return PatternRun(patterns=patterns, summary=summary, first_date=first, last_date=last)


def _visit_summary(visits) -> list[dict]:
    groups: dict[tuple[str, str, str], list] = {}
    for v in visits:
        groups.setdefault((v.site, v.label_group, v.behavior), []).append(v)
    rows = []
    for (site, group, behavior), members in sorted(groups.items(),
                                                   key=lambda kv: (kv[0][0], kv[0][1],
                                                                   BEHAVIORS.index(kv[0][2]))):
        basis = Counter(v.basis for v in members)
        rows.append({
            "site": site, "label_group": group, "behavior": behavior,
            "visits": len(members),
            "tracks": sum(len(v.tracks) for v in members),
            "incomplete": sum(1 for v in members if v.incomplete),
            "dwell_median_s": round(median(v.dwell for v in members), 1),
            "basis_mix": {b: basis.get(b, 0) for b in ("path", "box", "dwell_only")},
        })
    return rows
