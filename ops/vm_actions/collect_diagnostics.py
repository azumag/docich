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
  - fixed-category storage attribution for OpenCode DB/WAL/SHM, Soren
    logs/tmp/runtime caches, strategy archive, Git metadata, and VOICEVOX;
    scans are bounded, never follow symlinks, and emit no paths or filenames
  - docich program/corner state under the production state_dir
    (game_switch.json, game-switch/requests/*.json, retro_corner.json, paper_corner.json,
    paper_corner_manual.json, trading/presentation.json,
    trading/paper_improve_status.json): lifecycle statuses, timestamps and
    counters only. Announcement/script bodies, prompts and log bodies are
    never read out. FIFO output is limited to queued count and the head's
    fixed operation/target/age fields.
  - while the retro corner is actively running Hanjuku Hero, a bounded tail
    of Soren's speech debug log is parsed in memory for Hanjuku-only queue
    outcome counters. No log lines, queue names or speech text are emitted.
  - a bounded, redacted tail (last lines only) of the NetHack agent's own log
    (state_dir/logs/agent.log, written by supervise.run_callable_loop) so a
    corner that reaches gameplay but never acts stays diagnosable. No
    unrelated log body is read.
  - bounded, redacted captures of the committed NetHack runtime's tmux
    windows (window names, the birth/process window TTY, and the agent window)
    so a corner that is active but not progressing stays diagnosable. Never
    sends tmux input.
  - Soren boundary/A-B wait markers the corners gate on
    (tmp/state/corner_boundary_*.json, ab_state.json, ab_games.jsonl,
    ab_candidate/): only presence, counts, enums and mtimes; strategy/hash
    bodies and environment values are never read out.
  - the registered chat_worker's own live environ (#882), restricted to a
    fixed 4-name allowlist (never the raw block, never any other name),
    projected through the already-reviewed
    docich.semantic_decision.diagnostics.describe(), which returns only
    backend/route/requested_model/credential-presence -- never a credential
    value; COMMENT_CLASSIFIER_BACKEND (the non-secret enum gate the docich
    classifier reads) is also reported as its plain, length-capped value. This is the one narrow, reviewed exception to "raw
    environment values are never read out" above: it is a fixed-shape
    projection of a handful of non-secret configuration strings and a
    presence boolean, the same bounded-projection contract every other
    source in this file already follows, never an environment dump.

Never emitted during normal diagnostics: secrets, tokens, raw environment,
prompt/generation bodies, HTTP headers, or file contents. Error previews are
truncated and redacted. The sole exception is an owner-prepared, short-lived
Soren91 manual evidence export: while its fixed state marker is active, this
collector returns only one bounded base64 chunk from the fixed export bundle
instead of normal diagnostics. That bundle itself is produced from a strict
Soren91 evidence allowlist for explicit manual review.

Usage: collect_diagnostics.py <soren_root>
Exit 0 with JSON on stdout on success; nonzero (no usable stdout) on crash,
in which case the gateway refuses fail-closed.
"""
import base64
import configparser
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROD_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROD_ROOT / "src"))

from docich.runtime_backend import _pid_is_active, _process_is_zombie  # noqa: E402
from docich.semantic_decision.diagnostics import describe as _describe_semantic_decision  # noqa: E402

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
CHAIN_SUMMARY_MAX_COUNT = 1000
CHAIN_SUMMARY_RE = re.compile(
    r"\Avrl=(0|[1-9][0-9]{0,3});vda=(0|[1-9][0-9]{0,3});nfs=([01]);"
    r"term=(winner|all_failed|queue_giveup|gate_giveup)\Z"
)
QUEUE_GIVEUP_DETAIL_MAX_WAIT_SEC = 86400
QUEUE_GIVEUP_DETAIL_HOLDERS = (
    "radio_prepass",
    "radio_main",
    "news",
    "jiji",
    "celebration",
    "other",
    "unknown",
)
QUEUE_GIVEUP_DETAIL_RE = re.compile(
    r"\Await=(0|[1-9][0-9]{0,4});holder="
    r"(radio_prepass|radio_main|news|jiji|celebration|other|unknown)\Z"
)
BUDGET_EXHAUSTED_DETAIL_COMPONENTS = ("radio_prepass", "radio_main")
BUDGET_EXHAUSTED_DETAIL_MAX_COUNT = 99
BUDGET_EXHAUSTED_DETAIL_MAX_LAST_BUDGET_SEC = 240
BUDGET_EXHAUSTED_DETAIL_RE = re.compile(
    r"\Aexec=(0|[1-9][0-9]?);skip=(0|[1-9][0-9]?);"
    r"last_budget=(0|[1-9][0-9]{0,2});rem=0\Z"
)
AI_COMPONENTS = (
    "radio_prepass",
    "radio_main",
    "news_spam_check",
    "comment",
    "improvement",
    "other",
)
TMP_SO_ROOT = Path("/tmp")
TMP_SO_PATTERNS = (
    re.compile(r"^\..+-00000000\.so\Z"),
    re.compile(r"^\.bun-[A-Za-z0-9._-]+\.so\Z"),
)
TMP_SO_STALE_SEC = 6 * 60 * 60
TMP_SO_MAX_CANDIDATES = 4096
TMP_SO_MAX_PROC_FDS = 50000

STORAGE_MAX_ENTRIES = 100000


def _storage_allocated_bytes(st):
    blocks = getattr(st, "st_blocks", None)
    if isinstance(blocks, int) and blocks >= 0:
        return blocks * 512
    return max(0, int(getattr(st, "st_size", 0) or 0))


def _storage_base_result():
    return {
        "present": False,
        "scan_complete": True,
        "count": 0,
        "allocated_bytes": 0,
        "symlink_entries": 0,
        "hardlink_duplicates": 0,
    }


def _storage_tree_usage(path, *, max_entries=STORAGE_MAX_ENTRIES):
    path = Path(path)
    result = _storage_base_result()
    try:
        root_st = os.lstat(path)
    except FileNotFoundError:
        return result
    except OSError:
        result["scan_complete"] = False
        return result

    result["present"] = True
    seen = set()

    def account(st):
        key = (int(st.st_dev), int(st.st_ino))
        result["count"] += 1
        if key in seen:
            result["hardlink_duplicates"] += 1
            return
        seen.add(key)
        result["allocated_bytes"] += _storage_allocated_bytes(st)

    account(root_st)
    if stat.S_ISLNK(root_st.st_mode):
        result["symlink_entries"] += 1
        result["scan_complete"] = False
        return result
    if not stat.S_ISDIR(root_st.st_mode):
        if not stat.S_ISREG(root_st.st_mode):
            result["scan_complete"] = False
        return result

    stack = [path]
    while stack:
        current = stack.pop()
        try:
            entries = os.scandir(current)
        except OSError:
            result["scan_complete"] = False
            continue
        try:
            with entries:
                for entry in entries:
                    if result["count"] >= max_entries:
                        result["scan_complete"] = False
                        return result
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        result["scan_complete"] = False
                        continue
                    account(st)
                    if stat.S_ISLNK(st.st_mode):
                        result["symlink_entries"] += 1
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(Path(entry.path))
        except OSError:
            result["scan_complete"] = False
    return result


def _storage_file_usage(path):
    path = Path(path)
    result = _storage_base_result()
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return result
    except OSError:
        result["scan_complete"] = False
        return result

    result["present"] = True
    result["count"] = 1
    result["allocated_bytes"] = _storage_allocated_bytes(st)
    if stat.S_ISLNK(st.st_mode):
        result["symlink_entries"] = 1
        result["scan_complete"] = False
    elif not stat.S_ISREG(st.st_mode):
        result["scan_complete"] = False
    return result


def _storage_db_family(db_path):
    db_path = Path(db_path)
    entries = {
        "db": _storage_file_usage(db_path),
        "wal": _storage_file_usage(Path(str(db_path) + "-wal")),
        "shm": _storage_file_usage(Path(str(db_path) + "-shm")),
    }
    return {
        "scan_complete": all(item["scan_complete"] for item in entries.values()),
        "present_count": sum(1 for item in entries.values() if item["present"]),
        "allocated_bytes": sum(item["allocated_bytes"] for item in entries.values()),
        **entries,
    }


def _collect_storage_breakdown(
    soren,
    prod_root,
    *,
    home_root=None,
    voicevox_root=Path("/opt/voicevox"),
    max_entries=STORAGE_MAX_ENTRIES,
):
    """Fixed, bounded, identity-free storage attribution.

    Categories overlap by design and must not be summed. No file content is
    read, symlinks are never followed, and partial scans are marked incomplete.
    """
    soren = Path(soren)
    prod_root = Path(prod_root)
    home = Path(home_root) if home_root is not None else soren.parent
    voicevox_root = Path(voicevox_root)
    default_db = home / ".local" / "share" / "opencode" / "opencode.db"
    worker_db = soren / "tmp" / "state" / "xdg_data" / "opencode" / "opencode.db"

    return {
        "version": 1,
        "categories_overlap": True,
        "max_entries_per_tree": int(max_entries),
        "opencode_default": _storage_db_family(default_db),
        "opencode_worker": _storage_db_family(worker_db),
        "opencode_default_total": _storage_tree_usage(default_db.parent, max_entries=max_entries),
        "opencode_worker_total": _storage_tree_usage(worker_db.parent, max_entries=max_entries),
        "soren_logs": _storage_tree_usage(soren / "logs", max_entries=max_entries),
        "soren_tmp": _storage_tree_usage(soren / "tmp", max_entries=max_entries),
        "say_queue": _storage_tree_usage(soren / "tmp" / ".say_queue", max_entries=max_entries),
        "browser_profile": _storage_tree_usage(
            soren / "tmp" / "soviet_local_chromium_profile", max_entries=max_entries
        ),
        "strategy_archive": _storage_tree_usage(
            soren / "strategy_versions_archive" / "by_hash", max_entries=max_entries
        ),
        "docich_git": _storage_tree_usage(prod_root / ".git", max_entries=max_entries),
        "soren_live_git": _storage_tree_usage(soren / ".git", max_entries=max_entries),
        "soren_persist_git": _storage_tree_usage(
            home / "soren-persist" / ".git", max_entries=max_entries
        ),
        "voicevox_root": _storage_tree_usage(voicevox_root, max_entries=max_entries),
        "voicevox_archive": _storage_file_usage(voicevox_root / "voicevox.7z.001"),
    }


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
    "soren_loop": ("loop_pause.json", "loop_marker_created"),
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


# Fixed name allowlist (#882): only these exact variable NAMES are ever read
# from a live worker's environ. Values for the two credential names never
# leave _read_allowlisted_environ; docich.semantic_decision.diagnostics.describe()
# converts them to presence-only before this module ever formats output.
# These are exactly the names docich.comment_classifier reads:
# COMMENT_CLASSIFIER_BACKEND (plain enum gate, never a credential, so its
# value is also reported as-is below) and DOCICH_JEV_ROUTE plus that route's
# own credential. The retired DOCICH_SEMANTIC_BACKEND is no longer read by
# anything, so it is deliberately not read here either.
SEMANTIC_DECISION_ENV_ALLOWLIST = (
    "DOCICH_JEV_ROUTE",
    "TYPESAFE_API_KEY",
    "DOCICH_JEV_VERCEL_API_KEY",
    "COMMENT_CLASSIFIER_BACKEND",
)
COMMENT_CLASSIFIER_BACKEND_STR_MAX = 64


def _read_allowlisted_environ(pid, names):
    """Read only the given variable NAMES from /proc/<pid>/environ.

    Returns None on any read failure (process gone, permission, non-Linux).
    Never returns the raw environ block or a name outside ``names``.
    """
    try:
        raw = (Path("/proc") / str(pid) / "environ").read_bytes()
    except (FileNotFoundError, OSError):
        return None
    wanted = set(names)
    result = {}
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        key = key.decode("utf-8", "replace")
        if key in wanted:
            result[key] = value.decode("utf-8", "replace")
    return result


def _collect_semantic_decision(workers):
    """Effective docich comment-classifier config on the live chat_worker (#882).

    Reports whether the registered chat_worker's docich classifier calls Jev
    ("backend":"jev") or runs the heuristic only, and over which route --
    never a credential value, only its presence. A missing/dead worker or an
    unreadable environ is reported as such, never guessed as "heuristic"
    (an absent observation is not evidence of a disabled backend). The gate's
    plain value is also kept as comment_classifier_backend.
    """
    detail = workers.get("details", {}).get("chat_worker") or {}
    pid = detail.get("pid")
    if not pid or not detail.get("alive"):
        return {"present": False, "readable": False}
    env = _read_allowlisted_environ(pid, SEMANTIC_DECISION_ENV_ALLOWLIST)
    if env is None:
        return {"present": True, "readable": False}
    classifier_backend = env.get("COMMENT_CLASSIFIER_BACKEND") or None
    if type(classifier_backend) is str and len(classifier_backend) > COMMENT_CLASSIFIER_BACKEND_STR_MAX:
        classifier_backend = classifier_backend[:COMMENT_CLASSIFIER_BACKEND_STR_MAX]
    return {"present": True, "readable": True, "comment_classifier_backend": classifier_backend,
            **_describe_semantic_decision(env)}


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


def _parse_chain_summary(value):
    """Parse the fixed Soren chain_summary payload without exposing free text."""
    if not isinstance(value, str):
        return None
    match = CHAIN_SUMMARY_RE.fullmatch(value)
    if match is None:
        return None
    vrl, vda, nfs, terminal = match.groups()
    vrl = int(vrl)
    vda = int(vda)
    if vrl > CHAIN_SUMMARY_MAX_COUNT or vda > CHAIN_SUMMARY_MAX_COUNT or vda > vrl:
        return None
    return vrl, vda, int(nfs), terminal


def _parse_queue_giveup_detail(value):
    """Parse the producer's fixed queue holder record, fail-closed."""
    if not isinstance(value, str):
        return None
    match = QUEUE_GIVEUP_DETAIL_RE.fullmatch(value)
    if match is None:
        return None
    wait_raw, holder = match.groups()
    wait_sec = int(wait_raw)
    if wait_sec > QUEUE_GIVEUP_DETAIL_MAX_WAIT_SEC:
        return None
    return wait_sec, holder


def _parse_budget_exhausted_detail(value):
    """Parse Soren's fixed RADIO budget-exhaustion detail, fail-closed."""
    if not isinstance(value, str):
        return None
    match = BUDGET_EXHAUSTED_DETAIL_RE.fullmatch(value)
    if match is None:
        return None
    executed, skipped, last_budget = (int(item) for item in match.groups())
    if (
        executed > BUDGET_EXHAUSTED_DETAIL_MAX_COUNT
        or skipped > BUDGET_EXHAUSTED_DETAIL_MAX_COUNT
        or last_budget > BUDGET_EXHAUSTED_DETAIL_MAX_LAST_BUDGET_SEC
    ):
        return None
    return executed, skipped, last_budget


def _ai_component_bucket(label):
    """Collapse a private/dynamic AI label into the public fixed enum."""
    normalized = str(label or "").strip().lower()
    if normalized.startswith("news:spam_check"):
        return "news_spam_check"
    if normalized.startswith(("radio", "news", "jiji", "celebration")):
        return "radio_prepass" if "prepass" in normalized else "radio_main"
    if normalized.startswith("comment"):
        return "comment"
    if normalized.startswith(("improve", "improvement", "eloop")):
        return "improvement"
    return "other"


def _collect_ai(soren, now):
    stats_dir = soren / "tmp" / "state" / "ai_stats"
    window_start = now - DIAG_WINDOW_SEC
    days = {time.strftime("%Y%m%d", time.localtime(window_start)), time.strftime("%Y%m%d", time.localtime(now))}
    paths = [stats_dir / f"{day}.jsonl" for day in sorted(days)]
    attempts = successes = failures = rate_limits = winners = 0
    all_failed = queue_giveups = gate_giveups = 0
    budget_exhausted = 0
    budget_exhausted_components = {component: 0 for component in AI_COMPONENTS}
    budget_exhausted_detail_sampled = 0
    budget_exhausted_detail_malformed = 0
    budget_exhausted_detail_missing = 0
    budget_exhausted_detail_components = {
        component: {
            "sampled": 0,
            "exec_sum": 0,
            "skip_sum": 0,
            "last_budget_min_sec": 0,
            "last_budget_max_sec": 0,
        }
        for component in BUDGET_EXHAUSTED_DETAIL_COMPONENTS
    }
    chain_summary_sampled = 0
    multi_vercel_429_chains = 0
    multi_vercel_429_non_vercel_recovered = 0
    multi_vercel_429_all_failed = 0
    queue_giveup_detail_sampled = 0
    queue_giveup_detail_malformed = 0
    queue_giveup_detail_wait_max_sec = 0
    queue_giveup_detail_holders = {holder: 0 for holder in QUEUE_GIVEUP_DETAIL_HOLDERS}
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
        if kind == "budget_exhausted":
            # Observability-only fixed counters. Never publish this event's
            # agent/provider/model/error or dynamic label into recent_events.
            component = _ai_component_bucket(label)
            budget_exhausted += 1
            budget_exhausted_components[component] += 1
            if component in BUDGET_EXHAUSTED_DETAIL_COMPONENTS:
                raw_detail = event.get("error")
                if raw_detail is None:
                    # Older events did not carry dispatch detail. Keep them
                    # compatible and distinguish absence from malformed input.
                    budget_exhausted_detail_missing += 1
                else:
                    detail = _parse_budget_exhausted_detail(raw_detail)
                    if detail is None:
                        budget_exhausted_detail_malformed += 1
                    else:
                        executed, skipped, last_budget = detail
                        budget_exhausted_detail_sampled += 1
                        row = budget_exhausted_detail_components[component]
                        row["sampled"] += 1
                        row["exec_sum"] += executed
                        row["skip_sum"] += skipped
                        if row["sampled"] == 1:
                            row["last_budget_min_sec"] = last_budget
                        else:
                            row["last_budget_min_sec"] = min(row["last_budget_min_sec"], last_budget)
                        row["last_budget_max_sec"] = max(row["last_budget_max_sec"], last_budget)
            continue
        if kind == "chain_summary":
            # The producer intentionally stores only this fixed aggregate in
            # the error field. Reject anything outside that exact grammar and
            # never add chain summaries to recent_events, where arbitrary
            # labels/models/errors could become public or evict fail evidence.
            chain = _parse_chain_summary(event.get("error"))
            if chain is not None:
                vrl, vda, non_vercel_success, terminal = chain
                chain_summary_sampled += 1
                if vrl >= 2 and vda >= 2:
                    multi_vercel_429_chains += 1
                    if non_vercel_success == 1 and terminal == "winner":
                        multi_vercel_429_non_vercel_recovered += 1
                    if terminal == "all_failed":
                        multi_vercel_429_all_failed += 1
            continue
        if kind == "queue_giveup_detail":
            # This event is emitted with a constant label and fixed grammar by
            # Soren's queue observability shim. Parse only the exact allowlist
            # and never copy the raw error/label into diagnostics or the recent
            # event sample. Malformed content is counted, not reproduced.
            detail = _parse_queue_giveup_detail(event.get("error"))
            if detail is None:
                queue_giveup_detail_malformed += 1
            else:
                wait_sec, holder = detail
                queue_giveup_detail_sampled += 1
                queue_giveup_detail_wait_max_sec = max(queue_giveup_detail_wait_max_sec, wait_sec)
                queue_giveup_detail_holders[holder] += 1
            continue
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
        "budget_exhausted": budget_exhausted,
        "budget_exhausted_components": budget_exhausted_components,
        "budget_exhausted_detail_sampled": budget_exhausted_detail_sampled,
        "budget_exhausted_detail_malformed": budget_exhausted_detail_malformed,
        "budget_exhausted_detail_missing": budget_exhausted_detail_missing,
        "budget_exhausted_detail_components": budget_exhausted_detail_components,
        "chain_summary_sampled": chain_summary_sampled,
        "multi_vercel_429_chains": multi_vercel_429_chains,
        "multi_vercel_429_non_vercel_recovered": multi_vercel_429_non_vercel_recovered,
        "multi_vercel_429_all_failed": multi_vercel_429_all_failed,
        "queue_giveup_detail_sampled": queue_giveup_detail_sampled,
        "queue_giveup_detail_malformed": queue_giveup_detail_malformed,
        "queue_giveup_detail_wait_max_sec": queue_giveup_detail_wait_max_sec,
        "queue_giveup_detail_holders": queue_giveup_detail_holders,
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


def _git_text(repo, *args):
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
    docich_head = _git_text(PROD_ROOT, "rev-parse", "HEAD")
    if not docich_head or not re.fullmatch(r"[0-9a-f]{40}", docich_head):
        docich_head = None
    soviet_link = _git_text(PROD_ROOT, "ls-tree", "HEAD", "--", "games/soviet_now")
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


# Owned submodule allowlist for tracked-drift attribution. This MUST stay
# identical to the gateway's ``OWNED_SUBMODULES`` contract; a regression test
# asserts the two mappings are equal. Only fixed categories/counters are ever
# emitted for drift -- never paths, file names, diffs, bytes or raw exception
# text.
OWNED_SUBMODULES = {
    "games/soviet_now": "https://github.com/azumag/soviet_now.git",
    "games/hanjuku-sfc-speedrun": "https://github.com/azumag/hanjuku-sfc-speedrun.git",
}

TRACKED_DRIFT_CATEGORIES = (
    "parent_tracked_dirty",
    "owned_submodule_head_mismatch",
    "owned_submodule_tracked_dirty",
    "owned_submodule_missing_or_invalid",
)


def classify_tracked_drift(scan_complete, parent_dirty, submodule_drift):
    """Reduce tracked-drift probes to fixed booleans/counters only.

    ``submodule_drift`` is an iterable of per-submodule probe dicts (or ``None``
    when an allowlisted path is simply absent from this commit). An incomplete
    scan makes ``unknown`` and ``drift_detected`` explicit, so a failed probe is
    never reported as a clean zero (#412).
    """
    probes = [item for item in submodule_drift if item]
    missing = any(item.get("missing_or_invalid") for item in probes)
    head_mismatch = any(item.get("head_mismatch") for item in probes)
    tracked_dirty = any(item.get("tracked_dirty") for item in probes)
    scan_complete = bool(scan_complete)
    result = {
        "scan_complete": int(scan_complete),
        "parent_tracked_dirty": int(bool(parent_dirty)),
        "owned_submodule_head_mismatch": int(head_mismatch),
        "owned_submodule_tracked_dirty": int(tracked_dirty),
        "owned_submodule_missing_or_invalid": int(missing),
        "unknown": int(not scan_complete),
    }
    result["drift_detected"] = int(
        not scan_complete or any(result[name] for name in TRACKED_DRIFT_CATEGORIES)
    )
    return result


def _declared_submodule_url(root, path):
    """Return the ``.gitmodules`` url for ``path`` (``""`` if absent/unreadable)."""
    try:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(root / ".gitmodules", encoding="utf-8")
    except (OSError, configparser.Error):
        return ""
    for section in parser.sections():
        if parser.get(section, "path", fallback="") == path:
            return parser.get(section, "url", fallback="") or ""
    return ""


def _probe_tracked_drift_submodule(root, path):
    """Return ``(probe, complete)`` for one allowlisted submodule.

    ``probe`` is ``None`` when the path is not a gitlink in ``HEAD`` (nothing to
    attribute), a fixed-category dict otherwise. ``complete=False`` marks a scan
    that could not be finished and must become ``unknown``.
    """
    probe = {"missing_or_invalid": False, "head_mismatch": False, "tracked_dirty": False}
    link = _git_text(root, "ls-tree", "HEAD", "--", path)
    if link is None:
        return None, False
    if not link:
        return None, True
    fields = link.split()
    if len(fields) < 3 or fields[0] != "160000" or not re.fullmatch(r"[0-9a-f]{40}", fields[2]):
        probe["missing_or_invalid"] = True
        return probe, True
    gitlink = fields[2]
    if _declared_submodule_url(root, path) != OWNED_SUBMODULES[path]:
        probe["missing_or_invalid"] = True
        return probe, True
    work = root / path
    if not work.is_dir():
        probe["missing_or_invalid"] = True
        return probe, True
    # ``git -C`` climbs to the nearest enclosing repository, so an
    # uninitialized/empty submodule directory would otherwise be probed as the
    # parent checkout. Require the submodule directory to be its own worktree
    # root before attributing a head mismatch or dirty state.
    toplevel = _git_text(work, "rev-parse", "--show-toplevel")
    if toplevel is None:
        return None, False
    if Path(toplevel).resolve() != work.resolve():
        probe["missing_or_invalid"] = True
        return probe, True
    head = _git_text(work, "rev-parse", "HEAD")
    if head is None:
        return None, False
    if head != gitlink:
        probe["head_mismatch"] = True
    porcelain = _git_text(work, "status", "--porcelain", "--untracked-files=no")
    if porcelain is None:
        return None, False
    if porcelain:
        probe["tracked_dirty"] = True
    return probe, True


def _collect_tracked_drift(root):
    """Fixed-category attribution for why ``git_clean(root)`` would be false.

    Read-only. Emits only the fixed categories/counters from
    ``TRACKED_DRIFT_CATEGORIES`` plus ``scan_complete``/``unknown``/
    ``drift_detected``. Never emits paths, file names, diffs, bytes or raw
    exception text.
    """
    scan_complete = True
    parent_dirty = False
    parent = _git_text(root, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all")
    if parent is None:
        scan_complete = False
    else:
        parent_dirty = bool(parent)
    submodule_drift = []
    for path in OWNED_SUBMODULES:
        probe, complete = _probe_tracked_drift_submodule(root, path)
        submodule_drift.append(probe)
        if not complete:
            scan_complete = False
    return classify_tracked_drift(scan_complete, parent_dirty, submodule_drift)


# Corner/program state filenames under the docich production state_dir. These
# are the only files read by _collect_programs; announcement/script bodies are
# deliberately never surfaced (statuses, timestamps and counters only).
CORNER_STATE_FILES = {
    "corner_rotation": "corner_rotation.json",
    "game_switch": "game_switch.json",
    "retro_corner": "retro_corner.json",
    "paper_corner": "paper_corner.json",
    "paper_corner_manual": "paper_corner_manual.json",
    "nethack_corner": "nethack_corner.json",
    "nethack_corner_manual": "nethack_corner_manual.json",
}

# These are the remaining sources consulted by rotation's live and retired
# adapters. Do not derive filenames from state, catalog rows or request IDs.
ROTATION_CORNER_FILES = (
    "retro_corner", "retro_corner_manual", "paper_corner", "paper_corner_manual",
    "soren91_corner", "soren91_corner_manual", "nethack_corner", "nethack_corner_manual",
)
ROTATION_IMPROVE_GAMES = (
    "gnurobots", "ninvaders", "nsnake", "bastet", "moon-buggy",
    "pacman4console", "nethack", "hanjuku-hero", "soren91",
)
ROTATION_STATUSES = frozenset({
    "idle", "waiting", "starting", "active", "restoring", "preparing",
    "recovery_required", "failed", "completed", "interrupted", "expired",
    "running", "promoted", "kept", "improved", "dry-run", "skipped", "ab-staged",
})

# Fixed end-of-corner improvement reason taxonomy. Keep in sync with
# docich.corner_improve; unknown values stay "unknown" instead of leaking
# free-text result reasons or exception bodies.
ROTATION_IMPROVE_REASON_CODES = frozenset({
    "state-read", "corner-window", "gate-disabled", "llm-call", "llm-rc",
    "llm-empty", "llm-format", "llm-keys", "llm-values", "llm-unexpected",
    "eval", "lane-busy", "policy-promoted", "policy-incomplete", "policy-faults",
    "policy-below-margin", "policy-not-significant", "policy-identical",
    "policy-invalid", "policy-kept", "policy-eval", "ab-pending",
    "ab-incomplete", "ab-adopted", "ab-rejected", "ab-stale", "ab-invalid",
    "ab-eval", "ab-state", "ab-baseline-changed", "unexpected",
})
ROTATION_IMPROVE_PHASES = frozenset({"state", "llm", "eval", "unknown"})

# Fixed latch taxonomy of `corner_rotation.json`'s `error_kind`. Keep in sync
# with docich.corner_rotation.ERROR_KINDS (regression-tested): the exception
# text is never persisted or published, only this category.
ROTATION_ERROR_KINDS = frozenset({
    "adapter-state",
    "adapter-timestamp",
    "catalog-mismatch",
    "execution-error",
    "execution-unverified",
    "invalid-state",
    "unexpected",
})
ROTATION_PENDING_PHASES = frozenset({"selected", "dispatched"})


def _rotation_evidence_file(state_dir, relative):
    """Bounded fixed-path read; do not follow links to unrelated runtime data."""
    path = state_dir / relative
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        return True, False, None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                return True, False, None
            raw = handle.read(65537)
            if len(raw) > 65536:
                return True, False, None
            data = json.loads(raw)
            return True, isinstance(data, dict), data if isinstance(data, dict) else None
    except FileNotFoundError:
        return False, False, None
    except (OSError, ValueError):
        return True, False, None


def _rotation_enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else "unknown"


def _rotation_time(value):
    from docich.corner_rotation import timestamp, RotationError
    try:
        return timestamp(value)
    except (RotationError, ValueError, OverflowError, OSError):
        return None


def _rotation_lock_state(path):
    """Probe only an existing lock, retaining no lock and creating no files."""
    try:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            return "unknown"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return "unknown"
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "held"
            fcntl.flock(handle, fcntl.LOCK_UN)
            return "free"
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unknown"


def _collect_rotation_evidence(state_dir):
    """Evidence for an overdue wait, not permission to recover or dispatch.

    Worker health does not cover per-game post-corner improvement jobs.
    Preserve missing/failed/stale evidence so an operator can distinguish
    those waits from a raw failed PAPER record or a retired corner owner.
    """
    result = {"corners": {}, "improvements": {}}
    for name in ROTATION_CORNER_FILES:
        present, readable, raw = _rotation_evidence_file(state_dir, name + ".json")
        entry = {"present": present, "readable": readable}
        if readable:
            job = raw.get("improve_job")
            entry.update(
                status=_rotation_enum(raw.get("status"), ROTATION_STATUSES),
                completed_at=_rotation_time(raw.get("completed_at")),
                game=_rotation_enum(raw.get("game"), ROTATION_IMPROVE_GAMES),
                previous_game=_rotation_enum(raw.get("previous_game"), (*ROTATION_IMPROVE_GAMES, "sorengame")),
                improve_spawned=job.get("spawned") if isinstance(job, dict)
                and type(job.get("spawned")) is bool else None,
                recovery_required=raw.get("recovery_required")
                if type(raw.get("recovery_required")) is bool else None,
            )
        result["corners"][name] = entry
    for game in (*ROTATION_IMPROVE_GAMES, "paper"):
        relative = ("trading/paper_improve_status.json" if game == "paper"
                    else f"corner_improve_{game}.json")
        lock_name = "paper-improve.lock" if game == "paper" else f"corner-improve-{game}.lock"
        present, readable, raw = _rotation_evidence_file(state_dir, relative)
        entry = {"present": present, "readable": readable,
                 "lock": _rotation_lock_state(state_dir / "locks" / lock_name)}
        if readable:
            entry.update(
                status=_rotation_enum(raw.get("status"), ROTATION_STATUSES),
                started_at=_rotation_time(raw.get("started_at")),
                completed_at=_rotation_time(raw.get("completed_at")),
                reason_code=_rotation_enum(raw.get("reason_code"), ROTATION_IMPROVE_REASON_CODES),
                phase=_rotation_enum(raw.get("phase"), ROTATION_IMPROVE_PHASES),
            )
        result["improvements"][game] = entry
    present, readable, raw = _rotation_evidence_file(state_dir, "moon_buggy_ab.json")
    ab = {"present": present, "readable": readable}
    if readable:
        status = raw.get("status")
        ab["status"] = _rotation_enum(
            status, {"staged", "running", "completed", "promoted", "kept"}
        )
        results = raw.get("results")
        ab["matches"] = len(results) if isinstance(results, list) and len(results) <= 4 else None
        ab["target_matches"] = 4
        winner = raw.get("winner")
        ab["winner"] = winner if isinstance(winner, str) and winner in {"A", "B"} else None
        means = raw.get("means")
        if isinstance(means, dict):
            for arm, key in (("A", "baseline_mean"), ("B", "candidate_mean")):
                value = means.get(arm)
                try:
                    number = float(value) if type(value) in (int, float) else None
                except (OverflowError, ValueError):
                    number = None
                ab[key] = number if number is not None and math.isfinite(number) else None
    result["moon_buggy_ab"] = ab
    return result


# Canonical and legacy user-unit names for the common corner rotation timer.
# The legacy name becomes a relative alias of the canonical unit after the
# reviewed migration; diagnostics reports the governing unit name and whether
# the legacy name currently is that alias.
CANONICAL_ROTATION_TIMER = "docich-corner-rotation.timer"
LEGACY_ROTATION_TIMER = "docich-retro-corner.timer"


def _rotation_timer_selection(unit_dir=None):
    """Return (unit, legacy_alias) without executing anything.

    ``legacy_alias`` is True only when the legacy unit is a symlink resolving
    to the canonical name, False for a regular/missing legacy unit, and None
    when the unit directory cannot be inspected. Before migration the legacy
    name governs the timer; after migration the canonical name does.
    """
    directory = (
        Path(unit_dir)
        if unit_dir is not None
        else PROD_ROOT.parent / ".config" / "systemd" / "user"
    )
    legacy = directory / LEGACY_ROTATION_TIMER
    canonical = directory / CANONICAL_ROTATION_TIMER
    try:
        legacy_is_link = legacy.is_symlink()
        canonical_regular = canonical.is_file() and not canonical.is_symlink()
    except OSError:
        return LEGACY_ROTATION_TIMER, None
    if legacy_is_link:
        try:
            alias_ok = os.readlink(legacy) == CANONICAL_ROTATION_TIMER
        except OSError:
            return LEGACY_ROTATION_TIMER, None
        unit = CANONICAL_ROTATION_TIMER if (alias_ok or canonical_regular) else LEGACY_ROTATION_TIMER
        return unit, alias_ok
    if canonical_regular:
        return CANONICAL_ROTATION_TIMER, False
    return LEGACY_ROTATION_TIMER, False


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
    error_code = data.get("last_error_code")
    legacy_recovery_error = (
        isinstance(data.get("last_error"), str)
        and "canonical stateの復旧が必要です" in data.get("last_error", "")
    )
    recovery_required = (
        data.get("status") == "failed"
        and (error_code == "recovery_required" or legacy_recovery_error)
    )
    return {
        "status": _bounded_str(data.get("status"), 32),
        "date": _bounded_str(data.get("date"), 16),
        "game": _bounded_str(data.get("game"), 64),
        "previous_game": _bounded_str(data.get("previous_game"), 64),
        "requested_at": _bounded_time(data.get("requested_at")),
        "started_at": _bounded_time(data.get("started_at")),
        "ends_at": _bounded_time(data.get("ends_at")),
        "completed_at": _bounded_time(data.get("completed_at")),
        "target_matches": (
            data["target_matches"] if type(data.get("target_matches")) is int
            and 1 <= data["target_matches"] <= 100 else None
        ),
        "last_error": _bounded_str(data.get("last_error"), 200),
        "last_error_code": (
            "recovery_required" if error_code == "recovery_required" else None
        ),
        "recovery_required": recovery_required,
        "announcements": announcements,
        "improve_job": improve_job,
        "end_reason": (data.get("end_reason") if data.get("end_reason")
                       in {"game_over", "screen_stalled", "manual_saved_stop", "manual_forced_stop"} else None),
        "bot_phase": (data.get("bot_phase") if data.get("bot_phase") in {
            "transition", "name", "dialogue", "shop", "field", "field_menu",
            "battle_intro", "battle", "title_or_intro", "title", "month_menu", "concert", "event"} else None),
        "bot_actions_sent": _bounded_int(data.get("bot_actions_sent")),
        "battles_started": _bounded_int(data.get("battles_started")),
        "battles_finished": _bounded_int(data.get("battles_finished")),
        "screen_unchanged_seconds": _finite_number(data.get("screen_unchanged_seconds")),
        "bot_version": (data.get("bot_version") if data.get("bot_version") in {
            "hanjuku-script-v1", "hanjuku-chart-v2"} else None),
        "bot_chart": _project_bot_chart(data.get("bot_chart")),
        "narration": _project_counts(data.get("narration"),
                                     ("enqueued", "delivery_failed", "skipped")),
        "game_audio": _project_game_audio(data.get("game_audio")),
    }


def _project_counts(value, keys):
    if not isinstance(value, dict):
        return None
    return {key: _bounded_int(value.get(key)) for key in keys}


_SINK = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _project_game_audio(value):
    """Per-game stream evidence: status, sink name, volume percents, mute."""
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    streams = []
    for item in (value.get("streams") or [])[:4] if isinstance(value.get("streams"), list) else []:
        if not isinstance(item, dict):
            continue
        percents = item.get("volume_percent")
        streams.append({
            "sink": item.get("sink") if isinstance(item.get("sink"), str) and _SINK.match(item["sink"]) else None,
            "volume_percent": ([p for p in percents if type(p) is int and 0 <= p <= 200][:8]
                               if isinstance(percents, list) else None),
            "mute": item.get("mute") if isinstance(item.get("mute"), bool) else None,
        })
    return {
        "status": status if status in {"applied", "unverified", "no_stream", "pactl_failed", "error"} else None,
        "target_percent": _bounded_int(value.get("target_percent")),
        "streams": streams,
    }


_CHART_STEP = re.compile(r"^[0-9]{1,2}-[A-Za-z0-9]{1,4}$")
_MONTH = re.compile(r"^[0-9]{1,2}-[0-9]{1,2}$")
_VARIANT = re.compile(r"^[a-z_]{1,40}$")


def _project_bot_chart(value):
    """Allowlisted chart-progress counters only (no text, names or reasons)."""
    if not isinstance(value, dict):
        return None
    out = {}
    for key in ("chapter", "orders_launched", "orders_failed", "captured", "wins", "losses",
                "unclassified", "cards_used", "generals_lost", "gold"):
        out[key] = _bounded_int(value.get(key))
    step = value.get("chart_step")
    out["chart_step"] = step if isinstance(step, str) and _CHART_STEP.match(step) else None
    month = value.get("month")
    out["month"] = month if isinstance(month, str) and _MONTH.match(month) else None
    variant = value.get("strategy_variant")
    out["strategy_variant"] = variant if isinstance(variant, str) and _VARIANT.match(variant) else None
    out["name_entered"] = value.get("name_entered") is True
    out["name_matches"] = value.get("name_matches") is True
    return out


FIFO_OPERATIONS = frozenset({"start", "stop", "switch", "restart", "rotate", "recover"})
FIFO_MAX_RECEIPTS = 512


def _receipt_age_sec(value, now):
    if not isinstance(value, str) or not value:
        return -1
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return max(0, int(now - parsed.timestamp()))
    except (TypeError, ValueError, OverflowError, OSError):
        return -1


def _collect_game_switch_fifo(state_dir, now):
    """Return a bounded, identity-free view of the durable switch FIFO."""

    directory = Path(state_dir) / "game-switch" / "requests"
    result = {
        "present": False,
        "readable": False,
        "scan_complete": True,
        "receipt_count": 0,
        "queued_count": 0,
        "terminal_count": 0,
        "malformed_count": 0,
        "head": None,
    }
    try:
        if not directory.is_dir():
            return result
        result["present"] = True
        result["readable"] = True
        paths = sorted(directory.glob("*.json"))
    except OSError:
        result["readable"] = False
        return result
    if len(paths) > FIFO_MAX_RECEIPTS:
        result["scan_complete"] = False
        paths = paths[:FIFO_MAX_RECEIPTS]

    queued = []
    for path in paths:
        present, readable, data = _load_state_file(path)
        if not present or not readable or not isinstance(data, dict):
            result["malformed_count"] += 1
            continue
        result["receipt_count"] += 1
        status = data.get("status")
        if status == "queued":
            queued.append(data)
            result["queued_count"] += 1
        elif status in {"succeeded", "failed", "rolled_back"}:
            result["terminal_count"] += 1

    queued.sort(
        key=lambda item: (
            str(item.get("created_at", "")),
            int(item.get("generation", 0)) if isinstance(item.get("generation"), int) else 0,
        )
    )
    if queued:
        head = queued[0]
        operation = head.get("operation")
        result["head"] = {
            "status": "queued",
            "operation": operation if operation in FIFO_OPERATIONS else None,
            "target": _bounded_str(head.get("target"), 64),
            "age_sec": _receipt_age_sec(head.get("created_at"), now),
        }
    return result


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
            "reason_code": _rotation_enum(data.get("reason_code"), ROTATION_IMPROVE_REASON_CODES),
            "started_at": _bounded_time(data.get("started_at")),
            "updated_at": _bounded_time(data.get("updated_at")),
            "completed_at": _bounded_time(data.get("completed_at")),
            "changed": changed,
        }
    )
    return entry


def _rotation_policy():
    """Fixed projection of the configured dispatch policy (no secrets).

    Returns (schedule_mode, cooldown_seconds); unknown/unreadable stays None so
    the projection never guesses a policy from stale files.
    """
    try:
        import tomllib

        raw = _read_text_capped(PROD_ROOT / "config" / "docich.soren-live.toml", 16384) or ""
        section = tomllib.loads(raw).get("corner_rotation", {})
        if not isinstance(section, dict):
            return None, None
        mode = section.get("schedule_mode", "interval")
        if mode not in {"interval", "queue"}:
            return None, None
        cooldown = section.get("cooldown_hours", 24.0)
        if type(cooldown) not in (int, float) or isinstance(cooldown, bool):
            return None, None
        if not 0 < float(cooldown) <= 24 * 30:
            return None, None
        return mode, float(cooldown) * 3600.0
    except (OSError, ValueError, ImportError):
        return None, None


def _rotation_error_kind(value):
    """Project only the fixed latch category; absent stays None, unknown is unknown."""
    if value is None:
        return None
    if isinstance(value, str) and value in ROTATION_ERROR_KINDS:
        return value
    return "unknown"


def _rotation_pending_projection(state_dir, data, now):
    """Bounded identity of the latched reservation (#986), request id never emitted.

    The reservation's request UUID is compared against the fixed corner state
    files only, so an operator can tell "never started" from "already ended"
    without publishing request identity, payloads or file contents.
    """
    out = {
        "pending_corner": None,
        "pending_phase": None,
        "pending_age_sec": -1,
        "pending_owner": "absent",
        "pending_owner_status": "unknown",
    }
    pending = data.get("pending")
    if not isinstance(pending, dict):
        if pending is not None:
            out["pending_phase"] = "unknown"
        return out
    out["pending_corner"] = _bounded_str(pending.get("corner"), 64)
    out["pending_phase"] = (
        pending.get("phase") if pending.get("phase") in ROTATION_PENDING_PHASES
        else "unknown"
    )
    selected = _rotation_time(pending.get("selected_at"))
    if selected is not None:
        out["pending_age_sec"] = max(0, int(now - selected))
    request_id = pending.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        out["pending_owner"] = "unknown"
        return out
    out["pending_owner"] = "none"
    for name in ROTATION_CORNER_FILES:
        _, readable, raw = _rotation_evidence_file(state_dir, name + ".json")
        if not readable:
            # An unreadable fixed file may hold the owner; never report "none".
            out["pending_owner"] = "unknown"
            continue
        if raw.get("rotation_request_id") == request_id:
            out["pending_owner"] = name
            out["pending_owner_status"] = _rotation_enum(raw.get("status"), ROTATION_STATUSES)
            break
    return out


def _collect_corner_files(state_dir, payload, now):
    present, readable, data = _load_state_file(state_dir / CORNER_STATE_FILES["corner_rotation"])
    rotation = {"present": present, "readable": readable}
    if readable:
        # Fixed projection only: never emit seed, adapter errors or arbitrary
        # request payloads. Queued ownership remains local for operator recovery.
        status = data.get("status")
        rotation.update(
            status=status if status in {"ready", "waiting", "running", "recovery_required"} else "unknown",
            reason=_bounded_str(data.get("reason"), 64),
            next_due_at=_bounded_time(data.get("next_due_at")),
            last_seen_at=_bounded_time(data.get("last_seen_at")),
            last_slot_at=_bounded_time(data.get("last_slot_at")),
            interval_seconds=_finite_number(data.get("interval_seconds")),
            slot=_bounded_int(data.get("slot")),
            eligible_count=len(data["eligible"]) if isinstance(data.get("eligible"), list) else None,
            pending=isinstance(data.get("pending"), dict),
            error_kind=_rotation_error_kind(data.get("error_kind")),
        )
        rotation.update(_rotation_pending_projection(state_dir, data, now))
    mode, cooldown = _rotation_policy()
    rotation.update(schedule_mode=mode, cooldown_seconds=cooldown)
    payload["corner_rotation"] = rotation
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
    payload["game_switch_fifo"] = _collect_game_switch_fifo(state_dir, now)

    for name in (
        "retro_corner",
        "paper_corner",
        "paper_corner_manual",
        "nethack_corner",
        "nethack_corner_manual",
    ):
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
    payload["rotation_evidence"] = _collect_rotation_evidence(state_dir)


def _collect_programs(state_dir, soren, now):
    """Sanitized corner/program lifecycle plus boundary and A/B wait state.

    Combines the docich state_dir corner files with the Soren boundary/A-B
    markers the corners gate on. It never emits announcement text, script
    bodies, request payloads, environment values or file paths, and never
    mutates any file.
    """
    state_dir = Path(state_dir)
    timer_unit, legacy_alias = _rotation_timer_selection()
    payload = {
        "state_dir_found": state_dir.is_dir(),
        "corner_rotation": {"present": False, "readable": False},
        "corner_rotation_timer": {
            "unit": timer_unit,
            "active": _unit_is_active(timer_unit),
            "enabled": _unit_is_enabled(timer_unit),
            "legacy_alias": legacy_alias,
        },
        "game_switch": {"present": False, "readable": False},
        "game_switch_fifo": {
            "present": False,
            "readable": False,
            "scan_complete": True,
            "receipt_count": 0,
            "queued_count": 0,
            "terminal_count": 0,
            "malformed_count": 0,
            "head": None,
        },
        "retro_corner": {"present": False, "readable": False},
        "paper_corner": {"present": False, "readable": False},
        "paper_corner_manual": {"present": False, "readable": False},
        "presentation": {"present": False, "readable": False},
        "paper_improve": {"present": False, "readable": False, "age_sec": -1},
    }
    if state_dir.is_dir():
        _collect_corner_files(state_dir, payload, now)
    soren = Path(soren)
    retro = payload.get("retro_corner")
    game_switch = payload.get("game_switch")
    if (isinstance(retro, dict) and retro.get("readable") is True
            and retro.get("status") == "active"
            and retro.get("game") == "hanjuku-hero"
            and isinstance(game_switch, dict)
            and game_switch.get("active_game") == "hanjuku-hero"):
        retro["narration_playback"] = _collect_hanjuku_narration_playback(soren)
    payload["boundary"] = _collect_boundary(soren / "tmp" / "state", now)
    payload["ab"] = _collect_ab(soren, now)
    payload["soren_game"] = _collect_soren_game(soren, now)
    return payload


HANJUKU_PLAYBACK_LOG_MAX_BYTES = 128 * 1024
HANJUKU_PLAYBACK_LOG_MAX_LINES = 2048
_HANJUKU_QUEUE_BASENAME_RE = re.compile(
    r"(?P<name>[^/\s]*_hanjuku(?:_commentary|:commentary)\.(?:txt|playing))(?=$|[\s),])"
)
_HANJUKU_PLAYBACK_EMPTY = {
    "status": "unavailable",
    "sampled_bytes": None,
    "sampled_lines": None,
    "tail_truncated": None,
    "queue_started": None,
    "queue_completed": None,
    "queue_failed": None,
    "queue_unmatched_starts": None,
    "external_kill_markers": None,
    "truncated_playback_suspected": None,
    "partial_audio_retry_suppressed": None,
}


def _open_hanjuku_playback_log(soren):
    """Open one fixed log without following symlinks in the Soren path."""
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_fd = os.open(Path(soren), directory_flags)
    try:
        for part in ("tmp", ".say_queue"):
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(
            "debug.log",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
    finally:
        os.close(directory_fd)


def _collect_hanjuku_narration_playback(soren):
    """Return fixed Hanjuku playback counters from a bounded, private log tail.

    Queue basenames are used only as in-memory correlation keys. A start with
    no matching terminal event is evidence of an incomplete observation, not
    proof of cancellation: it can also be the currently playing item or fall
    across log rotation.
    """
    result = dict(_HANJUKU_PLAYBACK_EMPTY)
    fd = None
    try:
        fd = _open_hanjuku_playback_log(soren)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return result
        offset = max(0, info.st_size - HANJUKU_PLAYBACK_LOG_MAX_BYTES)
        os.lseek(fd, offset, os.SEEK_SET)
        chunks = []
        remaining = HANJUKU_PLAYBACK_LOG_MAX_BYTES + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        truncated = offset > 0 or len(raw) > HANJUKU_PLAYBACK_LOG_MAX_BYTES
        raw = raw[:HANJUKU_PLAYBACK_LOG_MAX_BYTES]
        if offset > 0:
            newline = raw.find(b"\n")
            raw = raw[newline + 1:] if newline >= 0 else b""
        lines = raw.decode("utf-8", errors="replace").splitlines()
        if len(lines) > HANJUKU_PLAYBACK_LOG_MAX_LINES:
            lines = lines[-HANJUKU_PLAYBACK_LOG_MAX_LINES:]
            truncated = True
    except (OSError, ValueError, TypeError):
        return result
    finally:
        if fd is not None:
            os.close(fd)

    active = {}
    started = completed = failed = 0
    kill_markers = truncated_suspected = retry_suppressed = 0
    for line in lines:
        match = _HANJUKU_QUEUE_BASENAME_RE.search(line)
        if match is None:
            continue
        identity = match.group("name").rsplit(".", 1)[0]
        if line.startswith("[_play_comment_queue "):
            if "再生開始:" in line:
                started += 1
                active[identity] = active.get(identity, 0) + 1
            elif "再生完了:" in line:
                completed += 1
                if active.get(identity, 0) > 1:
                    active[identity] -= 1
                else:
                    active.pop(identity, None)
            elif "再生失敗:" in line:
                failed += 1
                if active.get(identity, 0) > 1:
                    active[identity] -= 1
                else:
                    active.pop(identity, None)
        elif line.startswith("[say_enqueue ") and "label=hanjuku_commentary" in line:
            if "外部killフラグ検出" in line:
                kill_markers += 1
            if "say途中切断の疑い" in line:
                truncated_suspected += 1
            if "再試行せず完了扱い" in line and "既に" in line:
                retry_suppressed += 1

    result.update({
        "status": "available",
        "sampled_bytes": len(raw),
        "sampled_lines": len(lines),
        "tail_truncated": truncated,
        "queue_started": started,
        "queue_completed": completed,
        "queue_failed": failed,
        "queue_unmatched_starts": sum(active.values()),
        "external_kill_markers": kill_markers,
        "truncated_playback_suspected": truncated_suspected,
        "partial_audio_retry_suppressed": retry_suppressed,
    })
    return result


def _collect_soren_game(soren, now):
    """Fixed, read-only progress evidence for the active Soren match."""
    soren = Path(soren)
    path = soren / "game_state.json"
    state = _read_json(path)
    runner = _read_json(soren / "tmp/state/main_strategy_runner_active.json")
    result = {"present": path.is_file(), "readable": isinstance(state, dict),
              "state": "unknown", "age_sec": -1, "founding_seen": None,
              "make_soren_count": None, "game_count": None, "score": None,
              "pieces_count": None,
              "runner_pid": None, "runner_alive": None}
    if isinstance(state, dict):
        value = state.get("state")
        result["state"] = value if value in {"MOVE", "DROP", "WAITING", "STOP", "GAMEOVER"} else "unknown"
        count = state.get("makeSorenCount")
        result["make_soren_count"] = count if type(count) is int and 0 <= count <= 100000 else None
        score = state.get("score")
        result["score"] = score if type(score) is int and 0 <= score <= 1000000000 else None
        pieces = state.get("pieces")
        result["pieces_count"] = len(pieces) if isinstance(pieces, list) and len(pieces) <= 100000 else None
        try:
            result["age_sec"] = max(0, int(now - path.stat().st_mtime))
        except OSError:
            pass
    result["founding_seen"] = (soren / "tmp/markers/.soviet_created").exists()
    try:
        count = int((soren / "game_count.txt").read_text(encoding="utf-8").strip())
        result["game_count"] = count if 0 <= count <= 100000000 else None
    except (OSError, ValueError):
        pass
    if isinstance(runner, dict):
        pid = runner.get("pid")
        if type(pid) is int and 0 < pid <= 4194304:
            result["runner_pid"] = pid
            try:
                os.kill(pid, 0)
                result["runner_alive"] = True
            except (ProcessLookupError, PermissionError, OSError):
                result["runner_alive"] = False
    return result


# Opt-in stocks/FX paper-trading corners (src/docich/trading/markets/). Both
# markets default to enabled=false; this section only ever reads the same
# fixed config, systemd --user unit state and the worker's own health/
# experiment/report JSON that Runtime already writes. It never reads broker
# credentials, order paths, account identifiers or position prices.
MARKET_PAPER_MARKETS = ("stocks", "fx")
MARKET_PAPER_WORKER_STALE_SEC = 30
MARKET_PAPER_LINGER_USER = "ubuntu"


def _market_paper_config():
    try:
        import tomllib
    except ImportError:
        return {}
    raw = _read_text_capped(PROD_ROOT / "config" / "market-paper.toml", 16384)
    if raw is None:
        return {}
    try:
        data = tomllib.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _linger_enabled(user=MARKET_PAPER_LINGER_USER):
    """Whether `loginctl enable-linger <user>` has been set (own-user marker
    file under /var/lib/systemd/linger/, world-readable; no root needed)."""
    try:
        return Path("/var/lib/systemd/linger", user).is_file()
    except OSError:
        return None


def _systemctl_user(args, timeout=5):
    """Run `systemctl --user <args>` as this same VM-ops session. Returns
    (returncode, stdout) or None when the command cannot run at all (missing
    binary, timeout, no user session bus). A non-zero returncode with output
    (e.g. "inactive") is still a valid answer, not an error."""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(PROD_ROOT.parent),
        "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
        "LANG": "C.UTF-8",
    }
    try:
        proc = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.returncode, (proc.stdout or "").strip()


def _unit_is_active(unit):
    result = _systemctl_user(["is-active", unit])
    if result is None:
        return None
    _, out = result
    if out in ("active", "activating", "reloading"):
        return True
    if out in ("inactive", "failed", "deactivating", "unknown"):
        return False
    return None


def _unit_is_enabled(unit):
    result = _systemctl_user(["is-enabled", unit])
    if result is None:
        return None
    _, out = result
    if out in ("enabled", "enabled-runtime", "static", "alias", "linked", "linked-runtime"):
        return True
    if out in ("disabled", "masked", "masked-runtime", "not-found", "bad"):
        return False
    return None


# --- webui unit / served-UI observation (read-only) ---------------------------
#
# The webui is a long-running process, so "deployed" and "what the operator
# sees" can diverge: another instance holding the port, a unit installed from
# a different checkout, or a unit that never came back after a restart. This
# section answers only that question. Everything published is a bool / int /
# null — no path, no cmdline, no response bytes — and severity is unchanged
# (the webui is optional; see wiki/WebUI.md).

WEBUI_UNIT = "docich-webui.service"
WEBUI_PORT_DEFAULT = 8787
WEBUI_HTTP_TIMEOUT_SEC = 4
WEBUI_MAX_HTML_BYTES = 1_000_000
WEBUI_SOURCE_MAX_BYTES = 4_000_000
WEBUI_INDEX_RE = re.compile(r'INDEX_HTML = r"""(.*?)"""', re.S)


def _webui_config_port():
    """Port the service binds, read from the same config it reads."""
    try:
        import tomllib

        with open(PROD_ROOT / "config" / "docich.toml", "rb") as fh:
            cfg = tomllib.load(fh).get("webui") or {}
        port = int(cfg.get("port", WEBUI_PORT_DEFAULT))
    except Exception:
        return WEBUI_PORT_DEFAULT
    return port if 0 < port < 65536 else WEBUI_PORT_DEFAULT


def _webui_unit_dir():
    return PROD_ROOT.parent / ".config" / "systemd" / "user"


def _webui_deployed_index_digest():
    """sha256 of INDEX_HTML in the deployed src/docich/webui.py, or None."""
    path = PROD_ROOT / "src" / "docich" / "webui.py"
    try:
        if path.stat().st_size > WEBUI_SOURCE_MAX_BYTES:
            return None
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = WEBUI_INDEX_RE.search(text)
    if not match:
        return None
    return hashlib.sha256(match.group(1).encode("utf-8")).digest()


def _webui_fetch(port):
    """Bounded loopback GET of the webui index.

    Returns ``(body, error)`` with error in {None, "unreachable", "too_large"};
    never raises and never trusts a proxy (loopback only)."""
    url = f"http://127.0.0.1:{port}/"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    limit = WEBUI_MAX_HTML_BYTES
    body = bytearray()
    try:
        with opener.open(url, timeout=WEBUI_HTTP_TIMEOUT_SEC) as resp:
            length = resp.headers.get("Content-Length") if resp.headers is not None else None
            if length:
                try:
                    if int(length) > limit:
                        return b"", "too_large"
                except ValueError:
                    pass
            while len(body) <= limit:
                chunk = resp.read(min(65536, limit + 1 - len(body)))
                if not chunk:
                    break
                body += chunk
    except Exception:
        return b"", "unreachable"
    if len(body) > limit:
        return bytes(body), "too_large"
    return bytes(body), None


def _webui_unit_properties():
    """(MainPID, NRestarts) from one `systemctl --user show`, or None each."""
    main_pid = n_restarts = None
    result = _systemctl_user(
        ["show", WEBUI_UNIT, "--property=MainPID", "--property=NRestarts"], timeout=4
    )
    if result is None:
        return None, None
    _, out = result
    match = re.search(r"(?m)^MainPID=(\d+)$", out)
    if match:
        main_pid = int(match.group(1))
    match = re.search(r"(?m)^NRestarts=(\d+)$", out)
    if match:
        n_restarts = int(match.group(1))
    return main_pid, n_restarts


def _listen_inodes(port):
    """Socket inodes in TCP_LISTEN state on ``port``; None without /proc."""
    if not Path("/proc/net/tcp").exists() and not Path("/proc/net/tcp6").exists():
        return None
    inodes = set()
    for name in ("tcp", "tcp6"):
        try:
            text = Path("/proc/net", name).read_text(encoding="ascii", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 10 or parts[3] != "0A":
                continue
            try:
                if int(parts[1].rsplit(":", 1)[1], 16) != port:
                    continue
            except (IndexError, ValueError):
                continue
            inodes.add(parts[9])
    return inodes


def _pid_owns_socket(pid, inodes):
    """True when pid holds one of inodes; False / None when it cannot."""
    read_any = False
    try:
        # NOTE: Path(...).iterdir() is lazy, so a missing /proc/<pid> raises
        # FileNotFoundError on iteration, not on creation.
        for fd in Path(f"/proc/{pid}/fd").iterdir():
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            read_any = True
            if target.startswith("socket:[") and target[8:-1] in inodes:
                return True
    except FileNotFoundError:
        return False
    except OSError:
        return None
    if not read_any:
        return None
    return False


def _listener_is_unit(port, main_pid):
    """Is the unit's MainPID the process listening on the webui port?"""
    inodes = _listen_inodes(port)
    if inodes is None:
        return None
    if not inodes:
        return False
    if main_pid is None:
        return None
    if main_pid <= 0:
        return False
    return _pid_owns_socket(main_pid, inodes)


def _collect_webui():
    """Bounded observation: is the deployed UI what the operator can see?"""
    port = _webui_config_port()
    unit_file = (_webui_unit_dir() / WEBUI_UNIT).is_file()
    active = _unit_is_active(WEBUI_UNIT)
    enabled = _unit_is_enabled(WEBUI_UNIT)
    main_pid, n_restarts = _webui_unit_properties()
    listener = _listener_is_unit(port, main_pid)
    expected = _webui_deployed_index_digest()
    body, error = _webui_fetch(port)
    reachable = error in (None, "too_large")
    matches = None
    if reachable and expected is not None:
        if error == "too_large":
            matches = False
        else:
            matches = hashlib.sha256(body).digest() == expected
    return {
        "unit_file": unit_file,
        "unit_active": active,
        "unit_enabled": enabled,
        "main_pid": main_pid,
        "n_restarts": n_restarts,
        "served_port": port,
        "served_reachable": reachable,
        "served_matches_deployed": matches,
        "listener_is_unit": listener,
    }


def _market_paper_report_age_sec(sqlite_path, market, now):
    if not sqlite_path.is_file():
        return -1
    try:
        conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return -1
    try:
        conn.execute("PRAGMA query_only=1")
        row = conn.execute(
            "SELECT id FROM reports WHERE id LIKE ? ORDER BY id DESC LIMIT 1",
            (f"{market}:%",),
        ).fetchone()
    except sqlite3.Error:
        return -1
    finally:
        conn.close()
    if not row or not isinstance(row[0], str) or ":" not in row[0]:
        return -1
    try:
        end_ts = int(row[0].rsplit(":", 1)[1])
    except ValueError:
        return -1
    return int(now - end_ts) if end_ts > 0 else -1


def _collect_one_market_paper(state_dir, market, now, market_cfg, unit_dir):
    unit = f"docich-market-worker@{market}.service"
    worker_root = state_dir / "market-paper" / market
    health_path = worker_root / "health.json"
    experiment_path = worker_root / "experiment-status.json"
    sqlite_path = worker_root / "paper.sqlite3"

    h_present, h_readable, h_data = _load_state_file(health_path)
    health_status = _bounded_str(h_data.get("status"), 32) if h_readable else None
    health_age_sec = _file_age_sec(health_path, now) if h_present else -1
    market_as_of = _finite_number(h_data.get("market_as_of")) if h_readable else None
    positions = h_data.get("positions") if h_readable else None
    pending_liquidation = (
        h_data.get("pending_liquidation")
        if h_readable and isinstance(h_data.get("pending_liquidation"), bool)
        else None
    )
    risk_stopped = (
        h_data.get("risk_stopped") if h_readable and isinstance(h_data.get("risk_stopped"), bool) else None
    )
    accepted_quotes = h_data.get("accepted_quotes") if h_readable else None
    rejected_quotes = h_data.get("rejected_quotes") if h_readable else None

    e_present, e_readable, e_data = _load_state_file(experiment_path)

    corner_active = None
    if market == "stocks":
        c_present, c_readable, c_data = _load_state_file(state_dir / f"market-{market}-corner.json")
        if c_readable:
            corner_active = c_data.get("status") == "active"
        elif not c_present:
            corner_active = False

    # install_units() (manage_market_paper_units.sh) writes the shared
    # systemd *template* file (docich-market-worker@.service, no instance
    # name) once for both markets; systemd resolves the per-market instance
    # name (docich-market-worker@<market>.service, used for is-active/
    # is-enabled below) from that same template file at query/start time.
    try:
        unit_installed = (unit_dir / "docich-market-worker@.service").is_file()
    except OSError:
        unit_installed = False

    return {
        "enabled": market_cfg.get("enabled") is True,
        "mode": _bounded_str(market_cfg.get("mode"), 16),
        "unit_installed": unit_installed,
        "unit_active": _unit_is_active(unit),
        "unit_enabled": _unit_is_enabled(unit),
        "health_present": h_present,
        "health_readable": h_readable,
        "health_status": health_status,
        "health_age_sec": health_age_sec,
        "worker_stale": bool(h_present and health_age_sec > MARKET_PAPER_WORKER_STALE_SEC),
        "feed_unavailable": health_status == "price_feed_unavailable",
        "last_quote_age_sec": int(now - market_as_of) if market_as_of else -1,
        "market_as_of_raw": market_as_of,
        "pending_liquidation": pending_liquidation,
        "risk_stopped": risk_stopped,
        "positions_count": len(positions) if isinstance(positions, dict) else None,
        "accepted_quotes": _bounded_int(accepted_quotes) if isinstance(accepted_quotes, int) else None,
        "rejected_quotes": (
            [_bounded_str(s, 20) for s in rejected_quotes[:10]] if isinstance(rejected_quotes, list) else None
        ),
        "experiment_present": e_present,
        "experiment_status": _bounded_str(e_data.get("status"), 32) if e_readable else None,
        "experiment_age_sec": _file_age_sec(experiment_path, now) if e_present else -1,
        "sqlite_present": sqlite_path.is_file(),
        "last_report_age_sec": _market_paper_report_age_sec(sqlite_path, market, now),
        "corner_active": corner_active,
    }


def _collect_market_paper(state_dir, now, unit_dir=None):
    """Sanitized read-only view of the opt-in stocks/FX paper-trading
    corners. A failure anywhere degrades a field to null/false; it never
    raises, so a bug here can never break the rest of diagnostics."""
    if unit_dir is None:
        unit_dir = PROD_ROOT.parent / ".config" / "systemd" / "user"
    state_dir = Path(state_dir)
    try:
        config = _market_paper_config()
    except Exception:
        config = {}
    try:
        unit_dir_entries = sorted(p.name for p in unit_dir.iterdir())[:20] if unit_dir.is_dir() else []
    except OSError:
        unit_dir_entries = []
    payload = {
        "config_readable": bool(config),
        "linger_enabled": _linger_enabled(),
        "prod_root": str(PROD_ROOT),
        "unit_dir": str(unit_dir),
        "unit_dir_exists": unit_dir.is_dir(),
        "unit_dir_entries": unit_dir_entries,
    }
    for market in MARKET_PAPER_MARKETS:
        market_cfg = config.get(market)
        if not isinstance(market_cfg, dict):
            market_cfg = {}
        try:
            payload[market] = _collect_one_market_paper(state_dir, market, now, market_cfg, unit_dir)
        except Exception:
            payload[market] = {"error": "collect_failed"}
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


def _severity(workers, queues, ai, improvement, corners=None):
    if workers["required_down"] or workers["required_stale"]:
        return "critical"
    retro = corners.get("retro_corner") if isinstance(corners, dict) else None
    if isinstance(retro, dict) and retro.get("recovery_required") is True:
        return "warn"
    # A latched common rotation stops every automatic corner while the shared
    # plane stays healthy, so it must reach the runtime health alert (#986).
    rotation = corners.get("corner_rotation") if isinstance(corners, dict) else None
    if isinstance(rotation, dict) and rotation.get("status") == "recovery_required":
        return "warn"
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


# Fixed v1 Soren91 sent-to-sent metrics, never game acceptance or raw records.
DROP_STAGES = ('capture', 'analyze', 'ranking', 'decide', 'input', 'holdInput',
               'cooldown', 'poll', 'overlap', 'unattributed')
DROP_CALLS = DROP_STAGES[:-2]
DROP_REASONS = ('unknown-current', 'uncalibrated', 'invalid-board', 'confirm-frame',
                'preview-changed', 'board-moving', 'stable', 'stable-slow-advance',
                'non-move', 'other')
DROP_MAX_BYTES = 2 * 1024 * 1024


def _drop_number(value, integer=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and 0 <= value <= 9007199254740991
            and (not integer or type(value) is int))


def _drop_stats(values):
    if not values:
        return dict.fromkeys(('mean', 'median', 'p95', 'max', 'total'))
    values = sorted(values)
    return {k: round(v, 6) for k, v in {
        'mean': sum(values) / len(values),
        'median': values[math.ceil(len(values) * .5) - 1],
        'p95': values[math.ceil(len(values) * .95) - 1],
        'max': values[-1], 'total': sum(values),
    }.items()}


def _drop_summary(rows):
    total = sum(r['durationMs'] for r in rows)
    phases = {}
    for stage in ('interval', *DROP_STAGES):
        values = [r['durationMs'] if stage == 'interval' else r['stageMs'][stage]
                  for r in rows]
        phases[stage] = {
            **_drop_stats([v / 1000 for v in values]),
            'sharePercent': round(sum(values) / total * 100, 6) if total else None,
            'calls': sum(r['phaseCalls'][stage] for r in rows)
                     if rows and stage in DROP_CALLS else None,
        }
    return {'samples': len(rows), 'phasesSeconds': phases,
            'observations': sum(r['observations'] for r in rows),
            'holds': sum(r['holds'] for r in rows),
            'errors': sum(r['errors'] for r in rows),
            'reasonCounts': {k: sum(r['reasonCounts'][k] for r in rows) for k in DROP_REASONS}}


def _drop_profile_summary(data):
    p = data.get('dropProfile')
    if p is None:
        return {'profileStatus': 'missing'}
    if (type(data.get('schemaVersion')) is not int or data['schemaVersion'] != 1
            or not isinstance(p, dict) or type(p.get('schemaVersion')) is not int
            or p['schemaVersion'] != 1 or p.get('basis') != 'sent-to-sent'
            or p.get('acceptedDropsMeasured') is not False
            or not isinstance(p.get('session'), str)
            or not re.fullmatch(r'[a-f0-9-]{36}', p['session'])
            or p.get('capacity') != 128
            or not all(_drop_number(p.get(k), True) for k in ('totalSamples', 'evictedSamples'))
            or not isinstance(p.get('records'), list) or len(p['records']) > 128
            or p['totalSamples'] != p['evictedSamples'] + len(p['records'])):
        return {'profileStatus': 'invalid'}
    rows = p['records']
    usable = []
    for i, r in enumerate(rows, p['evictedSamples'] + 1):
        if (not isinstance(r, dict)
                or not all(_drop_number(r.get(k), True) for k in
                           ('sample', 'game', 'fromTurn', 'toTurn', 'endedAtMs',
                            'observations', 'holds', 'errors'))
                or r['sample'] != i or not _drop_number(r.get('durationMs'))
                or type(r.get('accountingValid')) is not bool
                or type(r.get('accountingErrorMs')) not in (int, float)
                or not math.isfinite(r['accountingErrorMs'])):
            return {'profileStatus': 'invalid'}
        for field, keys, integer in (('stageMs', DROP_STAGES, False),
                                     ('phaseCalls', DROP_CALLS, True),
                                     ('reasonCounts', DROP_REASONS, True)):
            if (not isinstance(r.get(field), dict)
                    or not all(_drop_number(r[field].get(k), integer) for k in keys)):
                return {'profileStatus': 'invalid'}
        error = r['durationMs'] - sum(r['stageMs'][k] for k in DROP_STAGES)
        if abs(error - r['accountingErrorMs']) > .11:
            return {'profileStatus': 'invalid'}
        if r['accountingValid'] and abs(error) <= 1 and r['durationMs'] > 0:
            usable.append(r)
    deferred = lambda r: any(r['reasonCounts'][k] for k in DROP_REASONS
                             if k not in ('stable', 'stable-slow-advance'))
    groups = {
        'withoutHold': [r for r in usable if not r['holds']],
        'withHold': [r for r in usable if r['holds']],
        'beforeTurn10': [r for r in usable if r['fromTurn'] < 10],
        'fromTurn10': [r for r in usable if r['fromTurn'] >= 10],
        'withoutDeferral': [r for r in usable if not deferred(r)],
        'withDeferral': [r for r in usable if deferred(r)],
    }
    games = sorted(set(r['game'] for r in usable))
    result = {
        'profileStatus': 'ok', 'basis': 'sent-to-sent', 'acceptedDropsMeasured': False,
        'session': p['session'], 'totalSamples': p['totalSamples'],
        'missingSamples': p['evictedSamples'], 'retainedSamples': len(rows),
        'excludedSamples': len(rows) - len(usable),
        'firstSample': rows[0]['sample'] if rows else None,
        'lastSample': rows[-1]['sample'] if rows else None,
        'firstEndedAtMs': rows[0]['endedAtMs'] if rows else None,
        'latestDropAtMs': max((r['endedAtMs'] for r in rows), default=None),
        'gameCount': len(games), 'omittedGameGroups': max(0, len(games) - 4),
        'all': _drop_summary(usable),
        'groups': {k: _drop_summary(v) for k, v in groups.items()},
        'games': [{'game': g, **_drop_summary([r for r in usable if r['game'] == g])}
                  for g in games[-4:]],
        # Bounded numeric representative, not a raw record or arbitrary map.
        'slowest': [{k: r[k] for k in ('sample', 'game', 'fromTurn', 'toTurn', 'endedAtMs')}
                    | _drop_summary([r])
                    for r in sorted(usable, key=lambda r: r['durationMs'], reverse=True)[:1]],
    }
    for key in ('updatedAtMs', 'sinceDropSentMs', 'game', 'turn', 'elapsedMs'):
        value = data.get(key)
        result[key] = value if _drop_number(value) else None
    # sinceDropSentMs is the open tail at the last flush, NOT a live stall clock.
    result['omittedComparisonGroups'] = 0
    while len(json.dumps(result).encode('utf-8')) > 24000:
        if result['games']:
            result['games'].pop(0)
            result['omittedGameGroups'] += 1
        elif result['groups']:
            result['groups'].popitem()
            result['omittedComparisonGroups'] += 1
        else:
            break
    return result



MANUAL_EVIDENCE_STATE_NAME = "soren91_manual_evidence_export.json"
MANUAL_EVIDENCE_BUNDLE_NAME = "soren91_manual_evidence_export.tar.gz"
MANUAL_EVIDENCE_STATE_MAX = 4096
MANUAL_EVIDENCE_BUNDLE_MAX = 3 * 1024 * 1024
MANUAL_EVIDENCE_CHUNK_BYTES = 24 * 1024
MANUAL_EVIDENCE_PART_CHARS = 480
MANUAL_EVIDENCE_MAX_CHUNKS = 256
MANUAL_EVIDENCE_TTL_MS = 10 * 60 * 1000
_MANUAL_EVIDENCE_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


def _collect_soren91_manual_evidence(soren, now_ms=None):
    """Return one fixed chunk only for a short-lived owner-prepared export.

    No paths come from state, and this function never writes or advances the
    cursor. Chunk selection is a separate owner-only exec action, preserving
    the diagnostics operation's read-only contract.
    """
    result = {"active": False}
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    runtime = soren / "soren91"
    state_dir = runtime / "tmp" / "state"
    state_path = state_dir / MANUAL_EVIDENCE_STATE_NAME
    bundle_path = state_dir / MANUAL_EVIDENCE_BUNDLE_NAME
    try:
        current = soren
        for part in ("soren91", "tmp", "state"):
            current = current / part
            if current.is_symlink():
                return result
        if state_path.is_symlink() or bundle_path.is_symlink():
            return result

        state_fd = os.open(state_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(state_fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MANUAL_EVIDENCE_STATE_MAX:
                return result
            raw = handle.read(MANUAL_EVIDENCE_STATE_MAX + 1)
        if len(raw) > MANUAL_EVIDENCE_STATE_MAX:
            return result
        state = json.loads(raw)
        if not isinstance(state, dict) or state.get("version") != 1:
            return result

        created = state.get("createdAtMs")
        expires = state.get("expiresAtMs")
        bundle_bytes = state.get("bundleBytes")
        chunk_bytes = state.get("chunkBytes")
        chunk_count = state.get("chunkCount")
        chunk_index = state.get("currentChunk")
        digest = state.get("bundleSha256")
        games = state.get("games")
        if (
            not isinstance(created, int)
            or not isinstance(expires, int)
            or created > now_ms + 60_000
            or expires < now_ms
            or expires - created != MANUAL_EVIDENCE_TTL_MS
            or not isinstance(bundle_bytes, int)
            or not 1 <= bundle_bytes <= MANUAL_EVIDENCE_BUNDLE_MAX
            or chunk_bytes != MANUAL_EVIDENCE_CHUNK_BYTES
            or not isinstance(chunk_count, int)
            or not 1 <= chunk_count <= MANUAL_EVIDENCE_MAX_CHUNKS
            or not isinstance(chunk_index, int)
            or not 0 <= chunk_index < chunk_count
            or not isinstance(digest, str)
            or not _MANUAL_EVIDENCE_SHA_RE.fullmatch(digest)
            or not isinstance(games, list)
            or not 1 <= len(games) <= 3
            or any(isinstance(game, bool) or not isinstance(game, int) or game < 0 for game in games)
        ):
            return result
        expected_chunks = (bundle_bytes + chunk_bytes - 1) // chunk_bytes
        if chunk_count != expected_chunks:
            return result

        bundle_fd = os.open(bundle_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(bundle_fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != bundle_bytes:
                return result
            offset = chunk_index * chunk_bytes
            expected_len = min(chunk_bytes, bundle_bytes - offset)
            handle.seek(offset)
            chunk = handle.read(expected_len)
        if len(chunk) != expected_len:
            return result
        encoded = base64.b64encode(chunk).decode("ascii")
        parts = [
            encoded[index:index + MANUAL_EVIDENCE_PART_CHARS]
            for index in range(0, len(encoded), MANUAL_EVIDENCE_PART_CHARS)
        ]
        if len(parts) > 100 or any(len(part) > MANUAL_EVIDENCE_PART_CHARS for part in parts):
            return result
        return {
            "active": True,
            "version": 1,
            "createdAtMs": created,
            "expiresAtMs": expires,
            "bundleBytes": bundle_bytes,
            "bundleSha256": digest,
            "chunkBytes": chunk_bytes,
            "chunkCount": chunk_count,
            "chunkIndex": chunk_index,
            "games": games,
            "parts": parts,
        }
    except (OSError, ValueError, TypeError, OverflowError, json.JSONDecodeError):
        return result



def _collect_soren91_drop_profile(soren):
    """One fixed regular file; no state writes, subprocesses or game observations."""
    path = soren / 'soren91' / 'tmp' / 'state' / 'soren91_loop_metrics.json'
    result = {'present': False, 'readable': False, 'profileStatus': 'unavailable'}
    try:
        # Reject symlinks in the entire path below the authorized Soren root.
        current = soren
        for part in ('soren91', 'tmp', 'state', 'soren91_loop_metrics.json'):
            current = current / part
            if current.is_symlink():
                return result
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            result['present'] = True
            if not stat.S_ISREG(info.st_mode) or info.st_size > DROP_MAX_BYTES:
                return result
            result['fileMtimeMs'] = int(info.st_mtime * 1000)
            result['fileBytes'] = info.st_size
            raw = handle.read(DROP_MAX_BYTES + 1)
            if len(raw) > DROP_MAX_BYTES:
                return result
        data = json.loads(raw)
        if not isinstance(data, dict):
            return result
        result['readable'] = True
        result.update(_drop_profile_summary(data))
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        result['profileStatus'] = 'unavailable'
    return result


NETHACK_AGENT_LOG_TAIL_BYTES = 8192
NETHACK_AGENT_LOG_LINES = 12
NETHACK_AGENT_LOG_LINE_LIMIT = 240


def _read_tail_lines(path, *, max_bytes=NETHACK_AGENT_LOG_TAIL_BYTES):
    """Read only the tail bytes of an append-only log (never the whole file)."""
    try:
        size = path.stat().st_size
        with path.open('rb') as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            raw = handle.read(max_bytes)
    except OSError:
        return None
    text = raw.decode('utf-8', errors='replace')
    return [line for line in text.splitlines() if line.strip()]


def _collect_nethack_agent_log(state_dir, now):
    """Bounded, redacted tail of the NetHack agent's own log.

    The agent loop appends one line per start/exit/error to
    ``logs/agent.log`` (supervise.run_callable_loop). A corner that reaches
    gameplay but never acts is otherwise invisible through this read-only
    channel, so emit only the last few lines, redacted and length-capped.
    No other log bodies are read.
    """
    path = Path(state_dir) / "logs" / "agent.log"
    entry = {"present": False, "readable": False, "mtime": None, "lines": []}
    if not path.is_file():
        return entry
    entry["present"] = True
    try:
        entry["mtime"] = int(path.stat().st_mtime)
    except OSError:
        return entry
    lines = _read_tail_lines(path)
    if lines is None:
        return entry
    entry["readable"] = True
    entry["lines"] = [
        _redact_text(line, NETHACK_AGENT_LOG_LINE_LIMIT)
        for line in lines[-NETHACK_AGENT_LOG_LINES:]
    ]
    return entry


NETHACK_PANE_LINES = 14
NETHACK_PANE_LINE_LIMIT = 240
_TMUX_TARGET_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")
_RUNTIME_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
# Fixed vocabularies for the NetHack boundary diag (#1015). Kept in sync with
# ``docich.adapters.nethack.CANCEL_REFUSAL_REASONS`` /
# ``PROMPT_CLASSES`` by ``test_nethack_boundary_diag.py``; duplicated here so
# this read-only collector never imports runtime code.
_BOUNDARY_DIAG_REASONS = frozenset(
    {
        "deadline_exceeded",
        "cancel_requested",
        "session_missing",
        "session_unowned",
        "process_target_absent",
        "process_window_ambiguous",
        "process_window_probe_failed",
        "capture_failed",
        "prompt_not_pending",
        "process_gone",
        "post_key_probe_failed",
        "post_key_capture_failed",
        "wait_timeout",
    }
)
_BOUNDARY_DIAG_PROMPT_CLASSES = frozenset(
    {
        "save_prompt_pending",
        "save_confirmation",
        "character_creation",
        "capture_failed",
        "unknown",
    }
)
_BOUNDARY_DIAG_OUTCOMES = frozenset({"suspended", "ended", "unknown"})


def _capture_tmux_pane(target, *, max_lines=NETHACK_PANE_LINES):
    """Read one pane's visible lines read-only; never sends input."""
    if not isinstance(target, str) or _TMUX_TARGET_RE.fullmatch(target) is None:
        return []
    try:
        proc = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", target],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return [_redact_text(line, NETHACK_PANE_LINE_LIMIT) for line in lines[-max_lines:]]


def _list_window_names(session):
    if not isinstance(session, str) or _TMUX_TARGET_RE.fullmatch(session) is None:
        return []
    try:
        proc = subprocess.run(
            ["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return [name.strip() for name in proc.stdout.splitlines() if name.strip()]


def _collect_nethack_boundary(state_dir, now):
    """Bounded, sanitized view of the active NetHack boundary diag (#1015).

    Reads only ``<state_dir>/runtimes/<runtime_id>/nethack_boundary_diag.json``
    for the *active* runtime, and projects fixed enums/booleans only: why a
    cancel was refused must be answerable without ever exposing pane text,
    paths, keys or argv.
    """
    result = {
        "present": False,
        "readable": False,
        "active_runtime": False,
        "stale_runtime": False,
        "operation": None,
        "reason": None,
        "prompt_class": None,
        "process_target_present": None,
        "process_alive": None,
        "save_signature_changed": None,
        "boundary_outcome": None,
        "generation": None,
        "age_sec": None,
    }
    try:
        data = json.loads((Path(state_dir) / "game_switch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    if not isinstance(data, dict):
        return result
    active = data.get("active")
    if not isinstance(active, dict):
        return result
    runtime_id = active.get("runtime_id")
    if not isinstance(runtime_id, str) or not _RUNTIME_ID_RE.fullmatch(runtime_id):
        return result
    result["active_runtime"] = True
    generation = active.get("generation")
    if type(generation) is int:
        result["generation"] = generation
    present, readable, diag = _load_state_file(
        Path(state_dir) / "runtimes" / runtime_id / "nethack_boundary_diag.json"
    )
    result["present"] = present
    result["readable"] = readable
    if not readable or not isinstance(diag, dict):
        return result
    operation = diag.get("operation")
    if operation in ("cancel", "wait"):
        result["operation"] = operation
    reason = diag.get("reason")
    if isinstance(reason, str) and reason in _BOUNDARY_DIAG_REASONS:
        result["reason"] = reason
    prompt_class = diag.get("prompt_class")
    if isinstance(prompt_class, str) and prompt_class in _BOUNDARY_DIAG_PROMPT_CLASSES:
        result["prompt_class"] = prompt_class
    boundary_outcome = diag.get("boundary_outcome")
    if isinstance(boundary_outcome, str) and boundary_outcome in _BOUNDARY_DIAG_OUTCOMES:
        result["boundary_outcome"] = boundary_outcome
    for key in ("process_target_present", "process_alive", "save_signature_changed"):
        value = diag.get(key)
        if isinstance(value, bool):
            result[key] = value
    recorded = diag.get("recorded_at")
    if isinstance(recorded, str):
        try:
            recorded_ts = dt.datetime.fromisoformat(recorded.replace("Z", "+00:00")).timestamp()
            if 0 <= recorded_ts <= now + 86400:
                result["age_sec"] = max(0, int(now - recorded_ts))
        except ValueError:
            pass
    diag_generation = diag.get("generation")
    if type(diag_generation) is int and diag_generation == generation:
        result["active_runtime"] = True
    else:
        # A diag left behind by an older generation must never be read as the
        # active runtime's evidence.
        result["active_runtime"] = False
        result["stale_runtime"] = True
    return result


def _collect_nethack_panes(state_dir, now):
    """Bounded read-only view of the committed NetHack runtime's tmux windows.

    A corner that is active but not progressing is otherwise invisible through
    this channel. The NetHack TTY lives in the runtime's birth/process window,
    not the presentation xterm window, so capture every window by name plus the
    resolved process and agent panes. Never sends tmux input.
    """
    result = {
        "present": False,
        "phase": None,
        "active_game": None,
        "windows": [],
        "game": [],
        "agent": [],
    }
    try:
        data = json.loads((Path(state_dir) / "game_switch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    if not isinstance(data, dict):
        return result
    result["present"] = True
    result["phase"] = _redact_text(str(data.get("phase") or ""), 32)
    active = data.get("active")
    if not isinstance(active, dict):
        return result
    result["active_game"] = _redact_text(str(active.get("game") or ""), 32)
    if active.get("game") != "nethack":
        return result
    session = active.get("adapter_session")
    game_window = active.get("game_window")
    generation = active.get("generation")
    if (
        not isinstance(session, str)
        or not isinstance(game_window, str)
        or type(generation) is not int
    ):
        return result
    agent_window = f"agent-g{generation}"
    names = _list_window_names(session)
    result["windows"] = [name for name in names if _TMUX_TARGET_RE.fullmatch(name)]
    for name in result["windows"][:4]:
        result.setdefault("all", []).append(
            {"name": name, "lines": _capture_tmux_pane(f"{session}:{name}")}
        )
    candidates = [name for name in names if name not in {game_window, agent_window}]
    if len(candidates) == 1:
        result["game"] = _capture_tmux_pane(f"{session}:{candidates[0]}")
    result["agent"] = _capture_tmux_pane(f"{session}:{agent_window}")
    return result


def main(argv):
    if len(argv) != 2:
        print("usage: collect_diagnostics.py <soren_root>", file=sys.stderr)
        return 2
    now = int(time.time())
    soren = Path(argv[1])
    manual_evidence = _collect_soren91_manual_evidence(soren, now * 1000)
    if manual_evidence.get("active"):
        # Keep the envelope intentionally tiny so the installed gateway's
        # 49 KiB diagnostics cap and 500-char per-string sanitizer remain
        # effective. Base64 is split into <=480-char list items above.
        payload = {
            "status": "ok",
            "soren91_manual_evidence": manual_evidence,
        }
        text = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        if len(text.encode("utf-8")) > MAX_JSON_BYTES:
            return 1
        sys.stdout.write(text + "\n")
        return 0
    workers = _collect_workers(soren, now)
    queues = _collect_queues(soren, now)
    ai = _collect_ai(soren, now)
    improvement = _collect_improvement(soren, now)
    corners = _collect_programs(_program_state_dir(), soren, now)
    payload = {
        "status": _severity(workers, queues, ai, improvement, corners),
        "meta": _collect_meta(soren, now),
        "tracked_drift": _collect_tracked_drift(PROD_ROOT),
        "workers": workers,
        "semantic_decision": _collect_semantic_decision(workers),
        "queues": {**queues, "queue_giveups_15m": ai["queue_giveups"]},
        "ai": {
            **ai,
            "attempts_15m": ai["attempts"],
            "failures_15m": ai["failures"],
            "rate_limits_15m": ai["rate_limits"],
            "fallbacks_15m": ai["fallbacks"],
            "all_failed_15m": ai["all_failed"],
            "budget_exhausted_15m": ai["budget_exhausted"],
        },
        "improvement": improvement,
        "corners": corners,
        "nethack_agent": _collect_nethack_agent_log(_program_state_dir(), now),
        "nethack_boundary": _collect_nethack_boundary(_program_state_dir(), now),
        "nethack_panes": _collect_nethack_panes(_program_state_dir(), now),
        "market_paper": _collect_market_paper(_program_state_dir(), now),
        "webui": _collect_webui(),
        "storage_breakdown": _collect_storage_breakdown(soren, PROD_ROOT),
        "storage_artifacts": _collect_tmp_shared_objects(now),
        "soren91_drop_profile": _collect_soren91_drop_profile(soren),
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        payload["ai"]["recent_events"] = []
        payload["workers"]["details"] = {}
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if len(text.encode("utf-8")) > MAX_JSON_BYTES:
            payload["ai"]["anomalous_components"] = {}
            text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        profile = payload["soren91_drop_profile"]
        if profile.get('profileStatus') == 'ok':
            profile['omittedComparisonGroups'] += len(profile['groups'])
            profile['omittedGameGroups'] += len(profile['games'])
            profile['groups'] = {}
            profile['games'] = []
            profile['slowest'] = []
            profile['representativeOmitted'] = True
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
