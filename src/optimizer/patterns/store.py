"""
Persistence for event metadata and recurring-pattern results.

Additive only: every statement in `SCHEMA` is CREATE TABLE IF NOT EXISTS or
CREATE INDEX IF NOT EXISTS on a NEW table, so applying it twice is a no-op
and no pre-existing table is ever altered. Nothing here updates or deletes a
row of a pre-existing table. Pattern results are append-only per run.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Iterator, Optional

# Tables that existed before this module. Never written by it.
PRE_EXISTING_TABLES = ("events", "config_snapshots", "analysis_runs", "recommendations")
NEW_TABLES = ("event_metadata", "pattern_runs", "pattern_results")

SCHEMA = """
CREATE TABLE IF NOT EXISTS event_metadata (
  event_id                TEXT PRIMARY KEY,
  region_x                REAL,
  region_y                REAL,
  region_w                REAL,
  region_h                REAL,
  top_score               REAL,
  average_estimated_speed REAL,
  velocity_angle          REAL,
  path_json               TEXT,
  sub_label               TEXT,
  attributes_json         TEXT,
  zones_json              TEXT,
  updated_at              REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pattern_runs (
  run_id            INTEGER PRIMARY KEY,
  status            TEXT NOT NULL,
  started_at        REAL NOT NULL,
  finished_at       REAL,
  message           TEXT,
  window_first_date TEXT,
  window_last_date  TEXT,
  summary_json      TEXT
);

CREATE TABLE IF NOT EXISTS pattern_results (
  run_id            INTEGER NOT NULL,
  pattern_key       TEXT NOT NULL,
  site              TEXT NOT NULL,
  label_group       TEXT NOT NULL,
  behavior          TEXT NOT NULL,
  cadence           TEXT NOT NULL,
  weekdays_json     TEXT NOT NULL,
  parity            INTEGER,
  window_median_min REAL,
  support_k         INTEGER,
  covered_n         INTEGER,
  hit_rate          REAL,
  q_value           REAL,
  confidence        TEXT NOT NULL,
  identity          TEXT,
  identity_reason   TEXT,
  detail_json       TEXT NOT NULL,
  created_at        REAL NOT NULL,
  PRIMARY KEY (run_id, pattern_key)
);
CREATE INDEX IF NOT EXISTS idx_pattern_results_key ON pattern_results(pattern_key);
"""


def apply_schema(conn: sqlite3.Connection) -> None:
    """Idempotent additive migration."""
    conn.executescript(SCHEMA)


def tables_present(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?)", NEW_TABLES
    ).fetchall()
    return len(rows) == len(NEW_TABLES)


# ---- event metadata ---------------------------------------------------------

def upsert_event_metadata(conn: sqlite3.Connection, meta: Optional[dict]) -> bool:
    """Insert or refresh one event's metadata. A fresher payload may update the
    row; a field missing from the fresher payload keeps its stored value."""
    if not meta or not meta.get("event_id"):
        return False
    region = meta.get("region") or [None, None, None, None]
    conn.execute(
        """
        INSERT INTO event_metadata (
          event_id, region_x, region_y, region_w, region_h, top_score,
          average_estimated_speed, velocity_angle, path_json, sub_label,
          attributes_json, zones_json, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(event_id) DO UPDATE SET
          region_x = COALESCE(excluded.region_x, event_metadata.region_x),
          region_y = COALESCE(excluded.region_y, event_metadata.region_y),
          region_w = COALESCE(excluded.region_w, event_metadata.region_w),
          region_h = COALESCE(excluded.region_h, event_metadata.region_h),
          top_score = COALESCE(excluded.top_score, event_metadata.top_score),
          average_estimated_speed =
            COALESCE(excluded.average_estimated_speed, event_metadata.average_estimated_speed),
          velocity_angle = COALESCE(excluded.velocity_angle, event_metadata.velocity_angle),
          path_json = COALESCE(excluded.path_json, event_metadata.path_json),
          sub_label = COALESCE(excluded.sub_label, event_metadata.sub_label),
          attributes_json = COALESCE(excluded.attributes_json, event_metadata.attributes_json),
          zones_json = COALESCE(excluded.zones_json, event_metadata.zones_json),
          updated_at = excluded.updated_at
        """,
        (
            meta["event_id"],
            region[0], region[1], region[2], region[3],
            meta.get("top_score"),
            meta.get("average_estimated_speed"),
            meta.get("velocity_angle"),
            _path_json(meta.get("path")),
            meta.get("sub_label"),
            json.dumps(meta["attributes"]) if meta.get("attributes") is not None else None,
            json.dumps(meta["zones"]) if meta.get("zones") is not None else None,
            time.time(),
        ),
    )
    return True


def _path_json(path: Optional[list]) -> Optional[str]:
    if not path:
        return None
    return json.dumps([[round(x, 4), round(y, 4), round(t, 3)] for x, y, t in path],
                      separators=(",", ":"))


def iter_pattern_rows(
    conn: sqlite3.Connection, start_epoch: float, end_epoch: float
) -> Iterator[sqlite3.Row]:
    """Events (any label) joined with their metadata, streamed row by row."""
    cur = conn.execute(
        """
        SELECT e.id, e.camera, e.label, e.sub_label, e.start_time, e.end_time,
               e.box_x, e.box_y, e.box_w, e.box_h,
               m.path_json, m.average_estimated_speed, m.velocity_angle,
               m.attributes_json, m.sub_label AS meta_sub_label
        FROM events e LEFT JOIN event_metadata m ON m.event_id = e.id
        WHERE e.start_time >= ? AND e.start_time <= ?
        ORDER BY e.start_time, e.id
        """,
        (start_epoch, end_epoch),
    )
    yield from cur


# ---- pattern results (append-only) -------------------------------------------

def insert_pattern_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    started_at: float,
    finished_at: float,
    message: Optional[str] = None,
    window_first_date: Optional[str] = None,
    window_last_date: Optional[str] = None,
    summary: Optional[dict] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO pattern_runs (
          run_id, status, started_at, finished_at, message,
          window_first_date, window_last_date, summary_json
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (run_id, status, started_at, finished_at, message, window_first_date,
         window_last_date, json.dumps(summary) if summary is not None else None),
    )


def insert_pattern_results(conn: sqlite3.Connection, run_id: int, patterns: list[dict]) -> None:
    now = time.time()
    conn.executemany(
        """
        INSERT INTO pattern_results (
          run_id, pattern_key, site, label_group, behavior, cadence, weekdays_json,
          parity, window_median_min, support_k, covered_n, hit_rate, q_value,
          confidence, identity, identity_reason, detail_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                run_id, p["pattern_key"], p["site"], p["label_group"], p["behavior"],
                p["cadence"], json.dumps(p["weekdays"]), p.get("parity"),
                p["window"]["median_min"], p["support"]["k"], p["support"]["n"],
                p["hit_rate"], p["q"], p["confidence"], p.get("identity"),
                p.get("identity_reason"), json.dumps(p), now,
            )
            for p in patterns
        ],
    )


# ---- reads (GET only; never compute, never write) ------------------------------

def latest_pattern_run(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pattern_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()


def latest_ok_pattern_run(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pattern_runs WHERE status='ok' ORDER BY run_id DESC LIMIT 1"
    ).fetchone()


def patterns_for_run(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT detail_json FROM pattern_results WHERE run_id=? ORDER BY pattern_key",
        (run_id,),
    ).fetchall()
    return [json.loads(r["detail_json"]) for r in rows]


def latest_pattern_by_key(conn: sqlite3.Connection, pattern_key: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT run_id, detail_json FROM pattern_results WHERE pattern_key=? "
        "ORDER BY run_id DESC LIMIT 1",
        (pattern_key,),
    ).fetchone()
    if not row:
        return None
    out = json.loads(row["detail_json"])
    out["run_id"] = row["run_id"]
    return out
