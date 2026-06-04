"""
Ingestion pipeline.

Each cycle:
  1. Read newest events since the last-seen start_time (paginated, oldest-first
     into the DB so the cursor advances monotonically).
  2. Snapshot the current /api/config so we have a record of what settings
     were live alongside the events.

Kometa-window backoff is enforced by the scheduler, not here — this module
runs whenever called.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Optional

from . import db
from .frigate_client import FrigateClient

logger = logging.getLogger(__name__)


def ingest_events(
    conn,
    client: FrigateClient,
    cameras: Optional[list[str]] = None,
    max_pages: int = 50,
    page_size: int = 200,
) -> int:
    """
    Pull new events since the latest start_time we already have.

    Returns the count of events upserted in this run. Pagination uses the
    `before` cursor walking backward from `now` until we hit our last-seen
    timestamp or run out of pages (max_pages is a safety stop).
    """
    last_seen = db.latest_event_start_time(conn) or 0.0
    inserted = 0
    cursor: Optional[float] = None  # None = "from now"
    pages = 0
    while pages < max_pages:
        pages += 1
        events = client.get_events(
            before=cursor,
            cameras=cameras,
            limit=page_size,
        )
        if not events:
            break
        with db.transaction(conn):
            for e in events:
                if not e.get("id"):
                    continue
                db.upsert_event(conn, e)
                inserted += 1
        oldest = min(float(e["start_time"]) for e in events if e.get("start_time"))
        if oldest <= last_seen:
            # We've reached events older than what we already had — done.
            break
        if len(events) < page_size:
            # Last page from Frigate's perspective.
            break
        # Step the cursor to one microsecond before the oldest we just got.
        cursor = oldest - 0.000001
    logger.info("ingest_events: %d upserts across %d pages (last_seen=%.0f)", inserted, pages, last_seen)
    return inserted


def snapshot_config(conn, client: FrigateClient) -> int:
    """
    Capture per-(camera, label) thresholds + min_area + stationary settings.

    Returns the number of (camera, label) rows written. Hash of the full
    raw config response is stored so callers can detect drift between runs.
    """
    raw = client.get_config()
    if not raw:
        logger.warning("snapshot_config: empty /api/config payload — skipping")
        return 0
    source_hash = hashlib.sha1(json.dumps(raw, sort_keys=True).encode()).hexdigest()

    cameras_cfg = raw.get("cameras", {}) or {}
    global_objects = raw.get("objects", {}) or {}
    global_filters = global_objects.get("filters", {}) or {}
    global_stationary = (
        ((raw.get("detect") or {}).get("stationary") or {}).get("max_frames") or {}
    ).get("objects") or {}

    rows: list[dict] = []
    for cam_name, cam_cfg in cameras_cfg.items():
        cam_objects = cam_cfg.get("objects", {}) or {}
        cam_filters = cam_objects.get("filters", {}) or {}
        cam_zones = list((cam_cfg.get("zones") or {}).keys())
        tracked = cam_objects.get("track") or global_objects.get("track") or []
        for label in tracked:
            filt = {**(global_filters.get(label, {}) or {}), **(cam_filters.get(label, {}) or {})}
            rows.append({
                "camera": cam_name,
                "label": label,
                "min_score": filt.get("min_score"),
                "threshold": filt.get("threshold"),
                "min_area": filt.get("min_area"),
                "stationary_max_frames": global_stationary.get(label),
                "zones": cam_zones,
            })
    with db.transaction(conn):
        db.insert_config_snapshots(conn, time.time(), rows, source_hash)
    logger.info("snapshot_config: %d (camera,label) rows captured (hash=%s)", len(rows), source_hash[:8])
    return len(rows)
