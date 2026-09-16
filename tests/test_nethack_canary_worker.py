from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from docich.nethack_canary_worker import (
    ARENA,
    CanaryWorkerError,
    _candidate_broker,
    _read_request,
    _start_game,
    _terminal_result,
    _timeout_result,
)


BROKER_SOCKET = "/canary/episode/.candidate-ipc/candidate.sock"


def baseline_request():
    return {
        "schema_version": 1,
        "experiment_id": "exp",
        "episode_id": "000",
        "arm": "baseline",
        "arena": dict(ARENA),
        "player_name": "canary_b_000",
        "max_turns": 1000,
        "seed": 42,
        "controller": {"kind": "baseline_p3b"},
        "requirements": {
            "isolation_mode": "container",
            "production_state_must_remain_untouched": True,
            "wizard_mode": False,
            "explore_mode": False,
        },
    }


def candidate_request():
    raw = baseline_request()
    raw["arm"] = "candidate"
    raw["player_name"] = "canary_c_000"
    raw["controller"] = {
        "kind": "candidate_strategist",
        "candidate_id": "cand",
        "candidate_version": "v1",
        "candidate_fingerprint": "f" * 64,
        "command_sha256": "c" * 64,
        "broker_socket": BROKER_SOCKET,
        "broker_timeout_s": 7.0,
    }
    return raw


def test_worker_request_requires_fixed_internal_arena_and_normal_play():
    raw = baseline_request()
    parsed = _read_request(json.dumps(raw))
    assert parsed["arena"] == ARENA

    raw["arena"]["save_dir"] = "/var/games/nethack/save"
    with pytest.raises(CanaryWorkerError):
        _read_request(json.dumps(raw))

    raw = baseline_request()
    raw["requirements"]["wizard_mode"] = True
    with pytest.raises(CanaryWorkerError):
        _read_request(json.dumps(raw))


def test_worker_request_rejects_unknown_surface():
    raw = baseline_request()
    raw["production_path"] = "/var/games/nethack"
    with pytest.raises(CanaryWorkerError):
        _read_request(json.dumps(raw))


def test_candidate_request_contains_broker_only_not_manifest_path():
    raw = candidate_request()
    parsed = _read_request(json.dumps(raw))
    assert parsed["controller"]["broker_socket"] == BROKER_SOCKET
    assert "manifest_path" not in parsed["controller"]
    assert _candidate_broker(parsed) == (BROKER_SOCKET, 7.0)

    raw["controller"]["manifest_path"] = "/canary/candidate.json"
    with pytest.raises(CanaryWorkerError):
        _read_request(json.dumps(raw))


def test_candidate_broker_socket_path_is_fixed():
    raw = candidate_request()
    raw["controller"]["broker_socket"] = "/tmp/candidate.sock"
    with pytest.raises(CanaryWorkerError):
        _read_request(json.dumps(raw))


def test_terminal_result_uses_xlog_facts_and_marks_broker_source():
    request = candidate_request()
    record = {
        "name": "canary_c_000",
        "death": "killed by a grid bug",
        "points": "123",
        "turns": "456",
        "maxlvl": "7",
        "achieve": "0x20",
    }
    result = _terminal_result(request, record, exit_reason="terminal_xlog")
    assert result["terminal_status"] == "dead"
    assert result["score"] == 123
    assert result["turns"] == 456
    assert result["max_depth"] == 7
    assert result["got_amulet"] is True
    assert result["seed"] == 42
    assert result["seed_applied"] is False
    assert result["candidate_action_source"] == "candidate_strategist_broker"
    assert result["production_state_touched"] is False


def test_baseline_never_uses_candidate_broker():
    parsed = _read_request(json.dumps(baseline_request()))
    assert _candidate_broker(parsed) is None


def test_timeout_result_records_last_observed_progress():
    result = _timeout_result(
        baseline_request(),
        reason="wall_timeout",
        turns=321,
        max_depth=2,
        last_message="Pick up the dagger? [ynq]",
    )
    assert result["worker_status"] == "completed"
    assert result["terminal_status"] == "timeout"
    assert result["exit_reason"] == "wall_timeout"
    assert result["turns"] == 321
    assert result["max_depth"] == 2
    assert result["last_message"] == "Pick up the dagger? [ynq]"
    assert result["production_state_touched"] is False


def test_game_worker_source_does_not_embed_candidate_process_launcher():
    source = (Path(__file__).resolve().parents[1] / "src/docich/nethack_canary_worker.py").read_text(
        encoding="utf-8"
    )
    assert "CommandStrategist" not in source
    assert "load_candidate_manifest" not in source
    assert "/canary/candidate.json" not in source
    assert "broker_socket" in source


class _FakeTmuxGame:
    def __init__(self, screens):
        self.screens = list(screens)
        self.keys: list[str] = []
        self.started = False

    def start(self) -> None:
        self.started = True

    def alive(self) -> bool:
        return True

    def capture(self) -> str:
        if len(self.screens) > 1:
            return self.screens.pop(0)
        return self.screens[0]

    def literal(self, value: str) -> None:
        self.keys.append(value)

    def special(self, value: str) -> None:  # pragma: no cover - not used here
        self.keys.append(value)


def test_start_game_answers_pick_and_confirmation_prompts():
    pick = "Shall I pick character's race, role, gender and alignment for you? [ynaq]\n"
    confirm = "Is this ok? [ynq]\n y * Yes; start game\n"
    more = "Aloha TestHero, welcome to NetHack!  You are a neutral male human Tourist.\n--More--\n"
    frame = [""] * 24
    frame[1] = ".@.."
    frame[2] = "...."
    frame[22] = "HP:10(10) Pw:3(3) AC:5 Exp:1"
    frame[23] = "Dlvl:1 T:2"
    gameplay = "\n".join(frame) + "\n"
    game = _FakeTmuxGame([pick, confirm, more, gameplay])
    text = _start_game(game, deadline=time.monotonic() + 30)
    assert game.started is True
    assert game.keys == ["y", "y", " "]
    assert "HP:10" in text


def test_start_game_dismisses_tutorial_before_returning_gameplay():
    tutorial = [""] * 24
    tutorial[0] = "Do you want a tutorial?"
    tutorial[1] = " y - Yes, do a tutorial"
    tutorial[2] = " n - No, just start play"
    tutorial[20] = "|(@.$|"
    tutorial[22] = "HP:18(18) Pw:1(1) AC:6 Exp:1"
    tutorial[23] = "Dlvl:1 T:1"
    gameplay = [""] * 24
    gameplay[1] = ".@.."
    gameplay[2] = "...."
    gameplay[22] = "HP:18(18) Pw:1(1) AC:6 Exp:1"
    gameplay[23] = "Dlvl:1 T:1"
    game = _FakeTmuxGame(["\n".join(tutorial) + "\n", "\n".join(gameplay) + "\n"])
    text = _start_game(game, deadline=time.monotonic() + 30)
    assert game.keys == ["n"]
    assert "Do you want a tutorial" not in text


def test_start_game_fails_closed_when_creation_never_completes():
    stuck = "Shall I pick character's race, role, gender and alignment for you? [ynaq]\n"
    game = _FakeTmuxGame([stuck])
    with pytest.raises(CanaryWorkerError, match="character creation"):
        _start_game(game, deadline=time.monotonic() - 1)
