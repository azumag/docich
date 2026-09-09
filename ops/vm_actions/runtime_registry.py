#!/usr/bin/env python3
"""Single source of truth for production runtime diagnostics coverage.

This registry describes the soviet_now workers, queue lanes, and diagnostic
constants that the read-only VM diagnostics collector
(ops/vm_actions/collect_diagnostics.py) understands. It exists so a new
worker / queue lane / provider does not silently fall out of diagnostics:
the collector discovers live state by scanning, and CI checks this registry
against start_all.sh (worker names, pid-file mapping, process patterns).

Conventions mirrored from soviet_now start_all.sh (do not drift them here
without updating the collector and the CI check):
  - default pid file: tmp/state/<name>.pid holding a plain PID
  - soren_loop: tmp/.soren_loop.lock/pid
  - soviet_watchdog: tmp/state/.soviet_watchdog.lock/owner (first int token)
  - pause marker (honored generically by the supervisor): tmp/state/<name>.paused
  - default AI queue stale age: AI_GENERATION_QUEUE_STALE_SEC=900
"""

# Required workers: the base pipeline production always runs. A required
# worker that is stopped (and not intentionally paused) is critical.
# Conditional workers (stream backend, overlays, watchdogs, extra chats)
# are optional: their absence is reported but never critical.
# (name, required, category, pid_relpath or None for the default convention,
#  special pid-file kind: "pid" | "owner")
WORKERS = (
    ("soren_loop", True, "loop", "tmp/.soren_loop.lock/pid", "pid"),
    ("improve_daemon", True, "improvement", None, "pid"),
    ("chat_worker", True, "chat", None, "pid"),
    ("youtube_worker", False, "chat", None, "pid"),
    ("kick_worker", False, "chat", None, "pid"),
    ("audio_worker", True, "audio", None, "pid"),
    ("deadline_monitor", True, "monitor", None, "pid"),
    ("radio_worker", True, "radio", None, "pid"),
    ("prediction_worker", True, "prediction", None, "pid"),
    ("poll_worker", False, "chat", None, "pid"),
    ("goal_worker", False, "chat", None, "pid"),
    ("soviet_watchdog", False, "monitor", "tmp/state/.soviet_watchdog.lock/owner", "owner"),
    ("status_overlay_watch", False, "overlay", None, "pid"),
    ("show_status_overlay_watch", False, "overlay", None, "pid"),
    ("soren_overlay_watch", False, "overlay", None, "pid"),
    ("obs_capture_watchdog", False, "stream", None, "pid"),
    ("direct_stream", False, "stream", None, "pid"),
    ("stream_noon_audit", False, "stream", None, "pid"),
    ("youtube_broadcast_guard", False, "stream", None, "pid"),
)

# Well-known AI generation lanes (see _ai_queue_lock_scope in lib/ai_generate.sh).
# Live lanes are discovered by scanning tmp/state/.ai_generation_locks, so new
# lanes never need a registry edit to be observed; this list only guarantees
# the historic lanes keep working.
KNOWN_LANES = ("radio", "comment", "local")

# Lock-directory name suffixes that are mutex guards, not lanes.
LANE_GUARD_SUFFIXES = (".owner_guard.lock", ".owner_guard.d")

# Pid files owned by the supervision infrastructure itself (not workers).
# Never reported as unregistered; liveness is still recorded in details.
KNOWN_INFRA_PIDFILES = ("tmp/state/start_all.pid",)

# Diagnostics window for AI telemetry aggregation (seconds).
DIAG_WINDOW_SEC = 900

# Lock older than this with a dead owner (or unparsable owner) is suspected stale.
# Mirrors the AI_GENERATION_QUEUE_STALE_SEC=900 default. Observation only:
# the collector never removes locks.
QUEUE_STALE_SEC = 900

# Freshness bound for the supervisor-written duplicates report.
DUPLICATES_FRESH_SEC = 600

# Output bounds (the gateway additionally caps total stdout at 64 KiB).
MAX_RECENT_EVENTS = 20
MAX_ERROR_PREVIEW_LEN = 200
MAX_COMPONENTS = 10
MAX_UNREGISTERED = 50
MAX_JSON_BYTES = 32768
MAX_JSONL_SCAN_LINES = 20000
MAX_JSONL_SCAN_BYTES = 8 * 1024 * 1024

# Key substrings that must never appear as emitted keys/values. Mirrors the
# SECRET_SUBSTRINGS convention in src/docich/webui.py (case-insensitive).
REDACT_KEY_SUBSTRINGS = (
    "API_KEY",
    "TOKEN",
    "SECRET",
    "STREAM_KEY",
    "PASSWORD",
    "AUTHORIZATION",
    "COOKIE",
    "PRIVATE_KEY",
)


def worker_names():
    return tuple(name for name, _required, _category, _pid, _kind in WORKERS)


def required_workers():
    return tuple(name for name, required, _category, _pid, _kind in WORKERS if required)


def worker_entry(name):
    for entry in WORKERS:
        if entry[0] == name:
            return entry
    return None


def default_pid_relpath(name):
    return f"tmp/state/{name}.pid"
