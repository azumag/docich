"""#697 communist law simulator: fixture contract, engine, overlay, status.

Offline only.  The fixture is a synthetic example and every test in this file
stays within the non-production boundary documented in
``docs/features/communist-law-simulator.md``.
"""

import json
from pathlib import Path

import pytest

from docich.communist_law_simulator import (
    CAVEATS,
    CATEGORIES,
    CORNER_ID,
    INITIAL_METRICS,
    LAW_IDS,
    LAWS,
    MAX_PROGRESS_FRAMES,
    MAX_SESSION_LAWS,
    MAX_TITLE_CHARS,
    METRICS,
    METRIC_MAX,
    METRIC_MIN,
    STATUS_TEXT,
    TERMINAL_STATUSES,
    LawSimError,
    apply_turn,
    load_session_fixture,
    parse_session,
    parse_session_json,
    progress_frames,
    render_overlay,
    session_trace,
    status_overlay_entry,
    status_text,
    trace_signature,
    validate_status,
)

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "communist_law_simulator"
    / "fixed_law_session_example.json"
)


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def make_payload(laws, title="第1回会議", republic="テスト共和国（架空）"):
    return {
        "corner": CORNER_ID,
        "session": {"title": title, "republic": republic, "laws": list(laws)},
    }


# --- fixture contract -------------------------------------------------------


def test_fixture_exists_and_is_synthetic():
    assert FIXTURE.is_file()
    data = load_fixture()
    assert data["schemaVersion"] == 1
    assert data["corner"] == CORNER_ID
    assert data["isSyntheticFixture"] is True
    assert data["scopeNote"].strip()
    assert data["session"]["title"].strip()
    assert data["session"]["republic"].strip()
    assert 1 <= len(data["session"]["laws"]) <= MAX_SESSION_LAWS
    assert len(set(data["session"]["laws"])) == len(data["session"]["laws"])
    assert all(law_id in LAWS for law_id in data["session"]["laws"])


def test_non_production_acceptance_boundary():
    acceptance = load_fixture()["nonProductionAcceptance"]
    assert acceptance["requiresReviewerApproval"] is True
    assert acceptance["mustNotPresentSyntheticAsReal"] is True
    assert acceptance["mustNotClaimRealState"] is True
    assert acceptance["mustKeepSimulationBoundaries"] is True
    assert acceptance["mustKeepCaveatsToEndFrame"] is True
    assert acceptance["productionWiringDeferred"] is True
    assert acceptance["llmProposalBrainDeferred"] is True
    assert acceptance["cornerRotationRegistrationDeferred"] is True


def test_law_library_integrity():
    assert len(LAW_IDS) == len(set(LAW_IDS))
    for law_id, law in LAWS.items():
        assert law.law_id == law_id
        assert law.name.strip() and law.summary.strip()
        assert law.category in CATEGORIES
        assert law.risks and all(risk.strip() for risk in law.risks)
        assert law.effects, f"law must have effects: {law_id}"
        for metric, delta in law.effects.items():
            assert metric in METRICS
            assert type(delta) is int and not isinstance(delta, bool)
            assert -30 <= delta <= 30
        assert any(delta != 0 for delta in law.effects.values())


def test_initial_metrics_are_in_bounds():
    assert set(INITIAL_METRICS) == set(METRICS)
    for value in INITIAL_METRICS.values():
        assert type(value) is int
        assert METRIC_MIN <= value <= METRIC_MAX


def test_status_catalog_is_complete():
    assert TERMINAL_STATUSES | {"in_session"} == set(STATUS_TEXT)
    assert status_text("plan_achieved")
    for status in STATUS_TEXT:
        assert status_text(status)


def test_strict_fixture_loader_accepts_repository_fixture():
    data = load_session_fixture()
    assert data["corner"] == CORNER_ID
    assert data["session"]["laws"][0] == "grain-procurement-tightening"


def test_strict_parse_rejects_duplicate_json_keys():
    raw = b'{"corner": "communist-law-simulator", "corner": "communist-law-simulator"}'
    with pytest.raises(LawSimError, match="duplicate_json_field"):
        parse_session_json(raw)


def test_strict_parse_rejects_nonfinite_and_oversize():
    with pytest.raises(LawSimError, match="nonfinite_json_value"):
        parse_session_json(b'{"session": {"title": NaN}}')
    with pytest.raises(LawSimError, match="session_payload_too_large"):
        parse_session_json(b"x" * (65 * 1024))


# --- engine: golden fixture trace ------------------------------------------


def test_fixture_session_reaches_plan_achieved():
    data = load_fixture()
    trace = session_trace(data)
    assert trace["corner"] == CORNER_ID
    assert trace["engine"] == "law-sim-v1"
    assert trace["finalStatus"] == "plan_achieved"
    assert len(trace["turns"]) == 11
    assert [turn["status"] for turn in trace["turns"][:-1]] == ["in_session"] * 10
    assert trace["finalMetrics"] == {
        "production": 86,
        "support": 26,
        "discipline": 63,
        "reserves": 25,
        "blackMarket": 25,
        "planProgress": 100,
    }
    assert trace["turns"][-1]["lawId"] == "rural-electrification"


def test_fixture_trace_is_deterministic():
    data = load_fixture()
    first = session_trace(data)
    second = session_trace(data)
    assert first == second
    signature = trace_signature(first)
    assert len(signature) == 64
    int(signature, 16)  # sha256 hex
    assert signature == trace_signature(second)


def test_fixture_warning_events_fire_once_per_crossing():
    trace = session_trace(load_fixture())
    warnings = [
        (turn["turn"], event["metric"])
        for turn in trace["turns"]
        for event in turn["events"]
    ]
    # Golden crossings measured on the fixed fixture session.
    assert (3, "reserves") in warnings
    assert (4, "support") in warnings
    assert (10, "production") in warnings
    seen_metrics = [metric for _, metric in warnings]
    assert len(seen_metrics) == len(set(seen_metrics))
    for _, event in [(turn["turn"], event) for turn in trace["turns"] for event in turn["events"]]:
        assert set(event) == {"metric", "text"}
        assert event["text"].strip()


def test_plan_gain_follows_production():
    trace = session_trace(load_fixture())
    # Turn 2 leaves production at 52 -> plan gain 5; turn 10 leaves 80 -> gain 8.
    assert trace["turns"][1]["planGain"] == 5
    assert trace["turns"][9]["planGain"] == 8
    assert trace["turns"][2]["deltas"]["planProgress"] == 10
    # Pin the science law's own effects: a silent delta change must not hide
    # behind the planProgress clamp on the final metrics.
    assert trace["turns"][1]["deltas"] == {
        "production": 6,
        "reserves": -5,
        "planProgress": 12,
    }


# --- engine: rules and fail-closed behaviour --------------------------------


def test_metrics_are_clamped_to_bounds():
    # blackMarket starts at 20; -10, -8 and -6 would reach -4 without clamping.
    trace = session_trace(
        make_payload(
            ["price-control-reform", "consumer-goods-priority", "anti-corruption-campaign"]
        )
    )
    assert trace["finalMetrics"]["blackMarket"] == 0
    # planProgress clamps at 100 on the fixture session.
    assert session_trace(load_fixture())["finalMetrics"]["planProgress"] == 100


def test_support_collapse_session():
    trace = session_trace(
        make_payload(
            [
                "grain-procurement-tightening",
                "currency-reform-confiscation",
                "five-year-plan-steel",
                "collective-farm-consolidation",
                "state-security-purge",
            ]
        )
    )
    assert trace["finalStatus"] == "collapse_public_unrest"
    assert [turn["status"] for turn in trace["turns"][:-1]] == ["in_session"] * 4
    assert trace["turns"][-1]["metricsAfter"]["support"] == 0


def test_session_after_terminal_is_rejected():
    collapsing = [
        "grain-procurement-tightening",
        "currency-reform-confiscation",
        "five-year-plan-steel",
        "collective-farm-consolidation",
        "state-security-purge",
    ]
    payload = make_payload(collapsing + ["price-control-reform"])
    with pytest.raises(LawSimError, match="session_continues_after_terminal"):
        session_trace(payload)


def test_plan_incomplete_session_ends_explicitly():
    trace = session_trace(
        make_payload(
            [
                "consumer-goods-priority",
                "workers-council-reform",
                "cultural-enlightenment-decree",
                "rural-electrification",
                "anti-corruption-campaign",
                "price-control-reform",
            ]
        )
    )
    assert trace["finalStatus"] == "plan_incomplete"
    assert trace["finalMetrics"]["planProgress"] < 100
    assert trace["turns"][-1]["status"] == "in_session"


def test_apply_turn_collapse_rules():
    base = dict(INITIAL_METRICS)
    # discipline collapse
    state = dict(base, discipline=5)
    record = apply_turn(state, "workers-council-reform")
    assert record["status"] == "collapse_party_split"
    assert record["metricsAfter"]["discipline"] == 0
    # reserves collapse
    state = dict(base, reserves=5)
    record = apply_turn(state, "machinery-import-program")
    assert record["status"] == "collapse_trade_default"
    assert record["metricsAfter"]["reserves"] == 0
    # black market takeover
    state = dict(base, blackMarket=95)
    record = apply_turn(state, "grain-procurement-tightening")
    assert record["status"] == "collapse_black_market"
    assert record["metricsAfter"]["blackMarket"] == 100
    # plan achieved through the production bonus alone
    state = dict(base, production=90, planProgress=95)
    record = apply_turn(state, "rural-electrification")
    assert record["status"] == "plan_achieved"
    assert record["metricsAfter"]["planProgress"] == 100
    # the input state is never mutated
    assert state["production"] == 90


def test_apply_turn_rejects_unknown_law_and_bad_state():
    with pytest.raises(LawSimError, match="unknown_law_id"):
        apply_turn(dict(INITIAL_METRICS), "not-a-law")
    with pytest.raises(LawSimError, match="metric_out_of_range"):
        apply_turn(dict(INITIAL_METRICS, support=101), "rural-electrification")
    with pytest.raises(LawSimError, match="metrics_keys_must_match_engine_metrics"):
        apply_turn(dict(INITIAL_METRICS, extra=1), "rural-electrification")


def test_session_validation_fails_closed():
    with pytest.raises(LawSimError, match="unknown_law_id"):
        parse_session(make_payload(["not-a-law"]))
    with pytest.raises(LawSimError, match="duplicate_law_id"):
        parse_session(make_payload(["rural-electrification", "rural-electrification"]))
    with pytest.raises(LawSimError, match="session_law_count_out_of_bounds"):
        parse_session(make_payload(LAW_IDS[:12] + ("rural-electrification",)))
    with pytest.raises(LawSimError, match="session_law_count_out_of_bounds"):
        parse_session(make_payload([]))
    with pytest.raises(LawSimError, match="unknown_corner_id"):
        parse_session({"corner": "other", "session": {"title": "t", "republic": "r", "laws": ["rural-electrification"]}})
    with pytest.raises(LawSimError, match="session_fields_must_be_exactly"):
        parse_session({"corner": CORNER_ID, "session": {"title": "t", "laws": ["rural-electrification"]}})
    with pytest.raises(LawSimError, match="session_title_length_out_of_bounds"):
        parse_session(make_payload(["rural-electrification"], title="あ" * (MAX_TITLE_CHARS + 1)))
    with pytest.raises(LawSimError, match="session_title_must_be_string"):
        parse_session(make_payload(["rural-electrification"], title=7))
    with pytest.raises(LawSimError, match="session_must_be_object"):
        parse_session(["not", "a", "dict"])


# --- overlay, progression and status contract -------------------------------


def test_render_overlay_payload():
    overlay = render_overlay(load_fixture())
    assert overlay["corner"] == CORNER_ID
    assert overlay["isSynthetic"] is True
    assert overlay["finalStatus"] == "plan_achieved"
    assert overlay["finalStatusText"] == status_text("plan_achieved")
    assert len(overlay["turns"]) == 11
    assert overlay["turns"][0]["lawId"] == "grain-procurement-tightening"
    assert overlay["initialMetrics"] == INITIAL_METRICS
    assert overlay["finalMetrics"]["planProgress"] == 100
    assert [w["turn"] for w in overlay["warnings"]] == [3, 4, 10]
    assert overlay["caveats"] == CAVEATS and overlay["caveats"]


def test_progress_frames_are_bounded_and_ordered():
    frames = progress_frames(load_fixture())
    assert len(frames) == 11 + 2
    assert len(frames) <= MAX_PROGRESS_FRAMES
    assert [frame["index"] for frame in frames] == list(range(len(frames)))
    assert frames[0]["kind"] == "start" and frames[-1]["kind"] == "end"
    assert all(frame["kind"] == "turn" for frame in frames[1:-1])
    assert frames[0]["heading"] == load_fixture()["session"]["title"]
    assert frames[1]["lawId"] == "grain-procurement-tightening"
    assert frames[-1]["heading"] == load_fixture()["session"]["title"]
    # The synthetic marker and the opening state are visible on the start frame.
    assert "合成シミュレーション" in frames[0]["text"]
    assert "生産40" in frames[0]["text"]
    assert "⚠" in frames[3]["text"]  # turn 3 carries the reserves warning


def test_status_overlay_entry_aligns_with_frames():
    data = load_fixture()
    frames = progress_frames(data)
    entry = status_overlay_entry(data)
    assert entry["corner"] == CORNER_ID
    assert entry["isSynthetic"] is True
    assert entry["totalFrames"] == len(frames)
    assert entry["startIndex"] == frames[0]["index"] == 0
    assert entry["endIndex"] == frames[-1]["index"] == len(frames) - 1
    assert entry["startKind"] == "start" and entry["endKind"] == "end"
    assert entry["finalStatus"] == "plan_achieved"
    assert entry["caveatKeys"] == sorted(CAVEATS)


def test_validate_status_accepts_canonical_frames():
    data = load_fixture()
    entry = validate_status(data)
    assert entry == status_overlay_entry(data)
    assert validate_status(data, progress_frames(data)) == entry


def test_validate_status_rejects_mutated_frames():
    data = load_fixture()
    frames = list(progress_frames(data))
    frames[1], frames[2] = frames[2], frames[1]
    with pytest.raises(LawSimError, match="not a contiguous bounded sequence"):
        validate_status(data, frames)
    # Index-preserving mutation: only the canonical equality check can catch it.
    frames = [dict(frame) for frame in progress_frames(data)]
    frames[1] = dict(frames[1], text="改ざんされた本文")
    with pytest.raises(LawSimError, match="do not match the validated progression"):
        validate_status(data, frames)


def test_validate_status_rejects_missing_end_caveats():
    data = load_fixture()
    frames = [dict(frame) for frame in progress_frames(data)]
    frames[-1] = dict(frames[-1], caveats={})
    with pytest.raises(LawSimError, match="must retain the caveats"):
        validate_status(data, frames)


def test_validate_status_rejects_kind_and_status_mutation():
    data = load_fixture()
    frames = [dict(frame) for frame in progress_frames(data)]
    frames[0] = dict(frames[0], kind="turn")
    with pytest.raises(LawSimError, match="must start and end with its own frames"):
        validate_status(data, frames)
    frames = [dict(frame) for frame in progress_frames(data)]
    frames[-1] = dict(frames[-1], finalStatus="plan_incomplete")
    with pytest.raises(LawSimError, match="final status does not match"):
        validate_status(data, frames)


def test_session_bounds_reflect_documented_limits():
    assert MAX_SESSION_LAWS == 12
    assert MAX_PROGRESS_FRAMES == 48
    assert METRIC_MIN == 0 and METRIC_MAX == 100
