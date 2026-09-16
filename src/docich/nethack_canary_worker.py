"""In-container NetHack gameplay worker for controlled canaries (P5h).

The container image compiles NetHack with VAR_PLAYGROUND fixed to
``/canary/episode/playground``.  This module owns the only interactive NetHack
process in the container, drives it through a private tmux TTY, and emits one
strict P5g worker result to stdout.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .actions import Action
from .nethack_canary_executor import CanaryKey, canary_execution_plan
from .nethack_candidate_eval import load_candidate_manifest
from .nethack_inventory import VisibleInventoryItem, parse_visible_inventory
from .nethack_observation import NethackObservation, normalize_tty
from .nethack_policy import NethackLayeredPolicy, PolicyDecision, assert_p3b_safe
from .nethack_run import AMULET_ACHIEVEMENT, classify_terminal_record, parse_xlog_line
from .nethack_strategist import CommandStrategist, StrategistDispatchResult, evaluate_proposal
from .nethack_strategy import build_strategic_request

REQUEST_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 256 * 1024
PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,31}$")
ARENA = {
    "episode_root": "/canary/episode",
    "playground_dir": "/canary/episode/playground",
    "save_dir": "/canary/episode/playground/save",
    "xlogfile": "/canary/episode/playground/xlogfile",
    "dump_dir": "/canary/episode/playground/dumps",
}


class CanaryWorkerError(RuntimeError):
    pass


def _read_request(text: str) -> dict[str, object]:
    if len(text.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise CanaryWorkerError("worker request exceeds size limit")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CanaryWorkerError("worker request is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise CanaryWorkerError("worker request must be object")
    allowed = {
        "schema_version", "experiment_id", "episode_id", "arm", "arena", "player_name",
        "max_turns", "wall_timeout_s", "seed", "controller", "requirements",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise CanaryWorkerError(f"worker request contains unknown fields: {unknown}")
    if raw.get("schema_version") != REQUEST_SCHEMA_VERSION:
        raise CanaryWorkerError("worker request schema_version invalid")
    if raw.get("arm") not in {"baseline", "candidate"}:
        raise CanaryWorkerError("worker arm invalid")
    arena = raw.get("arena")
    if arena != ARENA:
        raise CanaryWorkerError("worker arena must use the fixed container paths")
    player = raw.get("player_name")
    if not isinstance(player, str) or PLAYER_RE.fullmatch(player) is None:
        raise CanaryWorkerError("worker player_name invalid")
    max_turns = raw.get("max_turns")
    if type(max_turns) is not int or not 100 <= max_turns <= 10_000_000:
        raise CanaryWorkerError("worker max_turns invalid")
    wall = raw.get("wall_timeout_s", 900.0)
    if isinstance(wall, bool) or not isinstance(wall, (int, float)) or not 1 <= float(wall) <= 7200:
        raise CanaryWorkerError("worker wall_timeout_s invalid")
    seed = raw.get("seed")
    if seed is not None and (type(seed) is not int or seed < 0 or seed > 2**63 - 1):
        raise CanaryWorkerError("worker seed invalid")
    requirements = raw.get("requirements")
    if not isinstance(requirements, dict) or set(requirements) != {
        "isolation_mode", "production_state_must_remain_untouched", "wizard_mode", "explore_mode"
    }:
        raise CanaryWorkerError("worker requirements invalid")
    if (
        requirements.get("isolation_mode") != "container"
        or requirements.get("production_state_must_remain_untouched") is not True
        or requirements.get("wizard_mode") is not False
        or requirements.get("explore_mode") is not False
    ):
        raise CanaryWorkerError("worker isolation requirements invalid")
    controller = raw.get("controller")
    if not isinstance(controller, dict):
        raise CanaryWorkerError("worker controller invalid")
    if raw.get("arm") == "baseline":
        if controller != {"kind": "baseline_p3b"}:
            raise CanaryWorkerError("baseline controller must be baseline_p3b")
    else:
        required = {
            "kind", "manifest_path", "candidate_id", "candidate_version",
            "candidate_fingerprint", "command_sha256",
        }
        if set(controller) != required or controller.get("kind") != "candidate_strategist":
            raise CanaryWorkerError("candidate controller shape invalid")
        if controller.get("manifest_path") != "/canary/candidate.json":
            raise CanaryWorkerError("candidate manifest must be mounted at /canary/candidate.json")
    return raw


def _prepare_runtime() -> None:
    root = Path(ARENA["playground_dir"])
    save = Path(ARENA["save_dir"])
    dumps = Path(ARENA["dump_dir"])
    for path in (root, save, dumps):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    for name in ("xlogfile", "logfile", "record", "perm"):
        path = root / name
        path.touch(exist_ok=True)
        os.chmod(path, 0o600)
    home = Path("/tmp/home")
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    (home / ".nethackrc").write_text(
        "OPTIONS=!legacy,menustyle:traditional\n",
        encoding="utf-8",
    )
    os.chmod(home / ".nethackrc", 0o600)


class _TmuxGame:
    def __init__(self, player: str, *, runner=subprocess.run, sleep=time.sleep) -> None:
        self.player = player
        self.runner = runner
        self.sleep = sleep
        self.session = "docich-canary"
        self.target = f"{self.session}:0.0"

    def _run(self, argv: list[str], *, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
        result = self.runner(
            argv,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if getattr(result, "returncode", 1) != 0:
            raise CanaryWorkerError(
                f"tmux command failed: {' '.join(argv[:3])}: {str(getattr(result, 'stderr', ''))[:160]}"
            )
        return result

    def start(self) -> None:
        env = "HOME=/tmp/home TERM=xterm-256color"
        command = f"exec env {env} /usr/local/bin/nethack -u {self.player}"
        self._run(
            [
                "tmux", "new-session", "-d", "-x", "80", "-y", "24",
                "-s", self.session, command,
            ],
            timeout=10.0,
        )

    def alive(self) -> bool:
        result = self.runner(
            ["tmux", "has-session", "-t", self.session],
            text=True,
            capture_output=True,
            timeout=2.0,
            check=False,
        )
        return getattr(result, "returncode", 1) == 0

    def capture(self) -> str:
        return self._run(["tmux", "capture-pane", "-p", "-t", self.target]).stdout

    def literal(self, value: str) -> None:
        if not value:
            return
        self._run(["tmux", "send-keys", "-t", self.target, "-l", "--", value])
        self.sleep(0.08)

    def special(self, value: str) -> None:
        if value not in {"Enter", "Escape", "Space"}:
            raise CanaryWorkerError(f"unsupported tmux special key: {value}")
        self._run(["tmux", "send-keys", "-t", self.target, value])
        self.sleep(0.08)

    def apply_key(self, key: CanaryKey) -> None:
        if key.kind == "literal":
            self.literal(key.value)
        else:
            self.special(key.value)

    def apply_action(self, action: Action) -> None:
        if action.type != "text" or not action.text:
            raise CanaryWorkerError("P3b emitted a non-text action in canary worker")
        self.literal(action.text)

    def stop(self) -> None:
        self.runner(
            ["tmux", "kill-session", "-t", self.session],
            text=True,
            capture_output=True,
            timeout=2.0,
            check=False,
        )


def _terminal_record(player: str) -> dict[str, str] | None:
    path = Path(ARENA["xlogfile"])
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    matches: list[dict[str, str]] = []
    for line in lines:
        record = parse_xlog_line(line)
        if record.get("name") == player:
            matches.append(record)
    return matches[-1] if matches else None


def _int(record: dict[str, str], key: str) -> int | None:
    raw = record.get(key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw, 0 if key == "achieve" else 10)
    except ValueError:
        return None


def _terminal_result(request: dict[str, object], record: dict[str, str], *, exit_reason: str) -> dict[str, object]:
    bits = _int(record, "achieve")
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "worker_status": "completed",
        "episode_id": request["episode_id"],
        "arm": request["arm"],
        "isolation_mode": "container",
        "arena": dict(ARENA),
        "player_name": request["player_name"],
        "seed": request.get("seed"),
        # Stock NetHack 5.0 has no reviewed public deterministic seed interface.
        "seed_applied": False,
        "controller_kind": "baseline_p3b" if request["arm"] == "baseline" else "candidate_strategist",
        "terminal_status": classify_terminal_record(record),
        "score": _int(record, "points"),
        "turns": _int(record, "turns"),
        "max_depth": _int(record, "maxlvl"),
        "death_reason": record.get("death"),
        "got_amulet": bool(isinstance(bits, int) and bits & AMULET_ACHIEVEMENT),
        "exit_reason": exit_reason,
        "candidate_action_source": "baseline_p3b" if request["arm"] == "baseline" else "candidate_strategist",
        "production_state_touched": False,
    }


def _timeout_result(request: dict[str, object], *, reason: str) -> dict[str, object]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "worker_status": "completed",
        "episode_id": request["episode_id"],
        "arm": request["arm"],
        "isolation_mode": "container",
        "arena": dict(ARENA),
        "player_name": request["player_name"],
        "seed": request.get("seed"),
        "seed_applied": False,
        "controller_kind": "baseline_p3b" if request["arm"] == "baseline" else "candidate_strategist",
        "terminal_status": "timeout",
        "score": None,
        "turns": None,
        "max_depth": None,
        "death_reason": None,
        "got_amulet": False,
        "exit_reason": reason,
        "candidate_action_source": "baseline_p3b" if request["arm"] == "baseline" else "candidate_strategist",
        "production_state_touched": False,
    }


def _start_game(game: _TmuxGame, *, deadline: float) -> str:
    game.start()
    last = ""
    while time.monotonic() < deadline:
        if not game.alive():
            raise CanaryWorkerError("NetHack exited during character creation")
        text = game.capture()
        last = text
        observation = normalize_tty(text, cols=80, rows=24)
        if observation.player is not None and observation.vitals.hp is not None:
            return text
        lower = text.lower()
        if "shall i pick" in lower or ("pick a character" in lower and "[y" in lower):
            game.literal("y")
            continue
        if "--more--" in lower:
            game.literal(" ")
            continue
        time.sleep(0.10)
    raise CanaryWorkerError(f"NetHack character creation did not reach gameplay: {last[-160:]!r}")


def _probe_inventory(game: _TmuxGame) -> tuple[VisibleInventoryItem, ...]:
    game.literal("i")
    text = game.capture()
    inventory = parse_visible_inventory(text)
    game.special("Escape")
    return inventory


def _candidate_strategist(request: dict[str, object]) -> CommandStrategist | None:
    if request["arm"] != "candidate":
        return None
    controller = request["controller"]
    assert isinstance(controller, dict)
    manifest = load_candidate_manifest(Path("/canary/candidate.json"))
    expected = {
        "candidate_id": manifest.candidate_id,
        "candidate_version": manifest.version,
        "candidate_fingerprint": manifest.fingerprint,
        "command_sha256": manifest.command_hash,
    }
    for key, value in expected.items():
        if controller.get(key) != value:
            raise CanaryWorkerError(f"candidate manifest identity mismatch: {key}")
    command = manifest.command if isinstance(manifest.command, str) else list(manifest.command)
    return CommandStrategist(
        command,
        timeout_s=manifest.timeout_s,
        max_request_bytes=manifest.max_request_bytes,
        max_response_bytes=manifest.max_response_bytes,
        cwd=Path("/opt/docich"),
    )


def _candidate_keys(
    game: _TmuxGame,
    strategist: CommandStrategist,
    raw_text: str,
    observation: NethackObservation,
    decision: PolicyDecision,
) -> tuple[CanaryKey, ...]:
    inventory: tuple[VisibleInventoryItem, ...] = ()
    if observation.prompt == "none":
        inventory = _probe_inventory(game)
        raw_text = game.capture()
        observation = normalize_tty(raw_text, cols=80, rows=24)
    request = build_strategic_request(observation, decision, inventory)
    try:
        dispatch = strategist.dispatch(request)
    except Exception as exc:
        dispatch = StrategistDispatchResult(status="error", error=str(exc)[:200])
    if dispatch.status != "proposed" or dispatch.proposal is None:
        return ()
    evaluation = evaluate_proposal(
        request,
        dispatch.proposal,
        current_observation=observation,
        current_inventory=inventory,
    )
    plan = canary_execution_plan(
        request,
        evaluation,
        current_observation=observation,
        current_inventory=inventory,
    )
    return plan.keys if plan.allowed else ()


def run_episode(request: dict[str, object]) -> dict[str, object]:
    _prepare_runtime()
    player = str(request["player_name"])
    max_turns = int(request["max_turns"])
    wall_timeout = float(request.get("wall_timeout_s", 900.0))
    # Return before the outer P5g subprocess timeout so the launcher has time
    # to tear down the container and report a clean timeout result.
    deadline = time.monotonic() + max(1.0, wall_timeout - 5.0)
    game = _TmuxGame(player)
    policy = NethackLayeredPolicy()
    strategist = _candidate_strategist(request)
    last_candidate_fingerprint: tuple[object, ...] | None = None
    idle_cycles = 0
    try:
        raw_text = _start_game(game, deadline=deadline)
        while time.monotonic() < deadline:
            record = _terminal_record(player)
            if record is not None:
                return _terminal_result(request, record, exit_reason="terminal_xlog")
            if not game.alive():
                record = _terminal_record(player)
                if record is not None:
                    return _terminal_result(request, record, exit_reason="process_exit_xlog")
                return _timeout_result(request, reason="process_exited_without_terminal_xlog")

            raw_text = game.capture()
            observation = normalize_tty(raw_text, cols=80, rows=24)
            if observation.vitals.turn is not None and observation.vitals.turn >= max_turns:
                return _timeout_result(request, reason="max_turns")
            decision = policy.decide(observation)
            assert_p3b_safe(decision)
            acted = False
            if decision.actions:
                for action in decision.actions:
                    game.apply_action(action)
                acted = True
            elif request["arm"] == "candidate" and decision.requires_llm and strategist is not None:
                fingerprint = (
                    decision.intent,
                    observation.vitals.turn,
                    observation.vitals.dungeon_level,
                    observation.prompt,
                    observation.message,
                )
                if fingerprint != last_candidate_fingerprint:
                    keys = _candidate_keys(game, strategist, raw_text, observation, decision)
                    last_candidate_fingerprint = fingerprint
                    for key in keys:
                        game.apply_key(key)
                    acted = bool(keys)

            if acted:
                idle_cycles = 0
                time.sleep(0.10)
            else:
                idle_cycles += 1
                # A policy which cannot make progress should yield a bounded
                # canary timeout rather than burning the full wall-clock budget.
                if idle_cycles >= 40:
                    return _timeout_result(request, reason=f"policy_stall:{decision.intent}")
                time.sleep(0.10)
        return _timeout_result(request, reason="wall_timeout")
    finally:
        game.stop()


def main() -> int:
    try:
        request = _read_request(sys.stdin.read())
        result = run_episode(request)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "worker_status": "error",
                    "error": str(exc).replace("\n", " ")[:240],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
