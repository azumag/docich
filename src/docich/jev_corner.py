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
ACTIVE_STATUSES = frozenset({"preparing", "active", "restoring", "recovery_required"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "idle"})
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class JevCornerError(RuntimeError):
    """User-facing failure in the manual JEV corner."""


@dataclass(frozen=True)
class JevCornerConfig:
    """Static contract values for one explicitly started corner."""

    enabled: bool = False
    one_game: bool = True
    max_requests_per_run: int = 500
    decision_budget_ms: int = 1500
    http_timeout_ms: int = 1000
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
        "max_requests_per_run": raw.get("max_requests_per_run", 500),
        "decision_budget_ms": raw.get("decision_budget_ms", 1500),
        "http_timeout_ms": raw.get("http_timeout_ms", 1000),
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
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        g = load_global(_repo_root(), Path(args.config) if args.config else None)
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
