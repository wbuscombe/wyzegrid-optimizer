"""F1 persistence: additive migration, tolerant extraction, media exclusion, upsert (P-1..P-7)."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from optimizer import db, ingest
from optimizer.patterns import metadata, store

SRC = Path(__file__).resolve().parents[1] / "src" / "optimizer"
NEW_SQL_MODULES = sorted((SRC / "patterns").glob("*.py"))


def _conn(tmp_path) -> sqlite3.Connection:
    path = tmp_path / "t.db"
    db.init_db(path)
    return db.connect(path)


def _schema_snapshot(conn, tables) -> dict:
    snap = {}
    for name in tables:
        master = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE tbl_name=? ORDER BY name",
            (name,),
        ).fetchall()
        info = conn.execute(f"PRAGMA table_info({name})").fetchall()
        idx = conn.execute(f"PRAGMA index_list({name})").fetchall()
        rows = conn.execute(f"SELECT * FROM {name}").fetchall()
        snap[name] = ([tuple(r) for r in master], [tuple(r) for r in info],
                      [tuple(r) for r in idx], [tuple(r) for r in rows])
    return snap


def _payload(eid="1717.1-abc", **over) -> dict:
    base = {
        "id": eid, "camera": "cam_street", "label": "car", "sub_label": None,
        "start_time": 1_930_000_000.0, "end_time": 1_930_000_030.0, "zones": ["lane"],
        "top_score": None,
        "data": {
            "box": [0.4, 0.5, 0.1, 0.1], "region": [0.3, 0.4, 0.3, 0.3],
            "score": 0.8, "top_score": 0.85, "attributes": [],
            "average_estimated_speed": 0.0, "velocity_angle": 0.0, "type": "object",
            "path_data": [[[0.1, 0.6], 1_930_000_000.0], [[0.2, 0.6], 1_930_000_001.0]],
        },
    }
    base.update(over)
    return base


def test_p1_migration_ddl_is_additive_only():
    statements = [s.strip() for s in store.SCHEMA.split(";") if s.strip()]
    assert statements
    for stmt in statements:
        m = re.match(r"CREATE (TABLE|INDEX) IF NOT EXISTS (\w+)(?:\s+ON\s+(\w+))?", stmt)
        assert m, f"non-additive statement: {stmt[:60]}"
        target = m.group(2) if m.group(1) == "TABLE" else m.group(3)
        assert target in store.NEW_TABLES
        assert target not in store.PRE_EXISTING_TABLES
    assert not re.search(r"\b(ALTER|DROP|DELETE|UPDATE)\b", store.SCHEMA)

    pre = "|".join(store.PRE_EXISTING_TABLES)
    forbidden = [
        r"\bALTER\s+TABLE\b", r"\bDROP\s+(TABLE|INDEX|VIEW|TRIGGER)\b", r"\bDELETE\s+FROM\b",
        r"\bREPLACE\s+INTO\b", rf"\bUPDATE\s+({pre})\b", rf"\bINSERT\s+INTO\s+({pre})\b",
    ]
    # Positive control: every forbidden pattern fires on a known-bad statement.
    bad = ("ALTER TABLE events ADD COLUMN x; DROP TABLE recommendations; DELETE FROM events; "
           "REPLACE INTO events VALUES (1); UPDATE analysis_runs SET status='x'; "
           "INSERT INTO config_snapshots VALUES (1)")
    assert all(re.search(p, bad) for p in forbidden)
    for module in NEW_SQL_MODULES + [SRC / "ingest.py", SRC / "run_once.py"]:
        text = module.read_text()
        if module.parent.name != "patterns":
            # Only the lines this feature added are in scope for the existing modules.
            text = "\n".join(line for line in text.splitlines() if "pattern" in line)
        for pattern in forbidden:
            assert not re.search(pattern, text), f"{module.name}: {pattern}"
        # The only UPDATE in new code is the upsert of the NEW event_metadata table.
        for m in re.finditer(r"\bUPDATE\b", text):
            window = text[max(0, m.start() - 400):m.start()]
            assert "DO" in text[m.start() - 4:m.start()] and "event_metadata" in window


def test_p2_pre_existing_tables_identical_after_migration(tmp_path):
    conn = _conn(tmp_path)
    db.upsert_event(conn, _payload())
    run_id = db.start_run(conn, 0.0, 1.0)
    db.insert_recommendations(conn, run_id, [{"camera": "cam_street", "risk_class": "safe"}])
    db.insert_config_snapshots(conn, 1.0, [{"camera": "cam_street", "label": "car"}], "h")
    before = _schema_snapshot(conn, store.PRE_EXISTING_TABLES)
    store.apply_schema(conn)
    assert _schema_snapshot(conn, store.PRE_EXISTING_TABLES) == before
    assert store.tables_present(conn)


def test_p3_migration_twice_is_a_noop(tmp_path):
    conn = _conn(tmp_path)
    store.apply_schema(conn)
    store.upsert_event_metadata(conn, metadata.extract(_payload()))
    once = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    rows_once = conn.execute("SELECT * FROM event_metadata").fetchall()
    store.apply_schema(conn)
    twice = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    assert [tuple(r) for r in once] == [tuple(r) for r in twice]
    assert [tuple(r) for r in conn.execute("SELECT * FROM event_metadata")] == \
        [tuple(r) for r in rows_once]


def test_p4_extraction_tolerates_missing_null_and_malformed_keys():
    minimal = metadata.extract({"id": "only-id"})
    assert minimal["event_id"] == "only-id"
    for key in ("region", "top_score", "average_estimated_speed", "velocity_angle", "path",
                "sub_label", "attributes", "zones"):
        assert minimal[key] is None, key
    nulls = metadata.extract({"id": "n", "sub_label": None, "zones": None, "top_score": None,
                              "data": {k: None for k in ("region", "top_score", "attributes",
                                                         "average_estimated_speed",
                                                         "velocity_angle", "path_data")}})
    assert all(nulls[k] is None for k in nulls if k != "event_id")
    garbage = metadata.extract({
        "id": "g", "sub_label": ["svc", 0.9], "zones": "not-a-list", "top_score": "nan",
        "data": {"region": [1, 2], "top_score": float("inf"), "attributes": [{"x": 1}, 7],
                 "average_estimated_speed": "fast", "velocity_angle": True,
                 "path_data": [[[0.1, 0.2], 5.0], "junk", [[None, 0.2], 6.0], [0.3, 0.4, 7.0]]},
    })
    assert garbage["sub_label"] == "svc"
    assert garbage["region"] is None and garbage["top_score"] is None
    assert garbage["average_estimated_speed"] is None and garbage["velocity_angle"] is None
    assert garbage["zones"] is None and garbage["attributes"] == []
    assert garbage["path"] == [[0.1, 0.2, 5.0], [0.3, 0.4, 7.0]]
    assert metadata.extract({"id": "d", "data": "not-a-dict"})["path"] is None
    assert metadata.extract({"no": "id"}) is None
    assert metadata.extract(None) is None


class _FakeClient:
    def __init__(self, events):
        self.events = events

    def get_events(self, before=None, cameras=None, limit=200):
        return self.events if before is None else []


def test_p5_image_bearing_field_never_persisted(tmp_path):
    marker = "FAKE-IMAGE-BYTES-7f3c9a"
    payload = _payload("media-1", thumbnail=marker)
    payload["data"]["thumbnail"] = marker
    conn = _conn(tmp_path)
    store.apply_schema(conn)
    assert ingest.ingest_events(conn, _FakeClient([payload]), capture_metadata=True) == 1
    assert conn.execute("SELECT COUNT(*) FROM event_metadata").fetchone()[0] == 1
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for name in tables:
        for row in conn.execute(f"SELECT * FROM {name}"):
            assert all(marker not in str(v) for v in tuple(row)), name
    # Positive control: the scan finds the marker when it IS present.
    assert marker in json.dumps(payload)
    # Extraction is identical with and without the image-bearing field.
    clean = _payload("media-1")
    assert metadata.extract(clean) == metadata.extract(payload)


def test_p6_upsert_updates_same_event_without_duplicates(tmp_path):
    conn = _conn(tmp_path)
    store.apply_schema(conn)
    store.upsert_event_metadata(conn, metadata.extract(_payload("dup")))
    fresher = _payload("dup", sub_label="svc_label")
    fresher["data"]["top_score"] = 0.93
    fresher["data"]["path_data"] = fresher["data"]["path_data"] + [[[0.3, 0.6], 1_930_000_002.0]]
    fresher["data"]["region"] = None  # missing in the fresher payload: stored value is kept
    store.upsert_event_metadata(conn, metadata.extract(fresher))
    rows = conn.execute("SELECT * FROM event_metadata WHERE event_id='dup'").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["top_score"] == 0.93 and row["sub_label"] == "svc_label"
    assert len(json.loads(row["path_json"])) == 3
    assert row["region_x"] == 0.3


def test_p7_backfill_not_applicable_raw_payloads_are_not_stored(tmp_path):
    """NOT APPLICABLE: the events table keeps selected columns only; no raw
    payload exists locally to backfill from, and no Frigate re-fetch is allowed."""
    conn = _conn(tmp_path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    assert not cols & {"data", "raw", "payload", "data_json", "raw_json"}
    assert not any(re.search(r"def \w*backfill", p.read_text()) for p in NEW_SQL_MODULES)


def test_metadata_capture_failure_never_blocks_event_ingest(tmp_path):
    conn = _conn(tmp_path)  # event_metadata table deliberately NOT created
    assert ingest.ingest_events(conn, _FakeClient([_payload("iso-1")]), capture_metadata=True) == 1
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
