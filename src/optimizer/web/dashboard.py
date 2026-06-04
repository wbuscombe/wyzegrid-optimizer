"""
Read-only Flask dashboard.

Phase 1: shows current findings, recommendations queue, config snapshot,
runs history with cost tracker. No apply / approve buttons (Phase 2).

PHANTOM_MODE=1 boots without any DB / Frigate / API key — serves synthetic
data via `optimizer.phantom`.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from flask import Flask, jsonify, render_template

from .. import config as cfg_module
from .. import db as dbmod

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    cfg = cfg_module.load()
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["OPTIMIZER_CFG"] = cfg

    # ---- view helpers ----

    def latest_run_view() -> dict:
        """Pull the latest run + recommendations + config snapshot, or phantom data."""
        if cfg.phantom_mode:
            from .. import phantom
            findings, recs = phantom.phantom_findings_and_recommendations()
            history = phantom.phantom_history()
            return {
                "phantom": True,
                "run": {"id": 99, "status": "ok (phantom)", "claude_used": True,
                        "claude_cost_usd": 0.0053, "claude_input_tokens": 1840,
                        "claude_output_tokens": 620, "notes": "synthetic data"},
                "findings": findings,
                "recommendations": recs,
                "config_snapshot": {f"{cam}::{label}": v for (cam, label), v in
                                     sorted(phantom.phantom_config_map().items())},
                "history": history,
            }
        # Real mode — open DB
        dbmod.init_db(cfg.db_path)
        conn = dbmod.connect(cfg.db_path)
        try:
            run = dbmod.latest_run(conn)
            run_dict = dict(run) if run else None
            recs = (
                [dict(r) for r in dbmod.recommendations_for_run(conn, run["id"])]
                if run else []
            )
            findings = json.loads(run["findings_json"]) if run and run["findings_json"] else {}
            config_rows = dbmod.latest_config_snapshot(conn)
            config_snapshot = {
                f"{r['camera']}::{r['label']}": {
                    "min_score": r["min_score"], "threshold": r["threshold"],
                    "min_area": r["min_area"],
                    "stationary_max_frames": r["stationary_max_frames"],
                    "zones": json.loads(r["zones_json"] or "[]"),
                }
                for r in config_rows
            }
            history = [dict(r) for r in dbmod.runs_history(conn, 30)]
            return {
                "phantom": False,
                "run": run_dict,
                "findings": findings,
                "recommendations": recs,
                "config_snapshot": config_snapshot,
                "history": history,
            }
        finally:
            conn.close()

    # ---- routes ----

    @app.route("/")
    def index():
        return render_template("index.html", data=latest_run_view(), phantom=cfg.phantom_mode)

    @app.route("/recommendations")
    def recommendations_view():
        return render_template("recommendations.html", data=latest_run_view(), phantom=cfg.phantom_mode)

    @app.route("/trends")
    def trends_view():
        return render_template("trends.html", data=latest_run_view(), phantom=cfg.phantom_mode)

    @app.route("/cost")
    def cost_view():
        return render_template("cost.html", data=latest_run_view(), phantom=cfg.phantom_mode)

    @app.route("/api/latest")
    def latest_json():
        return jsonify(latest_run_view())

    @app.route("/api/health")
    def health():
        return jsonify({"ok": True, "phantom": cfg.phantom_mode})

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    cfg = cfg_module.load()
    app = create_app()
    logger.info("Dashboard starting on %s:%d (phantom=%s)",
                cfg.dashboard_host, cfg.dashboard_port, cfg.phantom_mode)
    app.run(host=cfg.dashboard_host, port=cfg.dashboard_port, debug=False)


if __name__ == "__main__":
    main()
