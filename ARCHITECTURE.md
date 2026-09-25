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
| `optimizer.web.patterns` | GET-only recurring-pattern API and dashboard section; reads stored results through a read-only SQLite connection |
| `optimizer.zma` | optional HTTP webhook poster for status events |
| `optimizer.patterns.settings` | every recurring-pattern threshold as a named constant, plus the shipped-empty identity map |
| `optimizer.patterns.metadata` | allowlisted event-metadata extraction (no image-bearing field) |
| `optimizer.patterns.visits` | tracks to visits, behavior classes (brief_stop, long_stay, pass_through) |
| `optimizer.patterns.localtime` | the one injectable local-time mapping and the fixed-epoch week parity |
| `optimizer.patterns.stats` | exact binomial and Fisher tails, Benjamini-Hochberg |
| `optimizer.patterns.discovery` | base-rate-tested windows, surfacing, G-PRIME-R cadence estimates |
| `optimizer.patterns.identity` | explicit-upstream-label identity gate |
| `optimizer.patterns.pipeline` | in-memory run: coverage, visits, discovery, identity, visit summary |
| `optimizer.patterns.store` | additive schema, metadata upsert, append-only pattern results, read helpers |
| `optimizer.patterns.stage` | failure-isolated run stage and kill switch |
| `optimizer.patterns.presentation` | estimate semantics: `cadence_is_estimate`, the limitations object, dashboard copy |
| `optimizer.patterns.synthetic` | deterministic synthetic generator for tests and PHANTOM_MODE (2031 dates, synthetic cameras) |

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

Recurring-pattern tables, created by `optimizer.patterns.store` with `CREATE ... IF NOT EXISTS` only. No pre-existing table is altered, and nothing updates or deletes a row of one.

```sql
CREATE TABLE event_metadata (        -- allowlisted detection metadata, upserted by event id
  event_id                TEXT PRIMARY KEY,
  region_x REAL, region_y REAL, region_w REAL, region_h REAL,
  top_score               REAL,
  average_estimated_speed REAL,
  velocity_angle          REAL,
  path_json               TEXT,      -- rounded [x, y, t] points; no image data
  sub_label               TEXT,
  attributes_json         TEXT,
  zones_json              TEXT,
  updated_at              REAL NOT NULL
);

CREATE TABLE pattern_runs (          -- one row per cycle that reached the stage
  run_id            INTEGER PRIMARY KEY,   -- the analysis_runs id
  status            TEXT NOT NULL,         -- 'ok' | 'error'
  started_at        REAL NOT NULL,
  finished_at       REAL,
  message           TEXT,                  -- sanitized one-line error, else NULL
  window_first_date TEXT,
  window_last_date  TEXT,
  summary_json      TEXT                   -- aggregate visit summary and run parameters
);

CREATE TABLE pattern_results (       -- append-only per run; never pruned
  run_id INTEGER NOT NULL, pattern_key TEXT NOT NULL,
  site TEXT NOT NULL, label_group TEXT NOT NULL, behavior TEXT NOT NULL,
  cadence TEXT NOT NULL, weekdays_json TEXT NOT NULL, parity INTEGER,
  window_median_min REAL, support_k INTEGER, covered_n INTEGER, hit_rate REAL,
  q_value REAL, confidence TEXT NOT NULL, identity TEXT, identity_reason TEXT,
  detail_json TEXT NOT NULL,         -- the full pattern record the API serves
  created_at REAL NOT NULL,
  PRIMARY KEY (run_id, pattern_key)
);
CREATE INDEX idx_pattern_results_key ON pattern_results(pattern_key);
```

## Scheduling

- Default interval: `OPTIMIZER_INTERVAL_SECONDS=86400` (nightly).
- **Kometa-window backoff:** the scheduler checks the current hour and skips any cycle that would land in 03:00–07:00 local — Kometa locks the NAS and we're a good neighbor. The skip is a no-op log line; the next eligible window is the next scheduled tick after 07:00.
- Each cycle: ingest new events since last cursor → snapshot config → run rule detectors → optionally call Claude → write `analysis_runs` + `recommendations` rows.
- Schedule learning reads up to 56 days already retained in SQLite, collapses repeated detections on one local calendar day to one visit, and requires at least three distinct weeks plus 60% modal-weekday agreement. `OPTIMIZER_TIMEZONE` selects the IANA timezone; an invalid value falls back visibly to UTC.
- After the run is recorded, the recurring-pattern stage runs (next section). It is failure-isolated and cannot change the run's status, findings, or recommendations.

## Recurring visit patterns

Patterns report WHEN and HOW activity recurs in detection metadata. They never report WHAT it is. The stage reads stored events plus `event_metadata`, keeps visits in memory for one run, and persists only aggregates and patterns. Every threshold below is a named constant in `optimizer.patterns.settings`.

### Visits and behavior

- Label groups: vehicle = car, truck, bus, motorcycle; person = person. Other labels are ignored by visits and patterns.
- A stratum is (camera, label group). `OPTIMIZER_PATTERN_SITE_GROUPS` can merge cameras into one site.
- A track joins the open visit when it starts within 90 s of the visit's end, or (stationary re-trigger link) when its box overlaps the visit's last box at IoU >= 0.8 within 1800 s. A missing end time counts as the start and marks the visit incomplete.
- Behavior: `long_stay` when dwell >= 1200 s; `brief_stop` when the path shows a halt (at least 10 s within 0.03 normalized distance of one point), or, without a usable path, dwell >= 20 s with first-to-last box displacement <= 0.10; otherwise `pass_through`. Each visit records its basis: path, box, or dwell_only.
- Local time comes from one injectable function. Production uses the scheduler's process-local clock, the same one its 02:00 anchor uses. Week parity counts whole weeks from a fixed Monday epoch, never ISO week numbers.

### Discovery

- Analysis window: the 112 complete local days before the run. A camera is covered on a date when it has any event that day. A weekday needs at least 4 covered weeks.
- For every stratum, behavior, weekday, and 30-minute window (15-minute step), k is the number of covered weeks with an onset in the window. The local null is the onset rate in the 120 minutes either side, and p is the exact binomial tail.
- Benjamini-Hochberg controls the false discovery rate across the whole family at `FDR_Q` 0.05. The value is calibrated: at 0.10 the synthetic null calibration surfaced 3 patterns across 60 seeds; at 0.05 it surfaces 1, none strong.
- Significant windows merge into intervals. An interval surfaces only with support >= 4 weeks, hit rate >= 0.5, time lift >= 3.0 over its own local null, and a p10-to-p90 spread <= 120 minutes. The lift floor is 3 because a window at a pure step edge in activity can reach about 2 against a surround that averages one quiet and one busy side.
- Confidence is `strong` (q <= 0.01, support >= 6, hit rate >= 0.75, spread <= 60) or `moderate` (q <= 0.10, support >= 4, hit rate >= 0.5, spread <= 120). Nothing weaker surfaces. `pass_through` patterns are computed and stored but hidden unless requested.

### Cadence (G-PRIME-R)

Cadence is estimated for each surfaced single-weekday interval against the time-of-day background b_ToD: the median, over the other weekdays with at least 4 covered weeks, of each weekday's rate of weeks with an onset in the same clock interval, (hits + 0.5) / (covered + 1). At least 3 qualifying weekdays are required; with fewer, the cadence is `recurring`. The median ignores up to two other weekdays that carry their own same-time pattern.

- Weekday specificity: the hit rate must be at least 3.0 times b_ToD with a binomial tail <= 0.01. A biweekly candidate is measured on its active parity.
- Parity: weeks split into A and B by the fixed-epoch parity. p_parity is a one-sided Fisher exact test that the busier parity hits more often. p_bgL tests whether the quieter parity is consistent with b_ToD.
- Periodicity, first match wins:
  - biweekly: the busier parity has at least 3 hit weeks and a hit rate >= 0.5, the quieter parity has at least 2 covered weeks, p_parity <= 0.05, and p_bgL > 0.01.
  - weekly: no parity contrast (p_parity > 0.05, or an exact tie) AND each parity on its own hits at least half of at least 2 covered weeks. Weekly asserts recurrence on both parities, so it needs evidence on both. The absence of a contrast alone is not that evidence.
  - indeterminate: otherwise.
- Cadence label, first match wins: `biweekly` (biweekly periodicity plus biweekly-form specificity), `weekly` (weekly periodicity plus weekly-form specificity), `weekday_set` (3 or more remaining weekdays of one stratum whose medians sit within 45 minutes), and `recurring` (surfaced, none of the above).

Every cadence label is an estimate. See [Cadence estimates and limitations](#cadence-estimates-and-limitations).

### Why the method looks like this

Each revision replaced a specific defect, found by a synthetic control:

- WYZE-020-R1 labelled biweekly by a fixed 85% concentration on one parity. That ignored the base rate: background stops on the off weeks pushed a planted biweekly pattern to weekly. R1 also calibrated `FDR_Q` from 0.10 to 0.05.
- WYZE-020-R2 (G-PRIME) measured background with the local null, which is biased low where an interval straddles an activity onset. It also tested weekday specificity on a pooled rate that another weekday's same-time pattern could inflate, and on an all-weeks hit rate that a biweekly pattern halves by construction.
- WYZE-020-R3 (G-PRIME-R) matches the clock time, takes a robust median over the other weekdays, and measures a biweekly candidate on its active parity.
- WYZE-020-R4 made weekly require evidence on both parities and replaced a contamination control whose stress could not be measured.
- WYZE-020-R5 ships every cadence label as an estimate and turns the dense-background control into a defect tripwire. No algorithm, threshold, scenario, or seed changed.

### Identity gate

Every pattern is unidentified by default and reads "Unidentified recurring pattern - no service identity assigned". An identity comes only from explicit upstream labels (a Frigate sub_label or attribute string) found in `OPTIMIZER_PATTERN_IDENTITY_LABEL_MAP`, which ships empty. It is assigned only when at least 3 member visits and at least 60% of members carry a label mapped to the same identity, and no other identity exceeds 20% of members. Otherwise identity is null with a reason: no explicit upstream label, insufficient label support, or conflicting labels. Identity is never inferred from base label, time, weekday, cadence, dwell, geometry, speed, or any model output, and no pattern code calls an LLM. The household-service schedule learner is unchanged and still needs explicit service labels.

### Persistence, failure isolation, and the kill switch

- The migration is additive and is applied by `run_once` at the start of a cycle, never by a GET. Applying it twice is a no-op.
- Ingestion upserts allowlisted metadata by event id. A metadata failure never blocks event ingestion. No raw payload and no image-bearing field is stored, so there is no backfill: history captured before this feature classifies on box or dwell only.
- The stage runs after the existing run is recorded. Any exception becomes a `pattern_runs` row with status `error` and a sanitized one-line message, and the run's status, findings, and recommendations are unchanged.
- `pattern_results` is append-only per run. Nothing is pruned or rewritten.
- `OPTIMIZER_PATTERNS_ENABLED=0` turns the stage off, and the API then reports `disabled`. The schema and metadata capture continue.
- The GET routes open SQLite read-only (`mode=ro`), so a request cannot write. PHANTOM_MODE computes the section from the synthetic generator in memory.

### Synthetic controls

All tests use the deterministic synthetic generator (synthetic cameras, 2031 dates). Stochastic controls are multi-seed with pass fractions fixed before running.

| Control | Seeds | Gate |
|---|---|---|
| S-3M positive control: P1 weekly Tuesday, P2 biweekly Thursday, P3 six-weekday door stop, P4 five-weekday long stay | 2031-2050 | P1 >= 19, P2 biweekly on parity A >= 18 and never weekly, P3 >= 19, P4 >= 19; at most 1 non-planted pattern per seed and 3 in total |
| S-4 null calibration | 1001-1050 busy, 2001-2010 quiet | at most 1 surfaced across all 60, none strong |
| S-9a dense-background biweekly, a defect tripwire | 3101-3120 | weekly in at most 4 seeds |
| S-9b false-biweekly guard | 3200 and 3201-3220 | weekly plants with skipped or missed weeks never read biweekly |
| S-10 quiet window with a partial off parity | 3000 | recurring, periodicity indeterminate |
| S-11 and S-12a | deterministic | exact Fisher tails; b_ToD median rules |
| S-12c same-clock contamination in a quiet window | 3401-3420 | Tuesday and Thursday weekly and Wednesday biweekly on parity A in >= 19; Wednesday weekly in 0; stress ratio >= 2 in every seed |

## Cadence estimates and limitations

Cadence labels (`weekly`, `biweekly`, `weekday_set`, `recurring`) are statistical estimates from detection metadata. They are not measurements, and they are not identifications. Every pattern in the API carries `cadence_is_estimate: true` and a `limitations` object that names the dense-background case and points to this section. The dashboard renders cadence as "Estimated cadence: <label>". No field anywhere reports an error rate.

**Dense background.** On busy scenes an every-other-week pattern can occasionally read as weekly or fail to surface. The evidence is synthetic. Control S-9a plants a biweekly vehicle stop (Wednesday 13:10-13:30, active on parity A) inside a synthetic street background of about three random stops a day, spread uniformly from 07:00 to 19:00, over the fixed seeds 3101-3120.

| S-9a outcome (synthetic scenario, fixed seeds 3101-3120) | Seeds |
|---|---|
| Not discovered: no window survives the false-discovery control | 15 |
| Discovered, estimated biweekly on parity A | 4 |
| Discovered, estimated weekly | 1 |

In the weekly seed, random background stops landed in 5 of the 8 off weeks, so both parities recur in that data. From 16 weeks it cannot be told apart from a weekly pattern with 3 misses, and no rule on these counts removes that residual without mislabelling genuine weekly patterns about as often. These counts characterize one synthetic scenario. They are not a rate for any camera.

S-9a stays in the suite as a defect tripwire: it fails if more than 4 of its 20 seeds read weekly. The ceiling is policy, fixed before running rather than fitted to the observed count: an estimate label that is wrong more than one time in five in its own worst-case stress scenario is unfit to ship.

Other limitations:

- The background-consistency test (alpha 0.01) is designed to reject about 1% of genuine biweekly patterns whose off weeks draw unusually busy background; those read `indeterminate` and so `recurring`.
- Cadence needs at least 3 other weekdays with 4 or more covered weeks. Otherwise it is `recurring`.
- Coverage is a proxy: a camera counts as covered on any local date with at least one event of any label, so a day with a partial outage still counts as covered.
- Overlapping objects in one stratum merge into one visit, one activity episode.
- The daily grid ends at local midnight, so activity that recurs across midnight is tested as two windows on adjacent weekdays.
- History captured before this feature has no stored path, so it classifies on box or dwell only until paths accumulate.
- Windows follow the scheduler's process-local clock. Set the container's `TZ` to see household-local windows.

## Deployment notes for recurring patterns

- New tables `event_metadata`, `pattern_runs`, and `pattern_results` are created by the first cycle after deploy (additive; never by a GET). Back up the database before the first deploy that carries them.
- First run: the tables are created, and metadata capture starts with newly ingested events only. The stage records a `pattern_runs` row; `/api/patterns/status` reports it.
- Measured synthetic runtime: the stage takes about 1.5 s for about 67,000 synthetic events on a development Mac (test S-8, limit 60 s).
- Kill switch: set `OPTIMIZER_PATTERNS_ENABLED=0` in `.env` and recreate the containers.
- Exposure: the output is household-routine data. The dashboard must stay LAN-only or behind access control. Check that no proxy or tunnel publishes the dashboard port before deploying.
- Deploy from a clean checkout or an export of the release tag. `scripts/deploy.sh` rsyncs its own directory, so untracked local files (a stray lock file or build metadata, for example) would travel with it.

## Failure modes (graceful)

| Condition | Behavior |
|---|---|
| Frigate unreachable | Skip cycle, log, retry next tick. `analysis_runs.status='error'`. |
| `ANTHROPIC_API_KEY` unset | Run rules layer only; recommendations come from the deterministic layer with no LLM polish. `analysis_runs.claude_used=0`. |
| Token budget exceeded | Abort the Claude call mid-run; rule-layer findings still recorded. |
| Database locked | Wait + retry with backoff; if persistent, abort cycle. |
| Pattern stage raises | `pattern_runs.status='error'` with a sanitized message; the run's status, findings, and recommendations are unchanged. |
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
