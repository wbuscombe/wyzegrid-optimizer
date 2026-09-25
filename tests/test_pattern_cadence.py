"""G-PRIME-R cadence controls (S-9a, S-9b, S-10, S-11, S-12a, S-12c).

Synthetic data only; every seed list is fixed here and was declared before
running. Scenarios use the declared cam_street background plus planted
vehicle brief stops (`synthetic.generate_street`). A single low-probability
draw is chance, not a defect, so the stochastic controls are multi-seed with
pass fractions declared up front.
"""
from __future__ import annotations

import json
from datetime import timedelta
from fractions import Fraction

import pytest

from optimizer.patterns import synthetic
from optimizer.patterns.discovery import cadence_test, time_of_day_background
from optimizer.patterns.localtime import week_index
from optimizer.patterns.stats import fisher_one_sided

from .pattern_helpers import (LOCAL, STREET, near, pooled_other_weekday_rate, run_payloads,
                              street_patterns, visible)

SP = synthetic.StreetPlant
A = synthetic.PARITY_A


def _span_offsets_of_parity(parity):
    return tuple(o for o in range(synthetic.SPAN_WEEKS)
                 if week_index(synthetic.SPAN_START + timedelta(weeks=o)) % 2 == parity)


B_OFFSETS = _span_offsets_of_parity(1 - A)   # the 8 parity-B weeks of the span

# S-9a: biweekly under the declared background, inside the uniform brief-stop
# period. A synthetic characterization of the dense-background case: such a
# plant is often not discovered at all, and when background stops fill enough
# of its off weeks it reads as weekly. Discovery, biweekly, and weekly counts
# are reported as a synthetic characterization of this scenario only. The
# biweekly property itself is carried by S-3M's P2.
S9A_PLANT = SP("S9A", 2, 13 * 60 + 10, 13 * 60 + 30, parity=A)
S9A_SEEDS = tuple(range(3101, 3121))
# DEFECT TRIPWIRE (WYZE-020-R5 E1). Cadence labels ship as estimates. This
# ceiling is policy, fixed before running and not fitted to any observed
# count: an estimate label that is wrong more than one time in five in its own
# worst-case stress scenario is unfit to ship.
S9A_TRIPWIRE_MAX_WEEKLY = 4
# S-9b: false-biweekly guard. (i) weekly except two parity-B weeks (the 3rd
# and 6th of the span's parity-B weeks); (ii) weekly with a 10% miss rate.
S9B_SKIP = (B_OFFSETS[2], B_OFFSETS[5])
S9B_I_PLANT = SP("S9B", 0, 14 * 60 + 10, 14 * 60 + 30, skip_weeks=S9B_SKIP)
S9B_I_SEED = 3200
S9B_II_PLANT = SP("S9B", 0, 14 * 60 + 10, 14 * 60 + 30, miss_rate=0.1)
S9B_II_SEEDS = tuple(range(3201, 3221))
# S-10: quiet pre-dawn window; all 8 parity-A weeks and 4 of 8 parity-B weeks
# (the 2nd, 4th, 6th, and 8th parity-B weeks are skipped).
S10_SKIP = B_OFFSETS[1::2]
S10_PLANT = SP("S10", 4, 5 * 60 + 10, 5 * 60 + 30, skip_weeks=S10_SKIP)
S10_SEED = 3000
# S-12c: same-clock contamination in a quiet pre-dawn window (WYZE-020-R4 W3,
# replacing S-12b). The declared background has no brief stops at 04:10-04:30,
# so every plant is discoverable and the stress control is always measurable.
S12C_PLANTS = (SP("S12C_TUE", 1, 4 * 60 + 10, 4 * 60 + 30),
               SP("S12C_THU", 3, 4 * 60 + 10, 4 * 60 + 30),
               SP("S12C_WED", 2, 4 * 60 + 10, 4 * 60 + 30, parity=A))
S12C_SEEDS = tuple(range(3401, 3421))
S12C_MIN_CLASSIFIED = 19
S12C_MAX_WED_WEEKLY = 0
S12C_MIN_STRESS_RATIO = 2.0


def _center(plant):
    return (plant.start_min + plant.end_min) / 2.0


def _single(patterns, weekday, plant):
    return [p for p in near(patterns, weekday, _center(plant)) if p["weekdays"] == [weekday]]


def test_scenario_offsets_are_the_declared_parity():
    assert len(B_OFFSETS) == 8
    assert all(o in B_OFFSETS for o in S9B_SKIP + S10_SKIP)
    assert len(set(S9B_SKIP)) == 2 and len(set(S10_SKIP)) == 4


def test_s9a_dense_background_weekly_tripwire():
    discovered, biweekly, weekly = 0, 0, 0
    for seed in S9A_SEEDS:
        cands = near(street_patterns(seed, (S9A_PLANT,)), 2, _center(S9A_PLANT))
        discovered += bool(cands)
        biweekly += any(p["cadence"] == "biweekly" and p["parity"] == A and p["weekdays"] == [2]
                        for p in cands)
        weekly += any(p["cadence"] == "weekly" for p in cands)
        print("S-9a", json.dumps({
            "seed": seed, "discovered": bool(cands),
            "patterns": [{"cadence": p["cadence"], "weekdays": p["weekdays"],
                          "parity": p["parity"], "periodicity": p["periodicity"],
                          "h_A": p["h_A"], "n_A": p["n_A"], "h_B": p["h_B"], "n_B": p["n_B"],
                          "b_tod": p["b_tod"]} for p in cands]}, default=str))
    print("S-9a synthetic characterization: discovered", discovered, "biweekly", biweekly,
          "weekly", weekly, "of", len(S9A_SEEDS), "seeds; tripwire limit",
          S9A_TRIPWIRE_MAX_WEEKLY)
    assert weekly <= S9A_TRIPWIRE_MAX_WEEKLY


def test_s9b_false_biweekly_guard():
    planted_weeks = {week_index(LOCAL(s)[0]) for s in _starts(S9B_I_SEED, (S9B_I_PLANT,))}
    assert len(planted_weeks) == 14
    cands = _single(street_patterns(S9B_I_SEED, (S9B_I_PLANT,)), 0, S9B_I_PLANT)
    print("S-9b(i)", [(p["cadence"], p["periodicity"], p["h_A"], p["h_B"], p["p_parity"])
                      for p in cands])
    assert [p["cadence"] for p in cands] == ["weekly"]

    biweekly_seeds = []
    for seed in S9B_II_SEEDS:
        cands = near(street_patterns(seed, (S9B_II_PLANT,)), 0, _center(S9B_II_PLANT))
        print("S-9b(ii)", seed, [(p["cadence"], p["weekdays"], p["periodicity"], p["h_A"],
                                  p["h_B"]) for p in cands])
        if any(p["cadence"] == "biweekly" for p in cands):
            biweekly_seeds.append(seed)
    assert biweekly_seeds == []


def _starts(seed, plants):
    """Start times of the planted events, from ground-truth origins."""
    origins: dict = {}
    events = synthetic.generate_street(seed, plants, origins=origins)
    names = {p.name for p in plants}
    return [e["start_time"] for e in events if origins[e["id"]][0] in names]


def test_s10_quiet_window_partial_off_parity_is_indeterminate():
    weeks = {week_index(LOCAL(s)[0]) for s in _starts(S10_SEED, (S10_PLANT,))}
    assert sum(1 for w in weeks if w % 2 == A) == 8
    assert sum(1 for w in weeks if w % 2 != A) == 4
    pats = street_patterns(S10_SEED, (S10_PLANT,))
    cands = _single(pats, 4, S10_PLANT)
    print("S-10", [(p["cadence"], p["periodicity"], p["h_A"], p["h_B"], p["p_parity"],
                    p["p_bgL"], p["b_tod"]) for p in cands])
    assert len(cands) == 1
    assert cands[0]["cadence"] == "recurring"
    assert cands[0]["periodicity"] == "indeterminate"


def test_s11_fisher_exact_tail_hand_values():
    total = 12870   # C(16, 8)
    for h_low, numerator in ((0, 1), (3, 165), (4, 495), (6, 3003)):
        value = fisher_one_sided(8, 8, h_low, 8)
        assert value == numerator / total
        assert Fraction(value).limit_denominator(total) == Fraction(numerator, total)
    # symmetry control: an equal split is not significant
    assert fisher_one_sided(4, 8, 4, 8) > 0.5


def test_w1_weekly_needs_evidence_on_both_parities():
    weeks = list(range(16))
    quiet = {0: (0, 16), 1: (0, 16), 2: (0, 16)}

    def hits(h_a, h_b):
        parity_a, parity_b = weeks[0::2], weeks[1::2]
        return sorted(parity_a[:h_a] + parity_b[:h_b])

    # No parity contrast (p_parity 2025/12870), but parity B is below MIN_HIT_RATE.
    test = cadence_test(weeks, hits(6, 3), quiet)
    assert test["p_parity"] > 0.05
    assert test["periodicity"] == "indeterminate" and test["cadence"] is None
    # Control: both parities at MIN_HIT_RATE or above read weekly.
    test = cadence_test(weeks, hits(6, 4), quiet)
    assert test["periodicity"] == "weekly" and test["cadence"] == "weekly"
    # An exact tie also needs both parities at MIN_HIT_RATE.
    assert cadence_test(weeks, hits(4, 4), quiet)["periodicity"] == "weekly"
    assert cadence_test(weeks, hits(3, 3), quiet)["periodicity"] == "indeterminate"
    # A parity with fewer than MIN_LOW_PARITY_WEEKS covered weeks cannot support weekly.
    assert cadence_test([0, 1, 2, 4, 6], [0, 1, 2, 4, 6], quiet)["periodicity"] == "indeterminate"


def test_s12a_time_of_day_background_median():
    assert time_of_day_background([0.05, 0.06, 0.07, 0.08, 0.09, 0.95]) == pytest.approx(0.075)
    assert time_of_day_background([0.05, 0.06, 0.07, 0.08, 0.90, 0.95]) == pytest.approx(0.075)
    assert time_of_day_background([0.05, 0.06, 0.07, 0.85, 0.90, 0.95]) == pytest.approx(0.46)
    assert time_of_day_background([0.05, 0.95]) is None
    # Two qualifying weekdays: specificity and consistency are unknown, cadence recurring.
    weeks = list(range(16))
    test = cadence_test(weeks, [w for w in weeks if w % 2 == 0], {0: (1, 16), 1: (0, 16)})
    assert test["b_tod"] is None and test["p_bgL"] is None
    assert test["weekday_specificity"]["result"] == "unknown"
    assert test["cadence"] == "recurring"
    # Control: the same pattern with three quiet qualifying weekdays reads biweekly.
    test = cadence_test(weeks, [w for w in weeks if w % 2 == 0],
                        {0: (1, 16), 1: (0, 16), 2: (1, 16)})
    assert test["periodicity"] == "biweekly" and test["cadence"] == "biweekly"


def test_s12c_quiet_window_same_clock_contamination():
    classified, wed_weekly, ratios = 0, 0, []
    tue_p, thu_p, wed_p = S12C_PLANTS
    for seed in S12C_SEEDS:
        payloads = synthetic.generate_street(seed, S12C_PLANTS)
        pats = visible(run_payloads(payloads).patterns)
        tue, thu, wed = _single(pats, 1, tue_p), _single(pats, 3, thu_p), _single(pats, 2, wed_p)
        ok = (any(p["cadence"] == "weekly" for p in tue)
              and any(p["cadence"] == "weekly" for p in thu)
              and any(p["cadence"] == "biweekly" and p["parity"] == A for p in wed))
        classified += ok
        wed_weekly += any(p["cadence"] == "weekly" for p in near(pats, 2, _center(wed_p)))
        ratio = None
        if wed:
            lo, hi = wed[0]["window"]["start_min"], wed[0]["window"]["end_min"]
            pooled = pooled_other_weekday_rate(payloads, STREET, 2, lo, hi)
            ratio = pooled / wed[0]["b_tod"]
        ratios.append(ratio)
        print("S-12c", json.dumps({
            "seed": seed, "classified": ok,
            "tue": [(p["cadence"], p["periodicity"], p["b_tod"]) for p in tue],
            "thu": [(p["cadence"], p["periodicity"], p["b_tod"]) for p in thu],
            "wed": [(p["cadence"], p["parity"], p["periodicity"], p["h_A"], p["h_B"], p["b_tod"],
                     [p["window"]["start_min"], p["window"]["end_min"]]) for p in wed],
            "stress_ratio": ratio}, default=str))
    print("S-12c classified", classified, "wed_weekly", wed_weekly, "ratios", ratios)
    # Positive control for the stress itself: in EVERY seed R1's pooled rate for
    # Wednesday's interval must exceed Wednesday's b_ToD by at least 2x, or the
    # scenario does not stress the estimator and this test is not evidence.
    assert all(r is not None and r >= S12C_MIN_STRESS_RATIO for r in ratios), ratios
    assert classified >= S12C_MIN_CLASSIFIED
    assert wed_weekly <= S12C_MAX_WED_WEEKLY
