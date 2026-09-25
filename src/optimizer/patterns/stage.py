"""
Run integration for the recurring-pattern stage (contract section I).

The stage runs after the existing analysis has been recorded. It is
failure-isolated: any exception becomes a `pattern_runs` row with status
`error` and a sanitized message, and it never raises into the run cycle, so
the run's status, findings, and recommendations are unchanged whatever
happens here. With the kill switch off the stage does nothing at all.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

from . import store
from .localtime import LocalTimeFn, process_local_time
from .pipeline import compute, load_start_epoch
from .settings import DEFAULT_PARAMS, PatternParams
from .visits import track_from_row

logger = logging.getLogger(__name__)

_MESSAGE_MAX = 160


def sanitize(exc: BaseException) -> str:
    """Exception class plus a short, single-line, printable message."""
    text = str(exc).splitlines()[0] if str(exc) else ""
    text = re.sub(r"[^\x20-\x7e]", "", text)[:_MESSAGE_MAX]
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def params_from_config(cfg) -> PatternParams:
    """Apply the environment-overridable settings from optimizer.config.Config."""
    return PatternParams(
        identity_label_map=dict(getattr(cfg, "pattern_identity_label_map", {}) or {}),
        site_groups=dict(getattr(cfg, "pattern_site_groups", {}) or {}),
    )


def prepare(conn) -> bool:
    """Apply the additive schema. Never raises; False means capture is skipped."""
    try:
        store.apply_schema(conn)
        return True
    except Exception as exc:  # isolation: the existing run must not notice
        logger.warning("pattern schema unavailable: %s", sanitize(exc))
        return False


def run_from_config(conn, run_id: int, cfg) -> dict:
    """The production entry point used by run_once. Never raises."""
    try:
        enabled = bool(getattr(cfg, "patterns_enabled", True))
        params = params_from_config(cfg)
    except Exception as exc:
        logger.warning("pattern stage settings unusable: %s", sanitize(exc))
        return {"status": "error", "message": sanitize(exc)}
    return run_stage(conn, run_id, enabled=enabled, params=params)


def run_stage(
    conn,
    run_id: int,
    *,
    enabled: bool = True,
    params: PatternParams = DEFAULT_PARAMS,
    now: Optional[float] = None,
    local_time: LocalTimeFn = process_local_time,
) -> dict:
    """Compute and persist this run's patterns. Never raises."""
    if not enabled:
        logger.info("pattern stage disabled by kill switch")
        return {"status": "disabled"}
    started = time.time()
    now = started if now is None else now
    try:
        rows = store.iter_pattern_rows(conn, load_start_epoch(now, params), now)
        result = compute((track_from_row(r, params) for r in rows), now, local_time, params)
        conn.execute("BEGIN")
        try:
            store.insert_pattern_run(
                conn, run_id, "ok", started, time.time(),
                window_first_date=result.first_date.isoformat(),
                window_last_date=result.last_date.isoformat(),
                summary=result.summary,
            )
            store.insert_pattern_results(conn, run_id, result.patterns)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        logger.info("pattern stage: %d patterns surfaced", len(result.patterns))
        return {"status": "ok", "patterns": len(result.patterns)}
    except Exception as exc:
        message = sanitize(exc)
        logger.warning("pattern stage failed: %s", message)
        try:
            store.insert_pattern_run(conn, run_id, "error", started, time.time(),
                                     message=message)
        except Exception as inner:
            logger.warning("pattern stage could not record its error: %s", sanitize(inner))
        return {"status": "error", "message": message}
