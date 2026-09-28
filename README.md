# wyzegrid-optimizer

Self-driven analysis agent for the WyzeGrid Frigate setup. Continuously reads Frigate's detection output, identifies patterns (mis-tagged events, threshold candidates, false-positive recurrences, model-limit symptoms), inventories the object identities Frigate actually supplied, learns recurring household-service windows from explicit labels/sub-labels, and writes plain-language recommendations to a small dashboard.

**Phase 1 status: read-only analysis only.** This service has zero write paths to Frigate config. Recommendations are surfaced to the dashboard for human review. Auto-apply (Phase 2) is gated on Phase 1 producing trustworthy output for a few cycles first.

## Design constraints

- **CPU-only ceiling.** No new hardware spend (no Coral, no GPU). The optimizer only ever recommends config tweaks the existing CPU model can act on: `threshold`, `min_score`, `min_area`, `zones`, motion masks, `stationary.max_frames`.
- **Off-limits forever:** the detection model, stream routing, camera firmware, PIA VPN container — all immutable from this service.
- **Honest "model limit" calls.** When the data shows the model fundamentally can't classify something at this resolution (e.g. cats), the recommendation is "model limit — no config change will fix this," not a futile threshold tweak.
- **Cost-conscious.** Claude (Haiku) gets summaries, not raw events. Every run logs token usage. The service runs nightly by default.

## Architecture

```
Frigate /api  ──poll──►  ingestion  ──►  SQLite  ──►  rule detectors  ──►  Claude (Haiku)  ──►  recommendations
                                                       (deterministic)       (interpretation)        │
                                                                                                     ▼
                                                                                              dashboard (read-only)
```

See `ARCHITECTURE.md` for the full data model and module layout.

## Setup

```bash
git clone https://github.com/wbuscombe/wyzegrid-optimizer.git
cd wyzegrid-optimizer

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: FRIGATE_URL, ANTHROPIC_API_KEY (optional — service runs rules-only without it)

# Optional: enforce gitleaks at commit time
brew install pre-commit
pre-commit install
```

## Run

### Local dev

```bash
source venv/bin/activate

# One-off analysis cycle (ingest → analyze → write recommendations)
python -m optimizer.run_once

# Long-running scheduler
python -m optimizer.scheduler

# Dashboard (read-only view of latest analysis run)
python -m optimizer.web.dashboard
# → http://localhost:5004
```

### Demo / Phantom mode

Boots with synthetic mock data — no Frigate connection, no API key needed. Used by CI and the screenshots/demo workflow.

```bash
PHANTOM_MODE=1 python -m optimizer.web.dashboard
# → http://localhost:5004  (mock cameras, mock recommendations)
```

### Production (NAS)

```bash
cd ~/docker/wyzegrid-optimizer
docker compose up -d --build
# Dashboard: http://192.168.50.x:5004
```

`scripts/deploy.sh` rsyncs its own directory to the NAS, so run it from a clean checkout or an export of a release tag: untracked local files would travel with it. Back up the database before a release that adds tables; see [Deployment notes for recurring patterns](ARCHITECTURE.md#deployment-notes-for-recurring-patterns).

## Tests

```bash
source venv/bin/activate
pytest                  # all tests
pytest -v               # verbose
pytest tests/test_analysis_rules.py::test_threshold_proximity   # single test
```

All commits must keep `pytest` green.

## What the analysis surfaces

The rule detectors compute:

| Detector | Question it answers |
|---|---|
| Score distribution | Where in the 0-100% score range do detections cluster, per camera+label? |
| Threshold proximity | How many detections sit within 5% of the current threshold? (Candidates for tuning.) |
| FP pattern | Are there recurring detections at the same approximate box position? (Stationary-object misclassification.) |
| Regression | Did a label's frequency drop sharply vs. its trailing baseline? Correlate with the last config change. |
| Per-camera profile | Which labels does each camera reliably detect vs. never? |
| Stationary re-trigger | The parked-car / waving-flag pattern. |
| Model-limit candidates | Labels with motion firing but detection rarely confirming → likely model can't classify; don't waste threshold tweaks on it. |
| Class swap | Is a `(camera, label)` systematically a misclassification of a different class (e.g. a cat labeled dog)? Feeds suspect events into the model-limit detector so it isn't fooled by them. |
| Shape mismatch | Are "person" events actually animal-shaped — a low, short box below a confident score? |
| Object identity | Which base labels and upstream sub-labels were actually observed per camera? Generic vehicles remain visibly unresolved. |
| Service schedule | From up to 56 days of explicit service identities, is there a recurring local weekday/time for garbage, recycling, mail, or package delivery? |

The service schedule is deliberately evidence-gated. It accepts explicit Frigate
labels or `sub_label` values such as `garbage_truck` or `mail_carrier`, collapses
same-day repeat detections into one visit, and requires at least three distinct
weeks plus 60% agreement on the weekday before calling a schedule learned.
Generic `car` or `truck` detections are never guessed into a service category.
Set `OPTIMIZER_TIMEZONE` to the household's IANA timezone to render local times.

Claude reads the structured findings and produces plain-language recommendations tagged `safe` / `risky` / `model-limit`.

## Recurring visit patterns

Each cycle also looks for opaque recurring visits: time-locked windows in which vehicles or people stop, or stay, on a camera more often than that camera's own base rate predicts. Tracks are merged into visits and classified as `brief_stop`, `long_stay`, or `pass_through`. Candidate windows are tested with exact binomial tails under Benjamini-Hochberg false-discovery control, and each surfaced window gets a cadence estimate: `weekly`, `biweekly`, `weekday_set`, or `recurring`.

- **Cadences are estimates.** They are statistical estimates from detection metadata, not measurements. On busy scenes an every-other-week pattern can occasionally read as weekly or fail to surface. Every pattern in the API carries `"cadence_is_estimate": true` and a `limitations` object pointing to [Cadence estimates and limitations](ARCHITECTURE.md#cadence-estimates-and-limitations); the dashboard renders "Estimated cadence: <label>".
- **Unidentified by default.** Patterns say when and how activity recurs, never what it is. Every row reads "Unidentified recurring pattern (no identity)" unless explicit upstream labels (Frigate `sub_label` or attributes) map to an identity through `OPTIMIZER_PATTERN_IDENTITY_LABEL_MAP`, which ships empty. Nothing is inferred from base label, time, cadence, geometry, speed, or any LLM.
- **Plain words and a named clock.** Each dashboard row also says, in plain words, how often it recurs, how sure the timing evidence is, and how many covered weeks it was seen in, all from existing fields; the statistics sit in a collapsed details row. Pattern times carry the name of the clock they are in (the container's process-local clock, read at runtime; set `TZ` for household time). The dashboard is laid out for phone width: wide tables scroll inside their own container.
- **No media.** Only allowlisted detection metadata is stored. No image or video is fetched, stored, or linked, and no response carries a media field.
- **Private output.** A recurring pattern is household-routine data. Keep the dashboard LAN-only or behind access control.
- **Kill switch.** `OPTIMIZER_PATTERNS_ENABLED=0` skips the stage; the API then reports `disabled`.

Read-only endpoints (GET only; POST, PUT, PATCH, and DELETE return 405):

| Endpoint | Returns |
|---|---|
| `GET /api/patterns` | Surfaced patterns from the latest successful pattern run. Filters: `camera`, `label_group`, `behavior`, `min_confidence` (`moderate` or `strong`), `include_pass_through=1` (pass-through patterns are hidden otherwise). |
| `GET /api/patterns/<pattern_key>` | One pattern from the latest run that holds it, with its weekly hit grid and cadence-test fields. |
| `GET /api/patterns/visits` | The aggregate visit summary of the latest successful pattern run. |
| `GET /api/patterns/status` | Whether the stage is enabled, and the latest pattern run's status and sanitized message. |

Each pattern includes its window, cadence, periodicity, weekdays, parity, support k/n, hit rate, lifts, q, confidence, behavior, basis mix, descriptors, recent flag, `identity`, `identity_reason`, `cadence_is_estimate`, and `limitations`. The method, the synthetic controls, and the limitations are in [ARCHITECTURE.md](ARCHITECTURE.md#recurring-visit-patterns).

## What it will NOT do

- Touch Frigate config (Phase 1)
- Touch the detection model, ever
- Touch stream routing, cameras, or PIA VPN, ever
- Recommend Coral / GPU / hardware purchases (CPU-only ceiling is a design constraint, not a gap to fix)
- Send raw event lists to Claude (cost + hallucination control — summaries only)

## See also

- `ARCHITECTURE.md` — module layout, data model, scheduling
- `SECURITY-PRACTICES.md` — secrets handling, API key rules
- `CHANGELOG.md` — release notes
- `CONTRIBUTING.md` — dev setup, commit style
