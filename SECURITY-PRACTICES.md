# Security Practices

> Living document. All contributors (human and AI) must follow these rules.

## Secrets

- `ANTHROPIC_API_KEY` and any future credential lives in `.env` at the repo root. `.env` is gitignored. `.env.example` is the committed template — no real values.
- The optimizer never prints, logs, or commits secrets. Reference by variable name only. When confirming a value is set, show length, never value.
- The Claude layer is **optional**: if `ANTHROPIC_API_KEY` is unset, the optimizer runs the deterministic rule layer and writes those findings directly as recommendations (no LLM polish). This is by design — the service must not hard-depend on a paid secret to run at all.

## Pre-commit

A gitleaks pre-commit config ships with the repo. Install with:

```bash
brew install pre-commit
pre-commit install
```

Without this, gitleaks runs only if you invoke it manually. **Do not bypass hooks** unless you've personally read what they would have flagged.

## Read-only by design (Phase 1)

This service has zero write paths to Frigate config. The Frigate HTTP client (`optimizer.frigate_client`) exposes only GET methods. Any future write path lands in a new `optimizer.apply` module behind an explicit `enable_writes=true` config flag — and that flag does not exist in Phase 1.

## Phantom mode hard guarantee

`PHANTOM_MODE=1` boots the dashboard with synthetic data, no `.env` required, no Frigate connection attempted, no Anthropic API call made. This is verified at startup: in phantom mode, the `frigate_client` and `claude_layer` modules are not imported.

## Recurring-pattern data

- **Household-routine data.** A recurring visit pattern (when a vehicle or person tends to arrive, and on which weekdays) describes the household's routine. Treat pattern output, the pattern tables, and database copies as private operational material.
- **Dashboard exposure.** Any dashboard or API that serves patterns must stay LAN-only or sit behind access control. Before deploying, confirm that no reverse proxy, tunnel, or port forward publishes the dashboard port.
- **Read-only surface.** The pattern routes are GET only (other verbs return 405) and open SQLite read-only, so a request cannot write.
- **Synthetic fixtures only.** Tests, fixtures, docs, and commit messages use the synthetic generator (synthetic camera names, 2031 dates). No production window, weekday, time, schedule, or count is ever committed.
- **No media, ever.** The pattern code never fetches, decodes, stores, renders, or links a camera image or video, and never calls a Frigate media endpoint. Metadata extraction is an allowlist that excludes image-bearing fields, and a test scans every pattern response for media keys and values.
- **No identity inference and no LLM.** Identity comes only from explicit upstream labels through a map that ships empty. The pattern code never calls the Claude layer.
- **Estimates, not rates.** Cadence labels are estimates. The dense-background limitation is documented from a synthetic control only; no committed text presents a synthetic count as a measured rate, and a test enforces that.

## Logging

- No event payloads are logged at INFO level (they may contain camera-related metadata Will treats as private).
- Token usage and HTTP status codes are logged. Token VALUES never are.
- Cost-tracker output shows token counts and computed USD, never the prompt or response body.

## Cost guard

- `OPTIMIZER_TOKEN_BUDGET` is a hard per-run ceiling. The Claude call aborts if its input + estimated output would exceed it. Phase 1 default: 20000 tokens (~$0.01 per run on Haiku — Will should see almost nothing on the bill).
- Token usage is logged into `analysis_runs.claude_input_tokens` / `claude_output_tokens` / `claude_cost_usd` so monthly cost is computable from the DB.

## What's intentionally NOT a security concern in this project

- Camera footage / snapshots: this service does not pull or render snapshots. It only reads event metadata (id, label, score, timestamps, box coordinates). The dashboard does not embed live frames.
- LAN trust boundary: same as `wyzegrid-web` — the dashboard is LAN-only behind the existing router. Guest-WiFi isolation is a router-level deferred item documented in the `wyzegrid-web` security doc.

## Incident response

If `ANTHROPIC_API_KEY` is exposed (committed, logged, pasted into an AI chat):

1. Rotate at console.anthropic.com immediately.
2. Update `.env` (mode 600) on every host running this service.
3. `docker compose up -d --force-recreate` to reload env.
4. Note the rotation in `CHANGELOG.md` under `### Security`.
