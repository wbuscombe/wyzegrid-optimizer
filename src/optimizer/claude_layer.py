"""
Claude (Haiku) interpretation layer.

Takes the structured findings dict from the deterministic layer, sends it to
Haiku as a JSON payload, gets back a list of recommendations tagged
safe / risky / model-limit. Logs token usage and computed cost.

Design constraints (encoded in the system prompt below):
  - Never recommend touching the detection model, stream routing, or camera
    config — only the config knobs (threshold, min_score, min_area, zones,
    motion masks, stationary.max_frames).
  - Be honest about model-limit findings: if the data shows the model can't
    do something at this resolution, the recommendation MUST be "model-limit
    — no config change will fix this," NOT a futile threshold tweak.
  - Output JSON only; no prose outside the JSON block.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# Approximate Haiku 4.5 pricing — used for the bookkeeping field only. Values
# are documented at console.anthropic.com; bump as the price page changes.
HAIKU_INPUT_USD_PER_MTOK = 1.00
HAIKU_OUTPUT_USD_PER_MTOK = 5.00


SYSTEM_PROMPT = """You are the interpretation layer of a Frigate detection-quality \
optimizer. You read structured statistical findings (already computed) and write \
plain-language recommendations as a JSON array.

HARD RULES — NON-NEGOTIABLE:

1. Recommendations may ONLY target these config knobs:
   - `threshold` (Frigate per-object filter)
   - `min_score`
   - `min_area`
   - `zones`
   - `motion.mask` / `motion.threshold` / `motion.contour_area`
   - `stationary.max_frames`

   You MUST NOT recommend changes to:
   - the detection model
   - stream routing / camera firmware / video resolution
   - hardware (Coral, GPU, etc.)
   - anything outside Frigate config

2. For findings flagged as model-limit (a label tracked by a camera but yielding \
zero detections despite the camera being active), the recommendation MUST be \
risk_class: "model-limit" and proposed_value: null, with the rationale stating \
plainly that this is a model limit and no config change will fix it. Do not \
invent a threshold tweak for a model-limit finding.

3. Output is a JSON object with one key "recommendations": [ ... ]. Each entry is:
   {
     "camera": str | null,
     "label": str | null,
     "param": str | null,
     "current_value": str | number | null,
     "proposed_value": str | number | null,
     "rationale": str,                  // one or two sentences, plain English
     "risk_class": "safe" | "risky" | "model-limit",
     "confidence": float (0-1),
     "expected_effect": str             // one sentence
   }

4. Conservative defaults: prefer fewer, higher-quality recommendations over \
many marginal ones. If the findings are clean, return an empty array.

5. Output JSON ONLY. No prose, no markdown fences, no commentary.
"""


USER_TEMPLATE = """Frigate detection findings for the last analysis window.

Current per-(camera,label) config snapshot (so you can reference current values):
{config_snapshot}

Deterministic findings (summarized, NOT raw events):
{findings}

Produce the JSON recommendations array per the system prompt."""


@dataclass
class ClaudeResult:
    used: bool
    recommendations: list[dict]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: Optional[str] = None


def _cost(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1_000_000 * HAIKU_INPUT_USD_PER_MTOK
        + output_tokens / 1_000_000 * HAIKU_OUTPUT_USD_PER_MTOK
    )


def _rule_layer_fallback_recs(findings: dict, config_map: dict) -> list[dict]:
    """When Claude isn't available, emit recommendations directly from the
    rule layer with sensible default risk classes. No LLM polish, but the
    service still produces actionable output."""
    recs: list[dict] = []

    # Model-limit findings → must be tagged honestly
    for f in findings.get("model_limit", {}).get("candidates", []):
        recs.append({
            "camera": f["camera"],
            "label": f["label"],
            "param": None,
            "current_value": None,
            "proposed_value": None,
            "rationale": (
                f"Camera produced {f['camera_total_events']} detections of other "
                f"labels but zero of '{f['label']}'. Almost certainly a model-limit "
                "on the CPU-only model; no config change will fix this."
            ),
            "risk_class": "model-limit",
            "confidence": 0.85,
            "expected_effect": "No change recommended; documented as model limit.",
        })

    # Tuning candidates from threshold_proximity
    for c in findings.get("threshold_proximity", {}).get("candidates", []):
        if not c.get("is_tuning_candidate"):
            continue
        thr = c["threshold"]
        # Conservative recommendation: lower threshold by 0.02 if many events sit just below
        cfg = config_map.get((c["camera"], c["label"]), {}) or {}
        proposed = round(max(0.50, thr - 0.02), 2)
        recs.append({
            "camera": c["camera"],
            "label": c["label"],
            "param": "threshold",
            "current_value": thr,
            "proposed_value": proposed,
            "rationale": (
                f"{c['in_band']} of {c['total']} detections sit within ±{c['band']:.2f} "
                f"of the current threshold ({thr}). A small reduction would shift the "
                "decision boundary on a meaningful number of borderline events."
            ),
            "risk_class": "safe" if proposed >= 0.55 else "risky",
            "confidence": 0.6,
            "expected_effect": "Marginal increase in detections of this label; monitor for FP uptick.",
        })

    # Stationary flicker hotspots → stationary.max_frames candidate
    for f in findings.get("stationary", {}).get("flicker_hotspots", []):
        current = f.get("current_stationary_max_frames")
        proposed = (current or 200) // 2 if current and current > 50 else None
        recs.append({
            "camera": f["camera"],
            "label": f["label"],
            "param": "stationary.max_frames",
            "current_value": current,
            "proposed_value": proposed,
            "rationale": (
                f"{f['flicker_event_count']} brief detections at the same position "
                "indicate a parked-object / fixed-misclassification pattern. Lowering "
                "stationary.max_frames would stop re-triggering on the stationary item."
            ),
            "risk_class": "safe",
            "confidence": 0.7,
            "expected_effect": "Stop re-triggering on the parked/fixed object.",
        })

    return recs


def interpret(
    findings: dict,
    config_map: dict,
    *,
    api_key: str,
    model: str = "claude-haiku-4-5",
    token_budget: int = 20_000,
) -> ClaudeResult:
    """Run the Claude interpretation pass. Falls back to rule-layer recommendations
    if api_key is empty or the call fails."""

    if not api_key:
        recs = _rule_layer_fallback_recs(findings, config_map)
        return ClaudeResult(used=False, recommendations=recs, input_tokens=0,
                            output_tokens=0, cost_usd=0.0,
                            error="ANTHROPIC_API_KEY unset; rule-layer fallback used")

    # Defer the import so the optional dep doesn't crash phantom-mode boots
    try:
        from anthropic import Anthropic
    except Exception as e:  # pragma: no cover
        recs = _rule_layer_fallback_recs(findings, config_map)
        return ClaudeResult(used=False, recommendations=recs, input_tokens=0,
                            output_tokens=0, cost_usd=0.0,
                            error=f"anthropic SDK not importable: {e}")

    client = Anthropic(api_key=api_key)
    config_snapshot = {
        f"{cam}::{label}": v for (cam, label), v in sorted(config_map.items())
    }
    user_msg = USER_TEMPLATE.format(
        config_snapshot=json.dumps(config_snapshot, indent=2),
        findings=json.dumps(findings, indent=2),
    )

    # Rough input-token estimate (chars/4) — if we're already over budget,
    # fall back without calling.
    est_input = (len(SYSTEM_PROMPT) + len(user_msg)) // 4
    if est_input > token_budget:
        recs = _rule_layer_fallback_recs(findings, config_map)
        return ClaudeResult(used=False, recommendations=recs, input_tokens=0,
                            output_tokens=0, cost_usd=0.0,
                            error=f"estimated input {est_input} tokens > budget {token_budget}; fallback used")

    try:
        msg = client.messages.create(
            model=model,
            max_tokens=min(4000, max(500, token_budget - est_input)),
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as e:
        recs = _rule_layer_fallback_recs(findings, config_map)
        return ClaudeResult(used=False, recommendations=recs, input_tokens=0,
                            output_tokens=0, cost_usd=0.0,
                            error=f"claude call failed: {type(e).__name__}: {e}")

    raw = "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
    try:
        payload = json.loads(raw)
        recs = payload.get("recommendations", [])
        if not isinstance(recs, list):
            raise ValueError("recommendations not a list")
    except Exception as e:
        logger.warning("Claude returned non-JSON payload: %s — using fallback", e)
        recs = _rule_layer_fallback_recs(findings, config_map)
        return ClaudeResult(
            used=True,
            recommendations=recs,
            input_tokens=getattr(msg.usage, "input_tokens", 0),
            output_tokens=getattr(msg.usage, "output_tokens", 0),
            cost_usd=_cost(getattr(msg.usage, "input_tokens", 0),
                           getattr(msg.usage, "output_tokens", 0)),
            error=f"non-JSON output: {e}",
        )

    in_tok = getattr(msg.usage, "input_tokens", 0)
    out_tok = getattr(msg.usage, "output_tokens", 0)
    return ClaudeResult(
        used=True,
        recommendations=recs,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=_cost(in_tok, out_tok),
    )
