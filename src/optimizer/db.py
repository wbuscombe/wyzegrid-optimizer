"""
SQLite layer — no ORM, just sqlite3 with explicit schema management.

Schema is idempotent: `init_db()` is safe to call on every startup. Migrations,
if any are ever needed, will live here as version-numbered upgrade steps.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id              TEXT PRIMARY KEY,
  camera          TEXT NOT NULL,
  label           TEXT NOT NULL,
  sub_label       TEXT,
  score           REAL,
  start_time      REAL NOT NULL,
  end_time        REAL,
  zones_json      TEXT,
  box_x           REAL,
  box_y           REAL,
  box_w           REAL,
  box_h           REAL,
  ratio           REAL,
  has_clip        INTEGER DEFAULT 0,
  has_snapshot    INTEGER DEFAULT 0,
  false_positive  INTEGER DEFAULT 0,
  ingested_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_camera_label ON events(camera, label);
CREATE INDEX IF NOT EXISTS idx_events_start_time   ON events(start_time);

CREATE TABLE IF NOT EXISTS config_snapshots (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_at           REAL NOT NULL,
  camera                TEXT NOT NULL,
  label                 TEXT NOT NULL,
  min_score             REAL,
  threshold             REAL,
  min_area              INTEGER,
  stationary_max_frames INTEGER,
  zones_json            TEXT,
  source_hash           TEXT
);
CREATE INDEX IF NOT EXISTS idx_config_snapshots_at ON config_snapshots(snapshot_at);

CREATE TABLE IF NOT EXISTS analysis_runs (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at           REAL NOT NULL,
  finished_at          REAL,
  status               TEXT NOT NULL,
  events_window_start  REAL,
  events_window_end    REAL,
  findings_json        TEXT,
  claude_used          INTEGER DEFAULT 0,
  claude_input_tokens  INTEGER,
  claude_output_tokens INTEGER,
  claude_cost_usd      REAL,
  notes                TEXT
);

CREATE TABLE IF NOT EXISTS recommendations (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id          INTEGER NOT NULL,
  camera          TEXT,
  label           TEXT,
  param           TEXT,
  current_value   TEXT,
  proposed_value  TEXT,
  rationale       TEXT,
  risk_class      TEXT NOT NULL,
  confidence      REAL,
  expected_effect TEXT,
  FOREIGN KEY (run_id) REFERENCES analysis_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_recs_run ON recommendations(run_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with sensible defaults for this service."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> None:
    """Idempotent schema creation."""
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN / COMMIT / ROLLBACK helper for batch writes."""
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---- writes ----

def upsert_event(conn: sqlite3.Connection, e: dict) -> None:
    """Insert or update one event. Idempotent on event id."""
    box = (e.get("data") or {}).get("box") or [None, None, None, None]
    if not isinstance(box, list) or len(box) != 4:
        box = [None, None, None, None]
    conn.execute(
        """
        INSERT INTO events (
          id, camera, label, sub_label, score, start_time, end_time,
          zones_json, box_x, box_y, box_w, box_h, ratio,
          has_clip, has_snapshot, false_positive, ingested_at
        ) VALUES (?,?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          end_time=excluded.end_time,
          score=excluded.score,
          has_clip=excluded.has_clip,
          has_snapshot=excluded.has_snapshot,
          false_positive=excluded.false_positive
        """,
        (
            e["id"],
            e["camera"],
            e["label"],
            (e.get("sub_label") or [None])[0] if isinstance(e.get("sub_label"), list) else e.get("sub_label"),
            float((e.get("data") or {}).get("score") or e.get("top_score") or 0.0),
            float(e["start_time"]),
            float(e["end_time"]) if e.get("end_time") else None,
            json.dumps(e.get("zones") or []),
            box[0], box[1], box[2], box[3],
            float((e.get("data") or {}).get("ratio") or 0.0) or None,
            1 if e.get("has_clip") else 0,
            1 if e.get("has_snapshot") else 0,
            1 if e.get("false_positive") else 0,
            time.time(),
        ),
    )


def insert_config_snapshots(
    conn: sqlite3.Connection, snapshot_at: float, rows: list[dict], source_hash: str
) -> None:
    """Each row is one (camera, label, params) tuple from /api/config."""
    conn.executemany(
        """
        INSERT INTO config_snapshots (
          snapshot_at, camera, label, min_score, threshold, min_area,
          stationary_max_frames, zones_json, source_hash
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                snapshot_at,
                r["camera"],
                r["label"],
                r.get("min_score"),
                r.get("threshold"),
                r.get("min_area"),
                r.get("stationary_max_frames"),
                json.dumps(r.get("zones") or []),
                source_hash,
            )
            for r in rows
        ],
    )


def start_run(conn: sqlite3.Connection, window_start: Optional[float], window_end: Optional[float]) -> int:
    cur = conn.execute(
        "INSERT INTO analysis_runs (started_at, status, events_window_start, events_window_end) "
        "VALUES (?, 'running', ?, ?)",
        (time.time(), window_start, window_end),
    )
    return cur.lastrowid or 0


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    findings: dict,
    claude_used: bool,
    claude_input_tokens: Optional[int] = None,
    claude_output_tokens: Optional[int] = None,
    claude_cost_usd: Optional[float] = None,
    notes: Optional[str] = None,
) -> None:
    conn.execute(
        """
        UPDATE analysis_runs
        SET finished_at=?, status=?, findings_json=?, claude_used=?,
            claude_input_tokens=?, claude_output_tokens=?, claude_cost_usd=?, notes=?
        WHERE id=?
        """,
        (
            time.time(),
            status,
            json.dumps(findings),
            1 if claude_used else 0,
            claude_input_tokens,
            claude_output_tokens,
            claude_cost_usd,
            notes,
            run_id,
        ),
    )


def insert_recommendations(conn: sqlite3.Connection, run_id: int, recs: list[dict]) -> None:
    if not recs:
        return
    conn.executemany(
        """
        INSERT INTO recommendations (
          run_id, camera, label, param, current_value, proposed_value,
          rationale, risk_class, confidence, expected_effect
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                run_id,
                r.get("camera"),
                r.get("label"),
                r.get("param"),
                str(r.get("current_value")) if r.get("current_value") is not None else None,
                str(r.get("proposed_value")) if r.get("proposed_value") is not None else None,
                r.get("rationale"),
                r.get("risk_class", "safe"),
                float(r.get("confidence") or 0.0),
                r.get("expected_effect"),
            )
            for r in recs
        ],
    )


# ---- reads ----

def latest_event_start_time(conn: sqlite3.Connection) -> Optional[float]:
    """Cursor for incremental ingest — start_time of the newest event we already have."""
    row = conn.execute("SELECT MAX(start_time) AS t FROM events").fetchone()
    return row["t"] if row and row["t"] is not None else None


def events_in_window(
    conn: sqlite3.Connection, window_start: float, window_end: float
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM events WHERE start_time BETWEEN ? AND ? ORDER BY start_time DESC",
            (window_start, window_end),
        )
    )


def latest_config_snapshot(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    row = conn.execute("SELECT MAX(snapshot_at) AS t FROM config_snapshots").fetchone()
    if not row or row["t"] is None:
        return []
    return list(
        conn.execute(
            "SELECT * FROM config_snapshots WHERE snapshot_at=? ORDER BY camera, label",
            (row["t"],),
        )
    )


def latest_run(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM analysis_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()


def recommendations_for_run(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM recommendations WHERE run_id=? ORDER BY risk_class, camera, label, param",
            (run_id,),
        )
    )


def runs_history(conn: sqlite3.Connection, limit: int = 30) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM analysis_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
    )
