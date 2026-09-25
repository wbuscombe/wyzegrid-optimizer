"""
Identity gate (contract section H).

Every pattern is unidentified by default. An identity comes only from an
explicit upstream label (a Frigate sub_label or attribute string) found in a
configured map that ships EMPTY. Base labels, time, weekday, cadence, dwell,
geometry, speed, and any model output are never consulted.
"""
from __future__ import annotations

from collections import Counter
from typing import Mapping, Optional

from .settings import DEFAULT_PARAMS, PatternParams
from .visits import Visit

NO_LABEL = "no explicit upstream label"
INSUFFICIENT = "insufficient label support"
CONFLICTING = "conflicting labels"


def visit_identities(visit: Visit, label_map: Mapping[str, str]) -> set[str]:
    """Identities a visit's explicit upstream labels map to (exact string keys)."""
    out = set()
    for track in visit.tracks:
        for label in track.explicit_labels:
            if label in label_map:
                out.add(label_map[label])
    return out


def assign(members: list[Visit],
           params: PatternParams = DEFAULT_PARAMS) -> tuple[Optional[str], str]:
    """(identity or None, identity_reason)."""
    label_map = params.identity_label_map
    total = len(members)
    if not label_map or total == 0:
        return None, NO_LABEL
    counts: Counter[str] = Counter()
    for visit in members:
        for ident in visit_identities(visit, label_map):
            counts[ident] += 1
    if not counts:
        return None, NO_LABEL
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    top, top_count = ranked[0]
    others = [c for ident, c in ranked[1:]]
    if any(c / total > params.identity_max_other_fraction for c in others):
        return None, CONFLICTING
    if top_count < params.identity_min_visits or top_count / total < params.identity_min_fraction:
        return None, INSUFFICIENT
    return top, "explicit upstream label"
