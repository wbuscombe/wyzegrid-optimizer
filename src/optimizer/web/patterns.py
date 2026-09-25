"""
Read-only recurring-pattern API and dashboard view (contract J).

GET only. Every route reads persisted pattern results and never computes or
writes: the database is opened read-only (SQLite `mode=ro`), so a request
cannot change a row even by accident. Flask answers POST, PUT, PATCH, and
DELETE on these routes with 405. With PHANTOM_MODE=1 the section is computed
from the synthetic generator only, never from a database.

Every pattern carries `cadence_is_estimate: true` and a `limitations` object
(`optimizer.patterns.presentation`). No response carries a media field,
value, or link, and nothing here calls an LLM.

Routes:
  GET /api/patterns                 surfaced patterns of the latest successful run
  GET /api/patterns/status          pattern stage status (kill switch, latest run)
  GET /api/patterns/visits          aggregate visit summary of the latest successful run
  GET /api/patterns/<pattern_key>   one pattern from the latest run that holds it
"""
from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from flask import Flask, jsonify, request

from ..patterns import presentation, store
from ..patterns.settings import BEHAVIORS, DEFAULT_BEHAVIORS

CONFIDENCE_LEVELS = ("moderate", "strong")
PHANTOM_SEED = 2031   # the declared synthetic positive scenario
_TRUE = ("1", "true", "yes", "on")
_EMPTY = {"status": "not_initialized", "latest": None, "ok_run_id": None,
          "window": None, "patterns": [], "summary": None}


def _read_only(db_path: Path) -> Optional[sqlite3.Connection]:
    """A read-only connection, or None when the database file does not exist."""
    path = Path(db_path)
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@lru_cache(maxsize=1)
def _phantom_state() -> dict:
    """The synthetic positive scenario, computed once per process in memory."""
    from ..patterns import synthetic
    from ..patterns.localtime import zoneinfo_local_time
    from ..patterns.pipeline import compute
    from ..patterns.visits import track_from_payload

    tracks = [t for t in (track_from_payload(p) for p in synthetic.generate(PHANTOM_SEED)) if t]
    now = synthetic.span_now()
    result = compute(tracks, now, zoneinfo_local_time(synthetic.TZ_NAME))
    window = {"first_date": result.first_date.isoformat(),
              "last_date": result.last_date.isoformat()}
    latest = {"run_id": None, "status": "ok", "started_at": now, "finished_at": now,
              "message": None}
    return {"status": "ok", "latest": latest, "ok_run_id": None, "window": window,
            "patterns": result.patterns, "summary": result.summary}


def _db_state(db_path: Path) -> dict:
    conn = _read_only(db_path)
    if conn is None:
        return dict(_EMPTY)
    try:
        if not store.tables_present(conn):
            return dict(_EMPTY)
        latest = store.latest_pattern_run(conn)
        if latest is None:
            return dict(_EMPTY, status="no_run")
        ok = store.latest_ok_pattern_run(conn)
        return {
            "status": latest["status"],
            "latest": {k: latest[k]
                       for k in ("run_id", "status", "started_at", "finished_at", "message")},
            "ok_run_id": ok["run_id"] if ok else None,
            "window": ({"first_date": ok["window_first_date"],
                        "last_date": ok["window_last_date"]} if ok else None),
            "patterns": store.patterns_for_run(conn, ok["run_id"]) if ok else [],
            "summary": json.loads(ok["summary_json"]) if ok and ok["summary_json"] else None,
        }
    finally:
        conn.close()


def read_state(cfg) -> dict:
    """Everything the pattern routes serve, read in one pass."""
    if not getattr(cfg, "patterns_enabled", True):
        return dict(_EMPTY, status="disabled")
    if cfg.phantom_mode:
        return _phantom_state()
    return _db_state(cfg.db_path)


def _visible(pattern: dict, include_pass_through: bool) -> bool:
    return include_pass_through or pattern["behavior"] in DEFAULT_BEHAVIORS


def select(patterns: list[dict], *, camera: Optional[str] = None,
           label_group: Optional[str] = None, behavior: Optional[str] = None,
           min_confidence: str = "moderate", include_pass_through: bool = False) -> list[dict]:
    """Filter stored patterns. pass_through stays hidden unless requested, either
    with include_pass_through or by asking for behavior=pass_through."""
    floor = CONFIDENCE_LEVELS.index(min_confidence)
    show_pass = include_pass_through or behavior == "pass_through"
    return [
        p for p in patterns
        if _visible(p, show_pass)
        and (camera is None or p["site"] == camera)
        and (label_group is None or p["label_group"] == label_group)
        and (behavior is None or p["behavior"] == behavior)
        and CONFIDENCE_LEVELS.index(p["confidence"]) >= floor
    ]


def dashboard_view(cfg) -> dict:
    """The dashboard section's data: default-visible rows with estimate copy."""
    snap = read_state(cfg)
    rows = []
    for p in select(snap["patterns"]):
        row = presentation.present(p)
        row["estimated_cadence"] = presentation.estimated_cadence(p["cadence"])
        rows.append(row)
    latest = snap["latest"] or {}
    return {
        "enabled": snap["status"] != "disabled",
        "status": snap["status"],
        "message": latest.get("message"),
        "run_id": snap["ok_run_id"],
        "window": snap["window"],
        "rows": rows,
        "hidden_pass_through": sum(1 for p in snap["patterns"]
                                   if p["behavior"] not in DEFAULT_BEHAVIORS),
    }


def _bad_request(message: str):
    return jsonify({"error": message}), 400


def register(app: Flask, cfg) -> None:
    """Attach the GET-only pattern routes to the dashboard app."""

    @app.route("/api/patterns", methods=["GET"])
    def patterns_list():
        args = request.args
        behavior = args.get("behavior") or None
        if behavior is not None and behavior not in BEHAVIORS:
            return _bad_request(f"behavior must be one of {', '.join(BEHAVIORS)}")
        min_confidence = args.get("min_confidence") or "moderate"
        if min_confidence not in CONFIDENCE_LEVELS:
            return _bad_request("min_confidence must be moderate or strong")
        filters = {
            "camera": args.get("camera") or None,
            "label_group": args.get("label_group") or None,
            "behavior": behavior,
            "min_confidence": min_confidence,
            "include_pass_through": args.get("include_pass_through", "").lower() in _TRUE,
        }
        snap = read_state(cfg)
        rows = [presentation.present(p) for p in select(snap["patterns"], **filters)]
        return jsonify({
            "enabled": snap["status"] != "disabled",
            "status": snap["status"],
            "run_id": snap["ok_run_id"],
            "analysis_window": snap["window"],
            "filters": filters,
            "count": len(rows),
            "patterns": rows,
        })

    @app.route("/api/patterns/status", methods=["GET"])
    def patterns_status():
        snap = read_state(cfg)
        latest = snap["latest"] or {}
        return jsonify({
            "enabled": snap["status"] != "disabled",
            "status": snap["status"],
            "run_id": latest.get("run_id"),
            "started_at": latest.get("started_at"),
            "finished_at": latest.get("finished_at"),
            "message": latest.get("message"),
            "latest_ok_run_id": snap["ok_run_id"],
            "pattern_count": len(select(snap["patterns"])),
            "pattern_count_all": len(snap["patterns"]),
        })

    @app.route("/api/patterns/visits", methods=["GET"])
    def patterns_visits():
        snap = read_state(cfg)
        return jsonify({
            "enabled": snap["status"] != "disabled",
            "status": snap["status"],
            "run_id": snap["ok_run_id"],
            "summary": snap["summary"],
        })

    @app.route("/api/patterns/<pattern_key>", methods=["GET"])
    def pattern_detail(pattern_key: str):
        if not getattr(cfg, "patterns_enabled", True):
            return jsonify({"enabled": False, "status": "disabled",
                            "error": "pattern analysis is disabled"}), 404
        if cfg.phantom_mode:
            found = _phantom_pattern(pattern_key)
        else:
            found = _db_pattern(cfg.db_path, pattern_key)
        if found is None:
            return jsonify({"enabled": True, "error": "unknown pattern_key"}), 404
        return jsonify({"enabled": True, "pattern": presentation.present(found)})


def _phantom_pattern(pattern_key: str) -> Optional[dict]:
    for p in _phantom_state()["patterns"]:
        if p["pattern_key"] == pattern_key:
            return dict(p, run_id=None)
    return None


def _db_pattern(db_path: Path, pattern_key: str) -> Optional[dict]:
    """The pattern from the latest run that holds `pattern_key`, or None."""
    conn = _read_only(db_path)
    if conn is None:
        return None
    try:
        if not store.tables_present(conn):
            return None
        return store.latest_pattern_by_key(conn, pattern_key)
    finally:
        conn.close()
