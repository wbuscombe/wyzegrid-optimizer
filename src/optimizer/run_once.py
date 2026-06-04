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
from . import db, ingest, zma
from .analysis import normalize_config_rows, normalize_events, run_all
from .claude_layer import interpret
from .frigate_client import FrigateClient

logger = logging.getLogger(__name__)


def run_cycle() -> dict:
    """Execute one cycle. Returns a dict summary suitable for logging/reporting."""
    cfg = cfg_module.load()
    if cfg.phantom_mode:
        logger.info("PHANTOM_MODE=1: skipping live ingest. Use the dashboard for the demo view.")
        return {"phantom": True}

    db.init_db(cfg.db_path)
    client = FrigateClient(cfg.frigate_url)
    conn = db.connect(cfg.db_path)

    started = time.time()
    window_start = (
        db.latest_event_start_time(conn) or (started - 7 * 86400)
    )
    run_id = db.start_run(conn, window_start, started)

    try:
        ingested = ingest.ingest_events(
            conn, client,
            cameras=cfg.cameras_filter or None,
        )
        snapshotted = ingest.snapshot_config(conn, client)

        # Build analysis window (last 24h) and baseline (prior 6d)
        now = time.time()
        recent = db.events_in_window(conn, now - 86400, now)
        baseline = db.events_in_window(conn, now - 7 * 86400, now - 86400)
        config_rows = db.latest_config_snapshot(conn)
        config_map = normalize_config_rows(config_rows)
        findings = run_all(
            normalize_events(recent),
            config_map,
            history_events=normalize_events(baseline),
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
        with db.transaction(conn):
            db.finish_run(conn, run_id, status="error", findings={}, claude_used=False,
                          notes=f"{type(e).__name__}: {e}")
        zma.post_status(cfg.zma_webhook_url, "run-error", {"error": str(e)})
        raise
    finally:
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
