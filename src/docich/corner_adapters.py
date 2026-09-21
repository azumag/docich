"""Corner execution adapters. No scheduling or random selection lives here."""
from __future__ import annotations

from dataclasses import replace
import fcntl
import json
from pathlib import Path

from .retro_corner import RetroCornerManager, load_retro_corner_config


class CornerExecutionError(RuntimeError):
    pass


class GameCornerAdapter:
    def __init__(self, g, corner):
        self.g, self.corner = g, corner
        cfg = replace(load_retro_corner_config(g), games=[corner.game],
                      daily_each_game=False, randomize_start=False)
        self.manager = RetroCornerManager(g, config=cfg)

    @property
    def state_path(self):
        return self.manager.state_path

    def eligible(self):
        return self.corner.game in self.manager._playable_games()

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

    def improvement_paths(self):
        root = Path(self.g.state_dir)
        return (root / "locks" / f"corner-improve-{self.corner.game}.lock",
                root / f"corner_improve_{self.corner.game}.json")

    def resources_released(self):
        """Wait for bounded post-corner jobs; unknown child ownership never passes.

        No arbitrary PID kill is safe here. A held job lock or missing terminal
        evidence blocks the next corner until completion/operator recovery.
        """
        from .corner_rotation import timestamp
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
            if not status_path.exists():
                return False
            status = json.loads(status_path.read_text())
            if status.get("status") not in {"promoted", "kept", "improved", "dry-run", "skipped"}:
                return False
            if timestamp(status.get("started_at")) < timestamp(state.get("completed_at")):
                return False
        return True


class MerikenCornerAdapter(GameCornerAdapter):
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

    def run(self, request):
        return self.manager.run_rotation(request["request_id"])


class NethackCornerAdapter(MerikenCornerAdapter):
    def __init__(self, g, corner):
        from .nethack_corner import NethackCornerManager, GAME_NAME
        if corner.game != GAME_NAME:
            raise CornerExecutionError("NetHack adapter game mismatch")
        self.g, self.corner = g, corner
        self.manager = NethackCornerManager(g)


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

    def run(self, request):
        return self.manager.run_rotation(request["request_id"])

    def improvement_paths(self):
        root = Path(self.g.state_dir)
        return root / "locks/paper-improve.lock", root / "trading/paper_improve_status.json"


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
        try:
            with adapter.manager.store.lock(exclusive=False):
                canonical, _ = adapter.manager.store.canonical.load()
                # Queueing is allowed while draining; unsafe rollback/failure is not.
                if canonical.get("phase") not in {"idle", "ready", "draining"}:
                    raise CornerExecutionError("game-switch requires recovery")
        except GameSwitchBusyError:
            return "queued"
        try:
            with program_slot(
                self.g, adapter.state_path,
                requested_at=request["selected_at"],
                wait_deadline_ts=self.clock() + self.wait_seconds,
                wait_boundary=True, sleep=self.sleep, now=self.clock,
            ):
                return adapter.run(request)
        except CornerWaitExpired:
            return "queued"
