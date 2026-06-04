"""
Long-running scheduler. Wraps `run_once.run_cycle` in a sleep loop with a
Kometa-window backoff.

Entry point: `python -m optimizer.scheduler`
"""
from __future__ import annotations

import logging
import signal
import time
from datetime import datetime

from . import config as cfg_module
from .run_once import run_cycle

logger = logging.getLogger(__name__)


KOMETA_WINDOW_START_HOUR = 3
KOMETA_WINDOW_END_HOUR = 7


def in_kometa_window(now: datetime | None = None) -> bool:
    """Local-time Kometa hold window. Be a good neighbor — skip cycles here."""
    now = now or datetime.now()
    return KOMETA_WINDOW_START_HOUR <= now.hour < KOMETA_WINDOW_END_HOUR


def _wait_until_next_eligible(interval: int) -> None:
    """Sleep `interval` seconds, waking earlier if a signal arrives."""
    end = time.time() + interval
    while time.time() < end:
        remaining = end - time.time()
        time.sleep(min(60, remaining))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cfg = cfg_module.load()
    if cfg.phantom_mode:
        logger.info("PHANTOM_MODE=1: scheduler exits immediately. Use the dashboard for demo.")
        return

    stopping = False

    def _stop(signum, frame):  # noqa: ARG001
        nonlocal stopping
        stopping = True
        logger.info("Received signal %d — finishing current cycle then exiting", signum)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    logger.info("Scheduler started (interval=%ds, kometa window %d-%d local)",
                cfg.interval_seconds, KOMETA_WINDOW_START_HOUR, KOMETA_WINDOW_END_HOUR)

    while not stopping:
        if in_kometa_window():
            logger.info("In Kometa window (hour=%d) — skipping cycle, sleeping 30 min",
                        datetime.now().hour)
            _wait_until_next_eligible(1800)
            continue

        try:
            summary = run_cycle()
            logger.info("Cycle complete: %s", summary)
        except Exception as e:
            logger.error("Cycle failed: %s: %s", type(e).__name__, e)

        if stopping:
            break
        _wait_until_next_eligible(cfg.interval_seconds)

    logger.info("Scheduler stopped cleanly")


if __name__ == "__main__":
    main()
