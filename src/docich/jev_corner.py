"""Manual, one-game JEV player corner for the existing sorengame runtime.

The corner does not start a second game runtime and does not use the normal
``GameSwitchCoordinator`` replacement path.  It asks the Soren lifecycle
broker to change only the player policy at a game boundary, then records a
small operator state under docich's private state directory.  The Soren loop
owns the one-game stop; this manager owns the explicit JEV -> existing
handover afterwards.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from .adapters.soren import SorenCoordinatorAdapter
from .adapters.base import AdapterError
from .config import ConfigError, GlobalConfig, load_game, load_global
from .game_switch import RuntimeSpec, atomic_write_json
from .naming import runtime_names
from .retro_corner import CornerResult, _safe_detail


GAME_NAME = "sorengame"
STATE_SCHEMA_VERSION = 1
STATE_FILE = "jev_corner.json"
LOCK_FILE = "locks/jev-corner.lock"
FIXED_MAX_REQUESTS_PER_RUN = 500
FIXED_DECISION_BUDGET_MS = 1500
FIXED_HTTP_TIMEOUT_MS = 1000
ACTIVE_STATUSES = frozenset({"preparing", "active", "restoring", "recovery_required"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "idle"})
PRECOMMIT_CAPABILITY_ERROR = "Soren player_policy_v1 capabilityを確認できません"
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
DIAGNOSE_CODES = {
    "ready": 0,
    "capability_missing": 41,
    "capability_invalid": 42,
    "player_state_invalid": 43,
    "corner_state_active": 44,
    "corner_state_invalid": 45,
}
RECOVER_DIAGNOSE_CODES = {
    "ready_stopped": 0,
    "corner_stale_precommit": 51,
    "corner_state_active": 52,
    "corner_state_invalid": 53,
    "player_state_invalid": 54,
    "player_policy_jev": 55,
    "lifecycle_boundary": 56,
    "lifecycle_stop_requested": 57,
    "lifecycle_stopping": 58,
    "lifecycle_stopped": 59,
    "lifecycle_resumable": 60,
    "lifecycle_unrecoverable": 61,
    "corner_recovery_required_no_player": 62,
    "corner_recovery_required_generation_mismatch": 63,
    "corner_recovery_required_request_invalid": 64,
    "corner_recovery_required_started": 65,
    "corner_preparing": 66,
    "corner_restoring": 67,
    "corner_active_committed": 68,
    "corner_recovery_required_invalid": 69,
    "corner_stale_precommit_recovery_lock": 70,
    "corner_stale_precommit_boundary": 71,
    "corner_stale_precommit_stop_requested": 72,
    "corner_stale_precommit_stopping": 73,
    "corner_stale_precommit_stopped": 74,
    "corner_stale_precommit_resumable": 75,
    "corner_stale_precommit_unrecoverable": 76,
}


class JevCornerError(RuntimeError):
    """User-facing failure in the manual JEV corner."""


@dataclass(frozen=True)
class JevCornerConfig:
    """Static contract values for one explicitly started corner."""

    enabled: bool = False
    one_game: bool = True
    max_requests_per_run: int = FIXED_MAX_REQUESTS_PER_RUN
    decision_budget_ms: int = FIXED_DECISION_BUDGET_MS
    http_timeout_ms: int = FIXED_HTTP_TIMEOUT_MS
    boundary_timeout_s: float = 7200.0
    candidate_version: str = "uniform25-v1"
    model: str = "jev-1.13.0"

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise JevCornerError("jev_corner.enabled はtrue/falseである必要があります")
        if type(self.one_game) is not bool or not self.one_game:
            raise JevCornerError("jev_corner.one_game はtrue固定です")
        for name in ("max_requests_per_run", "decision_budget_ms", "http_timeout_ms"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise JevCornerError(f"jev_corner.{name} は正の整数である必要があります")
        fixed_values = {
            "max_requests_per_run": FIXED_MAX_REQUESTS_PER_RUN,
            "decision_budget_ms": FIXED_DECISION_BUDGET_MS,
            "http_timeout_ms": FIXED_HTTP_TIMEOUT_MS,
        }
        for name, expected in fixed_values.items():
            if getattr(self, name) != expected:
                raise JevCornerError(f"jev_corner.{name} は{expected}固定です")
        if self.http_timeout_ms > self.decision_budget_ms:
            raise JevCornerError("jev_corner.http_timeout_ms はdecision_budget_ms以下である必要があります")
        if isinstance(self.boundary_timeout_s, bool) or not isinstance(self.boundary_timeout_s, (int, float)):
            raise JevCornerError("jev_corner.boundary_timeout_s は正の秒数である必要があります")
        if float(self.boundary_timeout_s) <= 0:
            raise JevCornerError("jev_corner.boundary_timeout_s は正の秒数である必要があります")
        if self.candidate_version != "uniform25-v1":
            raise JevCornerError("jev_corner.candidate_version はuniform25-v1固定です")
        if self.model != "jev-1.13.0":
            raise JevCornerError("jev_corner.model はjev-1.13.0固定です")


def load_jev_corner_config(g: GlobalConfig) -> JevCornerConfig:
    """Load the sorengame-local opt-in table; missing means disabled."""

    try:
        game = load_game(g, GAME_NAME)
    except ConfigError as exc:
        raise JevCornerError(f"sorengame定義を読み込めません: {_safe_detail(exc)}") from exc
    raw = game.raw.get("jev_corner", {}) if isinstance(game.raw, dict) else {}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise JevCornerError("[jev_corner] はtableである必要があります")
    values = {
        "enabled": raw.get("enabled", False),
        "one_game": raw.get("one_game", True),
        "max_requests_per_run": raw.get("max_requests_per_run", FIXED_MAX_REQUESTS_PER_RUN),
        "decision_budget_ms": raw.get("decision_budget_ms", FIXED_DECISION_BUDGET_MS),
        "http_timeout_ms": raw.get("http_timeout_ms", FIXED_HTTP_TIMEOUT_MS),
        "boundary_timeout_s": raw.get("boundary_timeout_s", 7200.0),
        "candidate_version": raw.get("candidate_version", "uniform25-v1"),
        "model": raw.get("model", "jev-1.13.0"),
    }
    return JevCornerConfig(**values)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _config_hash(config: JevCornerConfig) -> str:
    payload = {
        "schema": STATE_SCHEMA_VERSION,
        "game": GAME_NAME,
        "candidate_version": config.candidate_version,
        "model": config.model,
        "max_requests_per_run": config.max_requests_per_run,
        "decision_budget_ms": config.decision_budget_ms,
        "http_timeout_ms": config.http_timeout_ms,
        "one_game": config.one_game,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _soren_root(g: GlobalConfig) -> Path:
    try:
        game = load_game(g, GAME_NAME)
    except ConfigError as exc:
        raise JevCornerError(f"sorengame定義を読み込めません: {_safe_detail(exc)}") from exc
    root_raw = game.raw.get("soren") if isinstance(game.raw, dict) else None
    if not isinstance(root_raw, dict) or not isinstance(root_raw.get("root"), str):
        raise JevCornerError("sorengameの[soren].rootがありません")
    root = Path(root_raw["root"])
    if not root.is_absolute() or "\x00" in str(root):
        raise JevCornerError("sorengameの[soren].rootが不正です")
    return root.resolve()


def diagnose(g: GlobalConfig) -> str:
    """Return a fixed, non-sensitive preflight category for the owner operator."""

    root = _soren_root(g)
    capability_path = root / "tmp/state/game_lifecycle/player_capabilities.json"
    if not capability_path.is_file():
        return "capability_missing"
    capability = _read_json(capability_path)
    if (
        capability is None
        or capability.get("schema") != 1
        or capability.get("game") != GAME_NAME
        or not isinstance(capability.get("capabilities"), list)
        or "player_policy_v1" not in capability.get("capabilities", [])
    ):
        return "capability_invalid"
    pid = capability.get("pid")
    if type(pid) is not int or pid <= 0:
        return "capability_invalid"
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return "capability_invalid"
    cmdline = Path(f"/proc/{pid}/cmdline")
    if cmdline.exists():
        try:
            if "soviet_local.mjs" not in cmdline.read_bytes().decode(errors="replace"):
                return "capability_invalid"
        except OSError:
            return "capability_invalid"

    player_state = _read_json(root / "tmp/state/game_lifecycle/player_state.json")
    if player_state is not None and (
        player_state.get("schema") != 1
        or player_state.get("game") != GAME_NAME
        or player_state.get("policy") not in {"existing", "jev"}
        or type(player_state.get("player_generation")) is not int
        or player_state.get("player_generation") < 0
    ):
        return "player_state_invalid"
    if player_state is not None and player_state.get("policy") == "jev":
        return "corner_state_active"

    corner_state = _read_json(Path(g.state_dir) / STATE_FILE)
    if corner_state is not None:
        if corner_state.get("schema_version") != STATE_SCHEMA_VERSION or corner_state.get("game") != GAME_NAME:
            return "corner_state_invalid"
        if corner_state.get("status") in ACTIVE_STATUSES:
            return "corner_state_active"
        if corner_state.get("status") not in TERMINAL_STATUSES:
            return "corner_state_invalid"
    return "ready"


def _run_bridge_relaunch(root: Path) -> None:
    """Run the reviewed game-bridge-only relaunch entry point."""

    subprocess.run(
        [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-euo",
            "pipefail",
            "-c",
            "source ./eloop_lib.sh && _br_relaunch",
        ],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        timeout=240,
        check=True,
    )


def _expired_pre_stop_request(payload: dict[str, object]) -> bool:
    """Identify a reversible lifecycle request that expired before stop claim."""

    ack = payload.get("ack") if isinstance(payload.get("ack"), dict) else {}
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    if ack.get("status") not in {"boundary", "stop_requested"}:
        return False
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id or ack.get("request_id") != request_id:
        return False
    deadline_epoch = request.get("deadline_epoch")
    if isinstance(deadline_epoch, bool) or not isinstance(deadline_epoch, (int, float)):
        return False
    return float(deadline_epoch) < time.time()


def _stale_precommit_recovery_category(manager: "JevCornerManager") -> str:
    """Classify the lifecycle side of a proven stale corner without mutation."""

    lock_path = manager.adapter.root / "tmp/state/.runtime_recovery.lock"
    if lock_path.is_dir():
        try:
            if time.time() - lock_path.stat().st_mtime < 120:
                return "corner_stale_precommit_recovery_lock"
        except OSError:
            return "corner_stale_precommit_unrecoverable"
    try:
        payload = manager.adapter._status(time.monotonic() + 30.0, None)
    except Exception:
        return "corner_stale_precommit_unrecoverable"
    ack = manager.adapter._ack(payload)
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    resource = payload.get("resource") if isinstance(payload.get("resource"), dict) else {}
    if request.get("game") not in {None, GAME_NAME} or resource.get("game") not in {None, GAME_NAME}:
        return "corner_stale_precommit_unrecoverable"
    status = ack.get("status")
    if status in {"boundary", "stop_requested", "stopping", "stopped"}:
        return f"corner_stale_precommit_{status}"
    if (
        not ack
        and resource.get("status") == "stopped"
        and isinstance(request.get("request_id"), str)
        and resource.get("request_id") == request.get("request_id")
    ):
        return "corner_stale_precommit_resumable"
    return "corner_stale_precommit_unrecoverable"


def _recover_precommit_failure(corner_state: dict[str, object], player_state: dict[str, object] | None) -> bool:
    """Prove that a recovery_required corner never committed JEV."""

    base = (
        corner_state.get("status") == "recovery_required"
        and corner_state.get("policy") == "jev"
        and corner_state.get("started_at") is None
        and corner_state.get("completed_at") is None
        and isinstance(corner_state.get("request_id"), str)
        and UUID_RE.fullmatch(corner_state["request_id"]) is not None
    )
    if not base:
        return False
    if player_state is None:
        # The adapter checks the bridge capability before it creates the
        # remote player_change request.  In that exact failure case the
        # player snapshot may legitimately be absent, so allow recovery only
        # when the persisted error proves that no commit could have happened.
        return (
            corner_state.get("last_error") == PRECOMMIT_CAPABILITY_ERROR
            and type(corner_state.get("player_generation")) is int
            and corner_state.get("player_generation") >= 0
        )
    return (
        player_state.get("policy") == "existing"
        and type(player_state.get("player_generation")) is int
        and corner_state.get("player_generation") == player_state.get("player_generation")
    )


def recover_bridge_diagnose(g: GlobalConfig) -> str:
    """Return fixed, non-sensitive recovery preflight categories."""

    root = _soren_root(g)
    manager = JevCornerManager(g)
    player_path = manager.adapter.root / "tmp/state/game_lifecycle/player_state.json"
    player_state = _read_json(player_path)
    if player_state is None and player_path.exists():
        return "player_state_invalid"
    if player_state is not None and (
        player_state.get("schema") != 1
        or player_state.get("game") != GAME_NAME
        or player_state.get("policy") not in {"existing", "jev"}
        or type(player_state.get("player_generation")) is not int
        or player_state.get("player_generation") < 0
    ):
        return "player_state_invalid"
    if player_state is not None and player_state.get("policy") == "jev":
        return "player_policy_jev"

    corner_path = Path(g.state_dir) / STATE_FILE
    corner_state = _read_json(corner_path)
    if corner_state is None and corner_path.exists():
        return "corner_state_invalid"
    if corner_state is not None:
        if corner_state.get("schema_version") != STATE_SCHEMA_VERSION or corner_state.get("game") != GAME_NAME:
            return "corner_state_invalid"
        if corner_state.get("status") in ACTIVE_STATUSES:
            status = corner_state.get("status")
            if status == "recovery_required":
                if corner_state.get("started_at") is not None or corner_state.get("completed_at") is not None:
                    return "corner_recovery_required_started"
                if not isinstance(corner_state.get("request_id"), str) or UUID_RE.fullmatch(corner_state["request_id"]) is None:
                    return "corner_recovery_required_request_invalid"
                if player_state is None:
                    if _recover_precommit_failure(corner_state, None):
                        return _stale_precommit_recovery_category(manager)
                    return "corner_recovery_required_no_player"
                if corner_state.get("player_generation") != player_state.get("player_generation"):
                    return "corner_recovery_required_generation_mismatch"
                if _recover_precommit_failure(corner_state, player_state):
                    return _stale_precommit_recovery_category(manager)
                return "corner_recovery_required_invalid"
            elif status == "preparing":
                return "corner_preparing"
            elif status == "restoring":
                return "corner_restoring"
            else:
                return "corner_active_committed"
        if corner_state.get("status") not in TERMINAL_STATUSES:
            return "corner_state_invalid"

    payload = manager.adapter._status(time.monotonic() + 30.0, None)
    ack = manager.adapter._ack(payload)
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    resource = payload.get("resource") if isinstance(payload.get("resource"), dict) else {}
    if request.get("game") not in {None, GAME_NAME} or resource.get("game") not in {None, GAME_NAME}:
        return "lifecycle_unrecoverable"
    if ack.get("status") in {"boundary", "stop_requested", "stopping", "stopped"}:
        return f"lifecycle_{ack['status']}"
    if (
        not ack
        and resource.get("status") == "stopped"
        and isinstance(request.get("request_id"), str)
        and resource.get("request_id") == request.get("request_id")
    ):
        return "lifecycle_resumable"
    return "lifecycle_unrecoverable"


def _assert_stopped_bridge_recovery(g: GlobalConfig, manager: "JevCornerManager") -> dict[str, object]:
    """Fail closed unless only the previously parked game runtime is recoverable."""

    player_path = manager.adapter.root / "tmp/state/game_lifecycle/player_state.json"
    player_state = _read_json(player_path)
    if player_state is None and player_path.exists():
        raise JevCornerError("Soren player_state.jsonが不正です")
    player_policy = "existing"
    if player_state is not None:
        if (
            player_state.get("schema") != 1
            or player_state.get("game") != GAME_NAME
            or player_state.get("policy") not in {"existing", "jev"}
            or type(player_state.get("player_generation")) is not int
            or player_state.get("player_generation") < 0
        ):
            raise JevCornerError("Soren player_state.jsonの契約が不正です")
        player_policy = str(player_state.get("policy"))
        if player_policy == "jev":
            raise JevCornerError("JEV player policyがactiveのためbridge recoveryを実行できません")

    corner_path = Path(g.state_dir) / STATE_FILE
    corner_state = _read_json(corner_path)
    if corner_state is None and corner_path.exists():
        raise JevCornerError("jev corner stateを読み込めません")
    stale_corner_state = False
    if corner_state is not None:
        if corner_state.get("schema_version") != STATE_SCHEMA_VERSION or corner_state.get("game") != GAME_NAME:
            raise JevCornerError("jev corner state schemaが不正です")
        if corner_state.get("status") in ACTIVE_STATUSES:
            precommit_failure = _recover_precommit_failure(corner_state, player_state)
            if not precommit_failure:
                raise JevCornerError("JEV cornerがactiveのためbridge recoveryを実行できません")
            stale_corner_state = True
        elif corner_state.get("status") not in TERMINAL_STATUSES:
            raise JevCornerError("jev corner state statusが不正です")

    payload = manager.adapter._status(time.monotonic() + 30.0, None)
    ack = manager.adapter._ack(payload)
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    resource = payload.get("resource") if isinstance(payload.get("resource"), dict) else {}
    ack_status = ack.get("status")
    recoverable_ack_statuses = {"boundary", "stop_requested", "stopping", "stopped"}
    resumable_without_ack = (
        not ack
        and resource.get("status") == "stopped"
        and isinstance(request.get("request_id"), str)
        and resource.get("request_id") == request.get("request_id")
    )
    if ack_status not in recoverable_ack_statuses and not resumable_without_ack:
        raise JevCornerError("停止済みのSoren game-only requestを確認できません")
    request_id = ack.get("request_id") or request.get("request_id") or resource.get("request_id")
    if not isinstance(request_id, str):
        raise JevCornerError("Soren stopped request identityを確認できません")
    if request.get("request_id") not in {None, request_id} or resource.get("request_id") not in {None, request_id}:
        raise JevCornerError("Soren stopped request identityが一致しません")
    for record in (request, resource):
        if record.get("game") not in {None, GAME_NAME}:
            raise JevCornerError("Soren stopped requestのgameがsorengameではありません")
    if stale_corner_state:
        payload = {**payload, "_stale_corner_state": True}
    return payload


def refresh_bridge(g: GlobalConfig) -> None:
    """Reload only the live Soren game bridge at a safe game boundary."""

    category = diagnose(g)
    if category not in {"capability_missing", "capability_invalid"}:
        raise JevCornerError("Soren bridge refreshのpreflight条件を満たしません")
    root = _soren_root(g)
    manager = JevCornerManager(g)
    deadline = time.monotonic() + 600.0
    request_id = _new_uuid()
    try:
        # Drain the current match without suppressing input, then use the
        # existing game-only stop/fresh-start contract.  This parks only the
        # game-owned runtime; common overlay/audio/encoder workers remain up.
        manager.adapter.request_round_boundary(request_id, deadline, None)
        manager.adapter.cleanup_runtime(deadline, None)
        _run_bridge_relaunch(root)
        manager.adapter.materialize_runtime(deadline, None)
        manager.adapter.readiness(deadline, None)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise JevCornerError("Soren bridgeの再起動に失敗しました") from exc
    except Exception as exc:
        raise JevCornerError("Soren bridgeの安全な再起動に失敗しました") from exc
    capability = root / "tmp/state/game_lifecycle/player_capabilities.json"
    if not capability.is_file():
        raise JevCornerError("Soren bridge capabilityを確認できません")


def recover_bridge(g: GlobalConfig) -> None:
    """Recover the game-only runtime left stopped by a failed bridge refresh."""

    root = _soren_root(g)
    manager = JevCornerManager(g)
    payload = _assert_stopped_bridge_recovery(g, manager)
    deadline = time.monotonic() + 600.0
    try:
        manager.adapter.preflight(deadline, None)
        ack = manager.adapter._ack(payload)
        if ack and ack.get("status") != "stopped":
            if _expired_pre_stop_request(payload):
                # A boundary/stop_requested request has not crossed the
                # irreversible stopping fence.  If its deadline expired,
                # cancel that same reversible request before relaunching;
                # never create a second lifecycle request or force a stop.
                request_id = payload["request"]["request_id"]
                if not manager.adapter.cancel_round_boundary(request_id, deadline, None):
                    raise JevCornerError("期限切れのSoren pre-stop requestをcancelできません")
                payload = manager.adapter._status(deadline, None)
                if manager.adapter._ack(payload).get("status") != "cancelled":
                    raise JevCornerError("Soren pre-stop requestのcancel結果を確認できません")
            else:
                # A refresh can fail after parking the loop but before the
                # irreversible stop acknowledgement is written.  Complete
                # that same request; never create a second lifecycle request.
                manager.adapter.cleanup_runtime(deadline, None)
        _run_bridge_relaunch(root)
        manager.adapter.materialize_runtime(deadline, None)
        manager.adapter.readiness(deadline, None)
        if payload.get("_stale_corner_state") is True:
            manager.recover()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise JevCornerError("停止済みSoren game bridgeの復旧に失敗しました") from exc
    except Exception as exc:
        raise JevCornerError("停止済みSoren game runtimeの安全な復旧に失敗しました") from exc
    capability = root / "tmp/state/game_lifecycle/player_capabilities.json"
    if not capability.is_file():
        raise JevCornerError("Soren bridge capabilityを復旧後に確認できません")


class JevCornerManager:
    """Own the explicit JEV activation and restoration transactions."""

    def __init__(
        self,
        g: GlobalConfig,
        *,
        config: JevCornerConfig | None = None,
        adapter=None,
        now: Callable[[], float] = time.time,
    ):
        self.g = g
        self.config = config or load_jev_corner_config(g)
        self.state_path = Path(g.state_dir) / STATE_FILE
        self.lock_path = Path(g.state_dir) / LOCK_FILE
        self._now = now
        self.adapter = adapter or self._make_adapter()

    def _make_adapter(self) -> SorenCoordinatorAdapter:
        game = load_game(self.g, GAME_NAME)
        if game.adapter != "soren":
            raise JevCornerError("sorengameのadapterはsorenである必要があります")
        root_raw = game.raw.get("soren") if isinstance(game.raw, dict) else None
        if not isinstance(root_raw, dict) or not isinstance(root_raw.get("root"), str):
            raise JevCornerError("sorengameの[soren].rootがありません")
        root = Path(root_raw["root"]).resolve()
        capability = _read_json(root / "tmp/state/game_lifecycle/player_capabilities.json") or {}
        generation = capability.get("game_generation")
        if type(generation) is not int or generation < 1:
            # The adapter re-checks the live capability.  A syntactically valid
            # placeholder keeps construction side-effect free when the bridge
            # is absent; reconfigure then fails closed before request creation.
            generation = 1
        names = runtime_names(generation)
        spec = RuntimeSpec(
            game=GAME_NAME,
            adapter="soren",
            generation=generation,
            runtime_id=f"g{generation}-jev000000",
            lease_id=None,
            runtime_dir=Path(self.g.state_dir) / "runtimes" / f"g{generation}-jev000000",
            game_window=names.game_window,
            agent_window=names.agent_window,
            adapter_session=names.adapter_session,
        )
        try:
            return SorenCoordinatorAdapter(self.g, game, spec)
        except (AdapterError, ConfigError, ValueError, OSError) as exc:
            raise JevCornerError(f"Soren adapterを作成できません: {_safe_detail(exc)}") from exc

    @staticmethod
    def _default_state() -> dict[str, object]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "status": "idle",
            "game": GAME_NAME,
            "one_game": True,
            "policy": None,
            "request_id": None,
            "run_id": None,
            "player_generation": None,
            "game_generation": None,
            "config_hash": None,
            "started_at": None,
            "completed_at": None,
            "last_error": None,
        }

    def _read_state(self) -> dict[str, object]:
        state = _read_json(self.state_path)
        if state is None:
            if self.state_path.exists():
                raise JevCornerError("jev corner stateを読み込めません")
            return self._default_state()
        if state.get("schema_version") != STATE_SCHEMA_VERSION or state.get("game") != GAME_NAME:
            raise JevCornerError("jev corner state schemaが不正です")
        if state.get("status") not in ACTIVE_STATUSES | TERMINAL_STATUSES:
            raise JevCornerError("jev corner state statusが不正です")
        return state

    def _write_state(self, state: dict[str, object]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_path.parent, 0o700)
        atomic_write_json(self.state_path, state)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.lock_path.parent, 0o700)
        handle = self.lock_path.open("a+", encoding="utf-8")
        os.chmod(self.lock_path, 0o600)
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise JevCornerError("JEV corner処理が既に実行中です") from exc
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def _player_state(self) -> dict[str, object]:
        root = getattr(self.adapter, "root", None)
        path = Path(root) / "tmp/state/game_lifecycle/player_state.json" if root else None
        value = _read_json(path) if path else None
        if value is None:
            if path is not None and path.exists():
                raise JevCornerError("Soren player_state.jsonが不正です")
            return {
                "policy": "existing",
                "player_generation": 0,
                "game_generation": None,
            }
        if (
            value.get("schema") != 1
            or value.get("game") != GAME_NAME
            or value.get("policy") not in {"existing", "jev"}
            or type(value.get("player_generation")) is not int
            or value.get("player_generation") < 0
        ):
            raise JevCornerError("Soren player_state.jsonの契約が不正です")
        return value

    def _result(self, state: dict[str, object]) -> CornerResult:
        detail = state.get("last_error")
        return CornerResult(
            str(state.get("status") or "idle"),
            game=GAME_NAME,
            previous_game=None,
            detail=detail if isinstance(detail, str) else None,
        )

    def _request_change(self, state: dict[str, object], target_policy: str, deadline: float) -> dict[str, object]:
        current = self._player_state()
        current_policy = current.get("policy")
        if current_policy == target_policy:
            raise JevCornerError(f"player policyは既に{target_policy}です")
        expected_generation = current.get("player_generation")
        if type(expected_generation) is not int or expected_generation < 0:
            raise JevCornerError("active player_generationを確認できません")
        request_id = _new_uuid()
        run_id = _new_uuid()
        config_hash = _config_hash(self.config)
        state.update(
            status="preparing" if target_policy == "jev" else "restoring",
            policy=target_policy,
            request_id=request_id,
            run_id=run_id,
            player_generation=expected_generation,
            game_generation=current.get("game_generation"),
            config_hash=config_hash,
            last_error=None,
        )
        self._write_state(state)
        try:
            committed = self.adapter.reconfigure_player(
                request_id=request_id,
                target_policy=target_policy,
                run_id=run_id,
                expected_player_generation=expected_generation,
                config_hash=config_hash,
                deadline=deadline,
                cancel=None,
                game_generation=current.get("game_generation"),
            )
        except Exception as exc:
            # The adapter validates the committed snapshot before returning.
            # If it failed after a remote commit, do not report a clean retry;
            # leave an explicit recovery state for the operator.
            state.update(status="recovery_required", last_error=_safe_detail(exc))
            self._write_state(state)
            raise JevCornerError(_safe_detail(exc)) from exc
        if committed.get("policy") != target_policy:
            state.update(status="recovery_required", last_error="commit policy mismatch")
            self._write_state(state)
            raise JevCornerError("commit後のplayer policyを検証できません")
        state.update(
            status="active" if target_policy == "jev" else "completed",
            player_generation=committed.get("player_generation"),
            game_generation=committed.get("game_generation"),
            completed_at=None if target_policy == "jev" else _utc_now(),
            last_error=None,
        )
        if target_policy == "jev":
            state["started_at"] = _utc_now()
        self._write_state(state)
        return committed

    def start(self, *, timeout_s: float | None = None) -> CornerResult:
        timeout = float(timeout_s if timeout_s is not None else self.config.boundary_timeout_s)
        if timeout <= 0:
            raise JevCornerError("timeout_sは正の値である必要があります")
        with self._locked():
            state = self._read_state()
            if state.get("status") in ACTIVE_STATUSES:
                raise JevCornerError("JEV cornerは既にactiveまたは回復待ちです")
            state = self._default_state() if state.get("status") in TERMINAL_STATUSES else state
            state["one_game"] = True
            deadline = time.monotonic() + timeout
            self.adapter.preflight(deadline, None)
            self._request_change(state, "jev", deadline)
            return self._result(state)

    def finish(self, *, timeout_s: float | None = None) -> CornerResult:
        timeout = float(timeout_s if timeout_s is not None else self.config.boundary_timeout_s)
        if timeout <= 0:
            raise JevCornerError("timeout_sは正の値である必要があります")
        with self._locked():
            state = self._read_state()
            if state.get("status") not in {"active", "recovery_required"}:
                return CornerResult("noop", game=GAME_NAME, detail="not-active")
            deadline = time.monotonic() + timeout
            self.adapter.preflight(deadline, None)
            self._request_change(state, "existing", deadline)
            return self._result(state)

    def recover(self) -> CornerResult:
        with self._locked():
            state = self._read_state()
            current = self._player_state()
            if current.get("policy") == "existing":
                state.update(status="completed", completed_at=state.get("completed_at") or _utc_now(), last_error=None)
                self._write_state(state)
                return self._result(state)
            state.update(status="recovery_required", last_error="player policy is still jev; run finish")
            self._write_state(state)
            return self._result(state)

    def status(self) -> dict[str, object]:
        with self._locked():
            state = self._read_state()
            result = dict(state)
            result["config_enabled"] = self.config.enabled
            result["player_state"] = self._player_state()
            return result


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser():
    import argparse

    parser = argparse.ArgumentParser(prog="docich-jev-corner")
    parser.add_argument("--config", metavar="PATH")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--timeout-seconds", type=float)
    finish = sub.add_parser("finish")
    finish.add_argument("--timeout-seconds", type=float)
    sub.add_parser("recover")
    sub.add_parser("diagnose")
    sub.add_parser("recover-diagnose")
    sub.add_parser("refresh-bridge")
    sub.add_parser("recover-bridge")
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        g = load_global(_repo_root(), Path(args.config) if args.config else None)
        if args.command == "diagnose":
            category = diagnose(g)
            print(f"jev-corner: diagnose={category}")
            return DIAGNOSE_CODES[category]
        if args.command == "recover-diagnose":
            category = recover_bridge_diagnose(g)
            print(f"jev-corner: recover-diagnose={category}")
            return RECOVER_DIAGNOSE_CODES[category]
        if args.command == "refresh-bridge":
            refresh_bridge(g)
            print("jev-corner: bridge-refreshed")
            return 0
        if args.command == "recover-bridge":
            recover_bridge(g)
            print("jev-corner: bridge-recovered")
            return 0
        manager = JevCornerManager(g)
        if args.command == "status":
            value = manager.status()
            if args.json:
                print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            else:
                print(f"jev-corner: status={value.get('status')} policy={value.get('player_state', {}).get('policy')}")
            return 0
        if args.command == "recover":
            print(json.dumps(manager.recover().__dict__, ensure_ascii=False, separators=(",", ":")))
            return 0
        timeout = getattr(args, "timeout_seconds", None)
        result = manager.start(timeout_s=timeout) if args.command == "start" else manager.finish(timeout_s=timeout)
        print(json.dumps(result.__dict__, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ConfigError, JevCornerError) as exc:
        print(f"docich: エラー: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
