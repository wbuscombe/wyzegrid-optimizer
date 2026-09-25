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

# The copy the dashboard section renders, in one place so tests pin it exactly.
COPY = {
    "estimate_header": ESTIMATE_HEADER_TEXT,
    "disclaimer": DISCLAIMER_TEXT,
    "unidentified": UNIDENTIFIED_TEXT,
    "cadence_prefix": CADENCE_PREFIX,
    "limitations_doc": LIMITATIONS_DOC,
}


def limitations() -> dict:
    """The limitations object every pattern carries (a fresh copy each call)."""
    return {"dense_background": DENSE_BACKGROUND_TEXT, "documentation": LIMITATIONS_DOC}


def estimated_cadence(cadence: str) -> str:
    """The dashboard's cadence text, for example "Estimated cadence: weekly"."""
    return CADENCE_PREFIX + cadence


def present(pattern: dict) -> dict:
    """A stored pattern as the API and dashboard show it: every stored field,
    plus the estimate flag and the limitations object."""
    out = dict(pattern)
    out["cadence_is_estimate"] = CADENCE_IS_ESTIMATE
    out["limitations"] = limitations()
    return out
