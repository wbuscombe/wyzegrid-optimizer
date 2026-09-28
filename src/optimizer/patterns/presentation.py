"""
Estimate semantics for recurring-pattern output.

Cadence labels (weekly, biweekly, weekday_set, recurring) are statistical
ESTIMATES from detection metadata, never measurements and never
identifications. Every pattern the API or the dashboard shows carries
`cadence_is_estimate: true` and a `limitations` object naming the
dense-background case, with a pointer to the documentation section that
explains it.

No field here, or anywhere in the output, expresses an error rate. The only
evidence for the limitation is a fixed-seed synthetic control, and the
documentation presents it as synthetic evidence only.

The dashboard also puts each pattern in plain words, mapped one phrase per
value from fields that already exist (the confidence tier, the cadence label,
and support k/n). No new statistic is computed, no number from the synthetic
controls is shown, and no phrase names what or who an activity is.
"""
from __future__ import annotations

from .settings import DISCLAIMER_TEXT, UNIDENTIFIED_TEXT

CADENCE_IS_ESTIMATE = True
LIMITATIONS_DOC = "ARCHITECTURE.md#cadence-estimates-and-limitations"
DENSE_BACKGROUND_TEXT = (
    "On busy scenes an every-other-week pattern can occasionally read as weekly "
    "or fail to surface."
)
ESTIMATE_HEADER_TEXT = (
    "Cadences are statistical estimates from detection metadata. " + DENSE_BACKGROUND_TEXT
)
CADENCE_PREFIX = "Estimated cadence: "

# One plain phrase per confidence tier and per cadence label.
CONFIDENCE_PHRASES = {
    "strong": "Strong evidence the timing repeats",
    "moderate": "Moderate evidence the timing repeats",
}
CADENCE_PHRASES = {
    "weekly": "About once a week",
    "biweekly": "About every other week",
    "weekday_set": "On several weekdays at a similar time",
    "recurring": "Repeats at this time, with no clear weekly rhythm",
}
CERTAINTY_LEGEND = (
    "How sure: strong or moderate evidence (the confidence tier) that a pattern's "
    "timing repeats. It never says what or who the activity is. Covered weeks are "
    "weeks with any detection on that camera and weekday."
)
# The documented limitations (ARCHITECTURE.md), as page text and without numbers.
# The header already states the dense-background case.
OTHER_LIMITATIONS = (
    "An every-other-week pattern whose off weeks happen to be busy can read as "
    "recurring instead.",
    "A cadence needs enough covered weeks on other weekdays at the same time of day; "
    "without them it reads as recurring.",
    "A camera counts as covered on any day it logged at least one detection, so a day "
    "with a partial outage still counts.",
    "Overlapping objects on one camera merge into one visit.",
    "Activity that repeats across midnight is counted as two windows on neighbouring "
    "weekdays.",
    "Events from before pattern analysis began have no stored path, so they are "
    "classified from their box or dwell time only.",
    "Pattern times are in the clock the analysis runs on, which can differ from "
    "household time.",
)

# The copy the dashboard section renders, in one place so tests pin it exactly.
COPY = {
    "estimate_header": ESTIMATE_HEADER_TEXT,
    "disclaimer": DISCLAIMER_TEXT,
    "unidentified": UNIDENTIFIED_TEXT,
    "cadence_prefix": CADENCE_PREFIX,
    "limitations_doc": LIMITATIONS_DOC,
    "certainty_legend": CERTAINTY_LEGEND,
    "other_limitations": OTHER_LIMITATIONS,
}


def limitations() -> dict:
    """The limitations object every pattern carries (a fresh copy each call)."""
    return {"dense_background": DENSE_BACKGROUND_TEXT, "documentation": LIMITATIONS_DOC}


def estimated_cadence(cadence: str) -> str:
    """The dashboard's cadence text, for example "Estimated cadence: weekly"."""
    return CADENCE_PREFIX + cadence


def plain_confidence(confidence: str) -> str:
    """The confidence tier in plain words (an unknown tier shows as itself)."""
    return CONFIDENCE_PHRASES.get(confidence, confidence)


def plain_cadence(cadence: str) -> str:
    """The cadence label in plain words (an unknown label shows as itself)."""
    return CADENCE_PHRASES.get(cadence, cadence)


def seen_summary(pattern: dict) -> str:
    """Support k/n in words: covered weeks for one weekday, covered days (weekday
    by week) for a set of weekdays. For example "Seen in 11 of 16 covered weeks"."""
    k, n = pattern["support"]["k"], pattern["support"]["n"]
    if len(pattern["weekdays"]) == 1:
        return f"Seen in {k} of {n} covered weeks"
    return f"Seen on {k} of {n} covered days"


def present(pattern: dict) -> dict:
    """A stored pattern as the API and dashboard show it: every stored field,
    plus the estimate flag and the limitations object."""
    out = dict(pattern)
    out["cadence_is_estimate"] = CADENCE_IS_ESTIMATE
    out["limitations"] = limitations()
    return out
