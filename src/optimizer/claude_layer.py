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

2a. CLASS-SWAP findings (a (camera, label) whose events are likely \
misclassifications of a different class — e.g. a visiting cat being labeled \
"dog") are ALWAYS risk_class: "model-limit" with proposed_value: null. \
Threshold/min_area/zone changes cannot fix which LABEL the model assigns to \
something it sees — the model is confusing two classes, period. Recommending a \
config tweak for a class-swap finding is forbidden. The rationale must state \
which class the events are likely being confused with.

2b. SHAPE-MISMATCH findings (person events whose box geometry is animal-shaped \
— low + short — and whose score is below the person confident range) are \
ALWAYS risk_class: "model-limit" with proposed_value: null. Same reason: this \
is class confusion the model is doing, not a config knob.

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


# Conservative fallback when Claude omits or malforms `confidence`. Kept mid-low
# so a broken LLM response never yields an auto-applyable rec (Phase-2's
# auto-apply gate reads this field) yet stays non-zero so the rec still surfaces
# for human review. The rule-layer path already guarantees (0, 1] via its
# min()-capped formulas; this brings the Claude path to the same invariant.
DEFAULT_CLAUDE_CONFIDENCE = 0.5


def _clamp_confidence(value: object) -> float:
    """Coerce a Claude-supplied confidence into [0.0, 1.0].

    Missing / None / non-numeric / NaN / inf → DEFAULT_CLAUDE_CONFIDENCE;
    otherwise clamped into range. Protects the Phase-2 auto-apply invariant from
    an LLM that emits 1.5, -0.2, "high", or omits the field entirely.
    """
    try:
        c = float(value)
    except (TypeError, ValueError):
        return DEFAULT_CLAUDE_CONFIDENCE
    if c != c or c in (float("inf"), float("-inf")):  # NaN / ±inf
        return DEFAULT_CLAUDE_CONFIDENCE
    return max(0.0, min(1.0, c))


def _rule_layer_fallback_recs(findings: dict, config_map: dict) -> list[dict]:
    """When Claude isn't available, emit recommendations directly from the
    rule layer with sensible default risk classes. No LLM polish, but the
    service still produces actionable output.

    Every recommendation populates `confidence` in [0, 1] using a transparent,
    data-driven heuristic per type — Phase 2's auto-apply gate reads this
    field, so the heuristic is documented in ARCHITECTURE.md. The rule:
    more data + stronger pattern → higher confidence; thin or noisy → lower."""
    recs: list[dict] = []

    # ---- Class-swap findings (CRITICAL — runs before model-limit so duplicates
    #      are suppressed) ---------------------------------------------------
    swap_pairs_emitted: set[tuple[str, str]] = set()
    for f in findings.get("class_swap", {}).get("candidates", []):
        confidence = min(0.95, 0.55 + 0.04 * min(10, f["overlap_count"]))
        recs.append({
            "camera": f["camera"],
            "label": f["label"],
            "param": None,
            "current_value": None,
            "proposed_value": None,
            "rationale": (
                f"{f['overlap_count']} '{f['label']}' events on {f['camera']} share "
                f"box position and time with '{f['confused_with']}' events; mean "
                f"score {f['mean_score']:.2f} is below the label's confident range "
                f"({f['label_confident_low']:.2f}). The model is confusing the two "
                f"classes — config cannot fix class assignment."
            ),
            "risk_class": "model-limit",
            "confidence": confidence,
            "expected_effect": "No change recommended; documented as class confusion.",
        })
        swap_pairs_emitted.add((f["camera"], f["label"]))

    # ---- Shape-mismatch findings ----------------------------------------------
    for f in findings.get("shape_mismatch", {}).get("candidates", []):
        confidence = min(0.85, 0.45 + 0.08 * min(5, f["count"]))
        recs.append({
            "camera": f["camera"],
            "label": f.get("label", "person"),
            "param": None,
            "current_value": None,
            "proposed_value": None,
            "rationale": (
                f"{f['count']} 'person' events on {f['camera']} have animal-like "
                f"box geometry (low + short) and scores below the person confident "
                f"range ({f['person_confident_low']:.2f}). Likely misclassified "
                f"animal. Config cannot fix class confusion."
            ),
            "risk_class": "model-limit",
            "confidence": confidence,
            "expected_effect": "No change recommended; surfaced for human review.",
        })

    # ---- Zero-detection model-limit ------------------------------------------
    for f in findings.get("model_limit", {}).get("candidates", []):
        # Skip if class-swap already covered this (camera, label) — same
        # underlying weakness, no need for two rows.
        if (f["camera"], f["label"]) in swap_pairs_emitted:
            continue
        total = f["camera_total_events"]
        # More other-label activity → stronger evidence the camera is "live"
        # and the specific label just doesn't classify. Cap at 0.92.
        confidence = min(0.92, 0.55 + 0.001 * min(500, total))
        recs.append({
            "camera": f["camera"],
            "label": f["label"],
            "param": None,
            "current_value": None,
            "proposed_value": None,
            "rationale": (
                f"Camera produced {total} detections of other labels but zero of "
                f"'{f['label']}' (after excluding class-swap suspects). Almost "
                f"certainly a model-limit on the CPU-only model; no config change "
                f"will fix this."
            ),
            "risk_class": "model-limit",
            "confidence": confidence,
            "expected_effect": "No change recommended; documented as model limit.",
        })

    # ---- Threshold-proximity tuning candidates -------------------------------
    for c in findings.get("threshold_proximity", {}).get("candidates", []):
        if not c.get("is_tuning_candidate"):
            continue
        thr = c["threshold"]
        proposed = round(max(0.50, thr - 0.02), 2)
        in_band_pct = c.get("in_band_pct", 0.0)
        total = c.get("total", 0)
        # Confidence: cluster density (in-band share) + sample-size signal.
        confidence = min(0.90, 0.40 + 0.35 * in_band_pct + 0.25 * min(1.0, total / 100))
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
            "confidence": confidence,
            "expected_effect": (
                "Marginal increase in detections of this label; monitor for FP uptick."
            ),
        })

    # Stationary flicker hotspots → stationary.max_frames candidate.
    # Multiple hotspot buckets on the same (camera, label) all propose the SAME
    # config change — collapse to one recommendation per (camera, label, param)
    # with a combined evidence count.
    from collections import defaultdict
    stationary_groups: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"flicker_total": 0, "hotspots": 0, "current": None}
    )
    for f in findings.get("stationary", {}).get("flicker_hotspots", []):
        key = (f["camera"], f["label"])
        stationary_groups[key]["flicker_total"] += f["flicker_event_count"]
        stationary_groups[key]["hotspots"] += 1
        stationary_groups[key]["current"] = f.get("current_stationary_max_frames")

    for (cam, label), agg in stationary_groups.items():
        current = agg["current"]
        proposed = (current or 200) // 2 if current and current > 50 else None
        # Confidence: more hotspots × more events per hotspot = stronger signal.
        confidence = min(
            0.92,
            0.50 + 0.05 * min(8, agg["hotspots"]) + 0.001 * min(400, agg["flicker_total"]),
        )
        recs.append({
            "camera": cam,
            "label": label,
            "param": "stationary.max_frames",
            "current_value": current,
            "proposed_value": proposed,
            "rationale": (
                f"{agg['flicker_total']} brief detections across "
                f"{agg['hotspots']} fixed positions indicate parked-object / "
                f"fixed-misclassification patterns. Lowering stationary.max_frames "
                f"would stop re-triggering on these stationary items."
            ),
            "risk_class": "safe",
            "confidence": confidence,
            "expected_effect": "Stop re-triggering on parked/fixed objects.",
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
                            error=(f"estimated input {est_input} tokens > "
                                   f"budget {token_budget}; fallback used"))

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
        # Claude path: enforce the same (0, 1] confidence invariant the rule-layer
        # already holds. An LLM can emit 1.5, -0.2, "high", or omit the field —
        # any of which would otherwise reach the Phase-2 auto-apply gate as-is.
        for r in recs:
            if isinstance(r, dict):
                r["confidence"] = _clamp_confidence(r.get("confidence"))
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
