# wyzegrid-optimizer

Self-driven analysis agent for the WyzeGrid Frigate setup. Continuously reads Frigate's detection output, identifies patterns (mis-tagged events, threshold candidates, false-positive recurrences, model-limit symptoms), and writes plain-language recommendations to a small dashboard.

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

Claude reads the structured findings and produces plain-language recommendations tagged `safe` / `risky` / `model-limit`.

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
