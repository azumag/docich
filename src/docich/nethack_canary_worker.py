"""In-container NetHack gameplay worker for controlled canaries (P5h).

The game worker owns the private NetHack arena but never executes candidate
code. Candidate strategic requests cross a Unix socket to a sibling broker
container which has no arena mount. Only validated public proposals come back.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from .actions import Action
from .nethack_canary_executor import CanaryKey, canary_execution_plan
from .nethack_canary_rules import HUNGER_CONDITIONS
from .nethack_inventory import VisibleInventoryItem, parse_visible_inventory
from .nethack_observation import NethackObservation, normalize_tty
from .nethack_canary_tactics import CanaryTacticalPolicy
from .nethack_policy import NethackLayeredPolicy, PolicyDecision, assert_p3b_safe
from .nethack_run import AMULET_ACHIEVEMENT, classify_terminal_record, parse_xlog_line
from .nethack_strategist import ProposalEvaluation, evaluate_proposal
from .nethack_strategy import StrategicProposal, build_strategic_request, parse_strategic_proposal

REQUEST_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1
BROKER_SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 256 * 1024
MAX_BROKER_RESPONSE_BYTES = 32 * 1024
# The canary baseline eats only after this many turns since its last meal, so a
# single Hungry reading cannot empty the whole inventory or overshoot Satiated.
EAT_COOLDOWN_TURNS = 12
# Opt-in: when set to "1" the worker records every applied action as a JSONL
# trace so the P6 action verifier can check it against the catalog.
ACTION_TRACE_ENV = "DOCICH_CANARY_ACTION_TRACE"
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
            "kind", "candidate_id", "candidate_version", "candidate_fingerprint",
            "command_sha256", "broker_socket", "broker_timeout_s",
        }
        if set(controller) != required or controller.get("kind") != "candidate_strategist":
            raise CanaryWorkerError("candidate broker controller shape invalid")
        broker_socket = controller.get("broker_socket")
        if broker_socket != "/canary/episode/.candidate-ipc/candidate.sock":
            raise CanaryWorkerError("candidate broker socket is not the reviewed path")
        broker_timeout = controller.get("broker_timeout_s")
        if (
            isinstance(broker_timeout, bool)
            or not isinstance(broker_timeout, (int, float))
            or not 0.1 <= float(broker_timeout) <= 130.0
        ):
            raise CanaryWorkerError("candidate broker timeout is invalid")
    return raw


def _prepare_runtime(seed: int | None = None) -> None:
    root = Path(ARENA["playground_dir"])
    save = Path(ARENA["save_dir"])
    dumps = Path(ARENA["dump_dir"])
    for path in (root, save, dumps):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    # The image redirects NetHack's DEV_RANDOM to this file, so a provided seed
    # makes an episode reproducible (seed-controlled promotion comparisons).
    seed_path = Path(ARENA["episode_root"]) / "seed"
    seed_bytes = seed.to_bytes(8, "little", signed=False) if seed is not None else os.urandom(8)
    seed_path.write_bytes(seed_bytes)
    os.chmod(seed_path, 0o600)
    for name in ("xlogfile", "logfile", "record", "perm"):
        path = root / name
        path.touch(exist_ok=True)
        os.chmod(path, 0o600)
    home = Path("/tmp/home")
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    (home / ".nethackrc").write_text(
        # !legacy keeps the short startup, traditional menus keep the visible
        # frame small, !tutorial suppresses NetHack 5.0's blocking
        # "Do you want a tutorial?" query, and time exposes the T: turn counter
        # so the worker can enforce max_turns and report progress.
        "OPTIONS=!legacy,menustyle:traditional,!tutorial,time\n",
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
        "seed_applied": request.get("seed") is not None,
        "controller_kind": "baseline_p3b" if request["arm"] == "baseline" else "candidate_strategist",
        "terminal_status": classify_terminal_record(record),
        "score": _int(record, "points"),
        "turns": _int(record, "turns"),
        "max_depth": _int(record, "maxlvl"),
        "death_reason": record.get("death"),
        "got_amulet": bool(isinstance(bits, int) and bits & AMULET_ACHIEVEMENT),
        "exit_reason": exit_reason,
        "candidate_action_source": "baseline_p3b" if request["arm"] == "baseline" else "candidate_strategist_broker",
        "production_state_touched": False,
    }


def _timeout_result(
    request: dict[str, object],
    *,
    reason: str,
    turns: int | None = None,
    max_depth: int | None = None,
    last_message: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "worker_status": "completed",
        "episode_id": request["episode_id"],
        "arm": request["arm"],
        "isolation_mode": "container",
        "arena": dict(ARENA),
        "player_name": request["player_name"],
        "seed": request.get("seed"),
        "seed_applied": request.get("seed") is not None,
        "controller_kind": "baseline_p3b" if request["arm"] == "baseline" else "candidate_strategist",
        "terminal_status": "timeout",
        "score": None,
        "turns": turns,
        "max_depth": max_depth,
        "death_reason": None,
        "got_amulet": False,
        "exit_reason": reason,
        "candidate_action_source": "baseline_p3b" if request["arm"] == "baseline" else "candidate_strategist_broker",
        "production_state_touched": False,
        "last_message": last_message[:120] if isinstance(last_message, str) else None,
    }


def _start_game(game: _TmuxGame, *, deadline: float) -> str:
    game.start()
    last = ""
    while time.monotonic() < deadline:
        if not game.alive():
            raise CanaryWorkerError("NetHack exited during character creation")
        text = game.capture()
        last = text
        lower = text.lower()
        # A blocking startup query can overlay an already-drawn map, so answer
        # the reviewed prompts before the gameplay check below.
        if "do you want a tutorial" in lower:
            game.literal("n")
            continue
        if "shall i pick" in lower or "is this ok" in lower or ("pick a character" in lower and "[y" in lower):
            game.literal("y")
            continue
        if "--more--" in lower:
            game.literal(" ")
            continue
        observation = normalize_tty(text, cols=80, rows=24)
        if observation.player is not None and observation.vitals.hp is not None:
            return text
        time.sleep(0.10)
    raise CanaryWorkerError(f"NetHack character creation did not reach gameplay: {last[-160:]!r}")


def _probe_inventory(game: _TmuxGame) -> tuple[VisibleInventoryItem, ...]:
    game.literal("i")
    text = game.capture()
    inventory = parse_visible_inventory(text)
    game.special("Escape")
    return inventory


def _candidate_broker(request: dict[str, object]) -> tuple[str, float] | None:
    if request["arm"] != "candidate":
        return None
    controller = request["controller"]
    assert isinstance(controller, dict)
    return str(controller["broker_socket"]), float(controller["broker_timeout_s"])


def _broker_dispatch(socket_path: str, timeout_s: float, request) -> StrategicProposal | None:
    payload = request.to_json().encode("utf-8") + b"\n"
    if len(payload) > 64 * 1024:
        return None
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(timeout_s)
        client.connect(socket_path)
        client.sendall(payload)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BROKER_RESPONSE_BYTES:
                return None
            if b"\n" in chunk:
                break
    except (OSError, socket.timeout):
        return None
    finally:
        client.close()
    raw_line = b"".join(chunks).split(b"\n", 1)[0]
    try:
        envelope = json.loads(raw_line.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(envelope, dict) or set(envelope) != {"schema_version", "status", "proposal", "error"}:
        return None
    if envelope.get("schema_version") != BROKER_SCHEMA_VERSION or envelope.get("status") != "proposed":
        return None
    raw_proposal = envelope.get("proposal")
    if not isinstance(raw_proposal, dict):
        return None
    try:
        return parse_strategic_proposal(raw_proposal)
    except ValueError:
        return None


def _candidate_keys(
    game: _TmuxGame,
    broker: tuple[str, float],
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
    proposal = _broker_dispatch(broker[0], broker[1], request)
    if proposal is None:
        return ()
    evaluation: ProposalEvaluation = evaluate_proposal(
        request,
        proposal,
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


def _eat_allowed(turn: int | None, last_eat_turn: int | None) -> bool:
    """True when the visible turn counter allows another canary meal."""
    if turn is None:
        return False
    if last_eat_turn is None:
        return True
    return turn - last_eat_turn >= EAT_COOLDOWN_TURNS


def _append_trace(path: Path, record: dict[str, object]) -> None:
    """Best-effort action-trace append; tracing must never break an episode."""
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        return


def run_episode(request: dict[str, object]) -> dict[str, object]:
    _prepare_runtime(seed=request.get("seed"))
    player = str(request["player_name"])
    max_turns = int(request["max_turns"])
    wall_timeout = float(request.get("wall_timeout_s", 900.0))
    deadline = time.monotonic() + max(1.0, wall_timeout - 5.0)
    game = _TmuxGame(player)
    baseline = request["arm"] == "baseline"
    # The isolated canary baseline uses the reviewed tactical surface; the
    # candidate arm keeps the production P3b policy and broker dispatch.
    policy = CanaryTacticalPolicy() if baseline else NethackLayeredPolicy()
    broker = _candidate_broker(request)
    last_candidate_fingerprint: tuple[object, ...] | None = None
    idle_cycles = 0
    last_turns: int | None = None
    last_depth: int | None = None
    last_message: str | None = None
    last_eat_turn: int | None = None
    trace_path: Path | None = None
    if os.environ.get(ACTION_TRACE_ENV) == "1":
        trace_path = Path(ARENA["episode_root"]) / "action-trace.jsonl"
    try:
        _start_game(game, deadline=deadline)
        while time.monotonic() < deadline:
            record = _terminal_record(player)
            if record is not None:
                return _terminal_result(request, record, exit_reason="terminal_xlog")
            if not game.alive():
                record = _terminal_record(player)
                if record is not None:
                    return _terminal_result(request, record, exit_reason="process_exit_xlog")
                return _timeout_result(
                    request,
                    reason="process_exited_without_terminal_xlog",
                    turns=last_turns,
                    max_depth=last_depth,
                    last_message=last_message,
                )

            raw_text = game.capture()
            observation = normalize_tty(raw_text, cols=80, rows=24)
            last_turns = observation.vitals.turn
            last_depth = observation.vitals.dungeon_level
            last_message = observation.message
            if observation.vitals.turn is not None and observation.vitals.turn >= max_turns:
                return _timeout_result(
                    request,
                    reason="max_turns",
                    turns=last_turns,
                    max_depth=last_depth,
                    last_message=last_message,
                )
            inventory: tuple[VisibleInventoryItem, ...] = ()
            if (
                baseline
                and observation.prompt == "none"
                and any(condition in HUNGER_CONDITIONS for condition in observation.conditions)
                and _eat_allowed(observation.vitals.turn, last_eat_turn)
            ):
                # Hunger is visible, so probe the inventory before deciding; the
                # catalog's eat_food precondition then sees the food item.
                inventory = _probe_inventory(game)
                raw_text = game.capture()
                observation = normalize_tty(raw_text, cols=80, rows=24)
                last_turns = observation.vitals.turn
                last_depth = observation.vitals.dungeon_level
                last_message = observation.message
            decision = policy.decide(observation, inventory=inventory)
            if baseline:
                policy.assert_safe(decision)
            else:
                assert_p3b_safe(decision)
            acted = False
            if decision.actions:
                trace_before = observation.raw_text if trace_path is not None else ""
                for action in decision.actions:
                    game.apply_action(action)
                acted = True
                if decision.intent == "eat_food":
                    last_eat_turn = observation.vitals.turn
                if trace_path is not None:
                    trace_record: dict[str, object] = {
                        "intent": decision.intent,
                        "keys": [action.text for action in decision.actions],
                        "before": trace_before,
                        "after": game.capture(),
                    }
                    if decision.intent == "eat_food":
                        trace_record["inventory"] = [item.public_dict() for item in inventory]
                    _append_trace(trace_path, trace_record)
            elif request["arm"] == "candidate" and decision.requires_llm and broker is not None:
                fingerprint = (
                    decision.intent,
                    observation.vitals.turn,
                    observation.vitals.dungeon_level,
                    observation.prompt,
                    observation.message,
                )
                if fingerprint != last_candidate_fingerprint:
                    keys = _candidate_keys(game, broker, raw_text, observation, decision)
                    last_candidate_fingerprint = fingerprint
                    for key in keys:
                        game.apply_key(key)
                    acted = bool(keys)

            if acted:
                idle_cycles = 0
                time.sleep(0.10)
            else:
                idle_cycles += 1
                if idle_cycles >= 40:
                    return _timeout_result(
                        request,
                        reason=f"policy_stall:{decision.intent}",
                        turns=last_turns,
                        max_depth=last_depth,
                        last_message=last_message,
                    )
                time.sleep(0.10)
        return _timeout_result(
            request,
            reason="wall_timeout",
            turns=last_turns,
            max_depth=last_depth,
            last_message=last_message,
        )
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
