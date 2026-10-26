from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import docich.nethack_canary_worker as worker
from docich.nethack_canary_executor import CanaryKey
from docich.actions import Action
from docich.nethack_policy import PolicyDecision
from docich.nethack_canary_worker import (
    ARENA,
    CanaryWorkerError,
    _candidate_broker,
    _eat_allowed,
    _read_request,
    _run_acceptance_setup,
    _SevereRecoveryTally,
    _start_game,
    _terminal_result,
    _timeout_result,
    run_episode,
)
from docich.nethack_observation import normalize_tty


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
    # A provided seed is written to the arena DEV_RANDOM file and used by NetHack.
    assert result["seed_applied"] is True
    assert result["candidate_action_source"] == "candidate_strategist_broker"
    assert result["production_state_touched"] is False


def test_baseline_never_uses_candidate_broker():
    parsed = _read_request(json.dumps(baseline_request()))
    assert _candidate_broker(parsed) is None


def test_eat_cooldown_requires_a_visible_turn_and_spacing():
    from docich.nethack_canary_worker import EAT_COOLDOWN_TURNS

    assert _eat_allowed(None, None) is False
    assert _eat_allowed(50, None) is True
    assert _eat_allowed(50 + EAT_COOLDOWN_TURNS - 1, 50) is False
    assert _eat_allowed(50 + EAT_COOLDOWN_TURNS, 50) is True


def test_prepare_runtime_writes_a_seed_file(tmp_path, monkeypatch):
    from docich import nethack_canary_worker as worker

    episode = tmp_path / "episode-000"
    playground = episode / "playground"
    arena = {
        "episode_root": str(episode),
        "playground_dir": str(playground),
        "save_dir": str(playground / "save"),
        "xlogfile": str(playground / "xlogfile"),
        "dump_dir": str(playground / "dumps"),
    }
    monkeypatch.setattr(worker, "ARENA", arena)
    worker._prepare_runtime(seed=123)
    assert (episode / "seed").read_bytes() == (123).to_bytes(8, "little")
    worker._prepare_runtime(seed=None)
    assert len((episode / "seed").read_bytes()) == 8


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


def _screen(message="", *, condition="", turn=12, depth=1):
    grid = [""] * 21
    grid[10] = " " * 40 + "@"
    return "\n".join([
        message, *grid,
        f"Dlvl:{depth} HP:16(16) Pw:2(2) AC:6 Xp:1",
        f"T:{turn} {condition}".rstrip(),
    ])


class _PrayingGame:
    """Scripted tmux stand-in: a FoodPois frame, the reviewed prayer, then the result."""

    def __init__(self, *, prayer_cures: bool) -> None:
        self.prayer_cures = prayer_cures
        self.keys: list[str] = []
        self.mode = "play"
        self.typed = ""
        self.turn = 12
        self.severe = True

    def start(self) -> None:
        return None

    def alive(self) -> bool:
        return True

    def stop(self) -> None:
        return None

    def capture(self) -> str:
        if self.mode == "ext":
            return _screen("#" + self.typed, condition="FoodPois" if self.severe else "", turn=self.turn)
        if self.mode == "confirm":
            return _screen("Are you sure you want to pray? [yn] (n)", condition="FoodPois", turn=self.turn)
        return _screen("", condition="FoodPois" if self.severe else "", turn=self.turn)

    def literal(self, value: str) -> None:
        self.keys.append(value)
        if self.mode == "play" and value == "#":
            self.mode = "ext"
            self.typed = ""
        elif self.mode == "play" and value == ".":
            self.turn += 1
        elif self.mode == "ext" and value == "\n":
            self.mode = "confirm"
        elif self.mode == "ext":
            self.typed += value
        elif self.mode == "confirm" and value == "y":
            self.mode = "play"
            self.turn += 3
            if self.prayer_cures:
                self.severe = False

    def special(self, value: str) -> None:  # pragma: no cover - not used by the reviewed path
        self.keys.append(value)

    def apply_action(self, action) -> None:
        self.literal(action.text)

    def apply_key(self, key: CanaryKey) -> None:
        self.literal(key.value)


def _run_candidate(monkeypatch, game, *, max_turns=100):
    monkeypatch.setattr(worker, "_TmuxGame", lambda player: game)
    monkeypatch.setattr(worker, "_prepare_runtime", lambda seed=None: None)
    monkeypatch.setattr(worker, "_terminal_record", lambda player: None)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    request = candidate_request()
    request["max_turns"] = max_turns
    request["wall_timeout_s"] = 6
    return run_episode(request)


PRAYER_KEYS = ["#", "p", "r", "a", "y", "\n", "y"]


def test_candidate_arm_drives_reviewed_prayer_through_resolver_until_resolved(monkeypatch):
    game = _PrayingGame(prayer_cures=True)
    result = _run_candidate(monkeypatch, game)
    assert game.keys[: len(PRAYER_KEYS)] == PRAYER_KEYS
    assert game.keys.count("#") == 1
    assert result["terminal_status"] == "timeout"
    assert result["exit_reason"] == "max_turns"
    assert result["severe_recovery"] == {
        "acceptance_scenario": None,
        "severe_episodes": 1,
        "recovery_started": 1,
        "recovery_resolved": 1,
        "outcome": "resolved",
    }


def test_candidate_arm_fails_closed_without_a_second_prayer_when_status_persists(monkeypatch):
    game = _PrayingGame(prayer_cures=False)
    result = _run_candidate(monkeypatch, game)
    # The one-prayer budget holds: no second '#' while the status is still severe.
    assert game.keys[: len(PRAYER_KEYS)] == PRAYER_KEYS
    assert game.keys.count("#") == 1
    assert result["exit_reason"] == "policy_stall:progress_blocked"
    assert result["severe_recovery"]["outcome"] == "recovery_unresolved"
    assert result["severe_recovery"]["recovery_started"] == 1
    assert result["severe_recovery"]["recovery_resolved"] == 0


def test_severe_recovery_tally_counts_episodes_and_resolution_on_complete_frames():
    tally = _SevereRecoveryTally()
    assert tally.summary(candidate_request())["outcome"] == "not_observed"
    severe = normalize_tty(_screen(condition="FoodPois"), cols=80, rows=24)
    clear = normalize_tty(_screen(), cols=80, rows=24)
    partial = normalize_tty("", cols=80, rows=24)
    tally.observe_frame(severe)
    tally.observe_frame(severe)
    assert tally.severe_episodes == 1
    tally.observe_frame(partial)
    assert tally.severe_episodes == 1
    assert tally.summary(candidate_request())["outcome"] == "observed_unattempted"
    tally.recovery_sent(PolicyDecision("strategic", "status_recovery", "test", (Action(type="text", text="#"),)))
    assert tally.summary(candidate_request())["outcome"] == "recovery_unresolved"
    tally.recovery_open = False
    tally.recovery_open = True
    tally.observe_frame(partial)
    assert tally.recovery_resolved == 0
    tally.observe_frame(clear)
    assert tally.recovery_resolved == 1
    assert tally.summary(candidate_request())["outcome"] == "resolved"


def test_acceptance_requests_are_fail_closed_by_default_and_candidate_only(monkeypatch):
    raw = candidate_request()
    raw["acceptance"] = {"scenario": "severe_prayer"}
    with pytest.raises(CanaryWorkerError, match="acceptance episode request invalid"):
        _read_request(json.dumps(raw))
    monkeypatch.setitem(
        worker.ACCEPTANCE_SCENARIOS,
        "severe_prayer",
        ((CanaryKey(kind="literal", value="x"), lambda obs: True),),
    )
    assert _read_request(json.dumps(raw))["acceptance"] == {"scenario": "severe_prayer"}
    baseline = baseline_request()
    baseline["acceptance"] = {"scenario": "severe_prayer"}
    with pytest.raises(CanaryWorkerError, match="acceptance episode request invalid"):
        _read_request(json.dumps(baseline))
    raw["acceptance"] = {"scenario": "unreviewed"}
    with pytest.raises(CanaryWorkerError, match="acceptance episode request invalid"):
        _read_request(json.dumps(raw))


def test_acceptance_setup_sends_each_reviewed_key_only_on_its_frame():
    class _Setup:
        def __init__(self, screens):
            self.screens = list(screens)
            self.keys = []

        def capture(self):
            return self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]

        def apply_key(self, key):
            self.keys.append(key.value)

    wanted = _screen("What do you want to eat?")
    game = _Setup([_screen(""), wanted])
    steps = ((CanaryKey(kind="literal", value="e"), lambda obs: obs.prompt == "none" or "eat" in obs.message),)
    _run_acceptance_setup(game, steps, deadline=time.monotonic() + 30)
    assert game.keys == ["e"]
    never = ((CanaryKey(kind="literal", value="e"), lambda obs: False),)
    with pytest.raises(CanaryWorkerError, match="reviewed frame"):
        _run_acceptance_setup(_Setup([_screen("")]), never, deadline=time.monotonic() + 30)


def test_production_nethack_brain_does_not_import_the_canary_worker():
    root = Path(__file__).resolve().parents[1] / "src/docich"
    for name in ("agent/brains.py", "nethack_progress.py", "nethack_policy.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert "nethack_canary" not in source, name
        assert "ACCEPTANCE" not in source, name
