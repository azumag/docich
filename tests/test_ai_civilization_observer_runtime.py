"""Runtime behaviour tests for the AI civilization observer corner (#1705).

These pin the follow-up cards from docs/features/ai-civ-observer-corner.md:
bounded observation I/O, exact window diff, duplicate-event suppression,
deterministic topic selection, the structured diary draft, atomic record
persistence, and the bounded start/topic/end progression.  The module is
offline by construction; no test performs network I/O.
"""

import copy
import json
from pathlib import Path

import pytest

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "ai_civilization_observer"
    / "observation_v1.json"
)


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def runtime():
    from docich import ai_civilization_observer_runtime as mod

    return mod


def test_runtime_loads_the_bounded_fixture():
    mod = runtime()
    data = mod.load_observation(FIXTURE)
    assert data["scenario"] == "basic-diff"
    assert mod.is_synthetic(data) is True


def test_runtime_derives_exact_window_births_and_deaths():
    mod = runtime()
    data = mod.load_observation(FIXTURE)
    diff = mod.diff_observation(data, data["previous"])
    expected = data["derivedExpectations"]
    assert diff["hasDiffBasis"] is True
    assert diff["tick"] == data["snapshot"]["tick"]
    assert diff["previousTick"] == data["previous"]["tick"]
    assert diff["births"] == expected["windowBirths"]
    assert diff["deaths"] == expected["windowDeaths"]
    assert diff["populationLiving"] == expected["living"]
    assert diff["populationDelta"] == expected["living"] - data["previous"]["metrics"]["populationLiving"]
    # living = previous living + births - deaths
    assert expected["living"] == (
        data["previous"]["metrics"]["populationLiving"] + diff["births"] - diff["deaths"]
    )


def test_runtime_reports_unavailable_diff_without_a_complete_previous_set():
    mod = runtime()
    data = mod.load_observation(FIXTURE)
    diff = mod.diff_observation(data, None)
    assert diff["hasDiffBasis"] is False
    assert diff["births"] is None and diff["deaths"] is None
    assert diff["populationDelta"] is None


def test_runtime_rejects_an_incomplete_previous_evidence_set():
    mod = runtime()
    data = load_fixture()
    region_id = data["snapshot"]["regionId"]

    duplicated = copy.deepcopy(data["previous"])
    duplicated["livingAgentIds"] = ["agent-0", "agent-0", "agent-1"]
    duplicated["metrics"] = {"populationLiving": 3, "regionPopulation": {region_id: 3}}
    with pytest.raises(ValueError):
        mod.validate_previous(duplicated, region_id)

    over_cap = copy.deepcopy(data["previous"])
    ids = [f"agent-{i}" for i in range(mod.OBSERVER_MAX_TRACKED_AGENT_IDS + 1)]
    over_cap["livingAgentIds"] = ids
    over_cap["metrics"] = {"populationLiving": len(ids), "regionPopulation": {region_id: len(ids)}}
    with pytest.raises(ValueError):
        mod.validate_previous(over_cap, region_id)


def test_runtime_selects_topics_deterministically_within_the_corner_bound():
    mod = runtime()
    data = mod.load_observation(FIXTURE)
    diff = mod.diff_observation(data, data["previous"])
    topics = mod.select_topics(diff)
    assert len(topics) <= mod.OBSERVER_TOPIC_COUNT
    assert [topic["kind"] for topic in topics] == [
        "births",
        "deaths",
        "agent_conversation",
    ]
    # score desc, then tick asc, then eventId asc
    keys = [(-topic["score"], topic["tick"], topic["eventId"] or "") for topic in topics]
    assert keys == sorted(keys)
    # deep links to the upstream events are preserved verbatim
    assert topics[2]["eventId"] == "evt-1"
    assert topics[2]["title"] == data["events"][0]["message"]


def test_runtime_topic_order_is_by_importance_not_input_order():
    """Selection must re-rank; upstream event order is not importance order."""

    mod = runtime()
    diff = {
        "tick": 50,
        "regionId": "garden-1",
        "populationLiving": 3,
        "populationDelta": 0,
        "births": 0,
        "deaths": 0,
        "hasDiffBasis": True,
        "unseenEvents": (
            {"id": "evt-low", "tick": 5, "kind": "agent_moved", "message": "moved"},
            {"id": "evt-high", "tick": 9, "kind": "trade_completed", "message": "traded"},
            # same score and tick as evt-b2, so eventId ascending breaks the tie
            {"id": "evt-b2", "tick": 7, "kind": "agent_conversation", "message": "b"},
            {"id": "evt-a1", "tick": 7, "kind": "agent_conversation", "message": "a"},
        ),
        "seenEventIds": (),
    }
    topics = mod.select_topics(diff)
    assert [topic["eventId"] for topic in topics] == ["evt-high", "evt-a1", "evt-b2"]
    assert [topic["score"] for topic in topics] == [
        mod.EVENT_KIND_WEIGHTS["trade_completed"],
        mod.EVENT_KIND_WEIGHTS["agent_conversation"],
        mod.EVENT_KIND_WEIGHTS["agent_conversation"],
    ]


def test_runtime_suppresses_events_already_introduced():
    mod = runtime()
    data = mod.load_observation(FIXTURE)
    seen = tuple(event["id"] for event in data["events"])
    diff = mod.diff_observation(data, data["previous"], seen)
    assert diff["unseenEvents"] == ()
    topics = mod.select_topics(diff)
    assert all(topic["eventId"] is None for topic in topics)
    assert [topic["kind"] for topic in topics] == ["births", "deaths"]


def test_runtime_reintroduced_topic_event_ids_are_appended_to_seen():
    mod = runtime()
    data = mod.load_observation(FIXTURE)
    diff = mod.diff_observation(data, data["previous"])
    topics = mod.select_topics(diff)
    updated = mod.update_seen_event_ids(("evt-old",), topics)
    assert "evt-old" in updated
    assert "evt-1" in updated
    assert len(updated) == len(set(updated))

    # The cap keeps the newest ids and drops the oldest (FIFO).
    capped = mod.update_seen_event_ids((f"evt-{i}" for i in range(600)), (), cap=500)
    assert len(capped) == 500
    assert capped[0] == "evt-100"
    assert capped[-1] == "evt-599"


def test_runtime_same_tick_observation_is_idempotent_and_change_free():
    mod = runtime()
    data = mod.load_observation(FIXTURE)
    current = mod.normalize_observation(data)
    previous = {
        "tick": current["tick"],
        "regionId": current["regionId"],
        "metrics": dict(current["metrics"]),
        "livingAgentIds": list(current["livingAgentIds"]),
    }
    assert mod.should_persist(data, previous) is False
    diff = mod.diff_observation(data, previous, tuple(event["id"] for event in data["events"]))
    assert mod.has_changes(diff) is False
    assert mod.select_topics(diff) == ()
    assert mod.next_focus(diff) == ()


def test_runtime_record_is_saved_atomically_and_reloaded(tmp_path):
    mod = runtime()
    data = mod.load_observation(FIXTURE)
    diff = mod.diff_observation(data, data["previous"])
    topics = mod.select_topics(diff)
    record = mod.build_record(
        data,
        observed_at="2026-10-17T00:00:00+09:00",
        source_base_url="https://example.org/moyogarden",
        diff=diff,
        topics=topics,
    )
    path = mod.save_record(tmp_path, record)
    assert path.name == mod.RECORD_FILENAME
    assert path.is_file()
    # No leftover temp file: the replace is the only visible write.
    assert not list(tmp_path.glob("*.tmp"))
    assert mod.load_record(tmp_path) == record
    assert record["schemaVersion"] == mod.RECORD_SCHEMA_VERSION
    assert record["seenEventIds"] == ["evt-1"]
    assert record["topicSeeds"][0]["eventId"] == "evt-1"
    assert mod.load_record(tmp_path / "missing") is None


def test_runtime_record_omits_unavailable_births_and_deaths(tmp_path):
    """Without a complete previous set the record must not invent integers."""

    mod = runtime()
    data = mod.load_observation(FIXTURE)
    diff = mod.diff_observation(data, None)
    record = mod.build_record(
        data,
        observed_at="2026-10-17T00:00:00+09:00",
        source_base_url="https://example.org/moyogarden",
        diff=diff,
    )
    assert diff["births"] is None and diff["deaths"] is None
    assert "births" not in record["metrics"]
    assert "deaths" not in record["metrics"]
    assert record["metrics"]["populationLiving"] == len(record["livingAgentIds"])


def test_runtime_draft_and_overlay_disclose_the_synthetic_source():
    mod = runtime()
    data = mod.load_observation(FIXTURE)
    diff = mod.diff_observation(data, data["previous"])
    topics = mod.select_topics(diff)
    focus = mod.next_focus(diff, topics)
    draft = mod.build_diary_draft(data, diff, topics, focus)
    assert draft["isSynthetic"] is True
    assert draft["topics"] and draft["lines"]
    assert all(topic["title"] for topic in draft["topics"])

    overlay = mod.render_overlay(draft)
    assert overlay["corner"] == mod.CORNER_ID
    assert overlay["isSynthetic"] is True
    assert overlay["tick"] == diff["tick"]
    assert len(overlay["topics"]) == len(topics)


def test_runtime_progression_start_and_topic_frames_match_the_status_overlay():
    mod = runtime()
    data = mod.load_observation(FIXTURE)
    diff = mod.diff_observation(data, data["previous"])
    topics = mod.select_topics(diff)
    overlay = mod.render_overlay(
        mod.build_diary_draft(data, diff, topics, mod.next_focus(diff, topics))
    )
    frames = mod.progress_frames(overlay)
    assert [frame["kind"] for frame in frames] == ["start", "topic", "topic", "topic", "end"]
    assert [frame["index"] for frame in frames] == list(range(len(frames)))
    assert all(frame["total"] == len(frames) for frame in frames)
    assert len(frames) <= mod.MAX_PROGRESS_FRAMES

    entry = mod.status_overlay_entry(overlay, frames)
    assert entry["totalFrames"] == len(frames)
    assert entry["startIndex"] == 0 and entry["endIndex"] == len(frames) - 1
    assert mod.validate_progression(overlay, frames) == entry


def test_runtime_progression_has_no_topic_frames_when_nothing_changed():
    mod = runtime()
    data = mod.load_observation(FIXTURE)
    current = mod.normalize_observation(data)
    previous = {
        "tick": current["tick"],
        "regionId": current["regionId"],
        "metrics": dict(current["metrics"]),
        "livingAgentIds": list(current["livingAgentIds"]),
    }
    diff = mod.diff_observation(data, previous, tuple(e["id"] for e in data["events"]))
    overlay = mod.render_overlay(mod.build_diary_draft(data, diff, (), ()))
    frames = mod.progress_frames(overlay)
    assert [frame["kind"] for frame in frames] == ["start", "end"]
    assert mod.validate_progression(overlay, frames)


def test_runtime_progression_rejects_topic_frames_that_disagree_with_the_overlay():
    mod = runtime()
    data = mod.load_observation(FIXTURE)
    diff = mod.diff_observation(data, data["previous"])
    topics = mod.select_topics(diff)[:2]
    overlay = mod.render_overlay(mod.build_diary_draft(data, diff, topics, ()))

    frames = [dict(frame) for frame in mod.progress_frames(overlay)]
    extra = dict(frames[1])
    extra["key"] = "extra"
    frames.insert(2, extra)
    for index, frame in enumerate(frames):
        frame["index"] = index
        frame["total"] = len(frames)
    with pytest.raises(ValueError):
        mod.validate_progression(overlay, frames)


def test_runtime_rejects_observations_over_the_bounds():
    mod = runtime()
    data = load_fixture()

    oversized_snapshot = copy.deepcopy(data)
    oversized_snapshot["snapshot"]["blob"] = "x" * (mod.OBSERVATION_MAX_BYTES + 1)
    with pytest.raises(ValueError):
        mod.validate_observation(oversized_snapshot)

    too_many_events = copy.deepcopy(data)
    too_many_events["events"] = [
        {"id": f"evt-{i}", "tick": i, "kind": "agent_moved", "message": "m"}
        for i in range(mod.OBSERVATION_MAX_EVENTS_PER_RUN + 1)
    ]
    with pytest.raises(ValueError):
        mod.validate_observation(too_many_events)


def test_runtime_progression_rejects_an_empty_frame_set():
    mod = runtime()
    overlay = {
        "title": "AI文明観察日記",
        "isSynthetic": True,
        "tick": 1,
        "topics": [],
        "nextFocus": [],
    }
    with pytest.raises(ValueError):
        mod.validate_progression(overlay, [])
    with pytest.raises(ValueError):
        mod.status_overlay_entry(overlay, [])


def test_runtime_rejects_duplicate_event_ids():
    mod = runtime()
    data = load_fixture()
    data["events"][1]["id"] = data["events"][0]["id"]
    with pytest.raises(ValueError):
        mod.validate_observation(data)


def test_runtime_same_tick_introduces_remaining_events_across_reloads():
    """Regression for #2426 review P2: a same-tick introduced event must be
    persisted (keeping observedAt) instead of being re-selected forever."""

    mod = runtime()
    data = copy.deepcopy(load_fixture())
    events = [
        {"id": f"evt-{i}", "tick": i + 1, "kind": "trade_completed", "message": f"event {i}"}
        for i in range(4)
    ]
    data["events"] = events
    data["snapshot"]["events"] = events
    observed_at = "2026-10-17T00:00:00+09:00"

    import tempfile as _t
    from pathlib import Path as _P

    state_dir = _P(_t.mkdtemp())
    previous = None
    seen = ()

    # Run 1: 4 unseen -> 3 introduced, persisted.
    diff = mod.diff_observation(data, previous, seen)
    topics = mod.select_topics(diff)
    assert [t["eventId"] for t in topics] == ["evt-0", "evt-1", "evt-2"]
    assert mod.should_persist(data, previous) is True
    record = mod.build_record(
        data, observed_at=observed_at, source_base_url="https://example.org/moyogarden",
        diff=diff, topics=topics, seen_event_ids=seen,
    )
    assert record["observedAt"] == observed_at
    mod.save_record(state_dir, record)
    previous = record
    seen = tuple(record["seenEventIds"])
    assert seen == ("evt-0", "evt-1", "evt-2")

    # Run 2: same tick -> 1 introduced, persisted, observedAt unchanged.
    loaded = mod.load_record(state_dir)
    seen2 = tuple(loaded["seenEventIds"])
    diff2 = mod.diff_observation(data, loaded, seen2)
    topics2 = mod.select_topics(diff2)
    assert [t["eventId"] for t in topics2] == ["evt-3"]
    assert mod.should_persist(data, loaded) is True
    record2 = mod.build_record(
        data, observed_at=observed_at, source_base_url="https://example.org/moyogarden",
        diff=diff2, topics=topics2, seen_event_ids=seen2,
    )
    assert record2["observedAt"] == observed_at
    mod.save_record(state_dir, record2)
    assert tuple(record2["seenEventIds"]) == ("evt-0", "evt-1", "evt-2", "evt-3")

    # Run 3: reload -> 0 introduced -> not persisted.
    loaded3 = mod.load_record(state_dir)
    seen3 = tuple(loaded3["seenEventIds"])
    diff3 = mod.diff_observation(data, loaded3, seen3)
    topics3 = mod.select_topics(diff3)
    assert topics3 == ()
    assert mod.should_persist(data, loaded3) is False
