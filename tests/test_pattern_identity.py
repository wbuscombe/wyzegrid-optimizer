"""F5 identity gate (I-1..I-6) and the no-LLM static check (I-10).

Synthetic data only. Identity comes only from explicit upstream labels
through IDENTITY_LABEL_MAP, which ships empty; the test-local maps below
exist only inside these tests.
"""
from __future__ import annotations

import re
from pathlib import Path

from optimizer.analysis import service_schedule
from optimizer.patterns import identity, synthetic
from optimizer.patterns.settings import (DEFAULT_PARAMS, IDENTITY_LABEL_MAP, UNIDENTIFIED_TEXT,
                                         PatternParams)

from .pattern_helpers import (POSITIVE_SEED, planted_origin, positive_payloads, positive_run,
                              run_payloads)

SRC = Path(__file__).resolve().parents[1] / "src" / "optimizer"
TEST_LABEL = "svc_label_x"        # a test-local explicit upstream label
TEST_IDENTITY = "service_x"       # the identity a test-local map assigns to it
TEST_MAP = PatternParams(identity_label_map={TEST_LABEL: TEST_IDENTITY})


def _p1(patterns):
    found = [p for p in patterns if planted_origin(p) == "P1"]
    assert len(found) == 1, [(p["cadence"], p["weekdays"]) for p in found]
    return found[0]


def _labelled_run(p1_labels, params=TEST_MAP):
    return run_payloads(synthetic.generate(POSITIVE_SEED, p1_labels=p1_labels), params).patterns


def test_i1_empty_map_leaves_every_pattern_unidentified():
    assert dict(IDENTITY_LABEL_MAP) == {} and dict(DEFAULT_PARAMS.identity_label_map) == {}
    patterns = positive_run().patterns
    assert patterns
    for p in patterns:
        assert p["identity"] is None
        assert p["identity_reason"] == identity.NO_LABEL == "no explicit upstream label"
        assert p["label"] == UNIDENTIFIED_TEXT


def test_i2_label_on_80_percent_of_p1_members_assigns_identity():
    p1 = _p1(_labelled_run(((TEST_LABEL, 0.8),)))
    assert p1["identity"] == TEST_IDENTITY
    assert p1["identity_reason"] == "explicit upstream label"
    assert p1["label"] == TEST_IDENTITY


def test_i3_label_on_40_percent_is_insufficient():
    p1 = _p1(_labelled_run(((TEST_LABEL, 0.4),)))
    assert p1["identity"] is None
    assert p1["identity_reason"] == identity.INSUFFICIENT == "insufficient label support"


def test_i4_two_mapped_labels_half_and_half_conflict():
    params = PatternParams(identity_label_map={"svc_label_a": "service_a",
                                               "svc_label_b": "service_b"})
    p1 = _p1(_labelled_run((("svc_label_a", 0.5), ("svc_label_b", 0.5)), params))
    assert p1["identity"] is None
    assert p1["identity_reason"] == identity.CONFLICTING == "conflicting labels"


def test_i5_base_labels_never_produce_identity():
    # A map keyed by base labels must still assign nothing: base labels are
    # never explicit upstream labels.
    params = PatternParams(identity_label_map={lab: "service_x"
                                               for lab in ("car", "truck", "bus", "motorcycle",
                                                           "person")})
    patterns = run_payloads(positive_payloads(), params).patterns
    assert patterns
    assert all(p["identity"] is None and p["identity_reason"] == identity.NO_LABEL
               for p in patterns)


def test_i6_service_schedules_stay_insufficient_with_a_strong_unlabeled_pattern():
    # The positive scenario holds a strong weekly street pattern with no labels.
    p1 = _p1(positive_run().patterns)
    assert p1["confidence"] == "strong" and p1["identity"] is None
    result = service_schedule.detect(list(positive_payloads()), timezone_name=synthetic.TZ_NAME)
    statuses = {row["category"]: row["status"] for row in result["schedules"]}
    assert statuses == {"garbage": "insufficient-evidence", "recycling": "insufficient-evidence",
                        "mail": "insufficient-evidence", "package": "insufficient-evidence"}
    # Positive control: the same learner does learn once P1 carries an explicit
    # service label, so the assertion above can fail. The pattern stays
    # unidentified because the shipped map is empty.
    labelled = synthetic.generate(POSITIVE_SEED, p1_labels=(("garbage_truck", 1.0),))
    learned = service_schedule.detect(labelled, timezone_name=synthetic.TZ_NAME)
    assert {r["category"]: r["status"] for r in learned["schedules"]}["garbage"] == "learned"
    assert _p1(run_payloads(labelled).patterns)["identity"] is None


# ---- I-10: no new code path imports or calls the LLM layer --------------------

LLM_REFERENCE = re.compile(r"claude_layer|anthropic|ANTHROPIC|\binterpret\s*\(")
NEW_CODE = sorted((SRC / "patterns").glob("*.py")) + [SRC / "web" / "patterns.py"]


def llm_references(text: str) -> list[str]:
    return LLM_REFERENCE.findall(text)


def test_i10_no_new_code_path_imports_or_calls_the_llm_layer():
    # Positive control: the check fires on a planted import and call.
    planted = "from ..claude_" + "layer import interpret\nresult = interpret(findings)\n"
    assert len(llm_references(planted)) == 2
    assert len(NEW_CODE) >= 14
    for path in NEW_CODE:
        assert llm_references(path.read_text()) == [], path.name
