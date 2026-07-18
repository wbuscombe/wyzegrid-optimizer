# Professionalization Audit — wyzegrid-optimizer
_First audit, generated 2026-07-13 against §4 of the professionalization standard. This repo was
built with standards at creation but had never been through a dedicated audit pass._

## Ecosystem

Python ≥3.11 (Docker pins 3.12), Flask dashboard + a nightly ingest/analyze scheduler. Read-only
analyzer of the WyzeGrid Frigate setup: pulls detection metadata, runs deterministic detectors,
optionally interprets with Claude (Haiku), writes recommendations to SQLite. Public repo, MIT.
Tests via `pytest`; demo via `PHANTOM_MODE=1 python -m optimizer.web.dashboard`.

## Baseline

- HEAD at audit: `08f7698`, branch `main`.
- `pytest` → **45 passed**.
- Rollback tag: `pre-catchup-20260713-1919`.

## Checklist (PASS / GAP against §4)

| Category | Item | Status | Note |
|---|---|---|---|
| security | No live secrets in code or full git history | **PASS** | `git log --all -p | grep` for `sk-ant-…` / high-entropy `secret\|token\|api_key = "…"` → only variable names, the `.env.example` template, and a test fake (`api_key="sk-ant-fake"`). Clean. |
| security | `.env.example` current with referenced env vars | **FIXED** | Code read `OPTIMIZER_DATA_DIR` / `OPTIMIZER_DB_PATH` (config.py) but they were absent from the template → added a "Storage" section (both default to `./data`; Docker sets `OPTIMIZER_DATA_DIR=/data`). |
| security | No absolute local paths in code | **PASS (note)** | Only `scripts/deploy.sh` hard-codes NAS host/path (`cargo@192.168.50.3`, `/home/cargo/docker/…`) as **env-overridable defaults** in an ops script — acceptable; no `/Users` paths anywhere. |
| security | **No camera footage / snapshots / house data ever committed** (public repo) | **PASS** | `git log --all --stat | grep -iE '\.(jpg\|jpeg\|png\|mp4\|webp\|gif\|heic\|mov)'` → **zero**. `frigate_client.py` has only GET metadata helpers (no image bytes; `has_snapshot` is a 0/1 flag). `db.py` stores only labels/scores/timestamps/box coords. Dashboard templates have zero `<img>`/snapshot embeds. Matches the SECURITY-PRACTICES claim exactly. |
| security | `.gitignore` blocks footage defense-in-depth | **FIXED** | Added `*.jpg/*.jpeg/*.png/*.webp/*.gif/*.mp4/*.mov/*.heic` (nothing tracked today, so safe) so a future snapshot feature can't accidentally commit a frame; `git add -f` for intentional non-footage assets. |
| containers | Dockerfile non-root, base pinned to digest | **PASS** | System user `app` (uid/gid 1000, nologin), `USER app` before CMD; `FROM python:3.12-slim@sha256:090ba77e…` (resolved 2026-06-04, with a documented bump procedure). Dependabot has an open, unmerged PR proposing 3.14 — the 3.12 pin is a deliberate choice, not stale. |
| testing | 45 tests, all green; real critical-path coverage | **PASS** | `pytest` → 45 passed. The deterministic layer is genuinely (not shallowly) covered. |
| testing | class-swap + shape-mismatch detectors tested + documented | **PASS** | `tests/test_class_swap.py` (8), `tests/test_shape_mismatch.py` (4), `test_model_limit_uses_suspect_event_ids…` — reproduce the live cat↔dog scenario, share-based ranking, geometry cases; documented in ARCHITECTURE §"Detector ordering" + CHANGELOG. |
| testing | confidence-field population implemented + documented | **PASS (gap closed 2026-07-18)** | Rule-layer path had a per-type formula table + 7 tests. The Claude-path gap (`confidence` passed straight from LLM JSON; `db` wrote `float(x or 0.0)`) is now closed: `_clamp_confidence` clamps the Claude path to `[0,1]` and `_clamp01` guards the storage boundary, with a mocked Claude-success test. See Deferred #1. |
| phantom | Demo mode boots with zero secrets | **PASS** | Re-verified: `env -i … PHANTOM_MODE=1 python -m optimizer.web.dashboard` → `/api/health` 200 with no `*KEY*/*TOKEN*/*SECRET*` in the process env. Guards are layered (phantom short-circuits before DB/Claude; deferred `anthropic` import). No hard dependency introduced by the new detector code. |
| zma | ZMA integration coherent | **PASS** | Thin optional webhook (`post_status`), no-op when `ZMA_WEBHOOK_URL` unset, never raises, wired into `run_once` on complete/error. |
| docs | README/ARCHITECTURE/CONTRIBUTING/LICENSE/CHANGELOG present + accurate | **FIXED** | All present. Fixed staleness: README "What the analysis surfaces" table omitted `class_swap` + `shape_mismatch` (added 2 rows); ARCHITECTURE ASCII said "7 detectors" while its module table lists 9 (→ "9 detectors"). |
| ci | CI runs the suite + phantom smoke | **FIXED** | `.github/workflows/phantom.yml` was **referenced** by `.phantom.yml`, README, CONTRIBUTING and registered in `dependabot.yml` — but **no `.github/workflows/` existed**. Added a workflow running `pytest` + the zero-secret `PHANTOM_MODE=1` dashboard smoke boot (both validated locally). |

## Operational health (live deployment)

Pulled the real `analysis_runs` history from the NAS deployment:
- **Cadence PASS** — 30 nightly runs, 2026-06-14 → 2026-07-13, ~24h spacing, no missed nights in the
  visible window (spans the whole period since the 2026-06-11 detector hardening).
- **1 silent failure (RESOLVED 2026-07-18)** — the 2026-07-05 run errored (`ConnectionError:
  host='frigate' … Connection refused`), recovered the next night, but nothing alerted because
  `ZMA_WEBHOOK_URL` is unset in prod. Fixed: setup moved inside the alert path + opt-in ntfy failure
  poster. See Deferred #2.
- **Scheduler drift (RESOLVED 2026-07-18)** — the run clock permanently shifted 11:11 → 09:38 after
  that restart; the loop was a naive `sleep(interval)` keyed to the previous cycle's finish. Fixed
  with a fixed wall-clock anchor (`_next_run_at`, default 02:00). See Deferred #3.
- **Claude layer never run in prod** — all 30 runs log `ANTHROPIC_API_KEY unset; rule-layer fallback
  used`, `claude_cost_usd=0.0`. This is the designed graceful degradation (cost genuinely $0), not a
  bug — but it means the Claude interpretation path is unexercised live in addition to being
  untested (Deferred #1).

## Deferred / Backlog (behavior changes — out of scope for this security/professionalization pass)

> **Update 2026-07-18 (outstanding-items sweep):** Deferred #1–#3 RESOLVED. Details inline; see CHANGELOG (Unreleased → "Deferred audit items 2026-07-18"). 18 new tests, 63 total green.

1. ~~**Validate/clamp `confidence` on the Claude path + add a mocked Claude-success test.**~~
   **RESOLVED (2026-07-18)** — `_clamp_confidence` clamps the Claude-path `confidence` to `[0, 1]`
   (malformed/missing → conservative `0.5`) in `claude_layer.interpret()`; `_clamp01` enforces the
   same invariant at the `db.insert_recommendations` storage boundary. Mocked Claude-success test
   added.
2. ~~**Prod failure alerting.** Set `ZMA_WEBHOOK_URL` (or add an ntfy post on `status='error'`).~~
   **RESOLVED (2026-07-18)** — (a) run-cycle setup moved inside the try/except so setup failures
   (the 2026-07-05 shape) no longer escape silently; (b) opt-in `ntfy` failure poster (HTTP Basic
   auth, no-op until the four `NTFY_*` vars are set) wired to the run-error path. ZMA was found to be
   undeployed (no container/URL anywhere) and redundant with ntfy — ntfy (the deny-all, authenticated
   ecosystem pipeline at `http://192.168.50.7`) chosen as the real channel. **Ships disabled**: set
   `NTFY_URL`/`NTFY_TOPIC`/`NTFY_USER`/`NTFY_PASS` in the NAS `.env` to activate (credential not
   copied from wyzegrid-notify — owner to provide).
3. ~~**Anchor the scheduler to a wall-clock time** to stop nightly drift.~~
   **RESOLVED (2026-07-18)** — `scheduler._next_run_at` anchors the daily run to
   `OPTIMIZER_RUN_AT_HOUR:_MINUTE` (default 02:00), computed fresh each cycle; stable across restarts,
   tested. Note: this shifts the prod run time to 02:00 local (was drifting ~09:38).
4. **Optional niceties:** vendor Chart.js locally (dashboard currently loads it from a CDN — offline
   fragility, minor supply-chain surface); add `SECURITY.md` (GitHub disclosure policy) and
   issue/PR templates.

## Verdict

Genuinely solid, professionally-built repo. The Phase-1 read-only guarantee, the **no-footage
guarantee** (verified: zero images ever committed, no image-writing code path, no `<img>` in the
dashboard), phantom mode (zero-secret boot re-verified), and the detector-hardening work
(class-swap / shape-mismatch, both tested + documented) all hold up. This pass fixed the doc
staleness, added the missing CI workflow (the single largest professionalization gap — promised in
four places but absent), tightened `.env.example`, and added defense-in-depth image ignores. The
remaining items are behavior changes (confidence clamp, failure alerting, scheduler anchoring)
deferred to their own tasks — the biggest of these is that the Claude interpretation path is both
untested and unexercised in production, which is fine while prod runs rules-only but must be closed
before Phase-2 auto-apply relies on `confidence`.
