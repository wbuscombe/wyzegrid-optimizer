"""
Threshold proximity — detections that sit within a configurable band of the
current threshold. Many in the band = tuning candidates (small change moves
many events across the cutoff).
"""
from __future__ import annotations

from collections import defaultdict

DEFAULT_BAND = 0.05  # 5 percentage points


def detect(events: list[dict], config_map: dict, band: float = DEFAULT_BAND) -> dict:
    by_pair: dict[tuple[str, str], list[float]] = defaultdict(list)
    for e in events:
        by_pair[(e["camera"], e["label"])].append(e["score"])

    out = []
    for (cam, label), scores in sorted(by_pair.items()):
        cfg = config_map.get((cam, label), {}) or {}
        thr = cfg.get("threshold")
        if thr is None:
            continue
        # Count detections in [thr - band, thr + band]
        in_band = [s for s in scores if abs(s - thr) <= band]
        below_band = [s for s in scores if s < thr - band]
        above_band = [s for s in scores if s > thr + band]
        out.append({
            "camera": cam,
            "label": label,
            "threshold": thr,
            "band": band,
            "total": len(scores),
            "in_band": len(in_band),
            "in_band_pct": len(in_band) / len(scores) if scores else 0.0,
            "below_band": len(below_band),
            "above_band": len(above_band),
            "is_tuning_candidate": len(in_band) >= 5 and (len(in_band) / max(1, len(scores))) >= 0.15,
        })
    return {"candidates": out}
