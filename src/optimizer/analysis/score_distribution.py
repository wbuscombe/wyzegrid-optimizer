"""
Score distribution per (camera, label).

For each (camera, label) pair, compute:
  count, min, max, mean, median, p10, p25, p75, p90
plus the current threshold for context.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean, median


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def detect(events: list[dict], config_map: dict) -> dict:
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for e in events:
        if e["label"] and e["camera"] and e["score"] > 0:
            buckets[(e["camera"], e["label"])].append(e["score"])
    out = []
    for (cam, label), scores in sorted(buckets.items()):
        cfg = config_map.get((cam, label), {}) or {}
        out.append({
            "camera": cam,
            "label": label,
            "count": len(scores),
            "min": min(scores),
            "max": max(scores),
            "mean": mean(scores),
            "median": median(scores),
            "p10": _quantile(scores, 0.10),
            "p25": _quantile(scores, 0.25),
            "p75": _quantile(scores, 0.75),
            "p90": _quantile(scores, 0.90),
            "current_threshold": cfg.get("threshold"),
            "current_min_score": cfg.get("min_score"),
        })
    return {"distributions": out}
