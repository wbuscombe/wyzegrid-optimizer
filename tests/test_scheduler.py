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
