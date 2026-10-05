"""Bounded positive records for one fixed manual request; never cancellation proof."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from . import procs
from .hanjuku_manual_cancel import CancelRefused, _released_runtime
from .game_switch import validate_receipt
from .tmux import Tmux

PAGE_BYTES = 65536
MAX_PAGES = 256
MAX_SCAN_BYTES = 16 * 1024 * 1024
MAX_LINE_BYTES = 65536
MAX_MATCHES = 256
MAX_RUNTIMES = 8
BUDGET_SECONDS = 10  # Cooperative processing budget, not an interruptible I/O deadline.
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
RUNTIME = re.compile(r"g([1-9][0-9]{0,11})-[a-f0-9]{6,32}\Z")
OWNER_FILES = ("retro_corner_manual.json", "retro_corner.json")
TERMINAL = {"completed", "interrupted", "expired", "idle"}


def _stamp(value):
    if type(value) in (int, float):
        try:
            return value if math.isfinite(value) else None
        except OverflowError:
            return None
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() if parsed.tzinfo is not None else None
        except (ValueError, OverflowError, OSError):
            pass
    return None


class _BoundedProbe(Tmux):
    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline

    def _run(self, args, **kwargs):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        if args[:1] == ["has-session"] and len(args) == 3 and args[1] == "-t":
            command = ["tmux", "list-sessions", "-F", "#{session_name}"]
        elif (len(args) == 5 and args[0] == "list-windows" and args[1] == "-t"
              and args[3:] == ["-F", "#{window_name}"]):
            command = ["tmux", *args]
        else:
            raise ValueError("unsupported read-only probe")
        result = procs.run_bounded_output(command, max_output_bytes=16384,
                                         timeout=min(0.3, remaining), strip_tmux=True)
        stdout = result.stdout.decode("utf-8", errors="replace")
        if args[0] == "has-session" and result.returncode == 0:
            present = args[2] in stdout.splitlines()
            return subprocess.CompletedProcess(args, 0 if present else 1, "",
                                               "" if present else "can't find session")
        # Failed probes have no stderr absence marker: absence remains unknown.
        return subprocess.CompletedProcess(args, result.returncode, stdout, "")


def _open_fixed(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise OSError()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    handle = os.fdopen(fd, "rb")
    meta = os.fstat(handle.fileno())
    if not stat.S_ISREG(meta.st_mode):
        handle.close()
        raise OSError()
    return handle, meta


def _runtime(value, generation):
    match = RUNTIME.fullmatch(value) if isinstance(value, str) else None
    return value if (match and type(generation) is int
                     and int(match.group(1)) == generation) else None


def _log_records(state_dir, request_id, deadline, observe, checkpoint):
    out = {"present": None, "readable": False, "pages": 0,
           "scan_complete": False, "prefix_truncated": False,
           "record_truncated": False, "matches_truncated": False,
           "changed_during_scan": None, "budget_exhausted": False,
           "malformed_records": 0, "matching_records": 0,
           "initial_size_bytes": None, "required_pages": None, "bytes_read": 0,
           "eof_reached": False, "end_reason": "unavailable"}
    reason = checkpoint()
    if reason:
        out.update(end_reason=reason, budget_exhausted=reason == "budget_exhausted")
        return out
    try:
        handle, before = _open_fixed(state_dir / "logs/game_switch.log")
    except FileNotFoundError:
        out["present"] = False
        out["end_reason"] = "missing"
        return out
    except OSError:
        return out
    out.update(present=True, readable=True)
    with handle:
        size = before.st_size
        required = (size + PAGE_BYTES - 1) // PAGE_BYTES
        out.update(initial_size_bytes=size, required_pages=required)

        def source_reason():
            if time.monotonic() >= deadline:
                return "budget_exhausted"
            try:
                current = os.stat(state_dir / "logs/game_switch.log", follow_symlinks=False)
                if time.monotonic() >= deadline:
                    return "budget_exhausted"
                opened = os.fstat(handle.fileno())
                path = state_dir / "logs/game_switch.log"
                for part in (path, *path.parents):
                    if time.monotonic() >= deadline:
                        return "budget_exhausted"
                    if part.is_symlink():
                        return "source_replaced"
                if time.monotonic() >= deadline:
                    return "budget_exhausted"
                if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                    return "source_replaced"
                if current.st_size < size or opened.st_size < size:
                    return "source_shrunk"
                if ((opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                        != (size, before.st_mtime_ns, before.st_ctime_ns)
                        or (current.st_size, current.st_mtime_ns, current.st_ctime_ns)
                        != (size, before.st_mtime_ns, before.st_ctime_ns)):
                    return "source_changed"
            except OSError:
                return "source_unavailable"
            return None

        reason = "size_limit" if size > MAX_SCAN_BYTES else "page_limit" if required > MAX_PAGES else None
        pending = b""
        # One handle, one initial byte range, no restart or following appends.
        for _ in range(min(required, MAX_PAGES)):
            if reason:
                break
            reason = checkpoint() or source_reason()
            if not reason and time.monotonic() >= deadline:
                reason = "budget_exhausted"
            if reason:
                break
            try:
                block = handle.read(min(PAGE_BYTES, size - handle.tell()))
            except OSError:
                out["readable"] = False
                reason = "source_unavailable"
                break
            if not block:
                reason = "unexpected_eof"
                break
            out["pages"] += 1
            out["bytes_read"] += len(block)
            parts = (pending + block).split(b"\n")
            pending = parts.pop()
            for raw in parts:
                if time.monotonic() >= deadline:
                    reason = "budget_exhausted"
                    break
                if len(raw) > MAX_LINE_BYTES:
                    out["record_truncated"] = True
                    reason = "line_limit"
                    break
                try:
                    row = json.loads(raw)
                    if not isinstance(row, dict):
                        raise ValueError()
                except (ValueError, UnicodeError, RecursionError):
                    out["malformed_records"] += 1
                    continue
                if row.get("request_id") != request_id:
                    continue
                if out["matching_records"] >= MAX_MATCHES:
                    out["matches_truncated"] = True
                    reason = "match_limit"
                    break
                out["matching_records"] += 1
                reason = observe(row)
                if reason:
                    break
            reason = checkpoint() or source_reason() or reason
            if len(pending) > MAX_LINE_BYTES:
                out["record_truncated"] = True
                reason = reason or "line_limit"
        if not reason and pending:
            out["record_truncated"] = True
            reason = "partial_record"
        source = source_reason()
        reason = checkpoint() or source or reason
        out["changed_during_scan"] = None if source == "budget_exhausted" else source is not None
        out["budget_exhausted"] = reason == "budget_exhausted"
        out["eof_reached"] = handle.tell() == size
        out["end_reason"] = reason or ("malformed_records" if out["malformed_records"] else "eof")
        out["scan_complete"] = bool(
            out["end_reason"] == "eof" and out["eof_reached"])
    return out


def project(state_dir, manual, now, *, read_fixed):
    """Return only fixed enums/booleans/counts and numeric generation ordinals.

    ``read_fixed`` is the collector's bounded no-symlink object reader.
    Even a full retained log is best-effort, with no receipt-prune tombstones.
    It cannot prove all historical generations or unobserved resources covered.
    """
    out = {"observed": False, "requested_seen": False, "receipt_created_seen": False,
           "candidate_started_seen": False, "terminal_seen": False,
           "terminal_cleanup_clear_seen": False, "invalid_matching_records": 0,
           "matching_owners": 0, "terminal_owners": 0, "owner_read_unknown": 0,
           "generations_truncated": False, "generations": [], "log": None,
           "request_generation_coverage": "unknown", "resource_attribution_unknown": 1,
           "all_resources_released": None, "cancellation_authority": False,
           "checkpoint_status": "unobserved"}
    if (not isinstance(manual, dict) or manual.get("corner") != "hanjuku-hero"
            or manual.get("state_file") != "retro_corner_manual.json"):
        return out
    rid = manual.get("request_id")
    selected = _stamp(manual.get("selected_at"))
    if (not isinstance(rid, str) or not UUID.fullmatch(rid)
            or selected is None or not 0 <= selected <= now):
        return out
    out["observed"] = True
    state_dir = Path(state_dir)
    deadline = time.monotonic() + BUDGET_SECONDS
    stop_reason = None
    def fingerprint(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).digest()
    try:
        expected = fingerprint(manual)
    except (ValueError, TypeError, OverflowError):
        out["checkpoint_status"] = "invalid_checkpoint"
        return out

    def checkpoint():
        nonlocal stop_reason
        reason = stop_reason
        if reason:
            out["checkpoint_status"] = reason
            return reason
        if time.monotonic() >= deadline:
            reason = "budget_exhausted"
        else:
            try:
                _, readable, ledger = read_fixed(state_dir, "corner_rotation.json")
                if not readable or not isinstance(ledger.get("manual_pending"), dict):
                    reason = "reservation_unavailable"
                elif fingerprint(ledger["manual_pending"]) != expected:
                    reason = "reservation_changed"
            except (OSError, ValueError, TypeError, RecursionError):
                reason = "reservation_unavailable"
            if time.monotonic() >= deadline:
                reason = "budget_exhausted"
        if reason:
            stop_reason = reason
        out["checkpoint_status"] = reason or "stable"
        return reason

    runtimes = {}
    generation_conflicts = set()
    invalid_owner_runtimes = set()
    leases = {}

    def add(value, generation):
        runtime = _runtime(value, generation)
        if runtime is None:
            out["invalid_matching_records"] += 1
            return
        # Compare against retained identities before the output cap discards
        # this runtime. The conflict set is bounded by retained generations.
        if any(g == generation and r != runtime for g, r in runtimes):
            generation_conflicts.add(generation)
        key = (generation, runtime)
        if key in runtimes:
            return
        if len(runtimes) >= MAX_RUNTIMES:
            out["generations_truncated"] = True
            return
        runtimes[key] = {"generation": generation, "owner_terminal": None,
                         "lease_identity_observed": False, "owner_identity_conflict": False}

    def observe(row):
        stamp = _stamp(row.get("timestamp"))
        if (type(row.get("schema_version")) is not int or row.get("schema_version") != 1
                or row.get("operation") not in ("start", "switch")
                or row.get("target") != "hanjuku-hero"
                or stamp is None or not selected <= stamp <= now):
            out["invalid_matching_records"] += 1
            return
        event = row.get("event")
        out["requested_seen"] |= event == "requested"
        out["receipt_created_seen"] |= event in ("accepted", "queued")
        out["candidate_started_seen"] |= event == "candidate_started"
        terminal = (event in ("committed", "rollback_ready", "round_boundary_failed", "recovery_finished")
                    and row.get("result") in ("succeeded", "failed", "rolled_back"))
        out["terminal_seen"] |= terminal
        out["terminal_cleanup_clear_seen"] |= terminal and row.get("cleanup_pending") is False
        if row.get("runtime_id") is not None or row.get("generation") is not None:
            add(row.get("runtime_id"), row.get("generation"))
        return "generation_limit" if out["generations_truncated"] else None

    try:
        out["log"] = _log_records(state_dir, rid, deadline, observe, checkpoint)
    except (OSError, ValueError):
        out["log"] = {"readable": False, "scan_complete": False,
                      "end_reason": "source_unavailable", "initial_size_bytes": None,
                      "required_pages": None, "pages": None, "bytes_read": None}
    reason = out["log"].get("end_reason", "")
    if not stop_reason and (reason.startswith(("source_", "reservation_")) or reason == "budget_exhausted"):
        stop_reason = reason
    _, readable, receipt = (read_fixed(state_dir, f"game-switch/requests/{rid}.json")
                            if not checkpoint() else (False, False, {}))
    if readable and receipt.get("request_id") == rid:
        try:
            validate_receipt(receipt, state_dir, expected_request_id=rid)
            updated = _stamp(receipt.get("updated_at"))
            if (receipt.get("target") != "hanjuku-hero" or receipt.get("operation") not in ("start", "switch")
                    or updated is None or not selected <= updated <= now):
                raise ValueError()
        except Exception:
            out["invalid_matching_records"] += 1
        else:
            out["receipt_created_seen"] = True
            add(receipt.get("runtime_id"), receipt.get("generation"))
    for filename in OWNER_FILES:
        if checkpoint():
            break
        present, readable, owner = read_fixed(state_dir, filename)
        if present and not readable:
            out["owner_read_unknown"] += 1
        if not readable or owner.get("rotation_request_id") != rid:
            continue
        out["matching_owners"] += 1
        if owner.get("game") != "hanjuku-hero":
            out["invalid_matching_records"] += 1
            continue
        terminal = isinstance(owner.get("status"), str) and owner["status"] in TERMINAL
        identity = owner.get("bot_identity")
        if identity is None:
            identity = owner.get("runtime_identity")
        lease = identity.get("lease_id") if isinstance(identity, dict) else None
        if (not isinstance(identity, dict) or identity.get("game") != "hanjuku-hero"
                or not isinstance(lease, str) or not UUID.fullmatch(lease)):
            out["invalid_matching_records"] += 1
            continue
        if owner.get("bot_runtime_id") != identity.get("runtime_id"):
            out["invalid_matching_records"] += 1
            # At most two IDs per fixed owner. Remember both sides so an
            # inconsistent binding cannot certify either retained runtime.
            for value in (owner.get("bot_runtime_id"), identity.get("runtime_id")):
                if isinstance(value, str) and RUNTIME.fullmatch(value):
                    invalid_owner_runtimes.add(value)
            continue
        add(identity.get("runtime_id"), identity.get("generation"))
        if _runtime(identity.get("runtime_id"), identity.get("generation")) is None:
            continue
        out["terminal_owners"] += int(terminal)
        key = (identity.get("generation"), identity.get("runtime_id"))
        if key in runtimes:
            runtimes[key]["owner_terminal"] = terminal and runtimes[key]["owner_terminal"] is not False
            runtimes[key]["lease_identity_observed"] = True
            if key in leases and leases[key] != lease:
                runtimes[key]["owner_identity_conflict"] = True
            leases[key] = lease
    _, canonical_readable, canonical = (read_fixed(state_dir, "game_switch.json")
                                        if not checkpoint() else (False, False, {}))
    coverage_complete = out["log"].get("scan_complete") is True and not out["generations_truncated"]
    for (generation, runtime), entry in sorted(runtimes.items()):
        if runtime in invalid_owner_runtimes:
            entry.update(owner_identity_conflict=True, owner_terminal=None,
                         lease_identity_observed=False)
        entry.update(generation_conflict=generation in generation_conflicts,
                     canonical_tracks=None, resources_released=None)
        stable = checkpoint() is None
        if stable and canonical_readable:
            retired = canonical.get("retiring")
            identities = [canonical.get(k) for k in ("active", "candidate", "previous")]
            if isinstance(retired, list):
                identities += retired
            if (canonical.get("phase") in ("idle", "ready") and isinstance(retired, list)
                    and all(v is None or (isinstance(v, dict)
                            and _runtime(v.get("runtime_id"), v.get("generation")))
                            for v in identities)):
                entry["canonical_tracks"] = any(
                    v and (v.get("runtime_id") == runtime or v.get("generation") == generation)
                    for v in identities)
        if (coverage_complete and entry["canonical_tracks"] is False and not entry["generation_conflict"]
                and not entry["owner_identity_conflict"]):
            try:
                path = state_dir / "runtimes" / runtime
                if any(p.is_symlink() for p in (path, *path.parents)):
                    raise OSError()
                _released_runtime(state_dir, runtime, _BoundedProbe(deadline))
                entry["resources_released"] = True
            except CancelRefused as exc:
                if str(exc) == "runtime_resources_present":
                    entry["resources_released"] = False
            except Exception:
                pass
        elif (coverage_complete and entry["canonical_tracks"] is True and not entry["generation_conflict"]
              and not entry["owner_identity_conflict"]):
            entry["resources_released"] = False
        out["generations"].append(entry)
    reason = checkpoint()
    if reason:
        out["log"].update(end_reason=reason, scan_complete=False,
                          budget_exhausted=reason == "budget_exhausted")
        for entry in out["generations"]:
            entry.update(resources_released=None, canonical_tracks=None,
                         owner_terminal=None, lease_identity_observed=False)
    # The one mandatory unknown represents missing historical coverage. Never
    # report zero merely because every *observed* runtime looks released.
    out["resource_attribution_unknown"] += (
        out["owner_read_unknown"] + out["invalid_matching_records"]
        + int(out["generations_truncated"])
        + sum(e["resources_released"] is None or not e["lease_identity_observed"]
              for e in out["generations"]))
    out["resource_budget_exhausted"] = time.monotonic() >= deadline
    return out
