"""Infer recurring local service windows from explicitly identified events.

The detector is intentionally conservative.  It consumes only Frigate labels
or sub-labels that already identify a service; it does not turn a generic
``car`` event into a garbage, recycling, mail, or package claim.  Repeated
detections on one calendar day collapse into one visit before cadence is
estimated.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CATEGORY_ALIASES = {
    "garbage": {"garbage_truck", "trash_truck", "refuse_truck", "waste_truck"},
    "recycling": {"recycling_truck", "recycle_truck"},
    "mail": {"mail_truck", "mail_carrier", "postal_truck", "usps_truck"},
    "package": {
        "package_delivery", "delivery_driver", "delivery_truck", "parcel_delivery",
        "amazon_delivery", "fedex_delivery", "ups_delivery",
    },
}


def _token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _category(event: dict) -> tuple[str | None, str | None]:
    for source in (event.get("sub_label"), event.get("label")):
        token = _token(source)
        if not token:
            continue
        for category, aliases in CATEGORY_ALIASES.items():
            if token in aliases:
                return category, token
    return None, None


def _tz(timezone_name: str):
    try:
        return ZoneInfo(timezone_name), None
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc, f"unknown timezone {timezone_name!r}; UTC fallback used"


def detect(
    events: list[dict],
    _config_map: dict | None = None,
    *,
    timezone_name: str = "UTC",
    min_weeks: int = 3,
    min_weekday_share: float = 0.60,
) -> dict:
    tz, timezone_warning = _tz(timezone_name)
    visits: dict[str, dict[object, list[tuple[datetime, str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    categorized_events = 0

    for event in events:
        category, source = _category(event)
        if not category:
            continue
        try:
            timestamp = float(event["start_time"])
            if timestamp <= 0:
                continue
            when = datetime.fromtimestamp(timestamp, tz=tz)
        except (KeyError, TypeError, ValueError, OSError):
            continue
        categorized_events += 1
        visits[category][when.date()].append(
            (when, str(event.get("camera") or "unknown"), source or "")
        )

    schedules = []
    for category in CATEGORY_ALIASES:
        daily = visits.get(category, {})
        # Median of a day's repeated detections is a stable visit representative.
        representatives = []
        for day in sorted(daily):
            entries = daily[day]
            ordered = sorted(entries, key=lambda item: item[0])
            representatives.append(ordered[len(ordered) // 2])

        weekdays = Counter(item[0].weekday() for item in representatives)
        iso_weeks = {(item[0].isocalendar().year, item[0].isocalendar().week)
                     for item in representatives}
        sources = sorted({item[2] for item in representatives})
        cameras = sorted({item[1] for item in representatives})

        row = {
            "category": category,
            "status": "insufficient-evidence",
            "visits": len(representatives),
            "distinct_weeks": len(iso_weeks),
            "sources": sources,
            "cameras": cameras,
            "typical_weekday": None,
            "typical_local_time": None,
            "weekday_share": 0.0,
            "confidence": 0.0,
            "evidence": "No explicitly identified events were observed.",
        }
        if representatives:
            weekday, count = weekdays.most_common(1)[0]
            matching = [item[0] for item in representatives if item[0].weekday() == weekday]
            minute_values = [dt.hour * 60 + dt.minute for dt in matching]
            minute = int(round(median(minute_values)))
            share = count / len(representatives)
            row.update({
                "typical_weekday": ["Monday", "Tuesday", "Wednesday", "Thursday",
                                    "Friday", "Saturday", "Sunday"][weekday],
                "typical_local_time": f"{minute // 60:02d}:{minute % 60:02d}",
                "weekday_share": round(share, 3),
                "confidence": round(share * min(1.0, len(iso_weeks) / 6), 3),
            })
            if len(iso_weeks) >= min_weeks and share >= min_weekday_share:
                row["status"] = "learned"
                row["evidence"] = (
                    f"{len(representatives)} visits across {len(iso_weeks)} weeks; "
                    f"{count}/{len(representatives)} occurred on the modal weekday."
                )
            else:
                row["evidence"] = (
                    f"{len(representatives)} visits across {len(iso_weeks)} weeks; need at "
                    f"least {min_weeks} weeks and {min_weekday_share:.0%} weekday agreement."
                )
        schedules.append(row)

    generic_vehicle_events = sum(
        1 for event in events
        if _token(event.get("label")) in {"car", "truck", "bus", "motorcycle"}
        and _category(event)[0] is None
    )
    return {
        "timezone": timezone_name if timezone_warning is None else "UTC",
        "timezone_warning": timezone_warning,
        "events_examined": len(events),
        "categorized_events": categorized_events,
        "generic_vehicle_events": generic_vehicle_events,
        "schedules": schedules,
    }
