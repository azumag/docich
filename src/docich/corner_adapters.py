"""Corner execution adapters. No scheduling or random selection lives here."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path

from .corner_terminal import failed_improvement_is_terminal, normalize_terminal_paper_failure
from .retro_corner import RetroCornerManager, load_retro_corner_config


class CornerExecutionError(RuntimeError):
    pass


def _queue_dispatch(g) -> bool:
    """Whether the common rotation dispatches continuously (no improve wait)."""
    from .corner_catalog import schedule_mode

    return schedule_mode(g) == "queue"


TERMINAL_IMPROVEMENT_STATUSES = frozenset({
    "promoted", "kept", "improved", "dry-run", "skipped", "ab-staged",
})


def _improvement_released(status_path, state):
    """Whether one improve_job's durable record releases the next corner.

    The caller already observed the single-flight lock free.  Successful
    statuses keep the strict ledger timestamp contract; a ``failed`` record is
    accepted only as this run's own bounded terminal evidence.
    """
    if not status_path.exists():
        return False
    status = json.loads(status_path.read_text())
    if status.get("status") == "failed":
        return failed_improvement_is_terminal(status, state)
    if status.get("status") not in TERMINAL_IMPROVEMENT_STATUSES:
        return False
    from .corner_rotation import timestamp
    return timestamp(status.get("started_at")) >= timestamp(state.get("completed_at"))


class GameCornerAdapter:
    def __init__(self, g, corner):
        self.g, self.corner = g, corner
        cfg = replace(load_retro_corner_config(g), games=[corner.game],
                      daily_each_game=False, randomize_start=False)
        if corner.target_matches is not None:
            cfg = replace(cfg, target_matches=corner.target_matches)
        self.target_matches = cfg.target_matches
        self._target_matches_env = {
            "ninvaders": "NINVADERS_MAX_MATCHES",
            "nsnake": "NSNAKE_MAX_MATCHES",
            "bastet": "BASTET_MAX_MATCHES",
            "moon-buggy": "MOONBUGGY_MAX_MATCHES",
            "pacman4console": "PACMAN_MAX_MATCHES",
        }.get(corner.game)
        self.manager = RetroCornerManager(g, config=cfg)

    @property
    def state_path(self):
        return self.manager.state_path

    def eligible(self):
        return self.corner.game in self.manager._playable_games()

    def _runtime_target_matches(self, request=None):
        target = self.target_matches
        request_id = request.get("request_id") if isinstance(request, dict) else None
        if not isinstance(request_id, str):
            return target
        try:
            state = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return target
        persisted = state.get("target_matches") if isinstance(state, dict) else None
        if (isinstance(state, dict)
                and state.get("rotation_request_id") == request_id
                and type(persisted) is int and 1 <= persisted <= 100):
            return persisted
        return target

    @contextmanager
    def runtime_environment(self, request=None):
        """Expose the selected match limit only to the game subprocess.

        The manager's scorelog boundary is authoritative, but the tracked
        wrappers also auto-retry their foreground game.  Keeping the wrapper
        cap equal to the per-corner target prevents a second match from being
        started during the poll interval before the manager restores Soren.
        """
        ab_remaining = None
        ab_active = False
        if getattr(getattr(self, "corner", None), "game", None) == "moon-buggy":
            from .moon_buggy_ab import MoonBuggyABError, pending_matches

            request_id = request.get("request_id") if isinstance(request, dict) else None
            if isinstance(request_id, str):
                try:
                    ab_remaining = pending_matches(self.g.state_dir)
                except MoonBuggyABError as exc:
                    raise CornerExecutionError("Moon Buggy A/B state is invalid") from exc
                ab_active = ab_remaining is not None

        if ab_active:
            target_matches = ab_remaining
        elif hasattr(self, "target_matches"):
            target_matches = self._runtime_target_matches(request)
        else:
            target_matches = None
        if target_matches is None:
            yield
            return

        env_names = ["DOCICH_TARGET_MATCHES"]
        target_env = getattr(self, "_target_matches_env", None)
        if target_env:
            env_names.append(target_env)
        if ab_active:
            env_names.extend((
                "DOCICH_MOON_BUGGY_AB_STATE", "DOCICH_MOON_BUGGY_AB_ACTIVE",
                "DOCICH_MOON_BUGGY_AB_REQUEST",
            ))
        previous = {name: os.environ.get(name) for name in env_names}
        for name in env_names:
            if name == "DOCICH_TARGET_MATCHES" or name == target_env:
                os.environ[name] = str(target_matches)
        manager_config = self.manager.config if ab_active else None
        if ab_active:
            from .moon_buggy_ab import active_path, state_path

            os.environ["DOCICH_MOON_BUGGY_AB_STATE"] = str(state_path(self.g.state_dir))
            os.environ["DOCICH_MOON_BUGGY_AB_ACTIVE"] = str(active_path(self.g.state_dir))
            os.environ["DOCICH_MOON_BUGGY_AB_REQUEST"] = request_id
            self.manager.config = replace(manager_config, target_matches=target_matches)
        try:
            yield
        finally:
            if ab_active:
                self.manager.config = manager_config
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def observations(self):
        paths = [self.state_path, self.state_path.with_name(self.state_path.stem + "_manual.json")]
        for path in paths:
            if path.exists():
                state = json.loads(path.read_text())
                if not isinstance(state, dict):
                    raise CornerExecutionError("invalid adapter state")
                # Retro shares one execution state file across game adapters.
                if state.get("game", self.corner.game) == self.corner.game:
                    yield state

    def run(self, request):
        return self.manager.run_rotation(request["request_id"], self.corner.game)

    def reconcile_failed_start(self, request_id):
        return self.manager.reconcile_failed_rotation_start(request_id)

    def improvement_paths(self):
        root = Path(self.g.state_dir)
        return (root / "locks" / f"corner-improve-{self.corner.game}.lock",
                root / f"corner_improve_{self.corner.game}.json")

    def resources_released(self):
        """Wait for bounded post-corner jobs; unknown child ownership never passes.

        No arbitrary PID kill is safe here. A held job lock or a record that
        cannot prove this run ended blocks the next corner until completion or
        operator recovery.  Queue dispatch deliberately drops that wait: the
        improvement job is bounded and serialized by the improve lane, and the
        corner queue must keep firing (see docs/corner-rotation.md).
        """
        if _queue_dispatch(self.g):
            return True
        lock_path, status_path = self.improvement_paths()
        if lock_path.exists():
            with lock_path.open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return False
        for state in self.observations():
            job = state.get("improve_job") or {}
            if job.get("spawned") is not True:
                continue
            if not _improvement_released(status_path, state):
                return False
        return True


class MerikenCornerAdapter(GameCornerAdapter):
    DEFAULT_ENV_FILE = Path("/home/ubuntu/soren/soren91-macos-agent.env")
    ENV_KEYS = (
        "SOREN91_MACOS_AGENT_BASE_URL",
        "SOREN91_LOCAL_AGENT_TOKEN",
        "SOREN91_OCI_TAILSCALE_IP",
    )

    def __init__(self, g, corner):
        from .soren91_corner import Soren91CornerManager, GAME_NAME
        if corner.game != GAME_NAME:
            raise CornerExecutionError("Meriken adapter game mismatch")
        self.g, self.corner = g, corner
        self.manager = Soren91CornerManager(g)

    def eligible(self):
        if not self.manager.config.enabled:
            return False
        self.manager._validate_games()
        return True

    @classmethod
    def _read_env_file(cls, path):
        values = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise CornerExecutionError("Soren91接続情報ファイルを読めません") from exc
        for line_number, line in enumerate(lines, 1):
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            if raw.startswith("export "):
                raw = raw[7:].lstrip()
            key, separator, value = raw.partition("=")
            if not separator or key not in cls.ENV_KEYS:
                continue
            value = value.strip()
            if value[:1] in {"'", '"'}:
                if len(value) < 2 or value[-1] != value[0]:
                    raise CornerExecutionError(
                        f"Soren91接続情報ファイルの引用符が不正です (line {line_number})"
                    )
                value = value[1:-1]
            if not value:
                raise CornerExecutionError(
                    f"Soren91接続情報ファイルの値が空です (line {line_number})"
                )
            values[key] = value
        return values

    @contextmanager
    def runtime_environment(self, request=None):
        """Load Meriken credentials only for this adapter's execution."""
        path = Path(os.environ.get("DOCICH_SOREN91_ENV_FILE", str(self.DEFAULT_ENV_FILE)))
        if not path.is_file():
            yield
            return
        values = self._read_env_file(path)
        missing = object()
        previous = {key: os.environ.get(key, missing) for key in values}
        os.environ.update(values)
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is missing:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def run(self, request):
        return self.manager.run_rotation(request["request_id"])


class NethackCornerAdapter(GameCornerAdapter):
    def __init__(self, g, corner):
        from .nethack_corner import NethackCornerManager, GAME_NAME
        from .nethack_corner import load_nethack_corner_config
        from .retro_corner import load_retro_corner_config
        if corner.game != GAME_NAME:
            raise CornerExecutionError("NetHack adapter game mismatch")
        self.g, self.corner = g, corner
        fixed = load_nethack_corner_config(g)
        rolling = load_retro_corner_config(g)
        # The catalog owns whether this entry is enabled. Keep NetHack's
        # save-boundary settings, but not its legacy fixed-hour schedule.
        self.manager = NethackCornerManager(
            g,
            config=replace(
                fixed,
                enabled=True,
                duration_minutes=rolling.duration_minutes,
                timezone=rolling.timezone,
            ),
        )

    def eligible(self):
        from .config import load_game

        self.manager._validate_games()
        game = load_game(self.g, self.corner.game)
        required = self.manager._required_executables(game)
        return all(self.manager._executable_exists(path) for path in required)


class PaperCornerAdapter(GameCornerAdapter):
    def __init__(self, g, corner):
        from .paper_corner_fast import FastPaperCornerManager
        from .paper_corner import PAPER_VIEW_NAME
        if corner.game != PAPER_VIEW_NAME or corner.live_eligible is not False:
            raise CornerExecutionError("PAPER identity/safety contract mismatch")
        self.g, self.corner = g, corner
        self.manager = FastPaperCornerManager(g)

    @property
    def state_path(self):
        return self.manager.path

    def eligible(self):
        if not self.manager.enabled:
            return False
        self.manager._require_outputs()
        return True

    def observations(self):
        # PAPER owns dedicated files, unlike retro's shared per-game file.
        # Legacy manual runs may record game=null (or omit it altogether).
        for path in (self.state_path, self.state_path.with_name(self.state_path.stem + "_manual.json")):
            if not path.exists():
                continue
            state = json.loads(path.read_text())
            if not isinstance(state, dict):
                raise CornerExecutionError("invalid adapter state")
            yield normalize_terminal_paper_failure(self.g.state_dir, state, self.corner.game)

    def run(self, request):
        return self.manager.run_rotation(request["request_id"])

    def improvement_paths(self):
        root = Path(self.g.state_dir)
        return root / "locks/paper-improve.lock", root / "trading/paper_improve_status.json"


class RetiredCornerObserver:
    """Observe a removed catalog entry until its old work is terminal."""

    STATE_FILES = {
        "game": "retro_corner.json",
        "paper": "paper_corner.json",
        "meriken": "soren91_corner.json",
        "nethack": "nethack_corner.json",
    }

    def __init__(self, g, record):
        self.g = g
        self.corner_id = record["id"]
        self.game = record["game"]
        self.adapter_name = record["adapter"]
        self.state_path = Path(g.state_dir) / self.STATE_FILES[self.adapter_name]

    def observations(self):
        paths = [self.state_path, self.state_path.with_name(self.state_path.stem + "_manual.json")]
        for path in paths:
            if not path.exists():
                continue
            state = json.loads(path.read_text())
            if not isinstance(state, dict):
                raise CornerExecutionError("invalid retired corner state")
            if self.adapter_name == "paper" or state.get("game", self.game) == self.game:
                if self.adapter_name == "paper":
                    state = normalize_terminal_paper_failure(self.g.state_dir, state, self.game)
                yield state

    def improvement_paths(self):
        root = Path(self.g.state_dir)
        if self.adapter_name == "paper":
            return root / "locks/paper-improve.lock", root / "trading/paper_improve_status.json"
        return root / "locks" / f"corner-improve-{self.game}.lock", root / f"corner_improve_{self.game}.json"

    def resources_released(self):
        if _queue_dispatch(self.g):
            return True
        lock_path, status_path = self.improvement_paths()
        if lock_path.exists():
            with lock_path.open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return False
        for state in self.observations():
            job = state.get("improve_job") or {}
            if job.get("spawned") is not True:
                continue
            if not _improvement_released(status_path, state):
                return False
        return True


ADAPTERS = {
    "game": GameCornerAdapter,
    "meriken": MerikenCornerAdapter,
    "paper": PaperCornerAdapter,
    "nethack": NethackCornerAdapter,
}


def make_corner_adapter(g, corner):
    return ADAPTERS[corner.adapter](g, corner)


class CornerExecutionCoordinator:
    """One program-slot owner around adapter execution and game-switch receipts.

    GameSwitchCoordinator remains responsible for boundary, generation ownership,
    process/child stop verification, rollback, and the shared infrastructure.
    """
    def __init__(self, g, *, clock, sleep, wait_seconds=600):
        self.g, self.clock, self.sleep = g, clock, sleep
        self.wait_seconds = wait_seconds

    def execute(self, adapter, request):
        from .corner_boundary import program_slot, CornerWaitExpired
        from .game_switch import GameSwitchBusyError
        from contextlib import nullcontext
        try:
            with adapter.manager.store.lock(exclusive=False):
                canonical, _ = adapter.manager.store.canonical.load()
                # Queueing is allowed while draining; unsafe rollback/failure is not.
                if canonical.get("phase") not in {"idle", "ready", "draining"}:
                    raise CornerExecutionError("game-switch requires recovery")
        except GameSwitchBusyError:
            return "queued"
        try:
            environment = getattr(adapter, "runtime_environment", None)
            scope = environment(request) if callable(environment) else nullcontext()
            with scope:
                with program_slot(
                    self.g, adapter.state_path,
                    requested_at=request["selected_at"],
                    wait_deadline_ts=self.clock() + self.wait_seconds,
                    wait_boundary=True, sleep=self.sleep, now=self.clock,
                ):
                    return adapter.run(request)
        except CornerWaitExpired:
            return "queued"
