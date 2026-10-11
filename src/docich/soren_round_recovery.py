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


def incomplete_result(evidence=None, *, common_workers_changed=None, reason="descendant_exit_unproved"):
    """Fixed, public-safe schema; unobserved facts are null, never success."""
    evidence = evidence if isinstance(evidence, dict) else {}
    if type(reason) is not str or reason not in {"descendant_exit_unproved", "recovery_refused", "unexpected_failure"}:
        reason = "descendant_exit_unproved"
    result = dict(status="incomplete", reason=reason, descendant_exit="unproved",
                  containment="pidfd_snapshot")
    for name in ("captured_old_targets_gone", "fresh_game_progress_observed", "supervisor_unchanged"):
        value = evidence.get(name)
        result[name] = value if type(value) is bool else None
    candidates = evidence.get("unattributed_profile_candidates")
    result["unattributed_profile_candidates"] = candidates if type(candidates) is int and candidates >= 0 else None
    result["common_workers_changed"] = (common_workers_changed
        if type(common_workers_changed) is int and common_workers_changed >= 0 else None)
    return result


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
        evidence = self.effects.verify_new(recovery)
        try:
            changed = len(self.effects.common_changed(recovery))
        except (RecoveryRefused, OSError, ValueError):
            changed = None
        # The current snapshot method has no whole-descendant exit proof,
        # even when captured targets are gone and the fresh board progresses.
        return incomplete_result(evidence, common_workers_changed=changed)
