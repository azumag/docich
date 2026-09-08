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
  - tmp/state/improve_state.json, improve lock/monitor/retry files
  - deployed git HEADs (docich + intended soviet_now gitlink)

Never emitted: secrets, tokens, raw environment, prompt/generation bodies,
HTTP headers, file contents. Error previews are truncated and redacted.

Usage: collect_diagnostics.py <soren_root>
Exit 0 with JSON on stdout on success; nonzero (no usable stdout) on crash,
in which case the gateway refuses fail-closed.
"""
import json
import os
import re
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

VALUE_REDACT_RES = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|stream[_-]?key)\s*[:=]\s*\S+"),
)


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
    details = {}
    seen_pids = {}
    for name, is_required, _category, pid_rel, _kind in WORKERS:
        rel = pid_rel or default_pid_relpath(name)
        pid, stale = _parse_pid_file(soren / rel)
        is_paused = (state_dir / f"{name}.paused").is_file()
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
                if len(unregistered) >= MAX_UNREGISTERED:
                    break
                pid, stale = _parse_pid_file(path)
                alive = pid is not None and _pid_is_active(pid)
                unregistered.append(name)
                details[name] = {
                    "required": False,
                    "pid": pid,
                    "alive": bool(alive),
                    "zombie": bool(pid is not None and _process_is_zombie(pid)),
                    "stale_pid_file": bool(stale),
                    "paused": (state_dir / f"{name}.paused").is_file(),
                    "unregistered": True,
                }
        except OSError:
            pass
    return {
        "expected": len(WORKERS),
        "running": running,
        "stopped": sorted(stopped),
        "paused": sorted(paused),
        "duplicates": sorted(duplicates),
        "zombies": sorted(zombies),
        "stale_pid_files": sorted(stale_pid_files),
        "unregistered": sorted(unregistered),
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


def _severity(workers, queues, ai, improvement):
    if workers["required_down"] or workers["required_stale"]:
        return "critical"
    if (
        workers["paused"]
        or workers["unregistered"]
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
