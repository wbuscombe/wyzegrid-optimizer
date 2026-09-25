# Changelog

All notable changes to wyzegrid-optimizer are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-09-25

### Added: Opaque recurring-visit patterns, with cadence shipped as an estimate
- Persist allowlisted detection metadata (region, top score, speed, velocity angle, path, sub-label, attributes, zones) in a new `event_metadata` table, upserted by event id. Image-bearing fields are excluded, and a metadata failure never blocks event ingestion.
- Collapse tracks into visits (short gaps and stationary re-trigger links) and classify each visit as `brief_stop`, `long_stay`, or `pass_through`.
- Discover time-locked windows that beat their own local base rate (exact binomial tails, Benjamini-Hochberg at `FDR_Q` 0.05) and estimate their cadence with G-PRIME-R: a same-clock-time background taken as the median over the other weekdays, a Fisher exact parity test, and a weekly label that needs evidence on both week parities.
- Identity gate: every pattern is unidentified by default ("Unidentified recurring pattern - no service identity assigned"). An identity comes only from explicit upstream labels through `OPTIMIZER_PATTERN_IDENTITY_LABEL_MAP`, which ships empty.
- A failure-isolated run stage after the existing analysis, append-only `pattern_runs` and `pattern_results` tables (additive schema only), and the kill switch `OPTIMIZER_PATTERNS_ENABLED`.
- Read-only API: `GET /api/patterns`, `/api/patterns/<pattern_key>`, `/api/patterns/visits`, and `/api/patterns/status`. Other verbs return 405, and SQLite is opened read-only. Every pattern carries `cadence_is_estimate: true` and a `limitations` object that names the dense-background case and points to ARCHITECTURE.
- Dashboard section: cadence renders as "Estimated cadence: <label>" under a header stating that cadences are statistical estimates from detection metadata, that on busy scenes an every-other-week pattern can occasionally read as weekly or fail to surface, and that patterns are not identifications. PHANTOM_MODE renders it from the synthetic generator only.

### Changed: Documentation and lint hygiene for the pattern release
- README, ARCHITECTURE, SECURITY-PRACTICES, and `.env.example` document the method, the synthetic controls, the limitations (synthetic evidence only), privacy, the kill switch, and deployment notes.
- Wrap two long log lines in `ingest.py`, and remove an unused import and fix two style findings in `web/dashboard.py`, so the pre-commit flake8 hook passes on the edited files. No behavior change.

### Tests: Recurring patterns
- Synthetic-only tests for visits (V-1..V-7), persistence (P-1..P-7), statistics (S-1, S-2, S-11), the multi-seed positive control (S-3M), null calibration (S-4), S-5..S-8, cadence controls (S-9a as a defect tripwire, S-9b, S-10, S-12a, S-12c), the identity gate (I-1..I-6), run isolation and append-only results (I-7..I-9), the no-LLM check (I-10), the read-only API and dashboard (R-1..R-7), estimate semantics, and a static check that no committed text presents the synthetic dense-background count as a measured rate.

### Added — Object identity and household-service schedule analysis (2026-09-24)
- Preserve Frigate `sub_label` and zones through event normalization instead of dropping them after ingestion.
- Add a read-only object-identity inventory showing base labels, upstream sub-labels, per-camera counts, and unresolved generic vehicle events.
- Add a conservative 56-day schedule learner for explicitly identified garbage, recycling, mail, and package visits. Same-day repeats collapse to one visit; a learned schedule requires at least three weeks and 60% weekday agreement. Generic cars/trucks are never guessed into a service category.
- Surface both analyses on the dashboard with backward-compatible empty states for runs created by older versions.
- Add `OPTIMIZER_TIMEZONE` (default UTC) for local schedule rendering; invalid zones visibly fall back to UTC.

### Fixed — Deploy preservation boundary (2026-09-24)
- Protect the live `.env` and every `.env` backup sibling from rsync transfer and `--delete`, while continuing to deploy the tracked `.env.example` contract. Also exclude local `.venv` validation environments.
- Add a regression test that locks the include-before-exclude ordering and the NAS-only preservation patterns.

### Fixed — Deferred audit items (2026-07-18)
- **Claude-path confidence clamp (Deferred #1)** — `claude_layer.interpret()` now clamps every Claude-supplied `confidence` into `[0, 1]` (missing / non-numeric / NaN / inf → a conservative `0.5` default), matching the `(0, 1]` invariant the rule-layer already held via its `min()`-capped formulas. `db.insert_recommendations` clamps defensively at the storage boundary too. Protects the Phase-2 auto-apply gate. Tested end-to-end with a mocked Claude-success response carrying `1.5` / `-0.3` / missing / `"high"` confidences.
- **Failure alerting via ntfy (Deferred #2)** — a failed nightly run now alerts. Two parts: (a) run-cycle setup (DB init, Frigate client, connect, start_run) moved INSIDE the try/except so a failure *during setup* — the shape of the 2026-07-05 outage — fires the alert instead of escaping silently (with `conn`/`run_id` guards); (b) a new opt-in `ntfy` poster (HTTP Basic auth, no-op unless `NTFY_URL`/`NTFY_TOPIC`/`NTFY_USER`/`NTFY_PASS` are all set) wired to the run-error path, alongside the existing ZMA webhook. ZMA was never deployed (its own audit calls it redundant with the ntfy pipeline); ntfy is the ecosystem's real, authenticated alert channel. Ships **disabled** — set the four `NTFY_*` vars in prod `.env` to activate.
- **Scheduler wall-clock anchor (Deferred #3)** — the nightly loop no longer re-arms a rolling `sleep(interval)` from each cycle's finish (which drifted the run clock 11:11 → 09:38 across restarts). It now anchors to a fixed local wall-clock time (`OPTIMIZER_RUN_AT_HOUR` / `OPTIMIZER_RUN_AT_MINUTE`, default 02:00), computed fresh each cycle — stable across restarts, and kept outside the Kometa window. `OPTIMIZER_INTERVAL_SECONDS` retained for compatibility but no longer drives the schedule.

### Tests
- 18 new tests (63 total, all passing): confidence clamp (unit + end-to-end mocked-Claude + storage-invariant), run-cycle failure alerting (setup-failure, in-run-failure, webhook no-op-when-unset, ntfy-with-Basic-auth), ntfy poster (no-op/publish/non-2xx/never-raises), scheduler wall-clock anchor (today / tomorrow / stable-across-restart-times / on-slot-rolls-forward / out-of-range-clamp).

### Added — Professionalization audit (2026-07-13)
- **CI workflow** (`.github/workflows/phantom.yml`) — runs `pytest` + the zero-secret `PHANTOM_MODE=1` dashboard smoke boot on push/PR. Was referenced by `.phantom.yml`/README/CONTRIBUTING and registered in `dependabot.yml`, but the `.github/workflows/` directory never existed.
- `PROFESSIONALIZATION-AUDIT.md` — first §4 audit of the repo.
- `.gitignore` image globs (`*.jpg`/`*.png`/…) — defense-in-depth against ever committing camera footage to this public repo (no code path writes images today).

### Fixed — docs / config currency (2026-07-13)
- README "What the analysis surfaces" table now lists all 9 detectors (added class-swap + shape-mismatch rows).
- ARCHITECTURE ASCII diagram now says "9 detectors" (matches the module table below it).
- `.env.example` now documents `OPTIMIZER_DATA_DIR` / `OPTIMIZER_DB_PATH`.

### Added — Detection-quality detectors (2026-06-11)
- **Class-swap detector** (`optimizer.analysis.class_swap`) — finds `(camera, label)` pairs whose events are likely misclassifications of another class. Returns `suspect_event_ids` so `model_limit` can filter them out.
- **Shape-mismatch detector** (`optimizer.analysis.shape_mismatch`) — surfaces person events with animal-like box geometry (low + short) AND scores below the person confident-range. Tagged `model-limit` (config can't fix class confusion).
- **Confidence field populated on every recommendation type** — transparent per-type heuristic documented in ARCHITECTURE.md. Phase 2's auto-apply gate will read this.

### Fixed — Phase 2 blocker
- `model_limit` no longer un-flags `(camera, label)` when the only events of that label are class-swap misclassifications. Receives `suspect_event_ids` from `class_swap` and subtracts those events before the zero-detection check. Closes the self-reinforcing-error path that the week-1 review surfaced on `back_deck_cam/dog`.

### Changed
- `run_all` runs `class_swap` BEFORE `model_limit` and threads its `suspect_event_ids` into the latter.
- Claude system prompt now explicitly states class-swap and shape-mismatch findings are ALWAYS `risk_class=model-limit` with `proposed_value=null`. Recommending a config tweak for either is forbidden.
- Rule-layer fallback recommendations now de-duplicate: when class-swap flags `(camera, label)`, the same pair is suppressed from the zero-detection model-limit row.

### Tests
- 17 new tests (45 total, all passing): class-swap detection on the synthesized back_deck cat/dog scenario, healthy-data-not-flagged, height-tolerance prevents dog↔person false overlap, model_limit hardening, shape-mismatch geometry boundaries, confidence-field contract, class-swap-suppresses-zero-detection-duplicate, share-based ranking prefers rare-class overlap over numerous-class noise.

### Live data tuning
While verifying the detector on the production NAS deploy, two refinements surfaced:

1. **`confident_low` excludes the suspect camera from its baseline.** Originally the data-driven threshold was the 25th percentile across all cameras — but when one camera dominates a label's event count with misclassifications, those low scores drag the threshold down and the suspect camera appears "healthy" against its own contamination. Now each `(camera, label)` is judged against a `confident_low` computed from other cameras only. The live back_deck dog scenario moved from mean 0.59 vs threshold 0.56 (no flag) to mean 0.59 vs threshold 0.68 (flagged).
2. **Overlap candidates are ranked by share, not raw count.** A high-traffic class (e.g. person on a busy camera) trivially racks up overlap pairs with any other label. Now the detector ranks candidates by `overlap_pairs / total_events_of_other_class_on_camera`, so a rare-class overlap (cat) wins over numerous-class noise (person) — matching the user-observed truth that back_deck/dog is the visiting cat.


### Added — Phase 1: foundation + read-only analysis
- Project scaffolding (README, ARCHITECTURE, CONTRIBUTING, SECURITY-PRACTICES, LICENSE, .editorconfig, .gitignore, .dropboxignore, .env.example, .pre-commit-config.yaml, .phantom.yml).
- SQLite schema for `events`, `config_snapshots`, `analysis_runs`, `recommendations`.
- Frigate HTTP client (read-only) with incremental event polling.
- Seven deterministic rule detectors: score distribution, threshold proximity, FP patterns, regression, per-camera label profile, stationary re-trigger, model-limit candidates.
- Optional Claude (Haiku) interpretation layer with token-budget guard and cost logging. Falls back to rule-layer-only output when `ANTHROPIC_API_KEY` is absent.
- Read-only Flask dashboard with score trends, recommendations queue, config-snapshot history, token cost tracker.
- Phantom demo mode (`PHANTOM_MODE=1`) boots with synthetic data, no Frigate connection, no API key required.
- Optional ZMA status webhook (`ZMA_WEBHOOK_URL`) — POSTs run-complete events if set, no-op otherwise.
- Scheduler with Kometa-window backoff (skips 03:00–07:00).
- Docker image, non-root user, pinned by registry manifest digest.
- pytest suite covering each rule detector with synthetic events.

### Phase 1 explicitly does NOT include
- Auto-apply of config changes.
- Measure-and-revert loop.
- Any write path to Frigate config.
- Dashboard "apply" / "approve" buttons.

These land in Phase 2, gated on Phase 1 producing trustworthy recommendations across several real-data cycles.
