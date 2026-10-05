"""Explicit administrative cancellation of exactly two detached error entries.

This operator accepts acknowledged historical uncertainty; it is not evidence
of non-dispatch or resource release. It never releases the rotation reservation,
changes owners/receipts/canonical state, starts a game, or stops a process.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import uuid

from .game_switch import validate_state
from .hanjuku_manual_cancel import CancelRefused, TERMINAL_OWNER

FILES = {"scheduled": "retro_corner.json", "manual": "retro_corner_manual.json"}
OWNERS = (*FILES.values(), "paper_corner.json", "paper_corner_manual.json",
          "nethack_corner.json", "nethack_corner_manual.json", "soren91_corner.json",
          "soren91_corner_manual.json", "weather_corner.json")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REQUEST = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
RECORD_LIMIT = 256 * 1024
QUEUE_LIMIT = 64 * 1024
JOURNAL_LIMIT = 512 * 1024
JOURNAL_DIR = "docich_program_queue_admin_cancel"
REASON = "owner-approved-admin-cancellation-with-unknown-history"
REFUSALS = frozenset({"expected_context_required", "unknown_resources_not_acknowledged",
    "unsafe_state", "unreadable_evidence", "missing_evidence", "busy", "unsafe_lock",
    "lock_unavailable", "reservation_not_in_scope", "context_changed", "queue_not_error",
    "queue_history_unverified", "owner_not_terminal", "owner_requires_reconciliation",
    "program_owner_unverified", "switch_not_stable", "target_active", "receipt_now_present",
    "clock_regressed", "journal_unverified", "journal_limit", "journal_reuse_refused", "evidence_unverified"})


def _serialized(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _decode(raw):
    value = json.loads(raw, object_pairs_hook=_unique,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
    if not isinstance(value, dict):
        raise ValueError()
    return value


def _stamp(value):
    # Queue producers use finite epoch numbers. Do not coerce bools or text.
    if type(value) not in (int, float) or value < 0:
        raise CancelRefused("unreadable_evidence")
    try:
        if not math.isfinite(value):
            raise ValueError()
    except (ValueError, OverflowError):
        raise CancelRefused("unreadable_evidence") from None
    return value


def _open_dir(path):
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).absolute().parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read(path, *, limit=RECORD_LIMIT, optional=False):
    directory = fd = None
    try:
        directory = _open_dir(Path(path).parent)
        fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise CancelRefused("unsafe_state")
        raw = os.read(fd, limit + 1)
        eof = os.read(fd, 1)
        after = os.fstat(fd)
        leaf = os.stat(Path(path).name, dir_fd=directory, follow_symlinks=False)
        if (len(raw) != before.st_size or eof or len(raw) > limit
                or _identity(before) != _identity(after) or _identity(before) != _identity(leaf)):
            raise CancelRefused("context_changed")
        return raw, _decode(raw), _identity(before)
    except FileNotFoundError:
        if optional:
            return None, None, None
        raise CancelRefused("missing_evidence") from None
    except (OSError, ValueError, RecursionError):
        raise CancelRefused("unreadable_evidence") from None
    finally:
        if fd is not None:
            os.close(fd)
        if directory is not None:
            os.close(directory)


def _private_storage(info, mode, *, directory=False):
    if (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != mode
            or (not stat.S_ISDIR(info.st_mode) if directory else
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
        raise CancelRefused("journal_unverified")


def _read_journal(path, *, optional=False, limit=JOURNAL_LIMIT):
    directory = None
    try:
        directory = _open_dir(path.parent)
        _private_storage(os.fstat(directory), 0o700, directory=True)
        info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        _private_storage(info, 0o600)
    except FileNotFoundError:
        if optional:
            return None, None, None
        raise CancelRefused("journal_unverified") from None
    except OSError:
        raise CancelRefused("journal_unverified") from None
    finally:
        if directory is not None:
            os.close(directory)
    return _read(path, limit=limit, optional=optional)


@contextmanager
def _lock(path):
    directory = fd = None
    try:
        try:
            directory = _open_dir(Path(path).parent)
            fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise CancelRefused("unsafe_lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CancelRefused("busy") from None
        except OSError:
            raise CancelRefused("lock_unavailable") from None
        # Body I/O failures must not be mislabeled as lock acquisition failures.
        yield path, (info.st_dev, info.st_ino)
    finally:
        if fd is not None:
            os.close(fd)
        if directory is not None:
            os.close(directory)


def _reservation(ledger, now):
    request = ledger.get("manual_pending")
    weather = ledger.get("queued_manual")
    if (type(ledger.get("schema_version")) is not int or ledger["schema_version"] != 1
            or ledger.get("status") != "waiting"
            or ledger.get("reason") != "manual-request-needs-resume-or-recovery"
            or ledger.get("pending") is not None or not isinstance(request, dict)
            or request.get("corner") != "hanjuku-hero"
            or request.get("state_file") != "retro_corner_manual.json"
            or not isinstance(weather, dict) or weather.get("corner") != "weather"
            or not isinstance(request.get("request_id"), str)
            or not REQUEST.fullmatch(request["request_id"])
            or not isinstance(weather.get("request_id"), str)
            or not REQUEST.fullmatch(weather["request_id"])):
        raise CancelRefused("reservation_not_in_scope")
    # Match the existing reservation operator's accepted timestamp contract.
    from .corner_rotation import timestamp
    if now < timestamp(request.get("selected_at")) or now < timestamp(weather.get("selected_at")):
        raise CancelRefused("clock_regressed")
    return request


def _context(state_dir, program, now):
    paths = {"reservation": state_dir / "corner_rotation.json",
             "canonical": state_dir / "game_switch.json",
             "registry": program / "docich_program_active.json"}
    optional = {"registry"}
    for kind, filename in FILES.items():
        paths[f"queue_{kind}"] = program / "docich_program_queue" / filename
        paths[f"owner_{kind}"] = state_dir / filename
        optional.add(f"owner_{kind}")
    snapshots = {key: _read(path, limit=QUEUE_LIMIT if key.startswith("queue_") else RECORD_LIMIT,
                            optional=key in optional) for key, path in paths.items()}
    request = _reservation(snapshots["reservation"][1], now)
    rid = request["request_id"]
    for kind in FILES:
        owner = snapshots[f"owner_{kind}"][1]
        if owner is not None:
            if owner.get("status") not in TERMINAL_OWNER:
                raise CancelRefused("owner_not_terminal")
            if owner.get("rotation_request_id") == rid:
                raise CancelRefused("owner_requires_reconciliation")
    registry = snapshots["registry"][1]
    if registry is not None:
        registered = registry.get("owner_state")
        fixed = {str(state_dir / name): name for name in OWNERS}
        if not isinstance(registered, str) or registered not in fixed:
            raise CancelRefused("program_owner_unverified")
        # Select from a fixed map; never follow an arbitrary registry path.
        path = state_dir / fixed[registered]
        key = next((key for key, value in paths.items() if value == path), "registered_owner")
        if key not in snapshots:
            paths[key] = path
            snapshots[key] = _read(path)
        owner = snapshots[key][1]
        if (owner is None or owner.get("status") not in TERMINAL_OWNER
                or owner.get("rotation_request_id") == rid):
            raise CancelRefused("program_owner_unverified")
    canonical = snapshots["canonical"][1]
    try:
        validate_state(canonical)
    except Exception:
        raise CancelRefused("switch_not_stable") from None
    if (canonical.get("phase") not in {"idle", "ready"}
            or canonical.get("candidate") is not None or canonical.get("previous") is not None
            or canonical.get("retiring")):
        raise CancelRefused("switch_not_stable")
    active = canonical.get("active")
    # This recovery preserves idle or the current Soren runtime only.
    if active and active.get("game") != "sorengame":
        raise CancelRefused("target_active")
    paths["receipt"] = state_dir / "game-switch/requests" / f"{rid}.json"
    optional.add("receipt")
    snapshots["receipt"] = _read(paths["receipt"], optional=True)
    if snapshots["receipt"][0] is not None:
        raise CancelRefused("receipt_now_present")
    return paths, optional, snapshots


def _fingerprints(snapshots):
    return {key: _digest(value[0]) if value[0] is not None else None
            for key, value in snapshots.items()}


def _fingerprint(context):
    return _digest(_serialized({"schema_version": 1, "operation": "admin-cancel-retro-error-queues",
                                "context": context}))


def _queue_original(record, now):
    if record.get("status") != "error":
        raise CancelRefused("queue_not_error")
    if "admin_cancellation" in record:
        raise CancelRefused("queue_history_unverified")
    requested = _stamp(record.get("requested_at"))
    deadline = _stamp(record.get("wait_deadline_ts"))
    if deadline < requested:
        raise CancelRefused("queue_history_unverified")
    if now < requested:
        raise CancelRefused("clock_regressed")


def _replacement(record, expected, at):
    updated = dict(record)
    updated.update(status="cancelled", admin_cancellation={"schema_version": 1,
        "reason": REASON, "audit_id": expected, "at": at,
        "all_resources_released": None, "cancellation_authority": False})
    raw = _serialized(updated) + b"\n"
    if len(raw) > QUEUE_LIMIT:
        raise CancelRefused("journal_limit")
    return raw


def _encode(raw):
    return base64.b64encode(raw).decode("ascii")


def _journal(context, snapshots, expected, at):
    queues = {}
    for kind in FILES:
        raw, record, _ = snapshots[f"queue_{kind}"]
        _queue_original(record, at)
        queues[kind] = {"original": _encode(raw),
                        "replacement": _encode(_replacement(record, expected, at))}
    result = {"schema_version": 1, "operation": "admin-cancel-retro-error-queues",
              "approval_fingerprint": expected, "context": context, "at": at,
              "unknown_resources_acknowledged": True, "all_resources_released": None,
              "cancellation_authority": False, "queues": queues}
    if len(_serialized(result)) + 1 > JOURNAL_LIMIT:
        raise CancelRefused("journal_limit")
    return result


def _journal_queues(journal, expected, now):
    try:
        if (set(journal) != {"schema_version", "operation", "approval_fingerprint", "context", "at",
                            "unknown_resources_acknowledged", "all_resources_released", "cancellation_authority", "queues"}
                or type(journal["schema_version"]) is not int or journal["schema_version"] != 1
                or journal["operation"] != "admin-cancel-retro-error-queues"
                or journal["approval_fingerprint"] != expected
                or journal["unknown_resources_acknowledged"] is not True
                or journal["all_resources_released"] is not None
                or journal["cancellation_authority"] is not False
                or _fingerprint(journal["context"]) != expected
                or set(journal["queues"]) != set(FILES) or _stamp(journal["at"]) > now):
            raise ValueError()
        queues = {}
        for kind in FILES:
            row = journal["queues"][kind]
            if set(row) != {"original", "replacement"}:
                raise ValueError()
            original = base64.b64decode(row["original"], validate=True)
            replacement = base64.b64decode(row["replacement"], validate=True)
            if not 0 < len(original) <= QUEUE_LIMIT or not 0 < len(replacement) <= QUEUE_LIMIT:
                raise ValueError()
            record = _decode(original)
            _queue_original(record, journal["at"])
            if (_digest(original) != journal["context"][f"queue_{kind}"]
                    or replacement != _replacement(record, expected, journal["at"])):
                raise ValueError()
            queues[kind] = original, replacement
        return queues
    except (ValueError, TypeError, KeyError, AttributeError, CancelRefused, RecursionError):
        raise CancelRefused("journal_unverified") from None


def _recheck(paths, optional, snapshots, locks):
    for path, identity in locks:
        directory = None
        try:
            directory = _open_dir(path.parent)
            info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != identity:
                raise CancelRefused("context_changed")
        except OSError:
            raise CancelRefused("context_changed") from None
        finally:
            if directory is not None:
                os.close(directory)
    for key, path in paths.items():
        current = _read(path, limit=QUEUE_LIMIT if key.startswith("queue_") else RECORD_LIMIT,
                        optional=key in optional)
        if current[0] != snapshots[key][0] or current[2] != snapshots[key][2]:
            raise CancelRefused("context_changed")


def _write_at(directory, name, raw, *, exclusive=False):
    # Private temp, durable contents, then atomic install. Never follow a leaf.
    temporary = f".admin-cancel-{uuid.uuid4().hex}.tmp"
    fd = None
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            installed_info = os.fstat(stream.fileno())
            installed_identity = installed_info.st_dev, installed_info.st_ino
        if exclusive:
            os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            os.unlink(temporary, dir_fd=directory)
        else:
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
        return installed_identity
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _durable_journal(program_fd, audit_fd, name, raw):
    # A prior fsync failure can leave a readable but non-durable journal.
    # Re-establish durability on every attempt before touching either queue.
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=audit_fd)
    try:
        before = os.fstat(fd)
        _private_storage(before, 0o600)
        _private_storage(os.fstat(audit_fd), 0o700, directory=True)
        if not stat.S_ISREG(before.st_mode) or before.st_size != len(raw):
            raise CancelRefused("journal_unverified")
        contents = os.read(fd, len(raw) + 1)
        eof = os.read(fd, 1)
        after = os.fstat(fd)
        if contents != raw or eof or _identity(before) != _identity(after):
            raise CancelRefused("journal_unverified")
        os.fsync(fd)
        os.fsync(audit_fd)
        os.fsync(program_fd)
    finally:
        os.close(fd)


def cancel(g, *, expected=None, apply=False, acknowledge_unknown_resources=False,
           allow_journal_resume=True, now=time.time):
    """Dry-run or explicitly acknowledged, exact-context administrative action.

    The immutable journal precedes both queue writes. A partial application is
    resumable only with that journal and unchanged non-queue context. No rollback
    overwrites concurrent state, and no original record is deleted or pruned.
    """
    if expected is not None and (not isinstance(expected, str) or not DIGEST.fullmatch(expected)):
        raise CancelRefused("expected_context_required")
    if apply and expected is None:
        raise CancelRefused("expected_context_required")
    if apply and acknowledge_unknown_resources is not True:
        raise CancelRefused("unknown_resources_not_acknowledged")
    state_dir = Path(g.state_dir).absolute()
    from .trading.soren_output import resolve_soren_root
    program = resolve_soren_root(g).absolute() / "tmp/state"
    at = _stamp(now())
    with ExitStack() as held:
        locks = [held.enter_context(_lock(path)) for path in (
            state_dir / "locks/corner-rotation.lock", state_dir / "locks/retro-corner-tick.lock",
            state_dir / "locks/retro-corner.lock",
            state_dir / "locks/retro-corner-manual.lock", program / "docich_program.lock",
            state_dir / "locks/game-switch.lock")]
        paths, optional, snapshots = _context(state_dir, program, at)
        context = _fingerprints(snapshots)
        journal_path = program / JOURNAL_DIR / f"{expected}.json" if expected else None
        saved_raw, saved, _ = _read_journal(journal_path, optional=True) if journal_path else (None, None, None)
        if saved is not None:
            if allow_journal_resume is not True:
                raise CancelRefused("journal_reuse_refused")
            queues = _journal_queues(saved, expected, at)
            if set(context) != set(saved["context"]):
                raise CancelRefused("context_changed")
            for key, fingerprint in context.items():
                if key.startswith("queue_"):
                    kind = key.removeprefix("queue_")
                    if snapshots[key][0] not in queues[kind]:
                        raise CancelRefused("context_changed")
                elif fingerprint != saved["context"][key]:
                    raise CancelRefused("context_changed")
            fingerprint = expected
        else:
            for kind in FILES:
                _queue_original(snapshots[f"queue_{kind}"][1], at)
            fingerprint = _fingerprint(context)
            if expected is not None and fingerprint != expected:
                raise CancelRefused("context_changed")
            saved = _journal(context, snapshots, fingerprint, at)
            queues = _journal_queues(saved, fingerprint, at)
        _recheck(paths, optional, snapshots, locks)
        result = {"status": "admin-cancelled" if apply else "admin-check",
                  "scheduled": "cancelled" if apply else snapshots["queue_scheduled"][1]["status"],
                  "manual": "cancelled" if apply else snapshots["queue_manual"][1]["status"],
                  "all_resources_released": None, "cancellation_authority": False,
                  "reservation_preserved": True, "weather_queue_preserved": True}
        if not apply:
            # Private operator token, not part of the public context projection.
            result["approval_fingerprint"] = fingerprint
            result["unknown_resources_acknowledgement_required"] = True
            return result
        with ExitStack() as directories:
            program_fd = _open_dir(program)
            directories.callback(os.close, program_fd)
            if saved_raw is None:
                try:
                    os.mkdir(JOURNAL_DIR, 0o700, dir_fd=program_fd)
                    os.fsync(program_fd)
                except FileExistsError:
                    pass
            audit_fd = os.open(JOURNAL_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=program_fd)
            directories.callback(os.close, audit_fd)
            _private_storage(os.fstat(audit_fd), 0o700, directory=True)
            queue_fd = os.open("docich_program_queue", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=program_fd)
            directories.callback(os.close, queue_fd)
            if saved_raw is None:
                _recheck(paths, optional, snapshots, locks)
                _write_at(audit_fd, f"{fingerprint}.json", _serialized(saved) + b"\n", exclusive=True)
            _durable_journal(program_fd, audit_fd, f"{fingerprint}.json", saved_raw or _serialized(saved) + b"\n")
            for kind, filename in FILES.items():
                _recheck(paths, optional, snapshots, locks)
                journal_now, _, _ = _read_journal(program / JOURNAL_DIR / f"{fingerprint}.json")
                if journal_now != (saved_raw or _serialized(saved) + b"\n"):
                    raise CancelRefused("journal_unverified")
                key = f"queue_{kind}"
                original, replacement = queues[kind]
                if snapshots[key][0] == replacement:
                    continue
                current_dir = _open_dir(program / "docich_program_queue")
                try:
                    if os.fstat(current_dir).st_ino != os.fstat(queue_fd).st_ino or os.fstat(current_dir).st_dev != os.fstat(queue_fd).st_dev:
                        raise CancelRefused("context_changed")
                finally:
                    os.close(current_dir)
                _write_at(queue_fd, filename, replacement)
                current = _read(paths[key], limit=QUEUE_LIMIT)
                if current[0] != replacement:
                    raise CancelRefused("context_changed")
                snapshots[key] = current
            _recheck(paths, optional, snapshots, locks)
            journal_now, _, _ = _read_journal(program / JOURNAL_DIR / f"{fingerprint}.json")
            if journal_now != (saved_raw or _serialized(saved) + b"\n"):
                raise CancelRefused("journal_unverified")
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("check", "cancel-admin"))
    parser.add_argument("--expected")
    parser.add_argument("--acknowledge-unknown-resources", action="store_true")
    args = parser.parse_args(argv)
    try:
        from .config import load_global
        root = Path(__file__).resolve().parents[2]
        g = load_global(root, root / "config/docich.soren-live.toml")
        result = cancel(g, expected=args.expected, apply=args.operation == "cancel-admin",
                        acknowledge_unknown_resources=args.acknowledge_unknown_resources)
    except CancelRefused as exc:
        reason = str(exc)
        result = {"status": "refused", "reason": reason if reason in REFUSALS else "evidence_unverified"}
    except Exception:
        result = {"status": "refused", "reason": "evidence_unverified"}
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["status"] in {"admin-check", "admin-cancelled"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
