# Changelog

All notable changes to wyzegrid-optimizer are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
