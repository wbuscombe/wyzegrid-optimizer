"""Tests for the scheduler's Kometa-window backoff and the run-once main loop wiring."""
from __future__ import annotations

from datetime import datetime

from optimizer import scheduler


def test_in_kometa_window_3am_true():
    assert scheduler.in_kometa_window(datetime(2026, 6, 4, 3, 30)) is True


def test_in_kometa_window_6_59am_true():
    assert scheduler.in_kometa_window(datetime(2026, 6, 4, 6, 59)) is True


def test_in_kometa_window_7am_false():
    assert scheduler.in_kometa_window(datetime(2026, 6, 4, 7, 0)) is False


def test_in_kometa_window_evening_false():
    assert scheduler.in_kometa_window(datetime(2026, 6, 4, 19, 0)) is False


def test_in_kometa_window_midnight_false():
    assert scheduler.in_kometa_window(datetime(2026, 6, 4, 0, 0)) is False


# ---- Wall-clock anchor: stops the nightly-run drift (Deferred #3) -----------

def test_next_run_at_returns_today_when_slot_ahead():
    now = datetime(2026, 6, 4, 1, 0)   # 01:00, before the 02:00 anchor
    assert scheduler._next_run_at(2, 0, now) == datetime(2026, 6, 4, 2, 0)


def test_next_run_at_rolls_to_tomorrow_when_slot_passed():
    now = datetime(2026, 6, 4, 9, 38)  # after 02:00 — the drifted time
    assert scheduler._next_run_at(2, 0, now) == datetime(2026, 6, 5, 2, 0)


def test_next_run_at_stable_across_restart_times():
    """The drift bug: restarts at different times shifted the run clock
    (11:11 -> 09:38). A wall-clock anchor yields the SAME next run regardless of
    when the process happens to (re)start."""
    early = scheduler._next_run_at(2, 0, datetime(2026, 6, 4, 9, 38))
    late = scheduler._next_run_at(2, 0, datetime(2026, 6, 4, 11, 11))
    assert early == late == datetime(2026, 6, 5, 2, 0)


def test_next_run_at_exactly_on_slot_rolls_forward():
    # now == the slot exactly: take tomorrow, never a zero-length wait/double-fire.
    now = datetime(2026, 6, 4, 2, 0)
    assert scheduler._next_run_at(2, 0, now) == datetime(2026, 6, 5, 2, 0)


def test_next_run_at_clamps_out_of_range():
    now = datetime(2026, 6, 4, 1, 0)
    r = scheduler._next_run_at(25, 70, now)   # nonsense config
    assert r.hour == 23 and r.minute == 59    # clamped, no crash
