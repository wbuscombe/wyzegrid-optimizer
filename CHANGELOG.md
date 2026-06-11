# Changelog

All notable changes to wyzegrid-optimizer are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
