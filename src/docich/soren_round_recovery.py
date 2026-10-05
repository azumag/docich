"""Explicit game-only recovery: save evidence, kill the old tree, observe respawn.

No coordinator/rotation/lifecycle records are changed. The coordinator lock is
held through termination only; progress polling does not block the drain driver.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import time
import uuid

from .game_switch import GameSwitchStore


class RecoveryRefused(RuntimeError):
    pass


def read_object(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise RecoveryRefused("required evidence unavailable") from exc
    if not isinstance(value, dict):
        raise RecoveryRefused("required evidence is not an object")
    return value


class OwnedRoundRecovery:
    def __init__(self, state_dir, root, effects, *, clock=time.time):
        self.state_dir, self.root = Path(state_dir), Path(root)
        self.effects, self.clock = effects, clock
        self.store = GameSwitchStore(self.state_dir)

    def run(self):
        # A draining request may continue using the existing broker. Never take
        # broker.lock or edit its request/ack/control files. The short-lived
        # coordinator lock prevents a switch from retiring this active while
        # its game tree is replaced, and releases on every exit path.
        with self.store.lock(exclusive=True):
            canonical, missing = self.store.canonical.load()
            active = canonical.get("active")
            if (missing or not isinstance(active, dict)
                    or active.get("game") != "sorengame" or active.get("adapter") != "soren"
                    or canonical.get("phase") not in {"ready", "draining"}
                    or canonical.get("candidate") is not None or canonical.get("previous") is not None
                    or canonical.get("retiring") or canonical.get("operation") == "stop"):
                raise RecoveryRefused("active Soren is not ready or draining")
            started = self.clock()
            recovery = dict(
                recovery_id=dt.datetime.fromtimestamp(started, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                            + "-" + uuid.uuid4().hex[:8],
                active=active, started_epoch=started, inventory=self.effects.preflight())
            self.effects.archive(recovery)
            self.effects.stop(recovery)
        # The existing draining driver has a short writer reacquisition budget.
        # Let it process its boundary ACK while we observe natural respawn.
        self.effects.verify_new(recovery)
        return {"status": "completed", "result": "interrupted",
                "common_workers_changed": len(self.effects.common_changed(recovery))}
