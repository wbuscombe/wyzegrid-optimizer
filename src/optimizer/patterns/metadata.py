"""
Extract detection metadata from a Frigate `/api/events` payload.

Only an explicit allowlist of metadata keys is read, so image-bearing fields
(the event's base64 image column, for example) can never reach a persisted row.
Extraction is tolerant: a missing, null, or malformed key yields None and
never raises.

Key names come from the public Frigate v0.16.4 source
(frigate/events/maintainer.py, frigate/track/tracked_object.py):
  data.region, data.top_score, data.average_estimated_speed,
  data.velocity_angle, data.path_data ([[x, y], t] with normalized x, y),
  data.attributes ([{label, score, box}]), top-level sub_label and zones.
"""
from __future__ import annotations

import math
from typing import Any

# Tokens that must never appear as a key or value in pattern output. Used by
# the read-only API as a last-line guard and by the tests that prove it.
MEDIA_TOKENS = (
    "thumbnail", "snapshot", "clip", "recording", "preview",
    ".jpg", ".jpeg", ".png", ".mp4", ".m3u8", "/vod/",
)


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_box(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    nums = [_num(v) for v in value]
    if any(v is None for v in nums):
        return None
    return [round(v, 5) for v in nums]  # type: ignore[arg-type]


def _text(value: Any) -> str | None:
    if isinstance(value, (list, tuple)):  # legacy [label, score] form
        value = value[0] if value else None
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return None
    text = str(value).strip()
    return text[:200] or None


def _path(value: Any) -> list[list[float]] | None:
    """[[x, y], t] (or [x, y, t]) points -> [[x, y, t], ...] sorted by t.
    Malformed points are skipped; storage rounds when it serializes."""
    if not isinstance(value, (list, tuple)):
        return None
    points = []
    for item in value:
        try:
            if len(item) == 2:
                (x, y), t = item
            elif len(item) == 3:
                x, y, t = item
            else:
                continue
            x, y, t = float(x), float(y), float(t)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x) and math.isfinite(y) and math.isfinite(t):
            points.append([x, y, t])
    points.sort(key=lambda p: p[2])
    return points or None


def _attributes(value: Any) -> list[dict] | None:
    if not isinstance(value, (list, tuple)):
        return None
    out = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = _text(item.get("label"))
        if not label:
            continue
        out.append({"label": label, "score": _num(item.get("score")),
                    "box": parse_box(item.get("box"))})
    return out


def _zones(value: Any) -> list[str] | None:
    if not isinstance(value, (list, tuple)):
        return None
    return [z for z in (_text(v) for v in value) if z]


def extract(payload: Any) -> dict | None:
    """Allowlisted metadata for one event payload, or None without an id."""
    if not isinstance(payload, dict):
        return None
    event_id = _text(payload.get("id"))
    if not event_id:
        return None
    data = payload.get("data")
    data = data if isinstance(data, dict) else {}
    top_score = _num(data.get("top_score"))
    if top_score is None:
        top_score = _num(payload.get("top_score"))
    return {
        "event_id": event_id,
        "region": parse_box(data.get("region")),
        "top_score": top_score,
        "average_estimated_speed": _num(data.get("average_estimated_speed")),
        "velocity_angle": _num(data.get("velocity_angle")),
        "path": _path(data.get("path_data")),
        "sub_label": _text(payload.get("sub_label")),
        "attributes": _attributes(data.get("attributes")),
        "zones": _zones(payload.get("zones")),
    }


def contains_media(obj: Any) -> list[str]:
    """Every key or string value (recursively) that matches a media token."""
    hits: list[str] = []

    def _check(text: str) -> bool:
        low = text.lower()
        return any(tok in low for tok in MEDIA_TOKENS)

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                if _check(str(key)):
                    hits.append(str(key))
                _walk(val)
        elif isinstance(node, (list, tuple)):
            for val in node:
                _walk(val)
        elif isinstance(node, str) and _check(node):
            hits.append(node)

    _walk(obj)
    return hits


def scrub_media(obj: Any) -> Any:
    """Drop media-matching keys and blank media-matching strings (API guard)."""
    if isinstance(obj, dict):
        return {k: scrub_media(v) for k, v in obj.items() if not contains_media(str(k))}
    if isinstance(obj, (list, tuple)):
        return [scrub_media(v) for v in obj]
    if isinstance(obj, str) and contains_media(obj):
        return None
    return obj
