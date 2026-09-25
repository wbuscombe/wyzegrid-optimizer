"""
Run one full analysis cycle: ingest → snapshot config → analyze → write recs.

Entry point: `python -m optimizer.run_once`
"""
from __future__ import annotations

import json
import logging
import sys
import time

from . import config as cfg_module
from . import db, ingest, ntfy, zma
from .analysis import normalize_config_rows, normalize_events, run_all
from .claude_layer import interpret
from .frigate_client import FrigateClient
from .patterns import stage as pattern_stage

logger = logging.getLogger(__name__)


def run_cycle() -> dict:
    """Execute one cycle. Returns a dict summary suitable for logging/reporting."""
    cfg = cfg_module.load()
    if cfg.phantom_mode:
        logger.info("PHANTOM_MODE=1: skipping live ingest. Use the dashboard for the demo view.")
        return {"phantom": True}

    # Setup lives INSIDE the try so a failure during it (DB init, Frigate client,
    # connect, start_run) still fires the failure alert instead of escaping
    # silently — the gap that hid the 2026-07-05 outage. conn/run_id are guarded
    # in the except because a failure may occur before they are assigned.
    conn = None
    run_id = None
    try:
        db.init_db(cfg.db_path)
        client = FrigateClient(cfg.frigate_url)
        conn = db.connect(cfg.db_path)
        # Additive pattern schema; never raises (False = metadata capture skipped).
        metadata_ready = pattern_stage.prepare(conn)

        started = time.time()
        window_start = (
            db.latest_event_start_time(conn) or (started - 7 * 86400)
        )
        run_id = db.start_run(conn, window_start, started)

        ingested = ingest.ingest_events(
            conn, client,
            cameras=cfg.cameras_filter or None,
            capture_metadata=metadata_ready,
        )
        snapshotted = ingest.snapshot_config(conn, client)

        # Build analysis window (last 24h) and baseline (prior 6d)
        now = time.time()
        recent = db.events_in_window(conn, now - 86400, now)
        baseline = db.events_in_window(conn, now - 7 * 86400, now - 86400)
        schedule_history = db.events_in_window(conn, now - 56 * 86400, now)
        config_rows = db.latest_config_snapshot(conn)
        config_map = normalize_config_rows(config_rows)
        findings = run_all(
            normalize_events(recent),
            config_map,
            history_events=normalize_events(baseline),
            schedule_events=normalize_events(schedule_history),
            timezone_name=cfg.timezone_name,
        )

        result = interpret(
            findings, config_map,
            api_key=cfg.anthropic_api_key,
            model=cfg.model,
            token_budget=cfg.token_budget,
        )

        with db.transaction(conn):
            db.finish_run(
                conn, run_id, status="ok", findings=findings,
                claude_used=result.used,
                claude_input_tokens=result.input_tokens,
                claude_output_tokens=result.output_tokens,
                claude_cost_usd=result.cost_usd,
                notes=result.error,
            )
            db.insert_recommendations(conn, run_id, result.recommendations)

        # Recurring-visit patterns run after the existing analysis is recorded.
        # The stage is failure-isolated and never raises, so it cannot change
        # this run's status, findings, or recommendations.
        if metadata_ready:
            pattern_stage.run_from_config(conn, run_id, cfg)

        zma.post_status(cfg.zma_webhook_url, "run-complete", {
            "run_id": run_id,
            "ingested_events": ingested,
            "config_rows": snapshotted,
            "recommendations": len(result.recommendations),
            "claude_used": result.used,
            "cost_usd": result.cost_usd,
        })

        return {
            "run_id": run_id,
            "ingested": ingested,
            "snapshotted": snapshotted,
            "recommendations": len(result.recommendations),
            "claude_used": result.used,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd,
            "claude_error": result.error,
        }
    except Exception as e:
        # Record the run-error row only if we opened a run; but ALWAYS fire the
        # failure alert — this post_status is what was silent on 2026-07-05
        # (it ran, but ZMA_WEBHOOK_URL was unset in prod so it no-op'd).
        if conn is not None and run_id is not None:
            try:
                with db.transaction(conn):
                    db.finish_run(conn, run_id, status="error", findings={},
                                  claude_used=False, notes=f"{type(e).__name__}: {e}")
            except Exception:
                logger.exception("Failed to record run-error status in DB")
        zma.post_status(cfg.zma_webhook_url, "run-error", {"error": str(e)})
        ntfy.post_alert(
            cfg.ntfy_url, cfg.ntfy_topic, cfg.ntfy_user, cfg.ntfy_pass,
            message=f"Optimizer nightly run failed: {type(e).__name__}: {e}",
            title="wyzegrid-optimizer run-error",
        )
        raise
    finally:
        if conn is not None:
            conn.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        summary = run_cycle()
    except Exception as e:
        logger.error("Cycle failed: %s", e)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
