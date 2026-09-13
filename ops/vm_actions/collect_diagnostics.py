#!/usr/bin/env python3
"""Fixed read-only production runtime diagnostics collector.

Runs on the production VM from the deployed docich checkout (invoked only by
the owner-only VM gateway `diagnostics` operation with a fixed argv). It
observes soviet_now runtime state and prints one sanitized JSON document to
stdout. It never modifies production: no restarts, kills, cleanups, lock
removal, or writes of any kind.

Observed sources (all read-only):
  - tmp/state/*.pid pid files (+ specials from runtime_registry)
  - tmp/state/*.paused markers (supervisor pause gate)
  - tmp/state/worker_duplicates.json (supervisor duplicate report)
  - tmp/state/.ai_generation_locks/<lane>/owner lock files
  - tmp/state/ai_stats/YYYYMMDD.jsonl structured telemetry
  - tmp/state/improve_state.json, improve lock/monitor/retry/gate markers
  - deployed git HEADs (docich + intended soviet_now gitlink)
  - fixed, known temporary shared-object filename families under /tmp plus
    same-user /proc maps/fd references; only bounded counts/bytes/booleans are
    emitted, never filenames, PIDs, mappings or file contents
  - docich program/corner state under the production state_dir
    (game_switch.json, retro_corner.json, paper_corner.json,
    paper_corner_manual.json, trading/presentation.json,
    trading/paper_improve_status.json): lifecycle statuses, timestamps and
    counters only. Announcement/script bodies, prompts and log bodies are
    never read out.
  - Soren boundary/A-B wait markers the corners gate on
    (tmp/state/corner_boundary_*.json, ab_state.json, ab_games.jsonl,
    ab_candidate/): only presence, counts, enums and mtimes; strategy/hash
    bodies and environment values are never read out.

Never emitted: secrets, tokens, raw environment, prompt/generation bodies,
HTTP headers, file contents. Error previews are truncated and redacted.

Usage: collect_diagnostics.py <soren_root>
Exit 0 with JSON on stdout on success; nonzero (no usable stdout) on crash,
in which case the gateway refuses fail-closed.
"""
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

PROD_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROD_ROOT / "src"))

from docich.runtime_backend import _pid_is_active, _process_is_zombie  # noqa: E402

import importlib.util as _importlib_util  # noqa: E402


def _load_registry():
    spec = _importlib_util.spec_from_file_location(
        "vm_runtime_registry", str(PROD_ROOT / "ops" / "vm_actions" / "runtime_registry.py")
    )
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REG = _load_registry()
DIAG_WINDOW_SEC = _REG.DIAG_WINDOW_SEC
DUPLICATES_FRESH_SEC = _REG.DUPLICATES_FRESH_SEC
KNOWN_LANES = _REG.KNOWN_LANES
LANE_GUARD_SUFFIXES = _REG.LANE_GUARD_SUFFIXES
KNOWN_INFRA_PIDFILES = _REG.KNOWN_INFRA_PIDFILES
MAX_COMPONENTS = _REG.MAX_COMPONENTS
MAX_ERROR_PREVIEW_LEN = _REG.MAX_ERROR_PREVIEW_LEN
MAX_JSON_BYTES = _REG.MAX_JSON_BYTES
MAX_JSONL_SCAN_BYTES = _REG.MAX_JSONL_SCAN_BYTES
MAX_JSONL_SCAN_LINES = _REG.MAX_JSONL_SCAN_LINES
MAX_RECENT_EVENTS = _REG.MAX_RECENT_EVENTS
MAX_UNREGISTERED = _REG.MAX_UNREGISTERED
QUEUE_STALE_SEC = _REG.QUEUE_STALE_SEC
WORKERS = _REG.WORKERS
default_pid_relpath = _REG.default_pid_relpath
required_workers = _REG.required_workers

RATE_LIMIT_RC = "79"
TMP_SO_ROOT = Path("/tmp")
TMP_SO_PATTERNS = (
    re.compile(r"^\..+-00000000\.so\Z"),
    re.compile(r"^\.bun-[A-Za-z0-9._-]+\.so\Z"),
)
TMP_SO_STALE_SEC = 6 * 60 * 60
TMP_SO_MAX_CANDIDATES = 4096
TMP_SO_MAX_PROC_FDS = 50000

VALUE_REDACT_RES = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|stream[_-]?key)\s*[:=]\s*\S+"),
)

# Fixed lifecycle records that can prove ownership of a supervisor pause
# marker.  The record/marker contracts are owned by soviet_now's
# lib/game_lifecycle.sh.  Only the fixed ownership enum is ever emitted.
LIFECYCLE_PAUSE_RECORDS = {
    "improve_daemon": ("improvement_pause.json", "improvement_marker_created"),
    "prediction_worker": ("prediction_pause.json", "improvement_marker_created"),
    "soren_loop": ("loop_pause.json", "improvement_marker_created"),
    "soviet_watchdog": ("watchdog_pause.json", "improvement_marker_created"),
}
LIFECYCLE_DEADLINE_STATUSES = frozenset({
    "boundary",
    "stop_requested",
    "stopping",
    "resume_requested",
})
PAUSE_OWNERS = ("lifecycle_owned", "operator_owned", "unknown")
UNREGISTERED_HEALTH = ("alive", "paused", "stale_only", "unknown")


def _redact_text(text, limit=MAX_ERROR_PREVIEW_LEN):
    if not isinstance(text, str):
        text = str(text)
    for rx in VALUE_REDACT_RES:
        text = rx.sub("[REDACTED]", text)
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _read_text_capped(path, limit=4096):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return None


def _read_json(path):
    try:
        raw = _read_text_capped(path, 65536)
        if not raw or not raw.strip():
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _tmp_so_name_matches(name):
    return any(pattern.fullmatch(name) for pattern in TMP_SO_PATTERNS)


def _device_inode(st):
    try:
        return (os.major(st.st_dev), os.minor(st.st_dev), int(st.st_ino))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _collect_tmp_shared_objects(
    now,
    tmp_root=TMP_SO_ROOT,
    proc_root=Path("/proc"),
    euid=None,
    max_candidates=TMP_SO_MAX_CANDIDATES,
    max_proc_fds=TMP_SO_MAX_PROC_FDS,
):
    """Count known leaked temporary .so families without exposing identities.

    This is evidence collection only, not a deletion eligibility decision. The
    reference scan is intentionally scoped to processes owned by the same uid
    as the collector. Any truncation/read failure is reflected by a false
    completion flag so callers cannot interpret missing references as proof
    that a file is safe to remove.
    """
    euid = os.geteuid() if euid is None else int(euid)
    candidates = {}
    candidate_entries = 0
    foreign_owner_entries = 0
    hardlink_entries = 0
    scan_complete = True
    try:
        entries = os.scandir(tmp_root)
    except OSError:
        entries = None
        scan_complete = False
    if entries is not None:
        try:
            with entries:
                for entry in entries:
                    if not _tmp_so_name_matches(entry.name):
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        scan_complete = False
                        continue
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    candidate_entries += 1
                    if st.st_uid != euid:
                        foreign_owner_entries += 1
                        continue
                    key = _device_inode(st)
                    if key is None:
                        scan_complete = False
                        continue
                    if key not in candidates and len(candidates) >= max_candidates:
                        scan_complete = False
                        break
                    if st.st_nlink > 1:
                        hardlink_entries += 1
                    age = max(0, int(now - st.st_mtime))
                    candidates.setdefault(key, {"size": max(0, int(st.st_size)), "age": age})
        except OSError:
            scan_complete = False

    old_keys = {key for key, item in candidates.items() if item["age"] >= TMP_SO_STALE_SEC}
    referenced = set()
    reference_scan_complete = bool(scan_complete)
    fd_entries_scanned = 0
    try:
        proc_entries = os.scandir(proc_root)
    except OSError:
        proc_entries = None
        reference_scan_complete = False
    if proc_entries is not None:
        try:
            with proc_entries:
                for proc_entry in proc_entries:
                    if not proc_entry.name.isdigit():
                        continue
                    try:
                        pst = proc_entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        reference_scan_complete = False
                        continue
                    if pst.st_uid != euid:
                        continue

                    maps_path = Path(proc_entry.path) / "maps"
                    try:
                        with open(maps_path, "r", encoding="utf-8", errors="replace") as handle:
                            for line in handle:
                                fields = line.split(None, 5)
                                if len(fields) < 5 or fields[4] == "0":
                                    continue
                                try:
                                    major_hex, minor_hex = fields[3].split(":", 1)
                                    key = (int(major_hex, 16), int(minor_hex, 16), int(fields[4]))
                                except (TypeError, ValueError):
                                    continue
                                if key in candidates:
                                    referenced.add(key)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        reference_scan_complete = False

                    fd_root = Path(proc_entry.path) / "fd"
                    try:
                        fd_entries = os.scandir(fd_root)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        reference_scan_complete = False
                        continue
                    with fd_entries:
                        for fd_entry in fd_entries:
                            fd_entries_scanned += 1
                            if fd_entries_scanned > max_proc_fds:
                                reference_scan_complete = False
                                break
                            try:
                                fst = fd_entry.stat(follow_symlinks=True)
                            except FileNotFoundError:
                                continue
                            except OSError:
                                reference_scan_complete = False
                                continue
                            key = _device_inode(fst)
                            if key in candidates:
                                referenced.add(key)
                    if fd_entries_scanned > max_proc_fds:
                        break
        except OSError:
            reference_scan_complete = False

    candidate_bytes = sum(item["size"] for item in candidates.values())
    old_candidate_bytes = sum(candidates[key]["size"] for key in old_keys)
    referenced_bytes = sum(candidates[key]["size"] for key in referenced if key in candidates)
    if reference_scan_complete:
        old_unreferenced = old_keys - referenced
        old_unreferenced_count = len(old_unreferenced)
        old_unreferenced_bytes = sum(candidates[key]["size"] for key in old_unreferenced)
    else:
        old_unreferenced_count = None
        old_unreferenced_bytes = None
    return {
        "scan_complete": bool(scan_complete),
        "reference_scan_complete": bool(reference_scan_complete),
        "stale_age_sec": TMP_SO_STALE_SEC,
        "candidate_entries": candidate_entries,
        "candidate_count": len(candidates),
        "candidate_bytes": candidate_bytes,
        "old_candidate_count": len(old_keys),
        "old_candidate_bytes": old_candidate_bytes,
        "referenced_count": len(referenced),
        "referenced_bytes": referenced_bytes,
        "old_unreferenced_count": old_unreferenced_count,
        "old_unreferenced_bytes": old_unreferenced_bytes,
        "foreign_owner_entries": foreign_owner_entries,
        "hardlink_entries": hardlink_entries,
    }


def _lifecycle_request_active(state_dir, request_id, now):
    """Prove that a lifecycle-owned marker still belongs to an active handover.

    Matching marker/record ownership alone is insufficient: a controller can
    die after pausing workers and leave both behind.  Mirror the lifecycle
    broker's fixed identity/status contract and fail closed when the request is
    expired, terminal in a non-parked state, malformed, or mismatched.  No
    identity, game name, deadline, or free-form value is returned to callers.
    """
    lifecycle = state_dir / "game_lifecycle"
    request = _read_json(lifecycle / "request.json")
    ack = _read_json(lifecycle / "ack.json")
    if not isinstance(request, dict) or not isinstance(ack, dict):
        return False
    if request.get("schema") != 1 or ack.get("schema") != 1:
        return False
    if request.get("request_id") != request_id or ack.get("request_id") != request_id:
        return False
    for field in ("game", "generation", "deadline_epoch", "deadline_at"):
        if field not in request or field not in ack or ack.get(field) != request.get(field):
            return False
    status = ack.get("status")
    if status == "stopped":
        # The broker intentionally parks a stopped bridge until fresh-start.
        return True
    if status not in LIFECYCLE_DEADLINE_STATUSES:
        return False
    try:
        deadline = float(request.get("deadline_epoch"))
    except (TypeError, ValueError):
        return False
    return math.isfinite(deadline) and deadline > float(now)


def _pause_owner(state_dir, name, now):
    """Classify one existing pause marker without exposing marker contents."""
    marker = state_dir / f"{name}.paused"
    raw = _read_text_capped(marker, 512)
    if raw is None:
        return "unknown"
    value = raw.strip()
    if value.startswith("lifecycle:"):
        request_id = value[len("lifecycle:"):].strip()
        spec = LIFECYCLE_PAUSE_RECORDS.get(name)
        if not request_id or spec is None:
            return "unknown"
        record_name, owner_field = spec
        record = _read_json(state_dir / "game_lifecycle" / record_name)
        if (
            isinstance(record, dict)
            and record.get("request_id") == request_id
            and record.get(owner_field) is True
            and _lifecycle_request_active(state_dir, request_id, now)
        ):
            return "lifecycle_owned"
        return "unknown"
    try:
        marker_data = json.loads(value)
    except (TypeError, ValueError):
        marker_data = None
    if (
        isinstance(marker_data, dict)
        and marker_data.get("paused") is True
        and marker_data.get("source") == "webui"
    ):
        return "operator_owned"
    return "unknown"


def _parse_pid_file(path):
    """Return (pid_or_None, stale_bool). Stale = file exists but unusable/dead."""
    raw = _read_text_capped(path, 256)
    if raw is None:
        return None, False
    first = raw.strip().split()
    pid = None
    for token in first:
        if token.isdigit():
            pid = int(token)
            break
    if pid is None:
        return None, True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None, True
    except PermissionError:
        return pid, False
    except OSError:
        return None, True
    if _process_is_zombie(pid):
        return pid, False
    return pid, False


def _collect_workers(soren, now):
    state_dir = soren / "tmp" / "state"
    required = set(required_workers())
    running = 0
    stopped = []
    paused = []
    duplicates = []
    zombies = []
    stale_pid_files = []
    unregistered = []
    pause_ownership = {key: 0 for key in PAUSE_OWNERS}
    unregistered_health = {key: 0 for key in UNREGISTERED_HEALTH}
    details = {}
    seen_pids = {}
    for name, is_required, _category, pid_rel, _kind in WORKERS:
        rel = pid_rel or default_pid_relpath(name)
        pid, stale = _parse_pid_file(soren / rel)
        is_paused = (state_dir / f"{name}.paused").is_file()
        pause_owner = _pause_owner(state_dir, name, now) if is_paused else None
        alive = pid is not None and _pid_is_active(pid)
        zombie = pid is not None and _process_is_zombie(pid)
        if alive:
            running += 1
        if zombie:
            zombies.append(name)
        if stale:
            stale_pid_files.append(name)
        if is_paused:
            paused.append(name)
            pause_ownership[pause_owner] += 1
        elif not alive:
            stopped.append(name)
        if alive:
            seen_pids.setdefault(pid, []).append(name)
        details[name] = {
            "required": bool(is_required),
            "pid": pid,
            "alive": bool(alive),
            "zombie": bool(zombie),
            "stale_pid_file": bool(stale),
            "paused": bool(is_paused),
            "pause_owner": pause_owner,
        }
    dup_report = _read_json(state_dir / "worker_duplicates.json")
    if isinstance(dup_report, dict):
        age = now - int(dup_report.get("updated_at") or 0)
        entries = dup_report.get("duplicates")
        if age <= DUPLICATES_FRESH_SEC and isinstance(entries, list):
            for item in entries:
                if isinstance(item, dict) and item.get("name") in details:
                    duplicates.append(item["name"])
    for pid, names in seen_pids.items():
        if len(names) > 1:
            for name in names:
                if name not in duplicates:
                    duplicates.append(name)
    if state_dir.is_dir():
        try:
            known_rels = {entry[3] or default_pid_relpath(entry[0]) for entry in WORKERS}
            for path in sorted(state_dir.glob("*.pid")):
                rel = f"tmp/state/{path.name}"
                if rel in known_rels:
                    continue
                name = path.stem
                pid, stale = _parse_pid_file(path)
                alive = pid is not None and _pid_is_active(pid)
                record = {
                    "required": False,
                    "pid": pid,
                    "alive": bool(alive),
                    "zombie": bool(pid is not None and _process_is_zombie(pid)),
                    "stale_pid_file": bool(stale),
                    "paused": (state_dir / f"{name}.paused").is_file(),
                }
                if rel in KNOWN_INFRA_PIDFILES:
                    record["infra"] = True
                    details[name] = record
                    continue
                if len(unregistered) >= MAX_UNREGISTERED:
                    continue
                record["unregistered"] = True
                unregistered.append(name)
                if record["alive"]:
                    health = "alive"
                elif record["paused"]:
                    health = "paused"
                elif record["stale_pid_file"]:
                    health = "stale_only"
                else:
                    health = "unknown"
                record["unregistered_health"] = health
                unregistered_health[health] += 1
                details[name] = record
        except OSError:
            pass
    return {
        "expected": len(WORKERS),
        "running": running,
        "stopped": sorted(stopped),
        "paused": sorted(paused),
        "pause_ownership": pause_ownership,
        "duplicates": sorted(duplicates),
        "zombies": sorted(zombies),
        "stale_pid_files": sorted(stale_pid_files),
        "unregistered": sorted(unregistered),
        "unregistered_health": unregistered_health,
        "required_down": sorted(n for n in stopped if n in required),
        "required_stale": sorted(n for n in stale_pid_files if n in required),
        "details": details,
    }


def _parse_lock_owner(owner_path):
    """Parsequeue owner file. Never returns the owner token, only liveness facts."""
    raw = _read_text_capped(owner_path, 1024)
    if raw is None:
        return None
    pid = None
    label = ""
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "pid" and value.isdigit():
            pid = int(value)
        elif key == "label":
            label = _redact_text(value, 80)
    return {"pid": pid, "label": label}


def _collect_queues(soren, now):
    base = soren / "tmp" / "state" / ".ai_generation_locks"
    lanes = {}
    stale_locks = 0
    try:
        present = sorted(p.name for p in base.iterdir()) if base.is_dir() else []
    except OSError:
        present = []
    for lane in sorted(set(list(KNOWN_LANES) + present)):
        if any(lane.endswith(suffix) for suffix in LANE_GUARD_SUFFIXES):
            continue
        lock_dir = base / lane
        owner_path = lock_dir / "owner"
        locked = lock_dir.is_dir()
        info = {"locked": locked}
        if locked:
            try:
                age = int(now - lock_dir.stat().st_mtime)
            except OSError:
                age = -1
            owner = _parse_lock_owner(owner_path)
            owner_pid = owner["pid"] if owner else None
            alive = bool(owner_pid is not None and _pid_is_active(owner_pid))
            stale = (not alive) or (age >= 0 and age > QUEUE_STALE_SEC)
            if stale:
                stale_locks += 1
            info.update(
                {
                    "owner_alive": alive,
                    "owner_pid": owner_pid,
                    "age_sec": age,
                    "stale_suspected": bool(stale),
                }
            )
            if owner and owner["label"]:
                info["owner_label"] = owner["label"]
        lanes[lane] = info
    return {"lanes": lanes, "stale_locks": stale_locks}


def _iter_jsonl(paths, max_lines, max_bytes):
    lines = []
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= 0:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                if size > max_bytes:
                    handle.seek(max(0, size - max_bytes))
                    handle.readline()
                for line in handle:
                    lines.append(line)
                    if len(lines) >= max_lines:
                        return lines
        except OSError:
            continue
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return lines


def _split_agent(agent):
    if not agent or ":" not in agent:
        return "unknown", _redact_text(agent or "unknown", 80)
    provider, _, model = agent.partition(":")
    return _redact_text(provider or "unknown", 40), _redact_text(model or "unknown", 80)


def _collect_ai(soren, now):
    stats_dir = soren / "tmp" / "state" / "ai_stats"
    window_start = now - DIAG_WINDOW_SEC
    days = {time.strftime("%Y%m%d", time.localtime(window_start)), time.strftime("%Y%m%d", time.localtime(now))}
    paths = [stats_dir / f"{day}.jsonl" for day in sorted(days)]
    attempts = successes = failures = rate_limits = winners = 0
    all_failed = queue_giveups = gate_giveups = 0
    by_label = {}
    recent = []
    malformed = 0
    for line in _iter_jsonl(paths, MAX_JSONL_SCAN_LINES, MAX_JSONL_SCAN_BYTES):
        try:
            event = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if not isinstance(event, dict):
            malformed += 1
            continue
        try:
            ts = int(event.get("ts") or 0)
        except (ValueError, TypeError):
            malformed += 1
            continue
        if ts < window_start or ts > now + 60:
            continue
        kind = str(event.get("event") or "")
        label = _redact_text(str(event.get("label") or "unknown"), 80)
        agent = str(event.get("agent") or "")
        rc = str(event.get("rc") or "")
        entry = by_label.setdefault(label, {"fail": 0, "winner": 0, "agents": set(), "all_failed": 0})
        if kind == "attempt":
            attempts += 1
            continue
        if kind == "ok":
            successes += 1
            continue
        if kind == "fail":
            failures += 1
            entry["fail"] += 1
            entry["agents"].add(agent)
            if rc == RATE_LIMIT_RC:
                rate_limits += 1
        elif kind == "winner":
            winners += 1
            entry["winner"] += 1
            entry["agents"].add(agent)
        elif kind == "all_failed":
            all_failed += 1
            entry["all_failed"] += 1
        elif kind == "queue_giveup":
            queue_giveups += 1
        elif kind == "gate_giveup":
            gate_giveups += 1
        else:
            continue
        provider, model = _split_agent(agent)
        item = {
            "ts": ts,
            "component": label,
            "provider": provider,
            "model": model,
            "event": kind,
            "rc": _redact_text(rc, 16),
        }
        error = event.get("error")
        if error:
            item["error_preview"] = _redact_text(error)
        recent.append(item)
    recent = recent[-MAX_RECENT_EVENTS:]
    fallback_ok = 0
    for entry in by_label.values():
        if entry["fail"] > 0 and entry["winner"] > 0 and len(entry["agents"]) > 1:
            fallback_ok += 1
    anomalous = {}
    for label in sorted(by_label):
        entry = by_label[label]
        if entry["fail"] > 0 or entry["all_failed"] > 0:
            anomalous[label] = {
                "failures": entry["fail"],
                "all_failed": entry["all_failed"],
                "agents": sorted(a for a in entry["agents"] if a)[:5],
            }
            if len(anomalous) >= MAX_COMPONENTS:
                break
    return {
        "window_sec": DIAG_WINDOW_SEC,
        "attempts": attempts,
        "successes": successes,
        "failures": failures,
        "rate_limits": rate_limits,
        "winners": winners,
        "fallbacks": fallback_ok,
        "all_failed": all_failed,
        "queue_giveups": queue_giveups,
        "gate_giveups": gate_giveups,
        "malformed_lines": malformed,
        "anomalous_components": anomalous,
        "recent_events": recent,
    }


def _parse_epoch(value):
    try:
        number = int(value)
    except (ValueError, TypeError):
        return None
    return number if number > 0 else None


def _collect_improvement(soren, now):
    state_dir = soren / "tmp" / "state"
    state = _read_json(state_dir / "improve_state.json") or {}
    status = str(state.get("status") or "unknown")
    pid = state.get("pid")
    pid = pid if isinstance(pid, int) and pid > 0 else None
    pid_alive = bool(pid is not None and _pid_is_active(pid))
    started = _parse_epoch(state.get("started_at"))
    updated = _parse_epoch(state.get("updated_at"))
    lock_present = (soren / "tmp" / "improve.lock").is_file()
    monitor = _read_json(state_dir / "improve_monitor_status.json") or {}
    backoff_path = state_dir / "rate_limit_backoff"
    try:
        backing_off = backoff_path.is_file()
        backoff_age = int(now - backoff_path.stat().st_mtime) if backing_off else -1
    except OSError:
        backing_off, backoff_age = False, -1
    retry_batch = state_dir / "improve_retry_batch.json"
    try:
        retry_pending = retry_batch.is_file()
        retry_age = int(now - retry_batch.stat().st_mtime) if retry_pending else -1
        retry_bytes = int(retry_batch.stat().st_size) if retry_pending else 0
    except OSError:
        retry_pending, retry_age, retry_bytes = False, -1, 0
    running = status in ("running", "manual") and pid_alive
    stale = False
    if running and updated is not None and now - updated > 1800:
        stale = True
    if status in ("running", "manual") and not pid_alive:
        stale = True
    duration = int(now - started) if running and started else 0

    # Mirror the scheduler's stable file-backed gates without reading arbitrary
    # payloads. These fixed enums make idle retries actionable while preserving
    # the diagnostics contract: no policy evaluation, environment disclosure,
    # or runtime mutation occurs here.
    blocked_by = []
    if not running:
        if backing_off:
            blocked_by.append("rate_limit_backoff")
        if (state_dir / "peak_hour_defer").is_file():
            blocked_by.append("peak_hour_defer")
        # Soren's _improve_ab_pending intentionally treats mere presence as a
        # blocker until AB bookkeeping removes the state, including malformed
        # or aborted state.
        if (state_dir / "ab_state.json").exists():
            blocked_by.append("ab_pending")
        if (state_dir / "improve_daemon.paused").is_file():
            blocked_by.append("daemon_paused")
        if (lock_present or retry_pending) and not blocked_by:
            blocked_by.append("unknown")

    return {
        "running": bool(running),
        "status": _redact_text(status, 32),
        "pid": pid,
        "pid_alive": pid_alive,
        "started_at": started,
        "updated_at": updated,
        "duration_sec": duration,
        "phase": _redact_text(str(state.get("phase") or ""), 64),
        "stale": bool(stale),
        "lock_present": bool(lock_present),
        "backing_off": bool(backing_off),
        "backoff_age_sec": backoff_age,
        "retry_pending": bool(retry_pending),
        "retry_age_sec": retry_age,
        "retry_bytes": retry_bytes,
        "blocked_by": blocked_by,
        "monitor": {
            "checked_at": _parse_epoch(monitor.get("checked_at")),
            "status": _redact_text(str(monitor.get("status") or ""), 32),
        },
    }


def _git_head(repo, *args):
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", *args],
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        return out.decode("utf-8", "strict").strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, UnicodeError):
        return None


def _collect_meta(soren, now):
    docich_head = _git_head(PROD_ROOT, "rev-parse", "HEAD")
    if not docich_head or not re.fullmatch(r"[0-9a-f]{40}", docich_head):
        docich_head = None
    soviet_link = _git_head(PROD_ROOT, "ls-tree", "HEAD", "--", "games/soviet_now")
    soviet_head = None
    if soviet_link:
        fields = soviet_link.split()
        if len(fields) >= 3 and fields[0] == "160000" and re.fullmatch(r"[0-9a-f]{40}", fields[2]):
            soviet_head = fields[2]
    return {
        "generated_at": now,
        "window_sec": DIAG_WINDOW_SEC,
        "soren_root_exists": soren.is_dir(),
        "docich_head": docich_head,
        "soviet_head": soviet_head,
    }


# Corner/program state filenames under the docich production state_dir. These
# are the only files read by _collect_programs; announcement/script bodies are
# deliberately never surfaced (statuses, timestamps and counters only).
CORNER_STATE_FILES = {
    "game_switch": "game_switch.json",
    "retro_corner": "retro_corner.json",
    "paper_corner": "paper_corner.json",
    "paper_corner_manual": "paper_corner_manual.json",
}


def _program_state_dir():
    """Return the docich state_dir that holds corner state (read-only).

    Derives it from the same fixed config the corner systemd units use
    (`config/docich.soren-live.toml`), constrained to the production checkout;
    falls back to the known production directory name. Arbitrary paths from
    config or arguments are never followed.
    """
    try:
        import tomllib

        raw = _read_text_capped(PROD_ROOT / "config" / "docich.soren-live.toml", 16384) or ""
        rel = tomllib.loads(raw).get("paths", {}).get("state_dir")
        if isinstance(rel, str) and rel:
            candidate = (PROD_ROOT / rel).resolve()
            root = PROD_ROOT.resolve()
            if candidate == root or str(candidate).startswith(str(root) + os.sep):
                return candidate
    except (OSError, ValueError, ImportError):
        pass
    return PROD_ROOT / "run-soren-live"


def _load_state_file(path):
    """Return (present, readable, data) for a fixed corner state file."""
    try:
        if not path.is_file():
            return False, False, None
    except OSError:
        return False, False, None
    raw = _read_text_capped(path, 65536)
    if raw is None or not raw.strip():
        return True, False, None
    try:
        data = json.loads(raw)
    except ValueError:
        return True, False, None
    if not isinstance(data, dict):
        return True, False, None
    return True, True, data


def _bounded_str(value, limit):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    return _redact_text(value, limit)


def _bounded_int(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _bounded_time(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _redact_text(value, 40)
    return None


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _file_age_sec(path, now):
    try:
        return int(now - path.stat().st_mtime)
    except OSError:
        return -1


def _collect_boundary(root, now):
    """Freshness of the confirmed cycle boundaries the corners gate on.

    The boundary is published in the Soren state dir (see
    strategy/ab_gate.sh, strategy/improve.sh, twitch_predictions.sh). Only the
    completion timestamp and its age are surfaced.
    """
    result = {}
    for kind in ("improvement", "prediction"):
        present, readable, data = _load_state_file(root / f"corner_boundary_{kind}.json")
        completed = _finite_number(data.get("completed_at")) if readable else None
        result[kind] = {
            "present": present,
            "readable": readable,
            "completed_at": completed,
            "age_sec": int(now - completed) if completed is not None else -1,
        }
    return result


def _collect_ab(soren, now):
    """Sanitized interleaved A/B presence, progress and freshness.

    A/B state gates the improvement boundary, so a stalled A/B directly
    delays the PAPER/retro corner. Only counts, enums and mtimes are exposed;
    hashes, env strings and strategy bodies are not.
    """
    state_dir = soren / "tmp" / "state"
    present, readable, data = _load_state_file(state_dir / "ab_state.json")
    candidate_dir = state_dir / "ab_candidate"
    entry = {
        "state_present": present,
        "pattern": _bounded_str(data.get("pattern"), 16) if readable else None,
        "started_at": _bounded_str(data.get("started_at"), 32) if readable else None,
        "state_age_sec": _file_age_sec(state_dir / "ab_state.json", now),
        "games_recorded": _bounded_int(data.get("games_recorded")) if readable else None,
        "game_num_start": _bounded_int(data.get("game_num_start")) if readable else None,
        "games_lines": 0,
        "games_tainted": 0,
        "games_age_sec": _file_age_sec(state_dir / "ab_games.jsonl", now),
        "last_arm": None,
        "candidate_pending": (
            (candidate_dir / "meta.json").is_file()
            and (candidate_dir / "strategy.py").is_file()
        ),
    }
    lines = tainted = 0
    last_arm = None
    for line in _iter_jsonl([state_dir / "ab_games.jsonl"], MAX_JSONL_SCAN_LINES, MAX_JSONL_SCAN_BYTES):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        lines += 1
        if row.get("tainted") is True:
            tainted += 1
        if isinstance(row.get("arm"), str):
            last_arm = row["arm"]
    entry["games_lines"] = lines
    entry["games_tainted"] = tainted
    entry["last_arm"] = _bounded_str(last_arm, 8)
    return entry


def _project_corner_state(data):
    reports = data.get("reports")
    announcements = None
    if isinstance(reports, dict):
        announcements = {
            "total": len(reports),
            "overlay": sum(
                1 for item in reports.values()
                if isinstance(item, dict) and item.get("overlay") is True
            ),
            "speech": sum(
                1 for item in reports.values()
                if isinstance(item, dict) and item.get("speech") is True
            ),
        }
    improve = data.get("improve_job") if isinstance(data.get("improve_job"), dict) else None
    improve_job = None
    if improve is not None:
        improve_job = {
            "spawned": improve.get("spawned") if isinstance(improve.get("spawned"), bool) else None,
            "date": _bounded_str(improve.get("date"), 16),
            "error": _bounded_str(improve.get("error"), 160),
        }
    return {
        "status": _bounded_str(data.get("status"), 32),
        "date": _bounded_str(data.get("date"), 16),
        "game": _bounded_str(data.get("game"), 64),
        "previous_game": _bounded_str(data.get("previous_game"), 64),
        "requested_at": _bounded_time(data.get("requested_at")),
        "started_at": _bounded_time(data.get("started_at")),
        "ends_at": _bounded_time(data.get("ends_at")),
        "completed_at": _bounded_time(data.get("completed_at")),
        "last_error": _bounded_str(data.get("last_error"), 200),
        "announcements": announcements,
        "improve_job": improve_job,
    }


def _collect_paper_improve_status(state_dir, now):
    path = state_dir / "trading" / "paper_improve_status.json"
    present, readable, data = _load_state_file(path)
    entry = {
        "present": present,
        "readable": readable,
        "age_sec": _file_age_sec(path, now),
    }
    if not readable:
        return entry
    progress = _bounded_int(data.get("progress"))
    if progress is not None:
        progress = max(0, min(100, progress))
    changed = data.get("changed") if isinstance(data.get("changed"), bool) else None
    entry.update(
        {
            "status": _bounded_str(data.get("status"), 32),
            "phase": _bounded_str(data.get("phase"), 32),
            "progress": progress,
            "detail": _bounded_str(data.get("detail"), 160),
            "started_at": _bounded_time(data.get("started_at")),
            "updated_at": _bounded_time(data.get("updated_at")),
            "completed_at": _bounded_time(data.get("completed_at")),
            "changed": changed,
        }
    )
    return entry


def _collect_corner_files(state_dir, payload, now):
    present, readable, data = _load_state_file(state_dir / CORNER_STATE_FILES["game_switch"])
    entry = {"present": present, "readable": readable}
    if readable:
        active = data.get("active") if isinstance(data.get("active"), dict) else {}
        last = data.get("last_result") if isinstance(data.get("last_result"), dict) else {}
        entry.update(
            {
                "phase": _bounded_str(data.get("phase"), 32),
                "operation": _bounded_str(data.get("operation"), 32),
                "active_game": _bounded_str(active.get("game"), 64),
                "active_generation": _bounded_int(active.get("generation")),
                "next_generation": _bounded_int(data.get("next_generation")),
                "revision": _bounded_int(data.get("revision")),
                "last_status": _bounded_str(last.get("status"), 32),
                "last_error_code": _bounded_str(last.get("error_code"), 64),
                "last_to_game": _bounded_str(last.get("to_game"), 64),
                "updated_at": _bounded_str(data.get("updated_at"), 40),
            }
        )
    payload["game_switch"] = entry

    for name in ("retro_corner", "paper_corner", "paper_corner_manual"):
        present, readable, data = _load_state_file(state_dir / CORNER_STATE_FILES[name])
        entry = {"present": present, "readable": readable}
        if readable:
            entry.update(_project_corner_state(data))
        payload[name] = entry

    present, readable, data = _load_state_file(state_dir / "trading" / "presentation.json")
    entry = {"present": present, "readable": readable}
    if readable:
        entry.update(
            {
                "mode": _bounded_str(data.get("mode"), 16),
                "updated_at": _bounded_time(data.get("updated_at")),
            }
        )
    payload["presentation"] = entry
    payload["paper_improve"] = _collect_paper_improve_status(state_dir, now)


def _collect_programs(state_dir, soren, now):
    """Sanitized corner/program lifecycle plus boundary and A/B wait state.

    Combines the docich state_dir corner files with the Soren boundary/A-B
    markers the corners gate on. It never emits announcement text, script
    bodies, request payloads, environment values or file paths, and never
    mutates any file.
    """
    state_dir = Path(state_dir)
    payload = {
        "state_dir_found": state_dir.is_dir(),
        "game_switch": {"present": False, "readable": False},
        "retro_corner": {"present": False, "readable": False},
        "paper_corner": {"present": False, "readable": False},
        "paper_corner_manual": {"present": False, "readable": False},
        "presentation": {"present": False, "readable": False},
        "paper_improve": {"present": False, "readable": False, "age_sec": -1},
    }
    if state_dir.is_dir():
        _collect_corner_files(state_dir, payload, now)
    soren = Path(soren)
    payload["boundary"] = _collect_boundary(soren / "tmp" / "state", now)
    payload["ab"] = _collect_ab(soren, now)
    return payload


def _count_map_matches(items, counts, keys):
    if not isinstance(items, list) or not isinstance(counts, dict):
        return False
    total = 0
    for key in keys:
        value = counts.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
        total += value
    return total == len(items)


def _paused_workers_actionable(workers):
    paused = workers.get("paused") if isinstance(workers, dict) else None
    if not isinstance(paused, list) or not paused:
        return bool(paused)
    counts = workers.get("pause_ownership")
    if not _count_map_matches(paused, counts, PAUSE_OWNERS):
        return True
    return counts.get("unknown", 0) > 0


def _unregistered_workers_actionable(workers):
    unregistered = workers.get("unregistered") if isinstance(workers, dict) else None
    if not isinstance(unregistered, list) or not unregistered:
        return bool(unregistered)
    counts = workers.get("unregistered_health")
    if not _count_map_matches(unregistered, counts, UNREGISTERED_HEALTH):
        return True
    return any(counts.get(key, 0) > 0 for key in ("alive", "paused", "unknown"))


def _severity(workers, queues, ai, improvement):
    if workers["required_down"] or workers["required_stale"]:
        return "critical"
    if (
        _paused_workers_actionable(workers)
        or _unregistered_workers_actionable(workers)
        or workers["duplicates"]
        or workers["zombies"]
        or queues["stale_locks"] > 0
        or ai["all_failed"] > 0
        or ai["queue_giveups"] > 0
        or improvement["stale"]
        or improvement["retry_pending"]
    ):
        return "warn"
    return "ok"


def main(argv):
    if len(argv) != 2:
        print("usage: collect_diagnostics.py <soren_root>", file=sys.stderr)
        return 2
    now = int(time.time())
    soren = Path(argv[1])
    workers = _collect_workers(soren, now)
    queues = _collect_queues(soren, now)
    ai = _collect_ai(soren, now)
    improvement = _collect_improvement(soren, now)
    payload = {
        "status": _severity(workers, queues, ai, improvement),
        "meta": _collect_meta(soren, now),
        "workers": workers,
        "queues": {**queues, "queue_giveups_15m": ai["queue_giveups"]},
        "ai": {
            **ai,
            "attempts_15m": ai["attempts"],
            "failures_15m": ai["failures"],
            "rate_limits_15m": ai["rate_limits"],
            "fallbacks_15m": ai["fallbacks"],
            "all_failed_15m": ai["all_failed"],
        },
        "improvement": improvement,
        "corners": _collect_programs(_program_state_dir(), soren, now),
        "storage_artifacts": _collect_tmp_shared_objects(now),
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        payload["ai"]["recent_events"] = []
        payload["workers"]["details"] = {}
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if len(text.encode("utf-8")) > MAX_JSON_BYTES:
            payload["ai"]["anomalous_components"] = {}
            text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))