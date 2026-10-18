"""Bounded lifecycle owner for the read-only TRPG / murder-mystery view.

Scheduling and shared program-slot arbitration stay in corner_rotation and
corner_adapters. This module persists the corner's request and a fixed end
time. It never fetches during the display window, never talks to Twitch and
never writes viewer PII into persistent state. The deterministic scene engine
lives in ``trpg_corner``; this module only owns the lifecycle and the view
payload.

Catalog row stays default-disabled and manual-only (Issue #684, PR-3): the
owner can reserve it from the WebUI; enabling the 24h auto rotation is a
later, explicitly reviewed step. It always ends on the reviewed duration
bound or an explicit stop.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .trpg_view import PAYLOAD_FILENAME, VIEW_NAME, write_payload

STATE_FILENAME = "trpg_corner.json"
DEFAULT_DURATION_MINUTES = 8
MAX_DURATION_MINUTES = 30


class TrpgCornerError(RuntimeError):
    """A fixed, non-sensitive TRPG corner lifecycle failure."""


class TrpgCornerManager:
    """Run one TRPG display under the shared GameSwitch slot (minimal scaffold).

    The full GameSwitch boundary/rollback lifecycle is added in a follow-up;
    this scaffold persists request state and a view payload so the adapter can
    be registered and the corner reserved manually without production impact.
    """

    def __init__(self, g, *, duration_minutes=DEFAULT_DURATION_MINUTES,
                 clock=time.time, sleep=time.sleep, poll_s=1.0):
        if (type(duration_minutes) is not int
                or not 1 <= duration_minutes <= MAX_DURATION_MINUTES):
            raise TrpgCornerError(
                f"trpg duration must be an integer from 1 to {MAX_DURATION_MINUTES} minutes")
        self.g = g
        self.duration_minutes = duration_minutes
        self.clock, self.sleep, self.poll_s = clock, sleep, float(poll_s)
        self.path = Path(g.state_dir) / STATE_FILENAME
        self.state_path = self.path
        self.view_path = Path(g.state_dir) / PAYLOAD_FILENAME

    def eligible(self):
        return type(self.duration_minutes) is int and self.duration_minutes >= 1

    def _read_state(self):
        if not self.path.exists():
            return {"schema_version": 1, "status": "idle", "game": VIEW_NAME}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TrpgCornerError("trpg corner state is unreadable") from exc
        if not isinstance(state, dict):
            raise TrpgCornerError("trpg corner state is invalid")
        return state

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def observations(self):
        if not self.path.exists():
            return []
        state = self._read_state()
        if state.get("game", VIEW_NAME) != VIEW_NAME:
            raise TrpgCornerError("trpg corner state owner is invalid")
        return [state]

    def resources_released(self):
        return True

    def reconcile_failed_start(self, request_id, *, state_file=None):
        return False

    def run(self, request):
        request_id = request.get("request_id") if isinstance(request, dict) else None
        now = self.clock()
        state = {
            "schema_version": 1,
            "game": VIEW_NAME,
            "status": "active",
            "rotation_request_id": request_id,
            "started_at": now,
            "ends_at": now + self.duration_minutes * 60,
        }
        self._save(state)
        write_payload(self.view_path, {
            "schema": "trpg-view",
            "version": 1,
            "runtime_id": str(uuid.uuid4()),
            "generation": 1,
            "phase": "SCENE",
            "scene_text": "TRPGコーナーを準備しています",
            "choices": [],
            "votes": {},
            "status": "準備中",
            "updated_at": int(now),
        })
        # Minimal: mark completed immediately so the slot releases.
        state.update(status="completed", completed_at=self.clock())
        self._save(state)
        return "completed"

    def recover_failed_restore(self):
        return None
