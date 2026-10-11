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
from .corner_boundary import CornerWaitExpired, other_corner_busy, program_slot
from .external_video_receiver import (
    ExternalVideoError, MAX_RECEIVER_MINUTES, exclusive, prepare, read_json, read_receiver,
    receive, request_receiver_stop,
)
from .game_switch import (
    GameSwitchBusyError, GameSwitchCoordinator, GameSwitchStore, atomic_write_json,
)

VIEW_NAME = "external-video-view"
STATE_FILE = "external_video_corner.json"
STOP_FILE = "external_video_stop.json"
PENDING = {"queued", "in_progress", "busy"}
# The operator's End button is the normal finish; this is only the technical
# ceiling a systemd unit lifetime needs (24 h), not a play-time limit.
MAX_DURATION_MINUTES = 24 * 60
# A single stale frame (SRT hiccup, keyframe gap) must not end a long session;
# a real disconnect stays stale for far longer than this.
DISCONNECT_GRACE_S = 30
# The operator pressed "end" (WebUI/CLI): the game was cleared, speak about it.
OPERATOR_END = "operator-end"


def request_end(g, state, reason="manual"):
    """Identity-bound end request shared by the CLI and the WebUI."""
    if reason not in {"manual", OPERATOR_END}:
        raise ExternalVideoError("invalid end reason")
    if state.get("status") not in {"waiting", "starting", "active", "restoring"}:
        raise ExternalVideoError("external corner is not running; use recover for failed ownership")
    atomic_write_json(Path(g.state_dir) / STOP_FILE,
                      {"start_request_id": state["start_request_id"], "reason": reason})


class WaitingStopped(ExternalVideoError):
    pass


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
            from .stream_category import commit_hook
            # Same post-commit hook as PAPER/CLI: start switches the Twitch
            # category/title to FlyHome, and the restore puts the previous game back.
            coordinator = GameSwitchCoordinator(self.store, lambda spec: make_coordinator_adapter(g, spec),
                                                post_commit=commit_hook(g))
        self.coordinator = coordinator

    def canonical(self, *, patience_s=60):
        # A game switch / FIFO tick holds the exclusive lock for a few seconds at a
        # time. A non-blocking read that raised GameSwitchBusyError used to kill the
        # long-lived runner (2026-10-11 05:27) and leave the corner "active" with
        # nobody to honor the End button or restore Soren. Wait it out instead.
        for attempt in range(max(1, int(patience_s / 0.5))):
            try:
                with self.store.lock(exclusive=False):
                    return self.store.canonical.load()[0]
            except GameSwitchBusyError:
                self.sleep(0.5)
        with self.store.lock(exclusive=False):
            return self.store.canonical.load()[0]

    def save(self, state):
        atomic_write_json(self.path, state)

    def stop_requested(self, state):
        flag = Path(self.g.state_dir) / STOP_FILE
        return self.stopping or (flag.exists()
            and read_json(flag).get("start_request_id") == state.get("start_request_id"))

    def stop_reason(self, state):
        """"operator-end" only for an identity-bound flag; signals are "manual"."""
        flag = Path(self.g.state_dir) / STOP_FILE
        try:
            data = read_json(flag) if flag.exists() else {}
        except ExternalVideoError:
            return "manual"
        if data.get("start_request_id") == state.get("start_request_id") and data.get("reason") == OPERATOR_END:
            return OPERATOR_END
        return "manual"

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
            last = current.get("last_result") or {}
            recovered_start = (source and actual and isinstance(receipt, dict)
                and receipt.get("request_id") == state["start_request_id"]
                and receipt.get("target") == VIEW_NAME and receipt.get("status") == "failed"
                and body.get("request_id") == state["start_request_id"]
                and body.get("error_code") == "rollback_failed"
                and last.get("request_id") == state["start_request_id"]
                and last.get("status") == "rolled_back"
                and last.get("from_game") == source["game"] == actual["game"]
                and last.get("restored_generation") == actual["generation"]
                and actual["generation"] >= source["generation"])
            if stable(current) and (actual == source or rolled_back or recovered_start):
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

    def _initialize(self, state, current, receiver, require_audio):
        if not stable(current):
            raise ExternalVideoError("game-switch requires recovery before external corner")
        source = identity(current.get("active"))
        if source and source["game"] == VIEW_NAME:
            raise ExternalVideoError("external view has no matching corner owner")
        if not receiver["fresh"] or receiver["expires_at"] - self.clock() < 30:
            raise ExternalVideoError("receiver has no safe fresh video lifetime")
        if require_audio and not receiver.get("audio_present"):
            raise ExternalVideoError("OBS audio has not been decoded")
        state.update(status="starting", receiver_id=receiver["receiver_id"],
                     previous_runtime_identity=source, receiver_expires_at=receiver["expires_at"])
        self.save(state)

    def _receiver_at_turn(self, state, require_audio):
        # An expired reservation may be renewed only after its exact worker
        # has exited. A replacement by any other operator is never adopted.
        deadline = min(self.clock() + 30, state["wait_deadline_at"])
        renewed = False
        while True:
            self._check_wait(state)
            if self.clock() >= deadline:
                raise ExternalVideoError("OBS did not provide fresh video and audio at the reserved turn")
            receiver = read_receiver(self.g, expected=state["receiver_id"])
            if receiver["fresh"] and (not require_audio or receiver.get("audio_present")):
                return receiver
            if (not renewed and not receiver["alive"]
                    and (receiver.get("status") != "launching" or self.clock() >= receiver["expires_at"])):
                reservation = prepare(self.g, state["listen_ip"],
                                      min(max(60, state["duration_minutes"] + 10), MAX_RECEIVER_MINUTES),
                                      expected_receiver_id=state["receiver_id"])
                state["receiver_id"] = reservation["receiver_id"]
                self.save(state)
                renewed = True
            if self.clock() >= deadline:
                raise ExternalVideoError("OBS did not provide fresh video and audio at the reserved turn")
            self.sleep(1)

    def _check_wait(self, state):
        if self.stop_requested(state):
            raise WaitingStopped("external corner waiting was stopped")
        if self.clock() >= state["wait_deadline_at"]:
            raise CornerWaitExpired("external corner waiting expired")
        if (Path(self.g.state_dir) / "corners" / "external-video.paused").exists():
            raise WaitingStopped("external corner was paused while waiting")

    def closing(self, state):
        """Speak the post-clear impression once, after Soren is back. Never fails the corner."""
        if state.get("closing"):
            return
        state["closing"] = {"status": "speaking", "at": self.clock()}
        self.save(state)
        try:
            from .external_video_closing import speak_closing
            minutes = max(1, round((self.clock() - state["started_at"]) / 60))
            outcome = speak_closing(self.g, minutes)
        except Exception as exc:
            outcome = "failed:" + type(exc).__name__
        state["closing"] = {"status": outcome, "at": self.clock()}
        self.save(state)

    def run(self, duration_minutes=15, *, recovering=False, require_audio=True,
            wait_for_idle=False, wait_minutes=120):
        if type(duration_minutes) is not int or not 1 <= duration_minutes <= MAX_DURATION_MINUTES:
            raise ExternalVideoError(f"corner duration must be 1-{MAX_DURATION_MINUTES} minutes")
        if type(wait_minutes) is not int or not 1 <= wait_minutes <= 120:
            raise ExternalVideoError("corner waiting must be 1-120 minutes")
        if recovering and wait_for_idle:
            raise ExternalVideoError("recovery cannot create a new waiting reservation")
        with exclusive(Path(self.g.state_dir) / "external-video-corner.lock"):
            current = None if wait_for_idle else self.canonical()
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
                now = self.clock()
                state = dict(schema_version=1, game=VIEW_NAME, status="waiting",
                             start_request_id=str(uuid.uuid4()), requested_at=now,
                             duration_minutes=duration_minutes, wait_deadline_at=now + wait_minutes * 60)
                if wait_for_idle:
                    receiver = read_receiver(self.g)
                    state.update(receiver_id=receiver["receiver_id"], listen_ip=receiver["listen_ip"])
                    self.save(state)
                else:
                    # Preserve the immediate mode's refusal before mutation.
                    if not stable(current):
                        raise ExternalVideoError("game-switch requires recovery before external corner")
                    if other_corner_busy(self.g, self.path):
                        raise ExternalVideoError("another program owner must finish or recover")
                    self._initialize(state, current, read_receiver(self.g, fresh=True), require_audio)
            try:
                def waiting_sleep(seconds):
                    self._check_wait(state)
                    self.sleep(seconds)

                with program_slot(self.g, self.path, requested_at=state["requested_at"],
                                  wait_deadline_ts=(state["wait_deadline_at"] if wait_for_idle
                                      else min(self.clock() + 600, state["receiver_expires_at"])),
                                  wait_boundary=True,
                                  **(dict(sleep=waiting_sleep, now=self.clock) if wait_for_idle else {})):
                    if wait_for_idle:
                        self._check_wait(state)
                        receiver = self._receiver_at_turn(state, require_audio)
                        self._initialize(state, self.canonical(), receiver, require_audio)
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
                    lost_since = None
                    while state["status"] == "active" and not recovering:
                        if self.stop_requested(state):
                            reason = self.stop_reason(state)
                            break
                        if self.clock() >= state["ends_at"]:
                            break
                        try:
                            receiver = read_receiver(self.g, expected=state["receiver_id"])
                        except ExternalVideoError:
                            reason = "receiver-unavailable"
                            break
                        if receiver["fresh"]:
                            lost_since = None
                        else:
                            lost_since = self.clock() if lost_since is None else lost_since
                            if self.clock() - lost_since >= DISCONNECT_GRACE_S:
                                reason = "receiver-disconnected"
                                break
                        if identity(self.canonical().get("active")) != state["runtime_identity"]:
                            raise ExternalVideoError("external corner ownership changed during display")
                        self.sleep(1)
                    while not self.restore(state, reason):
                        self.sleep(1)
                    if reason == OPERATOR_END:
                        self.closing(state)
                    return state
            except Exception as exc:
                if state.get("status") == "waiting":
                    state.update(status="interrupted", recovery_required=False,
                                 completed_at=self.clock(), end_reason=("wait-expired"
                                     if isinstance(exc, CornerWaitExpired) else "waiting-cancelled"))
                    self.save(state)
                    if isinstance(exc, (CornerWaitExpired, WaitingStopped)):
                        return state
                    raise
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
    p.add_argument("--wait-for-idle", action="store_true", help="queue behind the current corner without requesting its end")
    p.add_argument("--wait-minutes", type=int, default=120, help="bounded FIFO wait, 1-120 minutes")
    p.add_argument("--video-only", action="store_true", help="explicitly permit an OBS source without audio")
    for command in ("status", "stop", "end", "recover", "receiver-stop"):
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
            request_end(g, state, "manual")
            output = {"status": "stop-requested"}
        elif args.command == "end":
            request_end(g, read_json(manager.path), OPERATOR_END)
            output = {"status": "end-requested"}
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
                                 require_audio=not getattr(args, "video_only", False),
                                 wait_for_idle=getattr(args, "wait_for_idle", False),
                                 wait_minutes=getattr(args, "wait_minutes", 120))
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except ExternalVideoError as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
