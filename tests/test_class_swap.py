"""
Tests for the class-swap detector.

The canonical scenario reproduces the live 2026-06-11 finding: back_deck_cam
"dog" events that are actually a visiting cat being mislabeled. Both labels
alternate at roughly the same box position within minutes of each other.
"""
from __future__ import annotations

import pytest

from optimizer.analysis import class_swap, model_limit, run_all


def _ev(eid, camera, label, score, t, box, dur=3.0):
    return {
        "id": eid,
        "camera": camera,
        "label": label,
        "score": score,
        "start_time": t,
        "end_time": t + dur,
        "box": box,
        "false_positive": False,
    }


@pytest.fixture
def back_deck_cat_dog_swap_events():
    """The live 2026-06-11 pattern, synthesized.

    - back_deck_cam: 6 low-confidence "dog" events at box_y≈0.37 (h≈0.30)
                     plus 5 "cat" events at the SAME box position within minutes.
    - back_yard_cam: 12 healthy "dog" events at score 0.75 (the real-dog
                     baseline that pushes the dog-confident-low above 0.65).
    - back_deck_cam: 100 person events so the camera has >=50 total
                     (otherwise model_limit won't even consider it).
    """
    events = []
    t0 = 1717500000.0
    DOG_BOX = [0.20, 0.35, 0.18, 0.30]
    CAT_BOX = [0.21, 0.37, 0.17, 0.30]
    # Six low-conf "dog" events on back_deck_cam at the cat's spot
    for i in range(6):
        events.append(_ev(f"deck_dog_{i}", "back_deck_cam", "dog",
                          score=0.55 + 0.02 * (i % 3), t=t0 + i * 120, box=DOG_BOX))
    # Five "cat" events at the same spot within a few minutes of each "dog"
    for i in range(5):
        events.append(_ev(f"deck_cat_{i}", "back_deck_cam", "cat",
                          score=0.62, t=t0 + 60 + i * 130, box=CAT_BOX))
    # Healthy real dogs on the yard — pushes the dog-confident-low above 0.65
    # so back_deck dogs at 0.55-0.59 fall BELOW it.
    for i in range(20):
        events.append(_ev(f"yard_dog_{i}", "back_yard_cam", "dog",
                          score=0.75 + 0.05 * (i % 4), t=t0 + i * 600,
                          box=[0.40, 0.50, 0.20, 0.35]))
    # back_deck person activity so the camera qualifies for model_limit consideration
    for i in range(100):
        events.append(_ev(f"deck_person_{i}", "back_deck_cam", "person",
                          score=0.70, t=t0 + i * 60,
                          box=[0.30, 0.15, 0.20, 0.65]))
    return events


@pytest.fixture
def back_deck_config_map():
    return {
        ("back_deck_cam", "person"): {"min_score": 0.55, "threshold": 0.65,
                                       "min_area": 3000, "stationary_max_frames": None,
                                       "zones": []},
        ("back_deck_cam", "dog"): {"min_score": 0.45, "threshold": 0.55,
                                    "min_area": 1500, "stationary_max_frames": None,
                                    "zones": []},
        ("back_deck_cam", "cat"): {"min_score": 0.45, "threshold": 0.55,
                                    "min_area": 1000, "stationary_max_frames": None,
                                    "zones": []},
        ("back_yard_cam", "dog"): {"min_score": 0.45, "threshold": 0.55,
                                    "min_area": 1500, "stationary_max_frames": None,
                                    "zones": []},
    }


def test_class_swap_detects_back_deck_cat_dog(back_deck_cat_dog_swap_events,
                                              back_deck_config_map):
    result = class_swap.detect(back_deck_cat_dog_swap_events, back_deck_config_map)
    dog_swap = [c for c in result["candidates"]
                if c["camera"] == "back_deck_cam" and c["label"] == "dog"]
    assert len(dog_swap) == 1
    swap = dog_swap[0]
    assert swap["confused_with"] == "cat"
    assert swap["mean_score"] < swap["label_confident_low"]
    assert swap["overlap_count"] >= 3
    # The detector exports the suspect ids so model_limit can subtract them
    assert all(eid.startswith("deck_dog_") for eid in result["suspect_event_ids"])
    assert len(result["suspect_event_ids"]) == 6


def test_healthy_data_is_not_flagged_as_class_swap():
    """Real dogs at healthy scores, NO co-located other-label events: no swap."""
    t0 = 1717500000.0
    events = []
    for i in range(20):
        events.append(_ev(f"yard_dog_{i}", "front_yard_cam", "dog",
                          score=0.80, t=t0 + i * 600, box=[0.4, 0.5, 0.2, 0.3]))
    result = class_swap.detect(events, {})
    assert result["candidates"] == []
    assert result["suspect_event_ids"] == []


def test_class_swap_requires_overlap_with_another_label():
    """Low scores alone aren't enough — there must be a co-located other label."""
    t0 = 1717500000.0
    events = []
    # Low-conf dogs alone — no cat events to overlap with
    for i in range(8):
        events.append(_ev(f"d_{i}", "back_deck_cam", "dog",
                          score=0.48, t=t0 + i * 120, box=[0.3, 0.4, 0.2, 0.3]))
    # Plus a baseline of healthy dogs elsewhere so confident_low is computed
    for i in range(20):
        events.append(_ev(f"yd_{i}", "back_yard_cam", "dog",
                          score=0.80, t=t0 + i * 600, box=[0.4, 0.5, 0.2, 0.3]))
    result = class_swap.detect(events, {})
    assert result["candidates"] == []


def test_model_limit_uses_suspect_event_ids_to_stay_flagged():
    """The corruption fix: if back_deck/dog's only events are class-swap suspects,
    the model-limit detector must still flag dog as model-limit."""
    t0 = 1717500000.0
    events = []
    # 100 back_deck persons so the camera meets the >=50 total threshold
    for i in range(100):
        events.append(_ev(f"p_{i}", "back_deck_cam", "person",
                          score=0.70, t=t0 + i * 60, box=[0.3, 0.15, 0.2, 0.65]))
    # 6 "dog" events on back_deck (the swaps)
    for i in range(6):
        events.append(_ev(f"deck_dog_{i}", "back_deck_cam", "dog",
                          score=0.55, t=t0 + i * 120, box=[0.20, 0.37, 0.18, 0.30]))

    # Without suspect filtering: model_limit sees dog=6 events → NOT flagged.
    naive = model_limit.detect(
        events,
        {("back_deck_cam", "dog"): {"threshold": 0.55}},
        suspect_event_ids=None,
    )
    assert not any(f["label"] == "dog" for f in naive["candidates"])

    # With the 6 swap ids in suspect_event_ids: dog → 0 effective events → FLAGGED.
    suspect = [f"deck_dog_{i}" for i in range(6)]
    hardened = model_limit.detect(
        events,
        {("back_deck_cam", "dog"): {"threshold": 0.55}},
        suspect_event_ids=suspect,
    )
    dog_flag = [f for f in hardened["candidates"]
                if f["camera"] == "back_deck_cam" and f["label"] == "dog"]
    assert len(dog_flag) == 1
    assert "excluding class-swap" in dog_flag[0]["evidence"]


def test_run_all_threads_suspect_ids_through_class_swap_into_model_limit(
        back_deck_cat_dog_swap_events, back_deck_config_map):
    """End-to-end: run_all wires class_swap → model_limit. back_deck/dog must
    appear in BOTH class_swap.candidates AND model_limit.candidates."""
    findings = run_all(back_deck_cat_dog_swap_events, back_deck_config_map,
                       history_events=[])
    swap = [c for c in findings["class_swap"]["candidates"]
            if c["camera"] == "back_deck_cam" and c["label"] == "dog"]
    assert len(swap) == 1
    # And model_limit still flags it because the dog events were all suspect
    ml = [c for c in findings["model_limit"]["candidates"]
          if c["camera"] == "back_deck_cam" and c["label"] == "dog"]
    assert len(ml) == 1
    # But back_deck_cam/cat is NOT model-limit (the cats are real — kept by the filter)
    cat_ml = [c for c in findings["model_limit"]["candidates"]
              if c["camera"] == "back_deck_cam" and c["label"] == "cat"]
    assert cat_ml == []


def test_class_swap_prefers_rare_class_over_common_class_noise():
    """The canonical case: a back_deck dog overlaps with BOTH cats (rare) and
    persons (numerous). The detector must pick CAT because the cat-overlap
    share (overlap / total cats) is high while person-overlap share is low —
    persons being numerous doesn't make them the confusion target.

    This pins the share-based ranking. Without it, raw-overlap-count picks
    person every time on a busy camera, masking real cat↔dog confusion."""
    t0 = 1717500000.0
    DOG_BOX = [0.20, 0.37, 0.18, 0.28]
    CAT_BOX = [0.21, 0.37, 0.17, 0.30]
    # Person at distance with similar height & centroid — fits the overlap
    # check but is numerous (low share).
    PERSON_BOX = [0.30, 0.32, 0.20, 0.30]
    events = []
    for i in range(8):
        events.append(_ev(f"deck_dog_{i}", "back_deck_cam", "dog",
                          score=0.55, t=t0 + i * 60, box=DOG_BOX))
    # Cat: 10 events, all within an hour of dog events → most cats overlap
    for i in range(10):
        events.append(_ev(f"deck_cat_{i}", "back_deck_cam", "cat",
                          score=0.62, t=t0 + 30 + i * 60, box=CAT_BOX))
    # Person: 200 events spread over the same window → many will overlap
    # by raw count but the share is tiny
    for i in range(200):
        events.append(_ev(f"deck_p_{i}", "back_deck_cam", "person",
                          score=0.70, t=t0 + i * 30, box=PERSON_BOX))
    # Real-dog baseline so confident_low > 0.55
    for i in range(20):
        events.append(_ev(f"yard_dog_{i}", "front_yard_cam", "dog",
                          score=0.80, t=t0 + i * 600,
                          box=[0.40, 0.50, 0.20, 0.32]))
    result = class_swap.detect(events, {})
    dog_swap = [c for c in result["candidates"]
                if c["camera"] == "back_deck_cam" and c["label"] == "dog"]
    assert len(dog_swap) == 1
    # The whole point: cat wins because its share is high, despite person
    # having more raw overlaps.
    assert dog_swap[0]["confused_with"] == "cat"


def test_class_swap_uses_data_driven_confident_low():
    """The detector should compute confident_low from the data, not a hardcoded
    number. Verify: when all dog scores are uniformly high, dogs at 0.65 are
    BELOW the data-driven low — and would qualify if other criteria met."""
    t0 = 1717500000.0
    events = []
    for i in range(40):
        events.append(_ev(f"baseline_{i}", "back_yard_cam", "dog",
                          score=0.85, t=t0 + i * 60, box=[0.3, 0.5, 0.2, 0.3]))
    # The 25th percentile of 40 uniform 0.85 dogs is 0.85, so even a 0.70-dog
    # falls below confident_low — but with no overlapping cat events, still
    # not a swap candidate.
    for i in range(6):
        events.append(_ev(f"low_{i}", "front_yard_cam", "dog",
                          score=0.70, t=t0 + i * 120, box=[0.3, 0.4, 0.2, 0.3]))
    result = class_swap.detect(events, {})
    assert result["candidates"] == []  # no overlap → no swap claim
