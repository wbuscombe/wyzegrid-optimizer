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
       │  optimizer.analysis.*      │  11 detectors, pure functions
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
| `optimizer.analysis.model_limit` | high motion / low detection → label the model can't classify (filters out class-swap suspects so it isn't fooled by mislabeled events) |
| `optimizer.analysis.class_swap` | `(camera, label)` whose events are likely misclassifications of a different class — feeds `suspect_event_ids` to `model_limit` to harden it |
| `optimizer.analysis.shape_mismatch` | person events whose box geometry is animal-shaped (low + short) + below-confident score |
| `optimizer.analysis.object_identity` | per-camera inventory of base labels and upstream sub-labels; never infers an identity from imagery |
| `optimizer.analysis.service_schedule` | conservative 56-day recurring-window learner for explicitly identified garbage, recycling, mail, and package visits |
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
- Schedule learning reads up to 56 days already retained in SQLite, collapses repeated detections on one local calendar day to one visit, and requires at least three distinct weeks plus 60% modal-weekday agreement. `OPTIMIZER_TIMEZONE` selects the IANA timezone; an invalid value falls back visibly to UTC.

## Failure modes (graceful)

| Condition | Behavior |
|---|---|
| Frigate unreachable | Skip cycle, log, retry next tick. `analysis_runs.status='error'`. |
| `ANTHROPIC_API_KEY` unset | Run rules layer only; recommendations come from the deterministic layer with no LLM polish. `analysis_runs.claude_used=0`. |
| Token budget exceeded | Abort the Claude call mid-run; rule-layer findings still recorded. |
| Database locked | Wait + retry with backoff; if persistent, abort cycle. |
| Phantom mode | Bypass all of the above; serve synthetic data. |

## Detector ordering — class-swap hardening

The detectors don't simply run in parallel. **`class_swap` runs BEFORE
`model_limit`, and its `suspect_event_ids` are threaded into the latter so
class-swap misclassifications are subtracted before the zero-detection check.**

Without this, the week-1 review surfaced a live failure mode: `back_deck_cam`
"dog" events that were actually a visiting cat being mislabeled. The previous
`model_limit` detector saw the new "dog" events and dropped the model-limit
flag — which would have caused Phase 2's auto-tune loop to read the
misclassifications as "model is detecting dogs now" and nudge the threshold
in the wrong direction (a self-reinforcing error). The fix:

1. `class_swap.detect` identifies `(camera, label)` pairs whose events look
   like a different class (low scores + box position/height + time overlap
   with another label on the same camera). It returns each suspect event's id.
2. `model_limit.detect` receives that id set and treats those events as if
   they don't exist when counting per-label events. So a camera whose only
   "dog" events are cat misclassifications is correctly re-flagged.

The same hardening applies to `shape_mismatch` (animal-shaped person events),
though that detector doesn't feed `model_limit` directly — it surfaces the
candidates as their own model-limit findings.

## Confidence heuristic

Every recommendation populates a `confidence ∈ (0, 1]` field via a transparent
heuristic in `claude_layer._rule_layer_fallback_recs`. Phase 2's auto-apply
gate will read this. The per-type rules:

| Type | Heuristic |
|---|---|
| Threshold tuning | `0.40 + 0.35×in_band_pct + 0.25×min(1, total/100)` — clusters tightly + larger dataset → higher confidence. Capped at 0.90. |
| Stationary | `0.50 + 0.05×min(8, hotspots) + 0.001×min(400, flicker_total)` — more co-located fixed-positions × more repeats → higher. Capped at 0.92. |
| Model-limit (zero detection) | `0.55 + 0.001×min(500, camera_total)` — busier camera = stronger evidence the model just can't classify this label. Capped at 0.92. |
| Class-swap | `0.55 + 0.04×min(10, overlap_count)` — more co-located+co-temporal pairs with another label → higher. Capped at 0.95. |
| Shape-mismatch | `0.45 + 0.08×min(5, count)` — more animal-shaped person events → higher. Capped at 0.85. |

These are deliberately simple and inspectable. Phase 2's auto-apply will
gate on something like `confidence >= 0.75 AND risk_class == "safe"`. Any
`risk_class == "model-limit"` is filtered out before the gate is even
considered — class-swap and shape-mismatch findings can NEVER auto-apply.

## Phase 2 hook points (not built yet)

These are explicitly NOT wired in Phase 1 — listed here so the data model and module boundaries don't paint Phase 2 into a corner:

- `recommendations.applied_at` / `applied_value` columns — for the auto-apply ledger.
- `optimizer.apply` module — would diff proposed vs current, write a guarded change to `/api/config` via Frigate's API (which is itself read-write — Phase 2 unlocks that path behind an explicit `enable_writes` config flag, off by default).
- `optimizer.measure_and_revert` — would compare before/after windows for the changed (camera, label, param) and revert if a regression triggers.
- Dashboard "apply" / "approve" buttons.

None of those exist in Phase 1. The shape of the tables anticipates them so the migration when Phase 2 lands is small.
