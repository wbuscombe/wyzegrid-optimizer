"""
Convert raw DB rows / Frigate event dicts into the lean dict shape the detectors expect.
"""
from __future__ import annotations

import json
from typing import Any


def normalize_events(rows: list[Any]) -> list[dict]:
    """Accept either sqlite3.Row objects or plain dicts; emit canonical dicts."""
    out = []
    for r in rows:
        get = (lambda k: r[k]) if hasattr(r, "keys") else r.get  # type: ignore[arg-type]
        bx, by, bw, bh = get("box_x"), get("box_y"), get("box_w"), get("box_h")
        out.append({
            "id": get("id"),
            "camera": get("camera"),
            "label": get("label"),
            "sub_label": get("sub_label"),
            "score": float(get("score") or 0.0),
            "start_time": float(get("start_time") or 0.0),
            "end_time": float(get("end_time")) if get("end_time") else None,
            "zones": json.loads(get("zones_json") or "[]"),
            "box": [bx, by, bw, bh] if all(v is not None for v in (bx, by, bw, bh)) else None,
            "false_positive": bool(get("false_positive")),
        })
    return out


def normalize_config_rows(rows: list[Any]) -> dict[tuple[str, str], dict]:
    """Latest config snapshot rows → {(camera,label): {...}}"""
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        get = (lambda k: r[k]) if hasattr(r, "keys") else r.get  # type: ignore[arg-type]
        cam, label = get("camera"), get("label")
        out[(cam, label)] = {
            "min_score": get("min_score"),
            "threshold": get("threshold"),
            "min_area": get("min_area"),
            "stationary_max_frames": get("stationary_max_frames"),
            "zones": json.loads(get("zones_json") or "[]"),
        }
    return out
