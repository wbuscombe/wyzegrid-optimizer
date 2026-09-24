from __future__ import annotations

from datetime import datetime, timedelta, timezone

from optimizer.analysis import object_identity, service_schedule
from optimizer.analysis.normalize import normalize_events


def _event(eid: str, when: datetime, *, label="truck", sub_label=None, camera="front_yard"):
    return {
        "id": eid,
        "camera": camera,
        "label": label,
        "sub_label": sub_label,
        "score": 0.9,
        "start_time": when.timestamp(),
        "end_time": None,
        "box": None,
        "false_positive": False,
    }


def test_object_identity_keeps_base_and_upstream_sub_labels():
    now = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)
    events = [
        _event("g", now, sub_label="garbage_truck"),
        _event("v", now + timedelta(hours=1), label="car"),
        _event("p", now + timedelta(hours=2), label="person", sub_label="mail_carrier"),
    ]
    result = object_identity.detect(events)
    assert result["events"] == 3
    assert result["sub_labeled_events"] == 2
    assert result["unresolved_vehicle_events"] == 1
    assert result["sub_labels"] == {"garbage_truck": 1, "mail_carrier": 1}


def test_service_schedule_learns_repeated_weekday_and_collapses_same_day_events():
    # Four Tuesdays at about 14:30 UTC. Two detections on the first day are one visit.
    first = datetime(2026, 8, 4, 14, 30, tzinfo=timezone.utc)
    events = []
    for week in range(4):
        when = first + timedelta(days=7 * week, minutes=week * 2)
        events.append(_event(f"g{week}", when, sub_label="garbage_truck"))
    events.append(_event("g0-repeat", first + timedelta(minutes=4), sub_label="garbage_truck"))
    result = service_schedule.detect(events, timezone_name="UTC")
    garbage = next(row for row in result["schedules"] if row["category"] == "garbage")
    assert garbage["status"] == "learned"
    assert garbage["visits"] == 4
    assert garbage["distinct_weeks"] == 4
    assert garbage["typical_weekday"] == "Tuesday"
    assert garbage["typical_local_time"] == "14:34"
    assert garbage["weekday_share"] == 1.0


def test_generic_vehicle_is_never_guessed_and_sparse_service_stays_unlearned():
    first = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    events = [
        _event("generic", first, label="truck"),
        _event("mail", first + timedelta(days=2), label="person", sub_label="mail_carrier"),
    ]
    result = service_schedule.detect(events, timezone_name="UTC")
    mail = next(row for row in result["schedules"] if row["category"] == "mail")
    assert result["generic_vehicle_events"] == 1
    assert result["categorized_events"] == 1
    assert mail["status"] == "insufficient-evidence"
    assert mail["visits"] == 1


def test_unknown_timezone_falls_back_to_utc_with_visible_warning():
    result = service_schedule.detect([], timezone_name="Not/A_Real_Zone")
    assert result["timezone"] == "UTC"
    assert "UTC fallback" in result["timezone_warning"]


def test_normalize_events_preserves_sub_label_and_zones():
    row = {
        "id": "x", "camera": "front", "label": "truck",
        "sub_label": "recycling_truck", "score": 0.8,
        "start_time": 1.0, "end_time": None,
        "zones_json": '["street"]', "box_x": None, "box_y": None,
        "box_w": None, "box_h": None, "false_positive": 0,
    }
    normalized = normalize_events([row])[0]
    assert normalized["sub_label"] == "recycling_truck"
    assert normalized["zones"] == ["street"]
