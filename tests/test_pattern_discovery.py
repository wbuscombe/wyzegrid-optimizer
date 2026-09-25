"""F4 discovery and calibration (S-1, S-2, S-3M, S-4..S-8). Synthetic data only,
fixed seeds. Controls over stochastic data are multi-seed with pass fractions
declared before running, because a single draw cannot separate chance from a
defect."""
from __future__ import annotations

import itertools
import json
import math
import time
from functools import lru_cache

import pytest

from optimizer import db
from optimizer.patterns import metadata, stage, store, synthetic
from optimizer.patterns.settings import DEFAULT_BEHAVIORS
from optimizer.patterns.stats import bh_qvalues, binom_sf

from .pattern_helpers import (BUSY_NULL_SEEDS, LOCAL, QUIET_NULL_SEEDS, find, planted_origin,
                              positive_payloads, positive_run, run_payloads, s3m_runs)

CENTER = synthetic.PLANTED_CENTERS
DAWN_RAMP = (5 * 60 + 15, 6 * 60 + 15)   # the 05:45 step, +/- one grid width


def _visible(patterns):
    return [p for p in patterns if p["behavior"] in DEFAULT_BEHAVIORS]


@lru_cache(maxsize=None)
def null_runs():
    out = []
    for seed in BUSY_NULL_SEEDS:
        out.append(("busy", seed, run_payloads(synthetic.generate(seed, planted=False)).patterns))
    for seed in QUIET_NULL_SEEDS:
        payloads = synthetic.generate(seed, planted=False,
                                      street_daily=synthetic.QUIET_STREET_DAILY)
        out.append(("quiet", seed, run_payloads(payloads).patterns))
    return out


def test_generator_emits_valid_tracks():
    events = positive_payloads()
    assert all(e["end_time"] >= e["start_time"] for e in events)
    assert all(e["start_time"] - 0.01 <= pt[1] <= e["end_time"] + 0.01
               for e in events for pt in e["data"]["path_data"])
    assert {e["camera"] for e in events} == set(synthetic.CAMERAS)


def test_s1_exact_binomial_tail_matches_direct_summation():
    for n in range(0, 11):
        for p in (0.0, 1e-9, 0.03, 0.2, 0.5, 0.77, 1 - 1e-9, 1.0):
            for k in range(0, n + 2):
                direct = sum(
                    math.prod(p if bit else 1 - p for bit in outcome)
                    for outcome in itertools.product((0, 1), repeat=n) if sum(outcome) >= k
                )
                assert binom_sf(k, n, p) == pytest.approx(direct, abs=1e-12)


def test_s2_benjamini_hochberg_worked_example():
    # Benjamini & Hochberg (1995), 15 p-values; at q = 0.05 exactly four are rejected.
    p = [0.0001, 0.0004, 0.0019, 0.0095, 0.0201, 0.0278, 0.0298, 0.0344, 0.0459,
         0.3240, 0.4262, 0.5719, 0.6528, 0.7590, 1.0000]
    expected = [0.0015, 0.003, 0.0095, 0.035625, 0.0603, 0.0638571428571, 0.0638571428571,
                0.0645, 0.0765, 0.486, 0.5811818181818, 0.714875, 0.7532307692308,
                0.8132142857143, 1.0]
    q = bh_qvalues(p)
    assert q == pytest.approx(expected, abs=1e-10)
    assert sum(v <= 0.05 for v in q) == 4
    shuffled = [p[i] for i in (14, 3, 0, 9, 7, 1, 12, 5, 2, 11, 6, 13, 4, 10, 8)]
    q2 = bh_qvalues(shuffled)
    assert [q2[shuffled.index(v)] for v in p] == pytest.approx(expected, abs=1e-10)


# S-3M: each planted pattern with the stratum, weekdays, cadence (and parity)
# it must be recovered with.
S3M_REQUIRED = {
    "P1": dict(site="cam_street", group="vehicle", behavior="brief_stop", cadence="weekly",
               weekdays=[1]),
    "P2": dict(site="cam_street", group="vehicle", behavior="brief_stop", cadence="biweekly",
               weekdays=[3]),
    "P3": dict(site="cam_door", group="person", behavior="brief_stop", cadence="weekday_set",
               weekdays=[0, 1, 2, 3, 4, 5]),
    "P4": dict(site="cam_back", group="vehicle", behavior="long_stay", cadence="weekday_set",
               weekdays=[0, 1, 2, 3, 4]),
}
# Pass fractions, declared before running (WYZE-020-R3): the background-
# consistency guard rejects about 1% of genuine biweekly draws, so P2 may miss
# 2 of 20; 3 or more misses in 20 would signal a defect, not chance.
S3M_MIN_RECOVERED = {"P1": 19, "P2": 18, "P3": 19, "P4": 19}
S3M_MAX_NONPLANTED_PER_SEED = 1
S3M_MAX_NONPLANTED_TOTAL = 3


def test_s3m_multi_seed_positive_control():
    recovered = {name: 0 for name in S3M_REQUIRED}
    p2_weekly_seeds, nonplanted, table = [], [], []
    for seed, patterns in s3m_runs():
        pats = _visible(patterns)
        row = {"seed": seed}
        for name, spec in S3M_REQUIRED.items():
            # Only a pattern derived from this plant counts, never a non-planted
            # pattern that happens to share the stratum, weekdays, and cadence.
            found = [p for p in find(pats, **spec) if planted_origin(p) == name]
            if name == "P2":
                found = [p for p in found if p["parity"] == synthetic.PARITY_A]
            for pat in found:
                # Wherever a planted pattern surfaces with its required cadence.
                assert abs(pat["window"]["median_min"] - CENTER[name]) <= 10, (seed, name)
                assert pat["confidence"] in ("moderate", "strong"), (seed, name)
            recovered[name] += len(found) == 1
            derived = [p for p in pats if planted_origin(p) == name]
            row[name] = [(p["cadence"], p["weekdays"], p["parity"], p["window"]["median"],
                          p["confidence"]) for p in derived]
        if any(p["cadence"] == "weekly" for p in pats if planted_origin(p) == "P2"):
            p2_weekly_seeds.append(seed)
        extra = [(seed, p["site"], p["behavior"], p["cadence"], p["weekdays"],
                  p["window"]["median"]) for p in pats if planted_origin(p) is None]
        assert len(extra) <= S3M_MAX_NONPLANTED_PER_SEED, extra
        nonplanted += extra
        table.append(row)
    for row in table:
        print("S-3M", json.dumps(row))
    print("S-3M recovered", recovered, "p2_weekly_seeds", p2_weekly_seeds,
          "nonplanted", len(nonplanted), nonplanted)
    for name, minimum in S3M_MIN_RECOVERED.items():
        assert recovered[name] >= minimum, (name, recovered)
    assert p2_weekly_seeds == []
    assert len(nonplanted) <= S3M_MAX_NONPLANTED_TOTAL


def test_s4_null_calibration_surfaces_almost_nothing():
    runs = null_runs()
    assert len(runs) == 60
    surfaced = [(kind, seed, p) for kind, seed, pats in runs for p in _visible(pats)]
    strong = [s for s in surfaced if s[2]["confidence"] == "strong"]
    print("S-4 surfaced", len(surfaced), "strong", len(strong),
          [(k, s, p["site"], p["behavior"], p["cadence"], p["window"]["median"])
           for k, s, p in surfaced])
    assert len(surfaced) <= 1
    assert len(strong) == 0


def test_s5_hit_rate_is_computed_over_covered_weeks():
    payloads = synthetic.generate(2031, offline_street_weeks=(4, 11))
    pats = run_payloads(payloads).patterns
    p1 = find(pats, site="cam_street", group="vehicle", behavior="brief_stop", weekdays=[1])
    assert len(p1) == 1
    assert p1[0]["support"] == {"k": 14, "n": 14}
    assert p1[0]["hit_rate"] == 1.0
    grid = [g for g in p1[0]["hit_grid"] if not g["covered"]]
    assert len(grid) == 2 and not any(g["hit"] for g in grid)


def test_s6_dawn_step_edge_surfaces_nothing():
    lo, hi = DAWN_RAMP
    candidates = [("positive", 2031, positive_run().patterns)] + null_runs()
    near = [(kind, seed, p["behavior"], p["window"]["median"])
            for kind, seed, pats in candidates for p in pats
            if p["site"] == "cam_street"
            and p["window"]["start_min"] < hi and p["window"]["end_min"] > lo]
    assert near == []


def _strip(patterns):
    return json.dumps(patterns, sort_keys=True, default=str)


def test_s7_determinism_same_dataset_same_keys_and_statistics():
    first = run_payloads(synthetic.generate(2031)).patterns
    second = run_payloads(synthetic.generate(2031)).patterns
    assert [p["pattern_key"] for p in first] == [p["pattern_key"] for p in second]
    assert _strip(first) == _strip(second)


def test_s8_scale_about_70k_events_under_60s(tmp_path):
    payloads = synthetic.generate(2031, street_daily=synthetic.STREET_DAILY * 1.55)
    assert 65_000 <= len(payloads) <= 75_000
    path = tmp_path / "scale.db"
    db.init_db(path)
    conn = db.connect(path)
    store.apply_schema(conn)
    with db.transaction(conn):
        for e in payloads:
            db.upsert_event(conn, e)
            store.upsert_event_metadata(conn, metadata.extract(e))
    run_id = db.start_run(conn, 0.0, 1.0)
    t0 = time.perf_counter()
    result = stage.run_stage(conn, run_id, now=synthetic.span_now(), local_time=LOCAL)
    elapsed = time.perf_counter() - t0
    print(f"S-8 events={len(payloads)} stage_runtime_s={elapsed:.2f}")
    assert result["status"] == "ok"
    assert elapsed < 60.0
