"""Explicit one-off OBS corner with durable switch/restore ownership.

The foreground start/recover runner holds the normal program slot. It can be
run in a bounded user unit; stop requests are identity-bound and do not kill
game, stream, audio, or sender processes. Nothing is added to automatic rotation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import time
import uuid

from .config import load_global
from .corner_boundary import other_corner_busy, program_slot
from .external_video_receiver import (
    ExternalVideoError, exclusive, prepare, read_json, read_receiver,
    receive, request_receiver_stop,
)
from .game_switch import GameSwitchCoordinator, GameSwitchStore, atomic_write_json

VIEW_NAME = "external-video-view"
STATE_FILE = "external_video_corner.json"
PENDING = {"queued", "in_progress", "busy"}


def identity(runtime):
    if runtime is None:
        return None
    if (not isinstance(runtime, dict) or type(runtime.get("generation")) is not int
            or runtime["generation"] < 1
            or any(not isinstance(runtime.get(k), str) or not runtime[k]
                   for k in ("game", "runtime_id", "lease_id"))):
        raise ExternalVideoError("runtime ownership is incomplete")
    return {k: runtime[k] for k in ("game", "generation", "runtime_id", "lease_id")}


def stable(state):
    return (state.get("phase") in {"idle", "ready"}
            and not any(state.get(k) for k in ("candidate", "previous", "retiring")))


class ExternalVideoCornerManager:
    def __init__(self, g, *, coordinator=None, clock=time.time, sleep=time.sleep):
        self.g, self.clock, self.sleep = g, clock, sleep
        self.path = Path(g.state_dir) / STATE_FILE
        self.store = GameSwitchStore(g.state_dir)
        self.stopping = False
        if coordinator is None:
            from .adapters import make_coordinator_adapter
            coordinator = GameSwitchCoordinator(self.store, lambda spec: make_coordinator_adapter(g, spec))
        self.coordinator = coordinator

    def canonical(self):
        with self.store.lock(exclusive=False):
            return self.store.canonical.load()[0]

    def save(self, state):
        atomic_write_json(self.path, state)

    def stop_requested(self, state):
        flag = Path(self.g.state_dir) / "external_video_stop.json"
        return self.stopping or (flag.exists()
            and read_json(flag).get("start_request_id") == state.get("start_request_id"))

    def _dispatch(self, state):
        source = state["previous_runtime_identity"]
        method = self.coordinator.switch if source else self.coordinator.start
        kwargs = dict(request_id=state["start_request_id"], timeout_s=600,
                      allow_boundary_timeout_extension=False)
        if source:
            kwargs["payload"] = {"expected_source": source}
        result = method(VIEW_NAME, **kwargs)
        if result.status in PENDING:
            return False
        if result.status != "succeeded":
            current = self.canonical()
            receipt = result.receipt
            body = receipt.get("result", {}) if isinstance(receipt, dict) else {}
            actual = identity(current.get("active"))
            rolled_back = (result.status == "rolled_back" and isinstance(receipt, dict)
                           and receipt.get("request_id") == state["start_request_id"]
                           and body.get("request_id") == state["start_request_id"]
                           and identity(body.get("active_runtime")) == actual
                           and (actual["game"] if actual else None) == (source["game"] if source else None))
            if stable(current) and (actual == source or rolled_back):
                state.update(status="interrupted", completed_at=self.clock(),
                             end_reason="start-rolled-back")
                self.save(state)
                return True
            state.update(status="failed", recovery_required=True, end_reason="start-failed")
            self.save(state)
            raise ExternalVideoError("external corner start requires recovery")
        receipt = result.receipt
        body = receipt.get("result", {}) if isinstance(receipt, dict) else {}
        owned = identity(body.get("active_runtime"))
        if (not isinstance(receipt, dict) or receipt.get("request_id") != state["start_request_id"]
                or receipt.get("status") != "succeeded" or receipt.get("target") != VIEW_NAME
                or body.get("request_id") != state["start_request_id"]
                or body.get("status") != "succeeded" or not owned or owned["game"] != VIEW_NAME
                or receipt.get("generation") != owned["generation"]
                or result.request_id != state["start_request_id"]):
            raise ExternalVideoError("external corner start receipt is invalid")
        current = self.canonical()
        if not stable(current) or identity(current.get("active")) != owned:
            raise ExternalVideoError("external corner commit is not confirmed")
        state.update(status="active", runtime_identity=owned, started_at=self.clock(),
                     ends_at=min(self.clock() + state["duration_minutes"] * 60,
                                 state["receiver_expires_at"]))
        self.save(state)
        return True

    def restore(self, state, reason):
        owned = identity(state.get("runtime_identity"))
        if not owned or owned["game"] != VIEW_NAME:
            raise ExternalVideoError("external corner restore owner is missing")
        current = self.canonical()
        source = state["previous_runtime_identity"]
        # Replay a durable restore request after a lost response; never create
        # a second restore or move away from a new operator's runtime.
        request_id = state.get("restore_request_id")
        if request_id is None:
            if not stable(current) or identity(current.get("active")) != owned:
                raise ExternalVideoError("external corner runtime ownership changed")
            request_id = str(uuid.uuid4())
            state.update(status="restoring", restore_request_id=request_id, end_reason=reason)
            self.save(state)
        kwargs = dict(request_id=request_id, timeout_s=120, payload={"expected_source": owned})
        result = (self.coordinator.switch(source["game"], **kwargs) if source
                  else self.coordinator.stop(**kwargs))
        if result.status in PENDING:
            return False
        receipt = result.receipt
        body = receipt.get("result", {}) if isinstance(receipt, dict) else {}
        current = self.canonical()
        actual = identity(current.get("active"))
        target = source["game"] if source else None
        if (result.status != "succeeded" or result.cleanup_pending
                or not isinstance(receipt, dict) or receipt.get("request_id") != request_id
                or receipt.get("status") != "succeeded" or body.get("request_id") != request_id
                or body.get("status") != "succeeded" or not stable(current)
                or (actual["game"] if actual else None) != target
                or identity(body.get("active_runtime")) != actual):
            state.update(status="failed", recovery_required=True, end_reason="restore-failed")
            self.save(state)
            raise ExternalVideoError("external corner restoration is unconfirmed")
        request_receiver_stop(self.g, state["receiver_id"])
        state.update(status="completed", recovery_required=False, completed_at=self.clock(),
                     restored_runtime_identity=actual)
        self.save(state)
        return True

    def run(self, duration_minutes=15, *, recovering=False, require_audio=True):
        if type(duration_minutes) is not int or not 1 <= duration_minutes <= 30:
            raise ExternalVideoError("corner duration must be 1-30 minutes")
        with exclusive(Path(self.g.state_dir) / "external-video-corner.lock"):
            current = self.canonical()
            if recovering:
                state = read_json(self.path)
                if state.get("status") not in {"starting", "active", "restoring", "failed"}:
                    raise ExternalVideoError("no external corner requires recovery")
                if not stable(current):
                    last = current.get("last_result") or {}
                    if last.get("request_id") not in {state.get("start_request_id"), state.get("restore_request_id")}:
                        raise ExternalVideoError("another corner owns canonical recovery")
                    recovered = self.coordinator.recover(timeout_s=120)
                    if recovered.status not in {"succeeded", "rolled_back"}:
                        raise ExternalVideoError("external corner recovery did not settle")
                    current = self.canonical()
                restore_id = state.get("restore_request_id")
                if restore_id and state.get("runtime_identity"):
                    receipt = self.store.receipts.load(restore_id)
                    body = receipt.get("result", {}) if isinstance(receipt, dict) else {}
                    actual = identity(current.get("active"))
                    owned = identity(state["runtime_identity"])
                    if (receipt and receipt.get("status") in {"failed", "rolled_back"}
                            and stable(current) and actual
                            and all(actual[k] == owned[k] for k in ("game", "runtime_id", "generation"))
                            and body.get("request_id") == restore_id
                            and identity(body.get("active_runtime")) == actual):
                        attempts = state.setdefault("restore_attempts", [])
                        if len(attempts) >= 3:
                            raise ExternalVideoError("external corner restore retry limit reached")
                        attempts.append(restore_id)
                        state.pop("restore_request_id")
                        state["runtime_identity"] = actual
                state["status"] = "restoring" if state.get("runtime_identity") else "starting"
                self.save(state)
            else:
                if (Path(self.g.state_dir) / "corners" / "external-video.paused").exists():
                    raise ExternalVideoError("external video corner is paused")
                if self.path.exists() and read_json(self.path).get("status") in {"starting", "active", "restoring", "failed"}:
                    raise ExternalVideoError("existing external corner must recover or finish")
                if not stable(current):
                    raise ExternalVideoError("game-switch requires recovery before external corner")
                source = identity(current.get("active"))
                if source and source["game"] == VIEW_NAME:
                    raise ExternalVideoError("external view has no matching corner owner")
                if other_corner_busy(self.g, self.path):
                    raise ExternalVideoError("another program owner must finish or recover")
                receiver = read_receiver(self.g, fresh=True)
                if receiver["expires_at"] - self.clock() < 30:
                    raise ExternalVideoError("receiver expiry is too near for a safe start")
                if require_audio and not receiver.get("audio_present"):
                    raise ExternalVideoError("OBS audio has not been decoded")
                now = self.clock()
                state = dict(schema_version=1, game=VIEW_NAME, status="starting",
                             start_request_id=str(uuid.uuid4()), receiver_id=receiver["receiver_id"],
                             previous_runtime_identity=source, requested_at=now,
                             duration_minutes=duration_minutes, receiver_expires_at=receiver["expires_at"])
                self.save(state)
            try:
                with program_slot(self.g, self.path, requested_at=state["requested_at"],
                                  wait_deadline_ts=min(self.clock() + 600, state["receiver_expires_at"]),
                                  wait_boundary=True):
                    if state["status"] == "starting":
                        receipt = self.store.receipts.load(state["start_request_id"])
                        if self.stop_requested(state) and receipt is None:
                            state.update(status="interrupted", completed_at=self.clock(), end_reason="stopped-before-start")
                            self.save(state)
                            return state
                        while not self._dispatch(state):
                            self.sleep(1)
                    if state["status"] == "interrupted":
                        return state
                    reason = "manual" if recovering else "duration"
                    while state["status"] == "active" and not recovering:
                        if self.stop_requested(state):
                            reason = "manual"
                            break
                        if self.clock() >= state["ends_at"]:
                            break
                        try:
                            receiver = read_receiver(self.g, expected=state["receiver_id"])
                        except ExternalVideoError:
                            reason = "receiver-unavailable"
                            break
                        if not receiver["fresh"]:
                            reason = "receiver-disconnected"
                            break
                        if identity(self.canonical().get("active")) != state["runtime_identity"]:
                            raise ExternalVideoError("external corner ownership changed during display")
                        self.sleep(1)
                    while not self.restore(state, reason):
                        self.sleep(1)
                    return state
            except Exception:
                # On a local preview/state failure, restore a still-proven
                # active view before retaining a failed owner. Never do this
                # when another runtime has acquired the display.
                if state.get("status") == "active":
                    current = self.canonical()
                    if stable(current) and identity(current.get("active")) == state.get("runtime_identity"):
                        try:
                            if self.restore(state, "operation-error"):
                                return state
                        except Exception:
                            pass
                if state.get("status") not in {"completed", "interrupted"}:
                    state.update(status="failed", recovery_required=True)
                    self.save(state)
                raise


def main(argv=None):
    parser = argparse.ArgumentParser(prog="external-video-corner")
    parser.add_argument("--config", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--listen-ip", required=True)
    p.add_argument("--wait-minutes", type=int, default=30)
    p = sub.add_parser("receive")
    p.add_argument("--receiver-id", required=True)
    p = sub.add_parser("start")
    p.add_argument("--duration-minutes", type=int, default=15)
    p.add_argument("--video-only", action="store_true", help="explicitly permit an OBS source without audio")
    for command in ("status", "stop", "recover", "receiver-stop"):
        sub.add_parser(command)
    args = parser.parse_args(argv)
    try:
        g = load_global(Path(__file__).resolve().parents[2], Path(args.config))
        manager = ExternalVideoCornerManager(g)
        if args.command == "prepare":
            output = prepare(g, args.listen_ip, args.wait_minutes)
        elif args.command == "receive":
            return receive(g, args.receiver_id)
        elif args.command == "status":
            output = {"receiver": read_receiver(g),
                      "corner": read_json(manager.path) if manager.path.exists() else {"status": "idle"}}
        elif args.command == "stop":
            state = read_json(manager.path)
            if state.get("status") not in {"starting", "active", "restoring"}:
                raise ExternalVideoError("external corner is not running; use recover for failed ownership")
            atomic_write_json(Path(g.state_dir) / "external_video_stop.json",
                              {"start_request_id": state["start_request_id"]})
            output = {"status": "stop-requested"}
        elif args.command == "receiver-stop":
            if manager.path.exists() and read_json(manager.path).get("status") in {"starting", "active", "restoring", "failed"}:
                raise ExternalVideoError("restore the external corner before stopping its receiver")
            state = read_receiver(g)
            request_receiver_stop(g, state["receiver_id"])
            output = {"status": "receiver-stop-requested"}
        else:
            signal.signal(signal.SIGTERM, lambda *_: setattr(manager, "stopping", True))
            signal.signal(signal.SIGINT, lambda *_: setattr(manager, "stopping", True))
            output = manager.run(args.duration_minutes if args.command == "start" else 15,
                                 recovering=args.command == "recover",
                                 require_audio=not getattr(args, "video_only", False))
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except ExternalVideoError as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
