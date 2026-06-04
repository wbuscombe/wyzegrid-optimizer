# Contributing to wyzegrid-optimizer

## Setup

```bash
git clone https://github.com/wbuscombe/wyzegrid-optimizer.git
cd wyzegrid-optimizer

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env (FRIGATE_URL and ANTHROPIC_API_KEY optional for dev — phantom mode needs neither)

brew install pre-commit
pre-commit install
```

## Run / test / verify

```bash
source venv/bin/activate
pytest                        # unit tests
PHANTOM_MODE=1 python -m optimizer.web.dashboard   # smoke test the dashboard with no secrets
python -m optimizer.run_once  # full one-shot cycle (requires Frigate reachable)
```

## Commit style

[Conventional Commits](https://www.conventionalcommits.org/). Body explains WHY, diff shows WHAT.

- `feat(analysis): add stationary re-trigger detector`
- `fix(ingest): paginate /api/events with `before` cursor on the oldest seen ts`
- `docs: clarify phantom-mode boot contract`
- `test(rules): cover the porch-never-sees-dogs case for per_camera_profile`
- `chore(deps): pin anthropic SDK`

## Tests

- TDD default. Every analysis detector ships with a unit test using synthetic events.
- `pytest` must pass before any commit.
- `PHANTOM_MODE=1` smoke-test must succeed with no `.env` present.

## What NOT to do

- Don't add a write path to Frigate config in Phase 1. The hard rule: this service is read-only against Frigate until Phase 2.
- Don't add Coral / GPU / hardware-purchase recommendations to the Claude prompt. The CPU-only ceiling is a design constraint.
- Don't log secrets. Don't pipe `curl -v` output that contains `Authorization:` headers.
- Don't bypass pre-commit hooks unless you understand exactly what you're skipping.
- Don't send raw event lists to Claude — only computed summaries (cost + hallucination control).

## Releases

Tags are semver (`v0.1.0`). Push tag → CI builds + publishes a Docker image (when CI is wired).
