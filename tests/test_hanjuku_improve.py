"""Issue #954 first unit: fixed-schema proposal, offline replay evaluator,
durable verdict. No live-loop change, no auto-apply."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_improve as improve
from docich import hanjuku_run

IDENTITY = {"game": "hanjuku-hero", "runtime_id": "g9-abcdef",
            "generation": 9, "lease_id": "lease-9"}


def run_state(**over):
    state = {**IDENTITY, "observations": 120, "actions_sent": 40,
             "battles_started": 3, "battles_finished": 2,
             "terminal_reason": "game_over", "observed_at": 5000.0,
             "api_key": "sk-live-must-never-persist"}
    state.update(over)
    return state


def game_over_events(count=120, step=10.0, start=1000.0):
    # Three rotating digests: never an unchanged run, game over at the end.
    events = []
    for index in range(count):
        events.append({"event": "observation", "at": start + index * step,
                       "phase": "title" if index >= count - 3 else "field",
                       "frame_sha256": f"digest-{index % 3}",
                       "unchanged_seconds": 0.0, "playing": True,
                       "terminal_reason": None})
    events[-1] = {**events[-1], "terminal_reason": "game_over"}
    terminal = start + (count - 1) * step
    return events, terminal


def truncating_events():
    # 200s perfectly steady, then lively play until game over at t=2190.
    events = []
    for index in range(120):
        at = 1000.0 + index * 10.0
        digest = "steady" if index < 20 else f"digest-{index % 3}"
        events.append({"event": "observation", "at": at, "phase": "field",
                       "frame_sha256": digest, "unchanged_seconds": 0.0,
                       "playing": True, "terminal_reason": None})
    events[-1] = {**events[-1], "terminal_reason": "game_over",
                  "phase": "title"}
    return events


def test_baseline_matches_live_detector_constants():
    assert improve.BASELINE_PARAMS == {
        "stall_seconds": float(hanjuku_run.STALL_SECONDS),
        "sample_gap_s": float(hanjuku_run.MAX_SAMPLE_GAP),
        "battle_debounce": int(hanjuku_run.BATTLE_PHASE_DEBOUNCE)}


def test_live_loop_has_no_provider_or_adapter_dependency():
    source = Path(improve.__file__).read_text(encoding="utf-8")
    imports = [line.strip() for line in source.splitlines()
               if line.strip().startswith(("import ", "from "))]
    blob = "\n".join(imports)
    for banned in ("provider", "openai", "anthropic", "adapter",
                   "hanjuku_bot", "LLM"):
        assert banned not in blob
    corner = (Path(improve.__file__).parent / "retro_corner.py").read_text(
        encoding="utf-8")
    assert "hanjuku-improvement-deferred" in corner


def test_exact_schema_proposal_binds_run_identity():
    events, _ = game_over_events()
    summary = improve.summarize_run(run_state(), events, IDENTITY)
    proposal = improve.build_proposal(summary, {"stall_seconds": 240.0})
    assert proposal["params"] == {**improve.BASELINE_PARAMS,
                                  "stall_seconds": 240.0}
    assert proposal["changed"] == ["stall_seconds"]
    assert proposal["run"] == {"runtime_id": "g9-abcdef", "generation": 9}
    assert proposal["baseline_version"] == improve.BASELINE_VERSION


@pytest.mark.parametrize("adjustments,reason", [
    ({"stall_seconds": 240.0, "retries": 3}, "unknown_key"),
    ({"stall_seconds": 30.0}, "out_of_range"),
    ({"sample_gap_s": 120.0}, "out_of_range"),
    ({"battle_debounce": 0}, "out_of_range"),
    ({"stall_seconds": float("nan")}, "non_finite"),
    ({"stall_seconds": float("inf")}, "non_finite"),
    ({"battle_debounce": True}, "bool_as_number"),
    ({"battle_debounce": 2.0}, "bad_type"),
    ({"stall_seconds": "240"}, "bad_type"),
])
def test_malformed_candidate_rejected_fail_closed(adjustments, reason):
    events, _ = game_over_events()
    summary = improve.summarize_run(run_state(), events, IDENTITY)
    with pytest.raises(improve.HanjukuImproveError) as exc:
        improve.build_proposal(summary, adjustments)
    assert exc.value.reason == reason
    verdict = improve.evaluate(run_state(), events, IDENTITY, adjustments)
    assert verdict["status"] == "rejected" and verdict["reason"] == reason


def test_identity_mismatch_rejected():
    events, _ = game_over_events()
    verdict = improve.evaluate(run_state(), events,
                               {**IDENTITY, "generation": 10},
                               {"stall_seconds": 240.0})
    assert verdict["status"] == "rejected"
    assert verdict["reason"] == "identity_mismatch"


def test_non_terminal_trace_rejected():
    events, _ = game_over_events()
    state = run_state(terminal_reason=None)
    verdict = improve.evaluate(state, events, IDENTITY,
                               {"stall_seconds": 240.0})
    assert (verdict["status"], verdict["reason"]) == ("rejected",
                                                      "not_terminal")


def test_thin_trace_rejected_as_insufficient_evidence():
    events, _ = game_over_events(count=40)
    state = run_state(observations=12)
    verdict = improve.evaluate(state, events, IDENTITY,
                               {"stall_seconds": 240.0})
    assert (verdict["status"], verdict["reason"]) == (
        "rejected", "insufficient_evidence")


def test_candidate_equal_to_baseline_rejected():
    events, _ = game_over_events()
    verdict = improve.evaluate(run_state(), events, IDENTITY, {})
    assert (verdict["status"], verdict["reason"]) == (
        "rejected", "insufficient_evidence")


def test_truncating_candidate_rejected_but_baseline_consistent():
    events = truncating_events()
    verdict = improve.evaluate(run_state(), events, IDENTITY,
                               {"stall_seconds": 60.0})
    assert (verdict["status"], verdict["reason"]) == (
        "rejected", "would_truncate_run")
    assert (verdict["candidate_latch_wall"]
            < verdict["summary"]["terminal_wall"])


def test_looser_candidate_accepted_without_applying():
    events, _ = game_over_events()
    verdict = improve.evaluate(run_state(), events, IDENTITY,
                               {"stall_seconds": 420.0,
                                "sample_gap_s": 20.0})
    assert (verdict["status"], verdict["reason"]) == ("accepted", "accepted")
    assert verdict["applied"] is False
    assert verdict["stages"] == {"proposal": "built",
                                 "evaluation": "accepted",
                                 "promotion": "withheld"}


def test_debounce_change_needs_battle_evidence():
    events, _ = game_over_events()
    state = run_state(battles_started=1, battles_finished=0)
    verdict = improve.evaluate(state, events, IDENTITY,
                               {"battle_debounce": 3})
    assert (verdict["status"], verdict["reason"]) == (
        "rejected", "insufficient_evidence")


def test_fixture_gate_logic_directly():
    assert improve._check_machinery() is None
    assert improve._check_fixtures(
        {**improve.BASELINE_PARAMS, "stall_seconds": 60.0}) is None
    # A dead detector (never latches on 1000s steady) must fail the gate.
    assert improve._check_fixtures(
        {**improve.BASELINE_PARAMS, "stall_seconds": 5000.0,
         "sample_gap_s": 15.0}) == "fixture_regression"


def test_malformed_events_never_raise_and_never_touch_baseline(tmp_path):
    before = dict(improve.BASELINE_PARAMS)
    events = [None, "x", 42, {"event": "observation"},
              {"event": "observation", "at": float("nan"),
               "frame_sha256": "d", "playing": True}]
    verdict = improve.evaluate(run_state(), events, IDENTITY,
                               {"stall_seconds": 240.0})
    assert verdict["status"] == "rejected"
    assert verdict["reason"] == "insufficient_evidence"
    assert improve.BASELINE_PARAMS == before
    assert list(tmp_path.iterdir()) == []


def test_evaluate_is_deterministic(tmp_path):
    events, _ = game_over_events()
    first = improve.evaluate(run_state(), events, IDENTITY,
                             {"stall_seconds": 420.0})
    second = improve.evaluate(run_state(), events, IDENTITY,
                              {"stall_seconds": 420.0})
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


def test_secret_and_junk_keys_never_persist(tmp_path):
    events, _ = game_over_events()
    verdict = improve.evaluate(run_state(), events, IDENTITY,
                               {"stall_seconds": 420.0})
    assert verdict["status"] == "accepted"
    blob = json.dumps(verdict, ensure_ascii=False)
    assert "sk-live" not in blob and "api_key" not in blob
    path = improve.record_evaluation(tmp_path, verdict)
    text = path.read_text(encoding="utf-8")
    assert "sk-live" not in text and "api_key" not in text
    loaded = json.loads(text)
    assert loaded["applied"] is False
    assert loaded["candidate_params"]["stall_seconds"] == 420.0
    assert set(loaded) == {"schema", "status", "reason", "detail",
                           "baseline_version", "baseline_params",
                           "candidate_params", "run", "summary", "stages",
                           "applied", "candidate_latch_wall",
                           "baseline_latch_wall", "generated_at"}


def test_record_rejects_non_verdict():
    with pytest.raises(improve.HanjukuImproveError):
        improve.record_evaluation("/tmp", {"status": "maybe"})


def test_suggest_stall_is_bounded_and_deterministic():
    assert improve.suggest_stall({"peak_unchanged_s": 100.0}) == {
        "stall_seconds": 200.0}
    assert improve.suggest_stall({"peak_unchanged_s": 0.0}) == {
        "stall_seconds": 60.0}
    assert improve.suggest_stall({"peak_unchanged_s": 1000.0}) == {
        "stall_seconds": 900.0}
    with pytest.raises(improve.HanjukuImproveError):
        improve.suggest_stall({"peak_unchanged_s": float("nan")})


def test_known_good_strategy_unchanged_after_rejection(tmp_path):
    events = truncating_events()
    verdict = improve.evaluate(run_state(), events, IDENTITY,
                               {"stall_seconds": 60.0})
    assert verdict["status"] == "rejected"
    path = improve.record_evaluation(tmp_path, verdict)
    assert json.loads(path.read_text())["applied"] is False
    assert improve.BASELINE_PARAMS == {
        "stall_seconds": 300.0, "sample_gap_s": 15.0, "battle_debounce": 2}
