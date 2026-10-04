"""Cancel one reviewed, detached Hanjuku manual reservation; never run recovery."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from .config import load_global
from .corner_rotation import timestamp
from .game_switch import GameSwitchStore, atomic_write_json, validate_request_id
from .naming import runtime_directory, runtime_id_generation, runtime_names
from .tmux import SESSION, Tmux
from .trading.soren_output import resolve_soren_root

TARGET = "hanjuku-hero"
OWNER_FILES = ("retro_corner.json", "retro_corner_manual.json")
TERMINAL_OWNER = {"idle", "completed", "interrupted", "expired"}
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class CancelRefused(RuntimeError):
    """Only fixed reason codes may cross the operator boundary."""


class _ProbeTmux(Tmux):
    def _run(self, args, **kwargs):
        # Never hold the writer locks indefinitely for a stuck tmux socket.
        return super()._run(args, timeout=3, **kwargs)


def _object(path, *, optional=False):
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise CancelRefused("unsafe_state")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as handle:
            meta = os.fstat(handle.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_size > 1024 * 1024:
                raise CancelRefused("unsafe_state")
            value = json.loads(handle.read(1024 * 1024 + 1))
    except FileNotFoundError:
        if optional:
            return None
        raise CancelRefused("missing_evidence") from None
    except (OSError, ValueError):
        raise CancelRefused("unreadable_evidence") from None
    if not isinstance(value, dict):
        raise CancelRefused("invalid_evidence")
    return value


@contextmanager
def _existing_lock(path):
    # check mode must not create locks, state directories or registrations.
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise CancelRefused("unsafe_lock")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise CancelRefused("unsafe_lock")
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
    except BlockingIOError:
        raise CancelRefused("busy") from None
    except OSError:
        raise CancelRefused("lock_unavailable") from None


def _released_runtime(state_dir, runtime_id, tmux):
    runtime = runtime_directory(state_dir, runtime_id)
    names = runtime_names(runtime_id_generation(runtime_id))
    if runtime.exists():
        # This existing presenter contract publishes stopped only after all
        # owned process groups have exited, including detached game children.
        presentation = _object(runtime / "presentation.json", optional=True)
        if not presentation or presentation.get("status") != "stopped":
            raise CancelRefused("runtime_resources_present")
    for name in (names.game_window, names.agent_window):
        if tmux.window_target_exists(f"{SESSION}:{name}", strict=True):
            raise CancelRefused("runtime_resources_present")
    if tmux.session_target_exists(names.adapter_session, strict=True):
        raise CancelRefused("runtime_resources_present")


def cancel(g, *, expected=None, apply=False, tmux=None, now=time.time):
    """Recheck the exact reservation under all writer locks before one ledger write.

    A missing receipt says nothing about historical execution. Cancellation
    still requires present-state proof that no Hanjuku receipt is in progress
    and the last fixed Hanjuku owner's resources have been released.
    """
    if apply and (not isinstance(expected, str) or not DIGEST.fullmatch(expected)):
        raise CancelRefused("expected_reservation_required")
    state_dir = Path(g.state_dir)
    program = resolve_soren_root(g) / "tmp/state"
    store = GameSwitchStore(state_dir)
    tmux = tmux or _ProbeTmux()
    with ExitStack() as held:
        for path in (
            state_dir / "locks/corner-rotation.lock",
            state_dir / "locks/retro-corner.lock",
            state_dir / "locks/retro-corner-manual.lock",
            program / "docich_program.lock",
            state_dir / "locks/game-switch.lock",
        ):
            held.enter_context(_existing_lock(path))
        path = state_dir / "corner_rotation.json"
        state = _object(path)
        request = state.get("manual_pending")
        if (state.get("schema_version") != 1 or state.get("status") != "waiting"
                or state.get("reason") != "manual-request-needs-resume-or-recovery"
                or state.get("pending") is not None or not isinstance(request, dict)
                or request.get("corner") != TARGET
                or request.get("state_file") != "retro_corner_manual.json"
                or not isinstance(state.get("queued_manual"), dict)
                or state["queued_manual"].get("corner") != "weather"):
            raise CancelRefused("reservation_not_in_scope")
        request_id = validate_request_id(request.get("request_id"))
        selected = timestamp(request.get("selected_at"))
        if now() < selected:
            raise CancelRefused("clock_regressed")
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest()
        if apply and fingerprint != expected:
            raise CancelRefused("reservation_changed")
        owners = []
        for name in OWNER_FILES:
            queue = _object(program / "docich_program_queue" / name, optional=True)
            if queue and queue.get("status") not in {"done", "expired", "cancelled"}:
                raise CancelRefused("program_queue_unverified")
            owner = _object(state_dir / name, optional=True)
            if owner is None:
                continue
            owners.append(owner)
            if owner.get("status") not in TERMINAL_OWNER:
                raise CancelRefused("owner_not_terminal")
            if owner.get("rotation_request_id") == request_id:
                raise CancelRefused("owner_requires_reconciliation")
        # Fixed owner files only: a registry is never a caller-controlled path.
        registry = _object(program / "docich_program_active.json", optional=True)
        if registry:
            registered = registry.get("owner_state")
            files = (*OWNER_FILES, "paper_corner.json", "paper_corner_manual.json",
                     "nethack_corner.json", "nethack_corner_manual.json",
                     "soren91_corner.json", "soren91_corner_manual.json", "weather_corner.json")
            if registered not in {str(state_dir / name) for name in files}:
                raise CancelRefused("program_owner_unverified")
            owner = _object(Path(registered))
            if (owner.get("rotation_request_id") == request_id
                    or (owner.get("game") == TARGET and owner.get("status") not in TERMINAL_OWNER)):
                raise CancelRefused("program_owner_unverified")
        _object(store.canonical.path, optional=True)
        canonical, missing = store.canonical.load()
        if (missing or canonical.get("phase") not in {"idle", "ready"}
                or canonical.get("request_id") == request_id
                or canonical.get("candidate") is not None
                or canonical.get("previous") is not None or canonical.get("retiring")):
            raise CancelRefused("switch_not_stable")
        active = canonical.get("active")
        if active and active.get("game") == TARGET:
            raise CancelRefused("target_active")
        _object(store.receipts._path(request_id), optional=True)
        receipt = store.receipts.load(request_id)
        if receipt is None:
            # Inspect the existing durable receipt contract, never infer
            # absence of execution from the current receipt's absence alone.
            directory = store.receipts.directory
            if directory.is_symlink():
                raise CancelRefused("unsafe_state")
            paths = list(directory.glob("*.json"))
            if len(paths) > 4096:
                raise CancelRefused("receipt_inventory_unverified")
            for path in paths:
                _object(path)
                row = store.receipts.load(validate_request_id(path.stem))
                if row is None:
                    raise CancelRefused("receipt_inventory_unverified")
                if row.get("target") == TARGET and row.get("status") not in {
                        "succeeded", "failed", "rolled_back"}:
                    raise CancelRefused("target_receipt_in_progress")
            previous = [owner for owner in owners if owner.get("game") == TARGET]
            if not previous:
                raise CancelRefused("resources_unverified")
            for owner in previous:
                identity = owner.get("bot_identity")
                if (not isinstance(identity, dict) or identity.get("game") != TARGET
                        or identity.get("runtime_id") != owner.get("bot_runtime_id")
                        or identity.get("generation") != runtime_id_generation(identity.get("runtime_id"))
                        or identity.get("generation") >= canonical["next_generation"]
                        or owner.get("recovery_required") is not False):
                    raise CancelRefused("resources_unverified")
                if active and active.get("runtime_id") == identity["runtime_id"]:
                    raise CancelRefused("runtime_active")
                _released_runtime(state_dir, identity.get("runtime_id"), tmux)
        else:
            result = receipt.get("result")
            if (receipt.get("target") != TARGET
                or receipt.get("operation") not in {"start", "switch"}
                or receipt.get("status") not in {"failed", "rolled_back"}
                or not isinstance(result, dict) or result.get("request_id") != request_id
                or result.get("status") != receipt["status"]
                or result.get("operation") != receipt["operation"]
                or result.get("cleanup_pending") is not False
                or timestamp(receipt["updated_at"]) < selected):
                raise CancelRefused("receipt_not_released")
            if active and active.get("runtime_id") == receipt["runtime_id"]:
                raise CancelRefused("runtime_active")
            _released_runtime(state_dir, receipt["runtime_id"], tmux)
        if not apply:
            return {"status": "eligible", "corner": TARGET, "fingerprint": fingerprint}
        # Retain the original request privately for audit. Queue, cooldown,
        # history, user pauses and every runtime/receipt remain untouched.
        state["last_manual_cancel"] = {"reservation": request, "at": now(),
                                       "reason": "owner-approved-detached-reservation"}
        state.update(manual_pending=None, status="waiting",
                     reason="manual-request-cancelled", error_kind=None)
        atomic_write_json(path, state)
        return {"status": "cancelled", "corner": TARGET, "weather_queue_preserved": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("check", "apply"))
    parser.add_argument("--expected", default=None)
    args = parser.parse_args(argv)
    try:
        root = Path(__file__).resolve().parents[2]
        g = load_global(root, root / "config/docich.soren-live.toml")
        result = cancel(g, expected=args.expected, apply=args.operation == "apply")
    except CancelRefused as exc:
        result = {"status": "refused", "reason": str(exc)}
    except Exception:
        result = {"status": "refused", "reason": "evidence_unverified"}
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["status"] in {"eligible", "cancelled"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
