# Architecture

## Layers

```
┌──────────────────────────────────────────────────────────────────────┐
│ Frigate at http://192.168.50.6:5000  (read-only, never written to)   │
└──────────────────────┬───────────────────────────────────────────────┘
                       │ HTTP poll: /api/events (incremental)
                       │           /api/stats   (snapshot)
                       │           /api/config  (snapshot)
                       ▼
       ┌────────────────────────────┐
       │  optimizer.ingest          │  resumable, cursor in DB
       └──────────────┬─────────────┘
                      ▼
              ┌───────────────┐
              │   SQLite      │  events, config_snapshots,
              │   (data/)     │  analysis_runs, recommendations
              └───────┬───────┘
                      ▼
       ┌────────────────────────────┐
       │  optimizer.analysis.*      │  7 detectors, pure functions
       │   (deterministic)          │  → produces a findings dict
       └──────────────┬─────────────┘
                      ▼
       ┌────────────────────────────┐
       │  optimizer.claude_layer    │  Haiku reads summaries, returns
       │   (skipped if no API key)  │  JSON recommendations with
       │                            │  risk: safe|risky|model-limit
       └──────────────┬─────────────┘
                      ▼
              ┌───────────────┐
              │  recommendations │  appended to DB, displayed in UI
              └───────┬───────┘
                      ▼
       ┌────────────────────────────┐
       │  optimizer.web.dashboard   │  Flask + Chart.js, read-only
       └────────────────────────────┘
```

## Modules

| Module | Responsibility |
|---|---|
| `optimizer.config` | env-var loading, defaults, PHANTOM_MODE switch |
| `optimizer.db` | SQLite schema creation, query helpers (no ORM) |
| `optimizer.frigate_client` | thin HTTP client for `/api/events`, `/api/stats`, `/api/config` |
| `optimizer.ingest` | incremental event pull (last-seen cursor), config snapshot each run |
| `optimizer.analysis.score_distribution` | per-(camera,label) score histogram + percentiles |
| `optimizer.analysis.threshold_proximity` | count of detections within 5% of current threshold |
| `optimizer.analysis.fp_patterns` | recurring same-box repeats, time-of-day clusters |
| `optimizer.analysis.regression` | trailing-baseline drift detection, correlated with config_snapshots |
| `optimizer.analysis.per_camera_profile` | label coverage per camera (porch-never-sees-dogs detector) |
| `optimizer.analysis.stationary` | parked-car / waving-flag re-trigger pattern |
| `optimizer.analysis.model_limit` | high motion / low detection → label the model can't classify |
| `optimizer.claude_layer` | structured-summary → Haiku → JSON recommendations + cost log |
| `optimizer.scheduler` | run loop with Kometa-window backoff and interval config |
| `optimizer.run_once` | one-shot analysis cycle (manual or cron) |
| `optimizer.phantom` | synthetic-data generator for PHANTOM_MODE |
| `optimizer.web.dashboard` | Flask app: trends, recommendations queue, cost tracker |
| `optimizer.zma` | optional HTTP webhook poster for status events |

## Data model

```sql
CREATE TABLE events (
  id              TEXT PRIMARY KEY,         -- Frigate event id
  camera          TEXT NOT NULL,
  label           TEXT NOT NULL,
  sub_label       TEXT,
  score           REAL,                     -- top_score / data.score
  start_time      REAL NOT NULL,
  end_time        REAL,
  zones_json      TEXT,                     -- JSON array of zone names
  box_x           REAL,                     -- normalized (0-1)
  box_y           REAL,
  box_w           REAL,
  box_h           REAL,
  ratio           REAL,                     -- aspect ratio
  has_clip        INTEGER DEFAULT 0,
  has_snapshot    INTEGER DEFAULT 0,
  false_positive  INTEGER DEFAULT 0,
  ingested_at     REAL NOT NULL             -- monotonic epoch for cursor logic
);
CREATE INDEX idx_events_camera_label ON events(camera, label);
CREATE INDEX idx_events_start_time   ON events(start_time);

CREATE TABLE config_snapshots (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_at   REAL NOT NULL,
  camera        TEXT NOT NULL,
  label         TEXT NOT NULL,              -- 'person' | 'car' | 'dog' | 'cat'
  min_score     REAL,
  threshold     REAL,
  min_area      INTEGER,
  stationary_max_frames INTEGER,
  zones_json    TEXT,
  source_hash   TEXT                        -- sha1 of the full /api/config response
);
CREATE INDEX idx_config_snapshots_at ON config_snapshots(snapshot_at);

CREATE TABLE analysis_runs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at      REAL NOT NULL,
  finished_at     REAL,
  status          TEXT NOT NULL,            -- 'running' | 'ok' | 'error' | 'aborted'
  events_window_start REAL,
  events_window_end   REAL,
  findings_json   TEXT,                     -- the deterministic-layer output
  claude_used     INTEGER DEFAULT 0,
  claude_input_tokens  INTEGER,
  claude_output_tokens INTEGER,
  claude_cost_usd      REAL,
  notes           TEXT
);

CREATE TABLE recommendations (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id          INTEGER NOT NULL,
  camera          TEXT,
  label           TEXT,
  param           TEXT,                     -- e.g. 'threshold', 'min_area'
  current_value   TEXT,
  proposed_value  TEXT,
  rationale       TEXT,
  risk_class      TEXT NOT NULL,            -- 'safe' | 'risky' | 'model-limit'
  confidence      REAL,                     -- 0-1
  expected_effect TEXT,
  FOREIGN KEY (run_id) REFERENCES analysis_runs(id)
);
CREATE INDEX idx_recs_run ON recommendations(run_id);
```

## Scheduling

- Default interval: `OPTIMIZER_INTERVAL_SECONDS=86400` (nightly).
- **Kometa-window backoff:** the scheduler checks the current hour and skips any cycle that would land in 03:00–07:00 local — Kometa locks the NAS and we're a good neighbor. The skip is a no-op log line; the next eligible window is the next scheduled tick after 07:00.
- Each cycle: ingest new events since last cursor → snapshot config → run rule detectors → optionally call Claude → write `analysis_runs` + `recommendations` rows.

## Failure modes (graceful)

| Condition | Behavior |
|---|---|
| Frigate unreachable | Skip cycle, log, retry next tick. `analysis_runs.status='error'`. |
| `ANTHROPIC_API_KEY` unset | Run rules layer only; recommendations come from the deterministic layer with no LLM polish. `analysis_runs.claude_used=0`. |
| Token budget exceeded | Abort the Claude call mid-run; rule-layer findings still recorded. |
| Database locked | Wait + retry with backoff; if persistent, abort cycle. |
| Phantom mode | Bypass all of the above; serve synthetic data. |

## Phase 2 hook points (not built yet)

These are explicitly NOT wired in Phase 1 — listed here so the data model and module boundaries don't paint Phase 2 into a corner:

- `recommendations.applied_at` / `applied_value` columns — for the auto-apply ledger.
- `optimizer.apply` module — would diff proposed vs current, write a guarded change to `/api/config` via Frigate's API (which is itself read-write — Phase 2 unlocks that path behind an explicit `enable_writes` config flag, off by default).
- `optimizer.measure_and_revert` — would compare before/after windows for the changed (camera, label, param) and revert if a regression triggers.
- Dashboard "apply" / "approve" buttons.

None of those exist in Phase 1. The shape of the tables anticipates them so the migration when Phase 2 lands is small.
