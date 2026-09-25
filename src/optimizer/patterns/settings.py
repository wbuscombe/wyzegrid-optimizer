"""
Tunables for recurring-visit pattern discovery.

Every tunable is a named constant. `PatternParams` bundles them so tests can
exercise the pipeline with explicit values; production uses `DEFAULT_PARAMS`.

Only three operational settings have environment overrides (parsed in
`optimizer.config`): the kill switch, the identity label map, and the site
groups. The statistical thresholds are code constants so the synthetic null
calibration in the test suite stays the evidence for them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Mapping

# ---- A. label groups --------------------------------------------------------
LABEL_GROUPS: Mapping[str, frozenset[str]] = {
    "vehicle": frozenset({"car", "truck", "bus", "motorcycle"}),
    "person": frozenset({"person"}),
}

# ---- B. visits --------------------------------------------------------------
VISIT_GAP_S = 90.0             # a track joins the open visit within this gap
STATIONARY_IOU = 0.8           # stationary re-trigger link: box IoU floor
STATIONARY_LINK_S = 1800.0     # stationary re-trigger link: maximum gap

# ---- C. behavior ------------------------------------------------------------
BEHAVIORS = ("brief_stop", "long_stay", "pass_through")
DEFAULT_BEHAVIORS = ("brief_stop", "long_stay")   # pass_through is hidden by default
LONG_STAY_S = 1200.0
HALT_MIN_S = 10.0              # a halt lasts at least this long ...
HALT_EPS = 0.03                # ... within this normalized distance of its first point
BRIEF_STOP_MIN_S = 20.0        # dwell floor for the no-path fallback
BRIEF_STOP_MAX_DISPLACEMENT = 0.10   # first-to-last box centroid displacement
EDGE_MARGIN = 0.15             # entry/exit side: nearest image edge within this

# ---- D. local time ----------------------------------------------------------
EPOCH_MONDAY = date(2001, 1, 1)   # a Monday; week_index counts whole weeks from it

# ---- E. coverage and analysis window -----------------------------------------
ANALYSIS_WINDOW_DAYS = 112
MIN_COVERED_WEEKS = 4

# ---- F. discovery family ----------------------------------------------------
GRID_WIDTH_MIN = 30
GRID_STEP_MIN = 15
SURROUND_MIN = 120
P0_FLOOR = 1e-9
FDR_Q = 0.05   # calibrated (WYZE-020-R1 Phase 4): 0.10 surfaced 3 null patterns

# ---- G. patterns ------------------------------------------------------------
MIN_LIFT_T = 3.0
MIN_SUPPORT_WEEKS = 4
MIN_HIT_RATE = 0.5
MAX_WINDOW_SPREAD_MIN = 120
WEEKDAY_ALPHA = 0.01
MIN_LIFT_W = 3.0
# G-PRIME-R cadence (WYZE-020-R3). The local null above is for discovery and
# surfacing only; cadence tests use the time-of-day background b_ToD.
PARITY_ALPHA = 0.05                   # one-sided Fisher exact test, high vs low parity
BACKGROUND_CONSISTENCY_ALPHA = 0.01   # the low parity must be consistent with b_ToD
MIN_PARITY_SUPPORT = 3                # hit weeks a biweekly claim needs on its active parity
# Covered weeks a parity needs for its hit rate to count: the low parity (n_L)
# of a biweekly claim, and each parity of a weekly claim (WYZE-020-R4 W1).
MIN_LOW_PARITY_WEEKS = 2
BTOD_MIN_WEEKDAYS = 3                 # qualifying other weekdays b_ToD needs; fewer = unknown
WEEKDAY_SET_MIN_DAYS = 3
WEEKDAY_SET_MEDIAN_TOL_MIN = 45
STRONG_MAX_Q = 0.01
STRONG_MIN_SUPPORT = 6
STRONG_MIN_HIT_RATE = 0.75
STRONG_MAX_SPREAD_MIN = 60
MODERATE_MAX_Q = 0.10
MODERATE_MIN_SUPPORT = 4
MODERATE_MIN_HIT_RATE = 0.5
MODERATE_MAX_SPREAD_MIN = 120
RECENT_WEEKS = 3
KEY_ROUND_MIN = 15

# ---- H. identity gate -------------------------------------------------------
IDENTITY_MIN_VISITS = 3
IDENTITY_MIN_FRACTION = 0.6
IDENTITY_MAX_OTHER_FRACTION = 0.2

# ---- I. run integration (environment-overridable in optimizer.config) --------
PATTERNS_ENABLED = True
IDENTITY_LABEL_MAP: Mapping[str, str] = {}   # ships EMPTY: every pattern is unidentified
SITE_GROUPS: Mapping[str, str] = {}          # empty: every camera is its own site

UNIDENTIFIED_TEXT = "Unidentified recurring pattern - no service identity assigned"
DISCLAIMER_TEXT = (
    "Patterns are statistical regularities in detection metadata, not identifications."
)


@dataclass(frozen=True)
class PatternParams:
    label_groups: Mapping[str, frozenset[str]] = field(default_factory=lambda: LABEL_GROUPS)
    visit_gap_s: float = VISIT_GAP_S
    stationary_iou: float = STATIONARY_IOU
    stationary_link_s: float = STATIONARY_LINK_S
    long_stay_s: float = LONG_STAY_S
    halt_min_s: float = HALT_MIN_S
    halt_eps: float = HALT_EPS
    brief_stop_min_s: float = BRIEF_STOP_MIN_S
    brief_stop_max_displacement: float = BRIEF_STOP_MAX_DISPLACEMENT
    edge_margin: float = EDGE_MARGIN
    epoch_monday: date = EPOCH_MONDAY
    analysis_window_days: int = ANALYSIS_WINDOW_DAYS
    min_covered_weeks: int = MIN_COVERED_WEEKS
    grid_width_min: int = GRID_WIDTH_MIN
    grid_step_min: int = GRID_STEP_MIN
    surround_min: int = SURROUND_MIN
    p0_floor: float = P0_FLOOR
    fdr_q: float = FDR_Q
    min_lift_t: float = MIN_LIFT_T
    min_support_weeks: int = MIN_SUPPORT_WEEKS
    min_hit_rate: float = MIN_HIT_RATE
    max_window_spread_min: int = MAX_WINDOW_SPREAD_MIN
    weekday_alpha: float = WEEKDAY_ALPHA
    min_lift_w: float = MIN_LIFT_W
    parity_alpha: float = PARITY_ALPHA
    background_consistency_alpha: float = BACKGROUND_CONSISTENCY_ALPHA
    min_parity_support: int = MIN_PARITY_SUPPORT
    min_low_parity_weeks: int = MIN_LOW_PARITY_WEEKS
    btod_min_weekdays: int = BTOD_MIN_WEEKDAYS
    weekday_set_min_days: int = WEEKDAY_SET_MIN_DAYS
    weekday_set_median_tol_min: int = WEEKDAY_SET_MEDIAN_TOL_MIN
    recent_weeks: int = RECENT_WEEKS
    key_round_min: int = KEY_ROUND_MIN
    identity_min_visits: int = IDENTITY_MIN_VISITS
    identity_min_fraction: float = IDENTITY_MIN_FRACTION
    identity_max_other_fraction: float = IDENTITY_MAX_OTHER_FRACTION
    identity_label_map: Mapping[str, str] = field(default_factory=dict)
    site_groups: Mapping[str, str] = field(default_factory=dict)

    def as_record(self) -> dict:
        """JSON-safe record of the thresholds a run used (label map size only)."""
        return {
            "visit_gap_s": self.visit_gap_s,
            "stationary_iou": self.stationary_iou,
            "stationary_link_s": self.stationary_link_s,
            "long_stay_s": self.long_stay_s,
            "halt_min_s": self.halt_min_s,
            "halt_eps": self.halt_eps,
            "brief_stop_min_s": self.brief_stop_min_s,
            "analysis_window_days": self.analysis_window_days,
            "min_covered_weeks": self.min_covered_weeks,
            "grid_width_min": self.grid_width_min,
            "grid_step_min": self.grid_step_min,
            "surround_min": self.surround_min,
            "fdr_q": self.fdr_q,
            "min_lift_t": self.min_lift_t,
            "min_support_weeks": self.min_support_weeks,
            "min_hit_rate": self.min_hit_rate,
            "max_window_spread_min": self.max_window_spread_min,
            "weekday_alpha": self.weekday_alpha,
            "min_lift_w": self.min_lift_w,
            "parity_alpha": self.parity_alpha,
            "background_consistency_alpha": self.background_consistency_alpha,
            "min_parity_support": self.min_parity_support,
            "min_low_parity_weeks": self.min_low_parity_weeks,
            "btod_min_weekdays": self.btod_min_weekdays,
            "identity_label_map_size": len(self.identity_label_map),
            "site_groups_size": len(self.site_groups),
        }


DEFAULT_PARAMS = PatternParams()
