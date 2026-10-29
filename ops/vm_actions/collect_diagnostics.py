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
  - fixed Soren supervisor unit/pidfile/start-time and public script/FD-255
    fingerprints only; these do not attest already-defined Bash functions
  - one bounded owner-only Soren stream-title journal, projected to fixed enums,
    UTC time, SHA and two nonsecret ID presence booleans only
  - bounded tails of three fixed title-updater logs, classified in memory to
    reviewed outcomes only, plus the daily script's mode and fixed user/system
    unit states. Bodies, identities, URLs and arbitrary paths are never emitted;
    historical observations never attest a current failure or the same run
  - fixed, known temporary shared-object filename families under /tmp plus
    same-user /proc maps/fd references; only bounded counts/bytes/booleans are
    emitted, never filenames, PIDs, mappings or file contents
  - fixed-category storage attribution for OpenCode DB/WAL/SHM, Soren
    logs/tmp/runtime caches, the strategy archive (total plus a fixed
    plaintext/compressed format split: counts and bytes only, never
    filenames), Git metadata, and VOICEVOX;
    scans are bounded, never follow symlinks, and emit no paths or filenames
  - the fixed host metrics ring (the first of the reviewed candidate
    locations under <home>, docich#2578): host counters sampled once a minute onto the
    persistent root disk so the hour before a guest-OS hang (#2278) survives
    the reboot that follows it. Projected to fixed numeric fields, baked-in
    retention constants, the fixed unit-state mask and derived down-unit names
    only -- ring lines, paths and any other free text are never emitted, and
    unit names are never read from configuration.
  - a fixed-label Chromium process inventory (owner / type / count / CPU /
    RSS / age only; cmdline, URL, path and PID are never emitted) for #970
    browser attribution.
  - docich program/corner state under the production state_dir
    (game_switch.json, game-switch/requests/*.json, weather_corner.json, tsuitate_corner.json,
    yoso_corner.json, doushi_corner.json, lawsim_corner.json, retro_corner.json,
    paper_corner.json,
    paper_corner_manual.json, trading/presentation.json,
    trading/paper_improve_status.json, trading/paper_experiment_control.json,
    trading/paper_strategy_evaluation.json): lifecycle statuses, timestamps,
    counters and finite PAPER result totals only. Strategy identity, rules,
    announcement/script bodies, prompts and log bodies are
    never read out. FIFO output is limited to queued count and the head's
    fixed operation/target/age fields.
  - while the retro corner is actively running Hanjuku Hero, a bounded tail
    of Soren's speech debug log is parsed in memory for Hanjuku-only queue
    outcome counters. No log lines, queue names or speech text are emitted.
    Its current runtime's scene producer, worker and bounded event log are
    separately projected to fixed enums, counts and ages; facts and scene
    identities are never emitted.
  - while the retro corner is actively running Hanjuku Hero, a bounded tail of
    that runtime's own decision log, projected in memory to the fixed
    screen-kind vocabulary, allowlisted pad button names, counts, ages and the
    trailing identical-signature run, so a corner that keeps repeating one
    operation stays diagnosable. Reasons, chart steps, names, hashes, runtime
    identifiers and log bodies are never read out.
  - the owned channel-point prediction round (state_dir/hanjuku_predictions.json
    plus at most three bounded records under runtimes/<runtime_id>/): a fixed
    enum naming which external record is decisive when the round stops
    validating, the persisted retry delay, presence/readability booleans and the
    terminal-reason enum. Runtime/lease identifiers, frame hashes and record
    bodies are never emitted.
  - a bounded, redacted tail (last lines only) of the NetHack agent's own log
    (state_dir/logs/agent.log, written by supervise.run_callable_loop) so a
    corner that reaches gameplay but never acts stays diagnosable. No
    unrelated log body is read.
  - bounded, redacted captures of the committed NetHack runtime's tmux
    windows (window names, the birth/process window TTY, and the agent window)
    so a corner that is active but not progressing stays diagnosable. Never
    sends tmux input.
  - the committed NetHack tile supervisor manifest, projected to fixed
    lifecycle/mode/reason enums, generation match and age only. Ownership PIDs,
    process arguments, filesystem paths and raw failure details stay private.
  - Soren boundary/A-B wait markers the corners gate on
    (tmp/state/corner_boundary_*.json, ab_state.json, ab_games.jsonl,
    ab_candidate/): only presence, counts, enums and mtimes; strategy/hash
    bodies and environment values are never read out.
  - the shared comment/audio queue the corners also gate on
    (${COMMENT_QUEUE_DIR:-tmp/.comment_queue}): only comment_*.txt /
    comment_*.playing counts, a fixed reason enum and the oldest item's age.
    Item names, texts and the Soren .env are never read out.
  - Soren game-lifecycle pause provenance (tmp/state/game_lifecycle/
    request.json, ack.json and the four fixed pause-ownership records):
    only the fixed request/ack status enums, whether a handover is active,
    which of the four managed workers carry a proven lifecycle-owned pause,
    and the fixed waiting-for enum. Request ids, game names, generations,
    deadlines, paths and record bodies are never read out.
  - the PulseAudio sink-input list (`pactl list sink-inputs`, read-only) so a
    muted or silent BGM/SE playback stream is observable without an ad-hoc
    owner shell (#968). Only index, sink name, fixed media role, mute,
    corked-when-reported, volume percents and a fixed player category are
    emitted; PIDs, raw application names, module/client identifiers and stream
    property dumps are never emitted.
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
import errno
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


def _load_founding_marker_rule():
    """The recovery guard's marker-ownership rule, shared instead of re-derived.

    Diagnostics report the same answer the guard acts on (#2345). A load
    failure must not fail the whole diagnostics operation, so it degrades to
    "unknown" rather than to a wrong answer.
    """
    try:
        spec = _importlib_util.spec_from_file_location(
            "recover_soren_game_rule", str(PROD_ROOT / "ops" / "vm_actions" / "recover_soren_game.py")
        )
        module = _importlib_util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.founding_marker_is_current
    except Exception:
        # Diagnostics must never fail closed here: an unavailable rule degrades
        # the two derived fields to null instead of failing the operation.
        return None


_REG = _load_registry()
_FOUNDING_MARKER_IS_CURRENT = _load_founding_marker_rule()
DIAG_WINDOW_SEC = _REG.DIAG_WINDOW_SEC
DUPLICATES_FRESH_SEC = _REG.DUPLICATES_FRESH_SEC
KNOWN_LANES = _REG.KNOWN_LANES
LANE_GUARD_SUFFIXES = _REG.LANE_GUARD_SUFFIXES
KNOWN_INFRA_PIDFILES = _REG.KNOWN_INFRA_PIDFILES
MAX_COMPONENTS = _REG.MAX_COMPONENTS
MAX_ERROR_PREVIEW_LEN = _REG.MAX_ERROR_PREVIEW_LEN
MAX_JSON_BYTES = _REG.MAX_JSON_BYTES
# The gateway reads this document over SSH with a 64 KiB stdout cap and then
# re-serializes it under a 64 KiB cap after adding its own metadata; exceeding
# either fails the whole diagnostics operation closed. Detail reductions aim at
# the softer MAX_JSON_BYTES target, and only the last-resort reductions below
# fall back to this hard ceiling. The ceiling is a guarantee, not a target: the
# final step of ``_diagnostics_budget`` drops whole sections until the envelope
# is under it, so the collector never returns a document the gateway rejects.
HARD_JSON_BYTES = 56 * 1024
# Heaviest-first drop order for the hard ceiling. ``corners`` is the largest
# remaining block (measured 23,374 B of a 50,280 B production envelope, #2533),
# so it goes first; the later entries only matter for a payload that is still
# over the ceiling once the corner projections are gone. ``host_metrics`` is
# deliberately last: it is the #2578 forensic evidence for the hour before a
# guest-OS hang (#2278), it cannot be reconstructed after the reboot, and the
# softer MAX_JSON_BYTES path already sheds half its sample list before any
# whole-section drop, so it yields only after every other section is gone.
_HARD_DROP_ORDER = ("corners", "ai", "workers", "nethack_history",
                    "soren91_drop_profile", "host_metrics")
# Sections that survive every ceiling reduction (#2805). ``corners`` is the
# first whole section dropped and every 予想王 input lives inside it, so while
# the production envelope stays above the ceiling the 予想王 ledger can only be
# observed from a run that happened to fit. ``prediction`` duplicates the same
# already-bounded dicts under a key the whole-section drops never take away.
_HARD_KEEP = ("meta", "prediction")
# Production thresholds of the 予想王 boards, kept in sync with
# ``docich.prediction_leaderboard.DEFAULT_MIN_ROUNDS`` /
# ``DEFAULT_MIN_PARTICIPATIONS``: the yoso adapter passes no ``--min-rounds`` or
# ``--min-participations`` override, so the defaults are the deployed values.
# The collector runs against a fixed argv and does not import the docich package.
_PREDICTION_MIN_ROUNDS = 3
_PREDICTION_MIN_PARTICIPATIONS = 1
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
# Fixed on-disk formats of the strategy permanent archive (by_hash). "plain" is
# what the soviet_now readers open today; "compressed" is what a reviewed
# reclaim step may produce once every reader can fall back to it. Bounded
# growth of this archive is judged by these two numbers only.
STRATEGY_ARCHIVE_FORMATS = (
    ("plain", ".py"),
    ("compressed", ".py.gz"),
)
OPENCODE_ATTRIBUTION_WINDOW_SEC = 24 * 60 * 60
OPENCODE_ATTRIBUTION_QUERY_TIMEOUT_SEC = 2.0
OPENCODE_CALLER_BUCKETS = (
    "radio_prepass",
    "radio_main",
    "comment",
    "improvement",
    "soren91",
    "probe",
    "other",
)
OPENCODE_CALLER_TITLES = {
    f"docich:{bucket}": bucket for bucket in OPENCODE_CALLER_BUCKETS
}


TWITCH_OAUTH_STATUSES = frozenset({
    "ok", "not_configured", "reauth_required", "network_error",
    "validate_unavailable", "token_endpoint_unavailable", "token_response_invalid",
    "env_unreadable", "token_format_rejected", "new_token_not_broadcaster",
    "new_token_missing_scopes", "new_token_token_invalid", "io_error",
    "consent_denied", "state_missing", "state_mismatch_or_expired",
    "client_credentials_missing", "refresh_token_missing",
})
TWITCH_OAUTH_SCOPES = frozenset({
    "channel:manage:broadcast", "chat:edit", "chat:read", "user:write:chat",
    "clips:edit", "channel:manage:polls", "channel:manage:predictions",
    "channel:read:goals",
})


def _twitch_oauth_timestamp(value):
    """Normalize a producer timestamp, never forward arbitrary source text."""
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|\+00:00)", value):
        return None, None
    try:
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.isoformat(timespec="seconds"), stamp.timestamp()
    except (ValueError, OverflowError, OSError):
        return None, None


def _collect_twitch_oauth(now):
    """Read only the fixed status record, not credentials or live OAuth APIs.

    These are historical producer observations, not evidence that a token is
    currently valid/expired. Error records intentionally have no expiry facts.
    """
    result = {"present": False, "readable": False, "status": "unknown",
              "action": None, "checked_at": None, "expires_at": None,
              "age_sec": None, "checked_at_future": None,
              "missing_scopes": [], "missing_scopes_known": False}
    fd = directory = None
    try:
        fd, directory, before = _stream_title_open_fixed(
            _program_state_dir(), ("twitch_oauth", "status.json"))
        result["present"] = True
        if before.st_size > 4096:
            return result
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(4097)
        after = os.fstat(fd)
        named = os.stat("status.json", dir_fd=directory, follow_symlinks=False)
        if (len(raw) > 4096
                or _stream_title_file_identity(before) != _stream_title_file_identity(after)
                or _stream_title_file_identity(after) != _stream_title_file_identity(named)):
            return result
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            return result
        result["readable"] = True
        status = data.get("status")
        if isinstance(status, str) and status in TWITCH_OAUTH_STATUSES:
            result["status"] = status
        action = data.get("action")
        if isinstance(action, str) and action in {"authorized", "refreshed", "no_change"}:
            result["action"] = action
        result["checked_at"], checked = _twitch_oauth_timestamp(data.get("checked_at"))
        result["expires_at"], _ = _twitch_oauth_timestamp(data.get("expires_at"))
        if checked is not None:
            result["checked_at_future"] = checked > now
            if checked <= now:
                result["age_sec"] = min(int(now - checked), 2**31 - 1)
        scopes = data.get("missing_scopes")
        if (isinstance(scopes, list) and len(scopes) <= len(TWITCH_OAUTH_SCOPES)
                and all(isinstance(s, str) and s in TWITCH_OAUTH_SCOPES for s in scopes)):
            result["missing_scopes"] = sorted(set(scopes))
            result["missing_scopes_known"] = True
    except FileNotFoundError:
        pass
    except (OSError, ValueError, RecursionError, _StreamTitleUnsafe):
        result["present"] = True
    finally:
        if fd is not None:
            os.close(fd)
        if directory is not None:
            os.close(directory)
    return result


def _collect_opencode_retention(soren, now):
    """Project only bounded enums/numbers; never forward exception or DB text."""
    OPENCODE_RETENTION_TIMER = _REG.OPENCODE_RETENTION_TIMER
    OPENCODE_RETENTION_MAX_AGE_SEC = _REG.OPENCODE_RETENTION_MAX_AGE_SEC
    result = {}
    # Fixed enums emitted by lib/opencode_db_retention.py; unknown values
    # degrade to "unknown" and raw text is never forwarded.
    reason_values = {"ok", "deadline", "insufficient_space", "space_unknown", "checkpoint_busy",
                     "unsafe_journal_mode", "sqlite_busy", "sqlite_error", "filesystem_or_input", "interrupted",
                     "insufficient_memory", "memory_unknown",
                     "wal_limit_unavailable", "bounded_prune_committed", "bounded_prune_io_error",
                     "sparse_unsupported", "sparse_lock_required", "sparse_identity_changed",
                     "sparse_deadline", "sparse_alignment", "sparse_filesystem_unsupported", "sparse_fd_required"}
    enums = {
        "status": {"running", "completed", "gate_timeout", "disabled", "deferred", "failed"},
        "reason": reason_values,
        "compact_storage": {"disk", "memory"},
        "stage": {"preflight", "delete", "compact_copy", "compact_writeback", "checkpoint", "vacuum", "done",
                  "compact_deferred", "sparse_reclaim", "sparse_reclaimed"},
        "compact_defer_reason": reason_values,
        "preflight_phase": {"input", "budget", "connect", "busy_timeout", "temp_store", "synchronous",
                            "locking_mode", "begin_exclusive", "commit_exclusive", "journal_mode",
                            "checkpoint", "pages", "eligible_count", "delete_budget", "complete"},
        "prune_mode": {"bounded_wal"},
        "recovery_action": {"inspect_io_or_add_capacity"},
    }
    numbers = ("started_at", "completed_at", "retention_days", "deleted_sessions", "eligible_sessions",
               "before_bytes", "after_bytes", "available_before_bytes", "available_after_bytes",
               "compact_bytes", "page_size", "page_count", "freelist_count",
               "selected_sessions", "remaining_sessions", "prune_batches", "wal_limit_bytes",
               "skipped_batches", "skipped_sessions",
               "sqlite_error_code", "sqlite_extended_error_code",
               "sparse_scanned_bytes", "sparse_allocated_before_bytes",
               "sparse_allocated_after_bytes", "sparse_reclaimed_bytes")
    booleans = ("bounded_prune_blocked", "sparse_complete")
    for label, filename in (("attempt", "opencode_db_retention.json"),
                            ("default", "opencode_retention_default.json"),
                            ("worker", "opencode_retention_worker.json")):
        path = Path(soren) / "tmp/state" / filename
        item = {"present": False, "readable": False}
        try:
            st = path.lstat()
            item["present"] = True
            if not stat.S_ISREG(st.st_mode) or st.st_size > 4096:
                result[label] = item
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, encoding="utf-8") as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
                    raise ValueError()
                data = json.loads(handle.read(4097))
            if not isinstance(data, dict):
                raise ValueError()
            item["readable"] = True
            for key, allowed in enums.items():
                value = data.get(key)
                if isinstance(value, str):
                    item[key] = value if value in allowed else "unknown"
            for key in numbers:
                value = data.get(key)
                if type(value) is int and 0 <= value < 2**63:
                    item[key] = value
            for key in booleans:
                value = data.get(key)
                if type(value) is bool:
                    item[key] = value
            stamp = item.get("completed_at", item.get("started_at"))
            if stamp is not None:
                item["age_sec"] = max(0, int(now - stamp))
                item["stale"] = stamp > now + 60 or now - stamp > OPENCODE_RETENTION_MAX_AGE_SEC
        except (OSError, ValueError):
            pass
        result[label] = item
    result["timer"] = {"unit": OPENCODE_RETENTION_TIMER}
    for action, field in (("is-active", "active"), ("is-enabled", "enabled")):
        try:
            env = {**os.environ, "XDG_RUNTIME_DIR": "/run/user/%d" % os.getuid()}
            proc = subprocess.run(["systemctl", "--user", action, "--quiet", OPENCODE_RETENTION_TIMER],
                                  capture_output=True, timeout=2, env=env)
            result["timer"][field] = proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            result["timer"][field] = None
    return result


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


def _storage_suffix_usage(path, formats, *, max_entries=STORAGE_MAX_ENTRIES):
    """Bounded, identity-free per-format accounting for one fixed tree.

    Emits only counts and allocated bytes per fixed suffix (never a filename,
    a path or file content), so an archive whose on-disk format is being
    changed by a reviewed reclaim step stays observable. The scan mirrors
    ``_storage_tree_usage``: bounded by ``max_entries``, symlinks are never
    followed, and hardlinked inodes are charged once. Any doubt marks the
    result incomplete instead of silently reporting a partial total.
    """
    path = Path(path)
    result = {
        "present": False,
        "scan_complete": True,
        "max_entries": int(max_entries),
        "formats": {name: {"count": 0, "allocated_bytes": 0} for name, _suffix in formats},
    }
    suffixes = tuple(formats)
    try:
        root_st = os.lstat(path)
    except FileNotFoundError:
        return result
    except OSError:
        result["scan_complete"] = False
        return result

    result["present"] = True
    if stat.S_ISLNK(root_st.st_mode) or not stat.S_ISDIR(root_st.st_mode):
        result["scan_complete"] = False
        return result

    seen = set()
    scanned = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if scanned >= max_entries:
                        result["scan_complete"] = False
                        return result
                    scanned += 1
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        result["scan_complete"] = False
                        continue
                    if stat.S_ISLNK(st.st_mode):
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(Path(entry.path))
                        continue
                    if not stat.S_ISREG(st.st_mode) or not any(
                        entry.name.endswith(suffix) for _name, suffix in suffixes
                    ):
                        continue
                    key = (int(st.st_dev), int(st.st_ino))
                    if key in seen:
                        continue
                    seen.add(key)
                    for name, suffix in suffixes:
                        if entry.name.endswith(suffix):
                            bucket = result["formats"][name]
                            bucket["count"] += 1
                            bucket["allocated_bytes"] += _storage_allocated_bytes(st)
        except OSError:
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


def _classify_sys_tmp_top_level(path, *, max_entries=STORAGE_MAX_ENTRIES):
    """Identity-free top-level classification of a /tmp-style directory.

    Returns counts and bytes per class so a runaway can be coarsely attributed
    without SSH: (1) git worktree/clone (directory containing a ``.git``
    marker), (2) files grouped by extension, (3) everything else. No names or
    content are reported. Partial scans set ``scan_complete=False``.
    """
    path = Path(path)
    result = {"git_worktree": {"count": 0, "allocated_bytes": 0},
              "by_extension": {},
              "other": {"count": 0, "allocated_bytes": 0},
              "scan_complete": True}
    try:
        root_st = os.lstat(path)
    except FileNotFoundError:
        return result
    except OSError:
        result["scan_complete"] = False
        return result
    if not stat.S_ISDIR(root_st.st_mode):
        result["scan_complete"] = False
        return result

    seen = 0
    try:
        entries = os.scandir(path)
    except OSError:
        result["scan_complete"] = False
        return result
    with entries:
        for entry in entries:
            if seen >= max_entries:
                result["scan_complete"] = False
                break
            seen += 1
            try:
                st = entry.stat(follow_symlinks=False)
            except (FileNotFoundError, OSError):
                result["scan_complete"] = False
                continue
            if stat.S_ISLNK(st.st_mode):
                result["other"]["count"] += 1
                result["other"]["allocated_bytes"] += _storage_allocated_bytes(st)
                continue
            if stat.S_ISDIR(st.st_mode):
                try:
                    has_git = (Path(entry.path) / ".git").exists()
                except OSError:
                    has_git = False
                    result["scan_complete"] = False
                if has_git:
                    result["git_worktree"]["count"] += 1
                    result["git_worktree"]["allocated_bytes"] += _storage_allocated_bytes(st)
                else:
                    result["other"]["count"] += 1
                    result["other"]["allocated_bytes"] += _storage_allocated_bytes(st)
                continue
            # file
            name = entry.name
            ext = ""
            if "." in name and not name.startswith("."):
                ext = name.rsplit(".", 1)[-1].lower()[:16]
            if not ext:
                ext = "(none)"
            bucket = result["by_extension"].setdefault(ext, {"count": 0, "allocated_bytes": 0})
            bucket["count"] += 1
            bucket["allocated_bytes"] += _storage_allocated_bytes(st)
    return result

# Case A of azumag/docich#2735: dry-run evidence only. A top-level /tmp entry is
# counted as a reclaim candidate when every gate passes; nothing is read for
# write, deleted or changed here. Deletion is a separate, later decision.
SYS_TMP_RECLAIM_MIN_BYTES = 100 * 1024 * 1024
SYS_TMP_RECLAIM_IDLE_SEC = 24 * 3600
SYS_TMP_RECLAIM_GIT_TIMEOUT_SEC = 5
SYS_TMP_RECLAIM_PROC_CMDLINE_BYTES = 1024 * 1024


def _sys_tmp_recently_touched(entry_path, *, now, idle_sec):
    """Cheap pre-gate: the entry, its .git and .git/objects mtimes.

    Returns ``(recent, complete)``. An unreadable entry is ``complete=False``
    and is kept by the caller.
    """
    for candidate in (entry_path, entry_path / ".git", entry_path / ".git" / "objects"):
        try:
            st = os.lstat(candidate)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            return True, False
        if now - st.st_mtime < idle_sec:
            return True, True
    return False, True


def _sys_tmp_recent_activity(path, *, now, idle_sec, max_entries):
    """Return ``(recent, complete)`` for regular files under ``path``.

    Symlinks are never followed. Any file whose mtime is within ``idle_sec``
    (a future mtime included) makes the tree recent. An unreadable or
    over-bound walk returns ``complete=False`` so the caller keeps the entry.
    """
    seen = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    if seen > max_entries:
                        return False, False
                    st = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(Path(entry.path))
                    elif not stat.S_ISLNK(st.st_mode) and now - st.st_mtime < idle_sec:
                        return True, True
        except OSError:
            return False, False
    return False, True


def _sys_tmp_git_clean(path):
    """Tri-state git check: True clean work tree, False verified not-clean
    (dirty, or a bare/non-work-tree repo), None when git could not verify.
    """
    base = [
        "git", "-c", "core.fsmonitor=false", "--no-optional-locks", "-C", str(path),
    ]
    try:
        inside = subprocess.run(
            base + ["rev-parse", "--is-inside-work-tree"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=SYS_TMP_RECLAIM_GIT_TIMEOUT_SEC,
            check=False,
        )
        if inside.returncode != 0:
            return None
        inside_out = inside.stdout.strip()
        if inside_out == b"false":
            return False
        if inside_out != b"true":
            return None
        status = subprocess.run(
            base + ["status", "--porcelain"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=SYS_TMP_RECLAIM_GIT_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if status.returncode != 0:
        return None
    return status.stdout == b""


def _sys_tmp_reference_scan(roots, *, proc_root, max_proc_fds, own_pid, euid):
    """Return ``(referenced, complete)`` for the byte paths in ``roots``.

    A root is referenced when any process cmdline contains it. cwd and fd are
    read only for processes owned by ``euid`` (the same scope as the tmp .so
    scanner and the reviewed shell helper's effective view); other users'
    cwd/fd are not readable and must not make the whole scan incomplete.
    Fd overflow and a missing proc root return ``complete=False``.
    """
    referenced = set()
    complete = True
    fds_seen = 0
    try:
        proc_entries = os.scandir(proc_root)
    except OSError:
        return referenced, False

    def _mark(target):
        for root in roots:
            if target == root or target.startswith(root + b"/"):
                referenced.add(root)

    with proc_entries:
        for proc_entry in proc_entries:
            if not proc_entry.name.isdigit() or int(proc_entry.name) == own_pid:
                continue
            base = Path(proc_entry.path)
            try:
                with open(base / "cmdline", "rb") as handle:
                    cmdline = handle.read(SYS_TMP_RECLAIM_PROC_CMDLINE_BYTES)
            except FileNotFoundError:
                continue
            except OSError:
                complete = False
                continue
            for root in roots:
                if root in cmdline:
                    referenced.add(root)
            try:
                if proc_entry.stat(follow_symlinks=False).st_uid != euid:
                    continue
            except FileNotFoundError:
                continue
            except OSError:
                complete = False
                continue
            try:
                _mark(os.fsencode(os.readlink(base / "cwd")))
            except FileNotFoundError:
                pass
            except OSError:
                complete = False
            try:
                with os.scandir(base / "fd") as fds:
                    for fd_entry in fds:
                        fds_seen += 1
                        if fds_seen > max_proc_fds:
                            return referenced, False
                        try:
                            _mark(os.fsencode(os.readlink(fd_entry.path)))
                        except FileNotFoundError:
                            continue
            except FileNotFoundError:
                continue
            except OSError:
                complete = False
    return referenced, complete


def _sys_tmp_reclaim_gate(path, *, now, max_entries, min_bytes, idle_sec, result, passed):
    """Append ``(root_bytes, allocated_bytes)`` for entries passing gates 1-5.

    Gate order is fixed: form, idle pre-gate, size, full idle walk, clean tree.
    Each doubt keeps the entry and clears ``result["scan_complete"]`` only when
    the scan itself was partial; a single doubtful entry never adds a count.
    """
    try:
        entries = os.scandir(path)
    except OSError:
        result["scan_complete"] = False
        return
    with entries:
        seen = 0
        for entry in entries:
            seen += 1
            if seen > max_entries:
                result["scan_complete"] = False
                break
            try:
                st = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError:
                result["scan_complete"] = False
                continue
            if not stat.S_ISDIR(st.st_mode):
                continue
            entry_path = Path(entry.path)
            try:
                if not (entry_path / ".git").exists():
                    continue
                recent, touch_complete = _sys_tmp_recently_touched(
                    entry_path, now=now, idle_sec=idle_sec
                )
                if not touch_complete:
                    result["scan_complete"] = False
                if recent or not touch_complete:
                    continue
                usage = _storage_tree_usage(entry_path, max_entries=max_entries)
                if not usage["scan_complete"]:
                    result["scan_complete"] = False
                    continue
                if usage["allocated_bytes"] < min_bytes:
                    continue
                recent, walk_complete = _sys_tmp_recent_activity(
                    entry_path, now=now, idle_sec=idle_sec, max_entries=max_entries
                )
                if not walk_complete:
                    result["scan_complete"] = False
                    continue
                if recent:
                    continue
                clean = _sys_tmp_git_clean(entry_path)
                if clean is None:
                    result["scan_complete"] = False
                    continue
                if not clean:
                    continue
            except OSError:
                result["scan_complete"] = False
                continue
            passed.append((os.fsencode(entry.path), usage["allocated_bytes"]))


def _sweep_sys_tmp_reclaim_candidates(
    path,
    *,
    max_entries=STORAGE_MAX_ENTRIES,
    now=None,
    proc_root="/proc",
    min_bytes=SYS_TMP_RECLAIM_MIN_BYTES,
    idle_sec=SYS_TMP_RECLAIM_IDLE_SEC,
    euid=None,
):
    """Identity-free dry-run count of /tmp entries a reclaim could consider.

    Only top-level directories that contain ``.git`` (clone or linked worktree)
    are considered, and an entry counts only when all of these hold: at least
    ``min_bytes`` allocated, no file touched within ``idle_sec``, a clean git
    work tree, and no running process whose cmdline, cwd or fd points at it.
    Symlinks are never followed. Anything that cannot be proven yields
    ``scan_complete=False`` and is not counted. Nothing is deleted or written.
    """
    now = time.time() if now is None else now
    result = {"count": 0, "allocated_bytes": 0, "scan_complete": True}
    passed = []
    try:
        path = Path(path)
        try:
            root_st = os.lstat(path)
        except FileNotFoundError:
            return result
        except OSError:
            result["scan_complete"] = False
            return result
        if not stat.S_ISDIR(root_st.st_mode):
            result["scan_complete"] = False
            return result
        _sys_tmp_reclaim_gate(
            path,
            now=now,
            max_entries=max_entries,
            min_bytes=min_bytes,
            idle_sec=idle_sec,
            result=result,
            passed=passed,
        )
        if passed:
            referenced, refs_complete = _sys_tmp_reference_scan(
                [root for root, _size in passed],
                proc_root=Path(proc_root),
                max_proc_fds=TMP_SO_MAX_PROC_FDS,
                own_pid=os.getpid(),
                euid=os.geteuid() if euid is None else int(euid),
            )
            if not refs_complete:
                result["scan_complete"] = False
            for root, allocated in passed:
                if not refs_complete or root in referenced:
                    continue
                result["count"] += 1
                result["allocated_bytes"] += allocated
    except Exception:
        # The collector must never raise; partial proven counts are kept.
        result["scan_complete"] = False
    return result


def _collect_storage_breakdown(
    soren,
    prod_root,
    *,
    home_root=None,
    voicevox_root=Path("/opt/voicevox"),
    sys_tmp=Path("/tmp"),
    var_log=Path("/var/log"),
    swap_file=Path("/swapfile"),
    max_entries=STORAGE_MAX_ENTRIES,
):
    """Fixed, bounded, identity-free storage attribution.

    Categories overlap by design and must not be summed. No file content is
    read, symlinks are never followed, and partial scans are marked incomplete.

    Version 2 (azumag/docich#2583) adds the fixed roots version 1 could not
    see, so the unattributed share of a decline window becomes attributable:
    the gateway's own state tree (bundles and preview releases), the known
    alternative checkouts, the system fixed areas (journal, /var/log, the
    swapfile), /tmp, and fixed subcategories of the production .git that
    separate repack churn from retained history. The container layers
    (/var/lib/docker) stay out on purpose: the collector uid cannot traverse
    them, and a permanently unreadable root would flip the shared incomplete
    flag forever instead of attributing bytes.
    """
    soren = Path(soren)
    prod_root = Path(prod_root)
    home = Path(home_root) if home_root is not None else soren.parent
    voicevox_root = Path(voicevox_root)
    sys_tmp = Path(sys_tmp)
    var_log = Path(var_log)
    swap_file = Path(swap_file)
    default_db = home / ".local" / "share" / "opencode" / "opencode.db"
    worker_db = soren / "tmp" / "state" / "xdg_data" / "opencode" / "opencode.db"

    return {
        "version": 2,
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
        "strategy_archive_formats": _storage_suffix_usage(
            soren / "strategy_versions_archive" / "by_hash",
            STRATEGY_ARCHIVE_FORMATS,
            max_entries=max_entries,
        ),
        "docich_git": _storage_tree_usage(prod_root / ".git", max_entries=max_entries),
        "soren_live_git": _storage_tree_usage(soren / ".git", max_entries=max_entries),
        "soren_persist_git": _storage_tree_usage(
            home / "soren-persist" / ".git", max_entries=max_entries
        ),
        "voicevox_root": _storage_tree_usage(voicevox_root, max_entries=max_entries),
        "voicevox_archive": _storage_file_usage(voicevox_root / "voicevox.7z.001"),
        # ---- version 2 fixed roots (azumag/docich#2583) ----
        "gateway_state": _storage_tree_usage(
            home / ".local" / "state" / "github-vm-ops", max_entries=max_entries
        ),
        "alt_checkout_tmp_sync": _storage_tree_usage(
            sys_tmp / "opencode" / "docich-sync", max_entries=max_entries
        ),
        "alt_checkout_soren_src": _storage_tree_usage(home / "soren-src", max_entries=max_entries),
        "system_journal": _storage_tree_usage(var_log / "journal", max_entries=max_entries),
        "system_log": _storage_tree_usage(var_log, max_entries=max_entries),
        "swapfile": _storage_file_usage(swap_file),
        "sys_tmp": _storage_tree_usage(sys_tmp, max_entries=max_entries),
        "sys_tmp_top_level": _classify_sys_tmp_top_level(sys_tmp, max_entries=max_entries),
        "sys_tmp_reclaim_candidates": _sweep_sys_tmp_reclaim_candidates(
            sys_tmp, max_entries=max_entries
        ),
        "docich_git_objects": _storage_tree_usage(
            prod_root / ".git" / "objects", max_entries=max_entries
        ),
        "docich_git_objects_pack": _storage_tree_usage(
            prod_root / ".git" / "objects" / "pack", max_entries=max_entries
        ),
    }


# Fixed host metrics ring (docich#2578). The sampler writes
# ``<home>/.local/state/docich/host-metrics/ring.jsonl`` once a minute on the
# persistent root disk. That path is deliberately not the program ``state_dir``
# and not ``soren/tmp``: both are cleaned, while this ring has to survive the
# reboot that follows a guest-OS hang (#2278). The collector is installed and
# run standalone, so it must not import the sampler -- the contract constants
# are duplicated here and a regression test parses ``host_metrics.py`` with
# importlib and fails when the two sets drift. The single source of truth is
# ops/vm_actions/host_metrics_contract.md.
INTERVAL_SEC = 60                 # systemd timer OnCalendar=*:*:00
MAX_POINTS = 240                  # 4 hours of history
MAX_BYTES = 256 * 1024
SAMPLES_MAX = 8
STALE_SEC = 3 * INTERVAL_SEC
SCHEMA_VERSION = 1
UNIT_ORDER = (
    "docich-corner-rotation.timer",
    "docich-corner-rotation.service",
    "docich-webui.service",
    "docich-paper-corner.timer",
    "soren-runtime.service",
    "docich-audio-priority.service",
)
# The ring's fixed candidate locations, in the sampler's reviewed order. The
# XDG state home is first, but on the production VM ``~/.local/state`` and its
# ``docich`` child are root-owned (created by a root-time ``install -d`` in the
# gateway/canary installers), so the unprivileged sampler cannot create
# anything below it and falls back to the ubuntu-owned ``~/.local/share`` tree,
# then to a plain home directory. The reader checks them in the same order and
# reports which one it read as ``ring_path_kind`` -- never the path itself.
HOST_METRICS_RING_CANDIDATES = (
    ("state", Path(".local") / "state" / "docich" / "host-metrics"),
    ("share", Path(".local") / "share" / "docich" / "host-metrics"),
    ("home", Path("docich-host-metrics")),
)
# A ring that already broke its own MAX_BYTES bound is refused instead of
# projecting a partial read as if it were the retained history.
HOST_METRICS_MAX_READ_BYTES = MAX_BYTES + 4096
def _host_metrics_count(value, maximum=2**63 - 1):
    if type(value) is not int or not 0 <= value <= maximum:
        return None
    return value


def _host_metrics_float(value, maximum=10**12):
    if type(value) not in (int, float):
        return None
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= maximum:
        return None
    return value


def _host_metrics_round(value):
    return None if value is None else round(float(value), 2)


def _host_metrics_line(line, now):
    """Parse one ring line into the contract's fixed values, or ``None``.

    A v1 point is kept as long as ``v`` and ``t`` are valid; every other source
    is optional and may legitimately be ``null`` (the sampler writes null when a
    source was unreadable on that tick). A malformed value still makes the line
    malformed -- an unknown shape is skipped, never guessed at -- but a degraded
    record with a valid timestamp stays a real point. Nothing from the line is
    carried through except these fixed values.
    """
    try:
        record = json.loads(line)
    except (ValueError, RecursionError):
        return None
    if not isinstance(record, dict) or type(record.get("v")) is not int:
        return None
    if record["v"] != SCHEMA_VERSION:
        return None
    t = record.get("t")
    if type(t) is not int or not 0 <= t <= max(0, now) + 86400:
        return None

    boot = record.get("boot")
    if boot is not None and (
            not isinstance(boot, str)
            or re.fullmatch(r"[0-9a-f]{12}", boot) is None):
        return None
    uptime_s = record.get("uptime_s")
    if uptime_s is not None and (type(uptime_s) is not int or uptime_s < 0):
        return None
    units = record.get("units")
    if units is not None and (type(units) is not int or units < 0):
        return None

    cpu = record.get("cpu")
    if cpu is not None:
        if (not isinstance(cpu, (list, tuple)) or len(cpu) != 3
                or any(type(item) is not int or item < 0 for item in cpu)):
            return None
        cpu = tuple(cpu)

    load = record.get("load")
    if load is not None:
        if not isinstance(load, (list, tuple)) or len(load) != 3:
            return None
        load_values = []
        for item in load:
            value = _host_metrics_float(item, maximum=10**6)
            if value is None:
                return None
            load_values.append(value)
        load = tuple(load_values)

    # A missing or unknown ``mem_kb`` key is the same honest "not reported" as
    # an explicit null; neither is ever turned into a guessed 0.
    mem_values = {
        "available": None, "dirty": None, "swap_free": None,
        "swap_total": None, "total": None, "writeback": None,
    }
    mem = record.get("mem_kb")
    if mem is not None:
        if not isinstance(mem, dict):
            return None
        for key in mem_values:
            value = mem.get(key)
            if value is None:
                continue
            parsed = _host_metrics_count(value)
            if parsed is None:
                return None
            mem_values[key] = parsed

    # Both PSI halves are kept: ``some`` shows contention, ``full`` shows
    # everyone stalled -- the strongest pre-hang signal -- so neither may be
    # sampled and then discarded. Null stays null per half.
    psi_values = [(None, None), (None, None), (None, None)]
    psi = record.get("psi")
    if psi is not None:
        if not isinstance(psi, dict):
            return None
        for index, key in enumerate(("cpu", "io", "mem")):
            pair = psi.get(key)
            if pair is None:
                continue
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                return None
            parsed = []
            for item in pair:
                if item is None:
                    parsed.append(None)
                    continue
                value = _host_metrics_float(item)
                if value is None:
                    return None
                parsed.append(value)
            psi_values[index] = tuple(parsed)

    disk_sectors = record.get("disk_sectors")
    if disk_sectors is not None:
        if (not isinstance(disk_sectors, (list, tuple)) or len(disk_sectors) != 2
                or any(type(item) is not int or item < 0
                       for item in disk_sectors)):
            return None
        disk_sectors = tuple(disk_sectors)
    disk_io_ms = None
    disk_ms = record.get("disk_ms")
    if disk_ms is not None:
        if (not isinstance(disk_ms, (list, tuple)) or len(disk_ms) != 2
                or any(type(item) is not int or item < 0 for item in disk_ms)):
            return None
        disk_io_ms = disk_ms[0]

    return {
        "boot": boot,
        "t": t,
        "uptime_s": uptime_s,
        "cpu": cpu,
        "load": load,
        "mem": mem_values,
        "psi": tuple(psi_values),
        "disk_sectors": disk_sectors,
        "disk_io_ms": disk_io_ms,
        "units": units,
    }


#: 2-bit unit states. ``0b11`` is "unknown" (activating/reloading/deactivating,
#: a scope without the unit, or a failed probe) and is deliberately NOT "down":
#: at boot every unit is transiently activating, and a unit that does not exist
#: in this scope must not look like a failed one.
UNIT_STATE_INACTIVE = 0b00
UNIT_STATE_ACTIVE = 0b01
UNIT_STATE_FAILED = 0b10
UNIT_STATE_UNKNOWN = 0b11


def _host_metrics_down_units(units):
    """Fixed names that are installed but stopped or failed; null is unknown."""
    if units is None:
        return []
    return [
        name for index, name in enumerate(UNIT_ORDER)
        if ((units >> (2 * index)) & 0b11) in (UNIT_STATE_INACTIVE,
                                               UNIT_STATE_FAILED)
    ]


HOST_METRICS_BOUNDARY_BOOTS = 2


def _host_metrics_home():
    """Runtime user's home by the sampler's rule; PROD_ROOT.parent fallback.

    The sampler writes ``pwd.getpwuid(...).pw_dir``-based paths, so the reader
    must resolve the same directory or a moved checkout would silently read a
    different (absent) ring. The production passwd entry is
    ``/home/ubuntu`` -- identical to the deployed checkout's parent today; the
    fallback only covers environments without ``pwd``.
    """
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        return PROD_ROOT.parent


def _host_metrics_trim_samples(samples, limit,
                               max_previous_boots=HOST_METRICS_BOUNDARY_BOOTS):
    """Keep the newest ``limit`` points plus the last point of older boots.

    "Newest N" alone is not enough for #2578: after the reboot that follows a
    guest-OS hang, the sampler refills the window with post-boot points, and a
    diagnostics run more than N intervals later would hide the last point before
    the hang -- the only evidence of the final hour. The last point of up to
    ``max_previous_boots`` older boots is therefore kept as a boundary, whatever
    the newest window contains. ``boot`` may be null (unknown); equality is the
    only test, because a null boot never proves a reboot either way.
    """
    if limit <= 0 or not samples:
        return []
    keep = set(range(max(0, len(samples) - limit), len(samples)))
    added = 0
    for index in range(len(samples) - 2, -1, -1):
        if samples[index]["boot"] == samples[index + 1]["boot"]:
            continue
        keep.add(index)
        added += 1
        if added >= max_previous_boots:
            break
    return [samples[index] for index in sorted(keep)]


# Read-only health of the #2578 sampler units themselves. The first production
# deploy of the ring failed inside the deploy hook with the VM-side output
# withheld, and the ring stayed absent: without these fields a dead sampler is
# invisible to the only sanctioned channel. Everything here is a fixed enum,
# count or age; the journal text and any path never leave the collector.
HOST_METRICS_SERVICE_UNIT = "docich-host-metrics.service"
HOST_METRICS_TIMER_UNIT = "docich-host-metrics.timer"
HOST_METRICS_JOURNAL_LINES = 40
# Five probes run per collection; a hung user manager must not eat the
# collector's 60 s gateway budget, so each probe gets a short leash.
HOST_METRICS_PROBE_TIMEOUT_SEC = 2.0
HOST_METRICS_UNIT_STATE_ENUMS = (
    "active", "inactive", "failed", "activating", "deactivating", "reloading",
)
HOST_METRICS_RESULT_ENUMS = (
    "success", "exit-code", "signal", "oom", "core-dump", "timeout",
    "start-limit-hit",
)
# Fixed journal classification, worst-first. Only the enum leaves; a run that
# finished normally therefore reads "finished" only when nothing worse matched.
# ``sampler_refused`` is the sampler's own bounded refusal line (its record
# could not fit the ring's line bound); the byte count it carries is parsed
# into ``refused_record_bytes`` and the journal text itself never leaves.
HOST_METRICS_JOURNAL_SIGNATURES = (
    "oom", "timeout", "python_traceback", "sampler_refused", "exited",
    "finished", "starting", "other",
)


def _host_metrics_runtime_state(name):
    """``systemctl --user is-active`` for one fixed unit, as a fixed enum."""
    proc = _systemctl_user(["is-active", name],
                           timeout=HOST_METRICS_PROBE_TIMEOUT_SEC)
    if proc is None:
        return "unknown"
    lines = (proc[1] or "").splitlines()
    state = lines[0].strip().lower() if lines else ""
    return state if state in HOST_METRICS_UNIT_STATE_ENUMS else "unknown"


def _host_metrics_runtime_enabled(name):
    """``systemctl --user is-enabled`` for one fixed unit, as a boolean."""
    proc = _systemctl_user(["is-enabled", name],
                            timeout=HOST_METRICS_PROBE_TIMEOUT_SEC)
    if proc is None:
        return None
    lines = (proc[1] or "").splitlines()
    value = lines[0].strip().lower() if lines else ""
    if value in ("enabled", "enabled-runtime"):
        return True
    if value in ("disabled", "static", "masked"):
        return False
    return None


def _host_metrics_runtime_properties():
    """Exit classification of the last service run from ``systemctl --user show``."""
    result = {
        "service_result": None,
        "service_exec_code": None,
        "service_exec_status": None,
        "service_n_restarts": None,
    }
    proc = _systemctl_user(
        ["show", HOST_METRICS_SERVICE_UNIT,
         "-p", "Result", "-p", "ExecMainCode", "-p", "ExecMainStatus",
         "-p", "NRestarts"],
        timeout=HOST_METRICS_PROBE_TIMEOUT_SEC,
    )
    if proc is None or proc[0] != 0:
        return result
    # Parse ``Key=Value`` lines instead of ``--value``: the value-only form
    # prints the properties in systemd's own order, not the ``-p`` order, and
    # the positional read mislabeled the real exit status (the incident was
    # exit 2, read as 1, for two deploy cycles #2578).
    values = {}
    for line in (proc[1] or "").splitlines():
        key, separator, raw = line.partition("=")
        if separator:
            values[key.strip()] = raw.strip()
    outcome = values.get("Result", "").lower()
    if outcome in HOST_METRICS_RESULT_ENUMS:
        result["service_result"] = outcome
    for key, prop in (("service_exec_code", "ExecMainCode"),
                      ("service_exec_status", "ExecMainStatus"),
                      ("service_n_restarts", "NRestarts")):
        if re.fullmatch(r"-?\d{1,10}", values.get(prop, "")):
            result[key] = int(values[prop])
    return result


def _host_metrics_runtime_journal(now):
    """Classify the bounded unit journal into one fixed enum; no text leaves."""
    out = {"readable": False, "lines": 0, "newest_age_sec": None,
           "signature": None, "refused_record_bytes": None}
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
    try:
        proc = subprocess.run(
            ["journalctl", "--user-unit", HOST_METRICS_SERVICE_UNIT,
             "-n", str(HOST_METRICS_JOURNAL_LINES), "--no-pager",
             "-o", "short-unix"],
            capture_output=True, text=True,
            timeout=HOST_METRICS_PROBE_TIMEOUT_SEC, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return out
    if proc.returncode != 0:
        return out
    lines = [line for line in (proc.stdout or "").splitlines() if line]
    out["readable"] = True
    out["lines"] = len(lines)
    newest = None
    signatures = []
    for line in lines:
        fields = line.split(None, 1)
        try:
            stamp = float(fields[0])
        except (IndexError, ValueError, TypeError, OverflowError):
            stamp = None
        # ``inf``/``nan``/``1e400`` are a hostile or corrupt journal line, not
        # a timestamp; parsing them into an age would raise and take the whole
        # diagnostics operation down -- the one channel that must survive.
        if stamp is not None and math.isfinite(stamp):
            newest = stamp
        lower = line.lower()
        if (re.search(r"\boom\b", lower) or "out of memory" in lower):
            signature = "oom"
        elif "timed out" in lower:
            signature = "timeout"
        elif "traceback (most recent call last)" in lower:
            signature = "python_traceback"
        elif "cannot write host metrics ring" in lower:
            signature = "sampler_refused"
            match = re.search(r"record_bytes=(\d{1,20})", line)
            if match is not None:
                out["refused_record_bytes"] = int(match.group(1))
        elif "main process exited" in lower or "failed with" in lower:
            signature = "exited"
        elif "finished " in lower:
            signature = "finished"
        elif "starting " in lower:
            signature = "starting"
        else:
            signature = "other"
        signatures.append(signature)
    if newest is not None:
        try:
            out["newest_age_sec"] = max(0, int(now - newest))
        except (ValueError, TypeError, OverflowError):
            pass
    for signature in HOST_METRICS_JOURNAL_SIGNATURES:
        if signature in signatures:
            out["signature"] = signature
            break
    return out


def _collect_host_metrics_runtime(now):
    """Fixed-shape, read-only health of the #2578 sampler units (no raw text)."""
    return {
        "service_state": _host_metrics_runtime_state(HOST_METRICS_SERVICE_UNIT),
        "service_enabled": _host_metrics_runtime_enabled(
            HOST_METRICS_SERVICE_UNIT),
        "timer_state": _host_metrics_runtime_state(HOST_METRICS_TIMER_UNIT),
        "timer_enabled": _host_metrics_runtime_enabled(
            HOST_METRICS_TIMER_UNIT),
        **_host_metrics_runtime_properties(),
        "journal": _host_metrics_runtime_journal(now),
    }


def _collect_host_metrics(home_root=None, now=None):
    """Project the fixed host metrics ring (#2578) without exposing its file.

    The ring lives at the first of the reviewed candidate directories under
    the runtime user's home (``~/.local/state/docich/host-metrics``, then
    ``~/.local/share/docich/host-metrics``, then ``~/docich-host-metrics``).
    ``main`` passes the runtime user's home explicitly (``soren.parent``); this
    projection never resolves a path from config or the environment, because
    the location is part of the sampler contract and the ring is reboot-
    persistent evidence that must not follow a mutable state_dir. Only fixed
    contract fields are emitted: never a path, a raw line or free text.
    ``ring_bytes`` is the bounded byte count actually read (0 when absent or
    refused), so the ring's on-disk size stays measurable.
    """
    now = int(time.time()) if now is None else int(now)
    home = Path(home_root) if home_root is not None else PROD_ROOT.parent
    result = {
        "status": "absent",
        "retention": {
            "interval_sec": INTERVAL_SEC,
            "max_points": MAX_POINTS,
            "max_bytes": MAX_BYTES,
        },
        "unit_order": list(UNIT_ORDER),
        "ring_bytes": 0,
        "points_available": 0,
        "points": 0,
        "samples_omitted": 0,
        "oldest_age_sec": None,
        "newest_age_sec": None,
        "ring_path_kind": None,
        "boots": 0,
        "current_boot": None,
        "spans_reboot": False,
        # Read-only health of the sampler units themselves: a stopped, failing
        # or OOM-killed sampler must be visible through this same section
        # instead of silently reading as an absent ring (#2578 deploy evidence).
        "runtime": _collect_host_metrics_runtime(now),
        "samples": [],
    }
    fd = None
    kind = None
    refused = None
    info = None
    try:
        # The writer skips candidates it cannot use; the reader must too, or a
        # dead first candidate (unreadable, or a symlink/directory where the
        # ring belongs) hides a live ring further down the list. The first
        # refusal is remembered and reported only when no candidate yielded
        # evidence -- absence is never reported while a ring exists.
        for name, relpath in HOST_METRICS_RING_CANDIDATES:
            path = home / relpath / "ring.jsonl"
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                continue
            except OSError:
                if refused is None:
                    refused = name
                continue
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                if refused is None:
                    refused = name
                os.close(fd)
                fd = None
                continue
            kind = name
            break
        if fd is None or info is None:
            if refused is not None:
                return {**result, "status": "unreadable"}
            return result
        if info.st_size > MAX_BYTES:
            # Compaction failed; a partial read of the newest lines would look
            # authoritative while silently missing retained history.
            return {**result, "status": "invalid",
                    "ring_path_kind": kind}
        raw = os.read(fd, HOST_METRICS_MAX_READ_BYTES + 1)
        if len(raw) > HOST_METRICS_MAX_READ_BYTES:
            return {**result, "status": "invalid", "ring_path_kind": kind}
        # Bounded bytes actually read. It is a count, never a path, and it
        # stays at 0 when nothing was read (absent/unreadable/refused).
        result["ring_bytes"] = min(len(raw), MAX_BYTES)
        # Which fixed candidate was read: an enum, never the path itself.
        result["ring_path_kind"] = kind
    except (OSError, ValueError):
        # Permission/IO errors and the ``O_NOFOLLOW`` symlink refusal.
        return {**result, "status": "unreadable"}
    finally:
        if fd is not None:
            os.close(fd)

    records = []
    for line in raw.splitlines():
        parsed = _host_metrics_line(line, now)
        if parsed is not None:
            records.append(parsed)
    if not records:
        # Present but nothing parseable: empty file, torn lines, or a schema
        # version this collector does not know (never guessed at).
        return {**result, "status": "invalid"}
    records = records[-MAX_POINTS:]

    # Per-interval deltas only between consecutive records of the same boot.
    # Both sides must also carry the source, and a null boot never matches:
    # a degraded record is not evidence about the interval (docich#2578).
    intervals = []
    previous = None
    for record in records:
        delta = {
            "cpu_busy_pct": None,
            "disk_read_sectors": None,
            "disk_write_sectors": None,
            "disk_io_ms": None,
        }
        same_boot = (
            previous is not None
            and record["boot"] is not None
            and previous["boot"] == record["boot"]
        )
        if same_boot:
            if previous["cpu"] is not None and record["cpu"] is not None:
                d_total = record["cpu"][0] - previous["cpu"][0]
                d_idle = record["cpu"][1] - previous["cpu"][1]
                d_iowait = record["cpu"][2] - previous["cpu"][2]
                if d_total > 0:
                    busy = 100.0 * (d_total - d_idle - d_iowait) / d_total
                    delta["cpu_busy_pct"] = round(
                        min(100.0, max(0.0, busy)), 2)
            if (previous["disk_sectors"] is not None
                    and record["disk_sectors"] is not None):
                # A counter reset inside one boot is not negative I/O; clamp
                # it to zero instead of projecting an impossible value.
                delta["disk_read_sectors"] = max(
                    0, record["disk_sectors"][0] - previous["disk_sectors"][0])
                delta["disk_write_sectors"] = max(
                    0, record["disk_sectors"][1] - previous["disk_sectors"][1])
            if previous["disk_io_ms"] is not None and record["disk_io_ms"] is not None:
                delta["disk_io_ms"] = max(
                    0, record["disk_io_ms"] - previous["disk_io_ms"])
        intervals.append(delta)
        previous = record

    samples = []
    for record, delta in zip(records, intervals):
        load = record["load"]
        samples.append({
            "age_sec": max(0, now - record["t"]),
            "boot": record["boot"],
            "t": record["t"],
            "uptime_s": record["uptime_s"],
            "load1": None if load is None else round(load[0], 2),
            "load5": None if load is None else round(load[1], 2),
            "load15": None if load is None else round(load[2], 2),
            "cpu_busy_pct": delta["cpu_busy_pct"],
            "mem_available_kb": record["mem"]["available"],
            "mem_total_kb": record["mem"]["total"],
            "swap_free_kb": record["mem"]["swap_free"],
            "swap_total_kb": record["mem"]["swap_total"],
            "dirty_kb": record["mem"]["dirty"],
            "writeback_kb": record["mem"]["writeback"],
            "psi_cpu_some10": _host_metrics_round(record["psi"][0][0]),
            "psi_cpu_full10": _host_metrics_round(record["psi"][0][1]),
            "psi_io_some10": _host_metrics_round(record["psi"][1][0]),
            "psi_io_full10": _host_metrics_round(record["psi"][1][1]),
            "psi_mem_some10": _host_metrics_round(record["psi"][2][0]),
            "psi_mem_full10": _host_metrics_round(record["psi"][2][1]),
            "disk_read_sectors": delta["disk_read_sectors"],
            "disk_write_sectors": delta["disk_write_sectors"],
            "disk_io_ms": delta["disk_io_ms"],
            "units": record["units"],
            "down_units": _host_metrics_down_units(record["units"]),
        })
    samples = _host_metrics_trim_samples(samples, SAMPLES_MAX)

    newest = records[-1]
    oldest = records[0]
    # A future timestamp (clock step) is clamped to age 0 rather than reported
    # as negative age; it is not evidence of health either way.
    newest_age_sec = max(0, now - newest["t"])
    # Only non-null boot IDs identify a reboot; a null boot is unknown and must
    # not collapse into a fake "same boot" or a fake second boot.
    boots = {record["boot"] for record in records if record["boot"] is not None}
    current_boot = next(
        (record["boot"] for record in reversed(records)
         if record["boot"] is not None),
        None,
    )
    result.update({
        "status": "stale" if newest_age_sec > STALE_SEC else "ok",
        "points_available": len(records),
        "points": len(samples),
        "oldest_age_sec": max(0, now - oldest["t"]),
        "newest_age_sec": newest_age_sec,
        "boots": len(boots),
        "current_boot": current_boot,
        "spans_reboot": len(boots) > 1,
        "samples": samples,
    })
    return result


CPU_PROFILE_REPORT = Path("/tmp/docich-cpu-profile-latest.json")
CPU_PROFILE_MAX_BYTES = 32768
CPU_PROFILE_MAX_COMPONENTS = 30
CPU_PROFILE_MAX_SPAWNS = 30
# Output-pressure caps. The producer already orders both lists busiest-first,
# so only the tail is dropped, and the omitted counts stay visible.
CPU_PROFILE_BUDGET_COMPONENTS = 10
CPU_PROFILE_BUDGET_SPAWNS = 10
# Last-resort floor. Even under the hardest output pressure the busiest rows
# stay, so the projection can still name the top consumers / spawn paths
# (azumag/docich#970) rather than showing host totals only.
CPU_PROFILE_FLOOR_COMPONENTS = 5
CPU_PROFILE_FLOOR_SPAWNS = 5
CPU_PROFILE_FRESH_SEC = 900


# Fixed broadcast-audio gap measurement (docich#1811). The one-shot reviewed
# helper writes this bounded report; the collector projects only fixed fields.
AUDIO_GAP_REPORT = Path("/tmp/docich-audio-gap-latest.json")
AUDIO_GAP_MAX_BYTES = 32768
AUDIO_GAP_FRESH_SEC = 900
AUDIO_GAP_MAX_ITEMS = 50

# Read-only projection of the audio-priority keeper (docich#1811): the
# root-owned installed copies vs the deployed tree, the systemd unit state,
# the keeper process, per-target thread priorities, and the hanjuku mirror's
# effective capture framerate. Nothing here mutates the VM.
AUDIO_PRIORITY_SCRIPT = Path("/usr/local/libexec/azumag-vm-ops/audio_priority/priority.py")
AUDIO_PRIORITY_UNIT_FILE = Path("/etc/systemd/system/docich-audio-priority.service")
AUDIO_PRIORITY_SERVICE = "docich-audio-priority"
AUDIO_PRIORITY_MAX_FILE_BYTES = 262144
AUDIO_PRIORITY_TARGET_NICE = -10
AUDIO_PRIORITY_KINDS = ("retroarch", "pulseaudio", "encoder-ffmpeg", "twica-feeder")
AUDIO_PRIORITY_MAX_PIDS_PER_KIND = 8
AUDIO_PRIORITY_JOURNAL_LINES = 400
AUDIO_PRIORITY_ENABLED_STATES = frozenset({
    "enabled", "enabled-runtime", "disabled", "masked", "masked-runtime",
    "not-found", "static", "alias", "linked", "linked-runtime", "indirect", "bad",
})


def _cpu_profile_number(value, *, maximum=10**12):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    value = float(value)
    if value < 0 or value > maximum:
        return None
    return int(value) if value.is_integer() else value


def _cpu_profile_label(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return None
    if value in {
        "retroarch", "x_server", "xdotool", "x_capture_tool", "x_query_tool",
        "audio_server", "audio_cli", "media_player", "browser", "obs", "tmux",
        "node", "sshd", "tailscale", "container_runtime", "curl", "git", "sleep",
        "inotifywait", "jq", "cat", "grep", "sed", "awk", "tail", "head",
        "cut", "tr", "wc", "date", "stat", "ps", "pgrep", "find", "sort",
        "readlink", "realpath", "basename", "dirname", "tee", "touch", "rm",
        "mv", "mkdir", "chmod", "cmp", "env", "nice", "timeout", "cksum",
        "sha256sum", "mktemp", "flock", "xargs",
        "kernel", "shell", "python", "other", "profiler",
    }:
        return value
    if re.fullmatch(r"ffmpeg:(?:capture|stream|x11grab|other)", value):
        return value
    if re.fullmatch(r"(?:game|docich):[a-z0-9-]{1,32}", value):
        return value
    if re.fullmatch(r"(?:py|sh):[A-Za-z0-9_.-]{1,48}", value):
        return value
    if re.fullmatch(r"worker:[a-z0-9_-]{1,48}", value):
        return value
    return None


def _cpu_profile_comm_label(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,15}", value):
        return None
    return value


def _collect_cpu_profile(now, path=CPU_PROFILE_REPORT):
    """Project one fixed profiler report without exposing arbitrary /tmp JSON."""
    result = {
        "schema_version": 1,
        "status": "missing",
        "present": False,
        "readable": False,
    }
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= CPU_PROFILE_MAX_BYTES:
            return {**result, "present": True, "status": "invalid"}
        raw = os.read(fd, CPU_PROFILE_MAX_BYTES + 1)
        if len(raw) > CPU_PROFILE_MAX_BYTES:
            return {**result, "present": True, "status": "invalid"}
        data = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        return result
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {**result, "present": True, "status": "unreadable"}
    finally:
        if fd is not None:
            os.close(fd)

    if not isinstance(data, dict) or data.get("schema") != "docich.cpu_profile.v1":
        return {**result, "present": True, "status": "invalid"}
    meta = data.get("meta")
    host = data.get("host")
    if not isinstance(meta, dict) or not isinstance(host, dict):
        return {**result, "present": True, "status": "invalid"}

    scenario = meta.get("scenario")
    generated_at = _cpu_profile_number(meta.get("generated_at"), maximum=253402300799)
    if (not isinstance(scenario, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", scenario)
            or generated_at is None):
        return {**result, "present": True, "status": "invalid"}
    age_sec = int(now - generated_at)
    if age_sec < -5:
        return {**result, "present": True, "status": "invalid"}

    projected_meta = {"scenario": scenario, "generated_at": int(generated_at)}
    for key, maximum in {
        "elapsed_sec": 1000,
        "interval_sec": 20,
        "samples": 10000,
        "ncpu": 4096,
        "worker_pidfiles_resolved": 10000,
        "profiler_cpu_sec": 1000,
    }.items():
        value = _cpu_profile_number(meta.get(key), maximum=maximum)
        if value is not None:
            projected_meta[key] = value

    projected_host = {}
    for key, maximum in {
        "cpu_busy_pct": 100,
        "cpu_iowait_pct": 100,
        "cpu_busy_pct_p50": 100,
        "cpu_busy_pct_p95": 100,
        "load1_p50": 100000,
        "load1_p95": 100000,
        "runnable_p50": 100000,
        "runnable_p95": 100000,
        "forks": 10**9,
        "forks_per_sec": 10**7,
        "ctxt_per_sec": 10**9,
        "processes_start": 10**7,
        "processes_end": 10**7,
        "processes_max": 10**7,
    }.items():
        value = _cpu_profile_number(host.get(key), maximum=maximum)
        if value is not None:
            projected_host[key] = value

    components = []
    raw_components = data.get("components")
    if isinstance(raw_components, list):
        for item in raw_components[:CPU_PROFILE_MAX_COMPONENTS]:
            if not isinstance(item, dict):
                continue
            label = _cpu_profile_label(item.get("component"))
            if label is None:
                continue
            row = {"component": label}
            for key, maximum in {
                "cpu_pct": 10**6,
                "cpu_share_pct": 100,
                "processes": 10**7,
                "spawned": 10**9,
                "vcs_per_sec": 10**9,
                "nvcs_per_sec": 10**9,
                "rss_kb": 10**12,
                "threads": 10**7,
            }.items():
                value = _cpu_profile_number(item.get(key), maximum=maximum)
                if value is not None:
                    row[key] = value
            components.append(row)

    spawns = []
    raw_spawns = data.get("spawns")
    if isinstance(raw_spawns, list):
        for item in raw_spawns[:CPU_PROFILE_MAX_SPAWNS]:
            if not isinstance(item, dict):
                continue
            component = _cpu_profile_label(item.get("component"))
            spawner = _cpu_profile_label(item.get("spawner"))
            count = _cpu_profile_number(item.get("count"), maximum=10**9)
            if component is None or spawner is None or count is None:
                continue
            spawns.append({"component": component, "spawner": spawner, "count": int(count)})

    # Compact summary that survives output pressure: even when the per-row
    # lists are trimmed to nothing, the top CPU consumers and spawn paths stay
    # visible (azumag/docich#970). The byte size is not a fixed cap: in the
    # synthetic fixture the value part is 58B usually (217B with long labels)
    # for cpu_top and 75B usually (375B with long labels) for spawn_top; labels
    # are already bounded to 64 chars and sanitized by _cpu_profile_label.
    cpu_top_parts = []
    for row in components[:3]:
        component = row.get("component")
        pct = row.get("cpu_pct")
        if isinstance(pct, (int, float)) and not isinstance(pct, bool):
            cpu_top_parts.append(f"{component} {pct:.0f}%")
        else:
            cpu_top_parts.append(str(component))
    cpu_top = " | ".join(cpu_top_parts)
    if len(components) > 3:
        # The aggregate tail is labeled `others` (plural) so it cannot be
        # confused with a real `other` component row in the head.
        others = 0.0
        for row in components[3:]:
            pct = row.get("cpu_pct")
            if isinstance(pct, (int, float)) and not isinstance(pct, bool):
                others += pct
        cpu_top = (cpu_top + f" | others {others:.0f}%") if cpu_top else f"others {others:.0f}%"

    spawn_top_parts = []
    for row in spawns[:3]:
        component = row.get("component")
        spawner = row.get("spawner")
        if component is None or spawner is None:
            continue
        count = row.get("count")
        if isinstance(count, (int, float)) and not isinstance(count, bool):
            spawn_top_parts.append(f"{component}<-{spawner} {count:.0f}")
        else:
            spawn_top_parts.append(f"{component}<-{spawner}")
    spawn_top = " | ".join(spawn_top_parts)

    # The residual `other` component is otherwise opaque. Expose only the
    # profiler's sanitized comm labels (<=16 chars, lowercase) and their CPU,
    # so the short-lived/generic consumers can be attributed without emitting
    # argv, paths or PIDs.
    other_top_parts = []
    raw_other = data.get("other_comms")
    if isinstance(raw_other, list):
        for item in raw_other[:5]:
            if not isinstance(item, dict):
                continue
            comm = _cpu_profile_comm_label(item.get("comm"))
            pct = _cpu_profile_number(item.get("cpu_pct"), maximum=10**6)
            if comm is None or pct is None:
                continue
            other_top_parts.append(f"{comm} {pct:.0f}%")
    other_top = " | ".join(other_top_parts)

    return {
        "schema_version": 1,
        "status": "ok" if age_sec <= CPU_PROFILE_FRESH_SEC else "stale",
        "present": True,
        "readable": True,
        "age_sec": age_sec,
        "meta": projected_meta,
        "host": projected_host,
        "components": components,
        "components_truncated": max(len(raw_components) - len(components), 0)
            if isinstance(raw_components, list) else 0,
        "spawns": spawns,
        "cpu_top": cpu_top,
        "spawn_top": spawn_top,
        "other_top": other_top,
    }


def _collect_audio_gaps(now, path=AUDIO_GAP_REPORT):
    """Project one fixed broadcast-audio gap measurement (docich#1811) without
    exposing arbitrary /tmp JSON. The helper only records the encoder input
    monitor and counts digital-zero runs, so this projection carries the fixed
    counters and never raw audio or paths."""
    result = {
        "schema_version": 1,
        "status": "missing",
        "present": False,
        "readable": False,
    }
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= AUDIO_GAP_MAX_BYTES:
            return {**result, "present": True, "status": "invalid"}
        raw = os.read(fd, AUDIO_GAP_MAX_BYTES + 1)
        if len(raw) > AUDIO_GAP_MAX_BYTES:
            return {**result, "present": True, "status": "invalid"}
        data = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        return result
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {**result, "present": True, "status": "unreadable"}
    finally:
        if fd is not None:
            os.close(fd)

    if not isinstance(data, dict) or data.get("schema") != "docich.audio_gap.v1":
        return {**result, "present": True, "status": "invalid"}
    meta = data.get("meta")
    gaps = data.get("gaps")
    if not isinstance(meta, dict) or not isinstance(gaps, dict):
        return {**result, "present": True, "status": "invalid"}

    generated_at = _cpu_profile_number(meta.get("generated_at"), maximum=253402300799)
    # The methodology (#1811) is the encoder input monitor at 48kHz stereo,
    # so anything else is not this measurement.
    if (meta.get("monitor") != "soren_null.monitor"
            or meta.get("rate") != 48000 or meta.get("channels") != 2
            or generated_at is None):
        return {**result, "present": True, "status": "invalid"}
    duration = _cpu_profile_number(meta.get("duration_sec"), maximum=10000)
    if duration is None or not 80 <= duration <= 120:
        return {**result, "present": True, "status": "invalid"}
    ratio = meta.get("nonzero_frame_ratio")
    if (isinstance(ratio, bool) or not isinstance(ratio, (int, float))
            or not math.isfinite(ratio) or not 0 <= ratio <= 1):
        return {**result, "present": True, "status": "invalid"}

    age_sec = int(now - generated_at)
    if age_sec < -5:
        return {**result, "present": True, "status": "invalid"}

    count = _cpu_profile_number(gaps.get("count"), maximum=10000)
    total_ms = _cpu_profile_number(gaps.get("total_ms"), maximum=10_000_000)
    max_ms = _cpu_profile_number(gaps.get("max_ms"), maximum=1000)
    long_silences = _cpu_profile_number(gaps.get("long_silences"), maximum=100_000)
    if count is None or total_ms is None or max_ms is None or long_silences is None:
        return {**result, "present": True, "status": "invalid"}

    raw_items = gaps.get("items")
    items = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if len(items) >= AUDIO_GAP_MAX_ITEMS:
                break
            if not isinstance(item, dict):
                continue
            start_ms = _cpu_profile_number(item.get("start_ms"), maximum=10_000_000)
            ms = _cpu_profile_number(item.get("ms"), maximum=1000)
            if start_ms is None or ms is None:
                continue
            items.append({"start_ms": start_ms, "ms": ms})

    return {
        "schema_version": 1,
        "status": "ok" if age_sec <= AUDIO_GAP_FRESH_SEC else "stale",
        "present": True,
        "readable": True,
        "age_sec": age_sec,
        "meta": {
            "generated_at": int(generated_at),
            "monitor": "soren_null.monitor",
            "rate": 48000,
            "channels": 2,
            "duration_sec": round(duration, 2),
            "nonzero_frame_ratio": round(ratio, 4),
        },
        "gaps": {
            "count": count,
            "total_ms": total_ms,
            "max_ms": max_ms,
            "long_silences": long_silences,
            "items": items,
        },
    }


def _audio_priority_sha256(path, limit=AUDIO_PRIORITY_MAX_FILE_BYTES):
    """Hash one installed copy without following symlinks; None when the
    file is absent, oversized, or not a regular file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    digest = hashlib.sha256()
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            return None
        remaining = info.st_size
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


def _audio_priority_module():
    """Load the keeper's own classification from the deployed tree so the
    target summary can never drift from priority.py's matching rules.
    Returns None whenever the reviewed module cannot be loaded; the summary
    then degrades to `classification: unavailable` instead of guessing."""
    path = PROD_ROOT / "ops" / "audio_priority" / "priority.py"
    name = "docich_ops_audio_priority"
    try:
        spec = _importlib_util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            return None
        module = _importlib_util.module_from_spec(spec)
        # dataclassの型解決はsys.modulesを参照するため、実行前に登録する。
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)
            raise
    except Exception:
        return None
    return module if hasattr(module, "wanted") and hasattr(module, "Proc") else None


def _audio_priority_thread_nice(proc_root, pid, tid):
    """Read one task's current nice from /proc/<pid>/task/<tid>/stat. The comm
    field may contain spaces or ')', so split after the last ')' like the
    keeper's own _thread() parser."""
    text = _read_text_capped(proc_root / str(pid) / "task" / str(tid) / "stat", limit=2048)
    if not text:
        return None
    rest = text.rsplit(")", 1)
    if len(rest) != 2:
        return None
    fields = rest[1].split()
    try:
        return int(fields[16])
    except (IndexError, ValueError):
        return None


def _audio_priority_cgroup(proc_root, pid):
    """Project the unified cgroup path of one audio process (#2008).

    A read-only, bounded system path. It answers a question diagnostics could
    not answer before: whether the shared PulseAudio server lives inside the
    ``soren-bgm.service`` cgroup (killed by that unit's restart, 2026-10-11
    05:55/08:05) or under the user manager as intended. Anything unexpected is
    projected as None, never guessed.
    """
    raw = _read_text_capped(proc_root / str(pid) / "cgroup", limit=4096)
    if not raw:
        return None
    for line in raw.splitlines():
        # Unified hierarchy is the only line we read: "0::/system.slice/x.service".
        if not line.startswith("0::"):
            continue
        path = line[3:].strip()
        if not path.startswith("/") or len(path) > 256:
            return None
        if not all(ch.isalnum() or ch in "/@._:-" for ch in path):
            return None
        return path
    return None


def _audio_priority_scan(proc_root, uid, module):
    """One bounded /proc pass. Returns (targets, keeper, mirror):
    targets maps each keeper kind to a bounded summary, keeper is the keeper
    process dict, mirror is the hanjuku mirror ffplay summary."""
    targets = {
        kind: {"present": False, "processes": 0, "threads": 0,
               "max_nice": None, "at_target": 0, "cgroup": None}
        for kind in AUDIO_PRIORITY_KINDS
    }
    keeper = {"present": False, "pid": None, "threads": 0}
    mirror = {"present": False, "framerate": None}
    try:
        names = os.listdir(proc_root)
    except OSError:
        return targets, keeper, mirror
    try:
        pids = sorted(int(name) for name in names if name.isdigit())
    except ValueError:
        return targets, keeper, mirror
    wanted = getattr(module, "wanted", None)
    proc_cls = getattr(module, "Proc", None)
    for pid in pids[:4000]:
        base = proc_root / str(pid)
        comm = _read_text_capped(base / "comm", limit=64)
        if not comm:
            continue
        comm = comm.strip()
        cmdline = _read_text_capped(base / "cmdline", limit=4096)
        argv = tuple(part for part in (cmdline or "").split("\0") if part)
        try:
            owner = os.stat(base).st_uid
        except OSError:
            continue
        if comm.startswith("python") and any(
                arg.endswith("audio_priority/priority.py") for arg in argv):
            try:
                threads = len(os.listdir(base / "task"))
            except OSError:
                threads = 0
            keeper = {"present": True, "pid": pid, "threads": threads}
            continue
        if comm == "ffplay" and "x11grab" in argv:
            # presentation.py L484 launches the mirror ffplay with the
            # single-dash "-framerate" (ffmpeg input option), not "--framerate";
            # accept both spellings so the effective value is readable either way.
            for index, arg in enumerate(argv):
                if arg in ("--framerate", "-framerate") and index + 1 < len(argv):
                    try:
                        mirror = {"present": True,
                                  "framerate": min(max(int(argv[index + 1]), 0), 240)}
                    except ValueError:
                        mirror = {"present": True, "framerate": None}
                    break
            else:
                mirror = {"present": True, "framerate": None}
            continue
        if wanted is None or proc_cls is None or owner != uid:
            continue
        try:
            proc = proc_cls(pid=pid, uid=owner, comm=comm, argv=argv)
        except TypeError:
            # The deployed keeper's Proc grew required fields; never guess.
            continue
        kind = wanted(proc, uid)
        if kind not in targets:
            continue
        summary = targets[kind]
        summary["present"] = True
        if summary["processes"] < AUDIO_PRIORITY_MAX_PIDS_PER_KIND:
            summary["processes"] += 1
        # The first process of each kind defines its owning cgroup; one bounded
        # path per kind is what the #2008 safety question needs.
        if summary["cgroup"] is None:
            summary["cgroup"] = _audio_priority_cgroup(proc_root, pid)
        try:
            tids = os.listdir(base / "task")
        except OSError:
            continue
        for tid in tids:
            nice = _audio_priority_thread_nice(proc_root, pid, tid)
            if nice is None:
                continue
            summary["threads"] += 1
            summary["max_nice"] = nice if summary["max_nice"] is None else max(summary["max_nice"], nice)
            if nice <= AUDIO_PRIORITY_TARGET_NICE:
                summary["at_target"] += 1
    return targets, keeper, mirror


def _audio_priority_journal_tail(timeout=5):
    """Bounded recent keeper journal text, or None when journalctl is
    unavailable or refuses the reader (e.g. no membership). Output never
    leaves this collector; only fixed counters are projected."""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
    try:
        proc = subprocess.run(
            ["journalctl", "-u", f"{AUDIO_PRIORITY_SERVICE}.service",
             "--since", "-24h", "-n", str(AUDIO_PRIORITY_JOURNAL_LINES),
             "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout or ""


def _collect_audio_priority(proc_root=Path("/proc"), uid=None,
                            script_path=None, unit_file=None,
                            deployed_script=None, deployed_unit=None):
    """Read-only projection of the audio-priority keeper state (docich#1811).

    Every sub-part fails open: a missing file, an unreadable journal, or an
    unavailable systemd query degrades to `None`/`unknown` and never raises,
    so one broken probe cannot blank the rest of diagnostics. The section
    never mutates the VM and never affects the top-level payload status; the
    owner reads the composite `status` to see whether the installed root-owned
    copies still match the deployed reviewed tree."""
    if uid is None:
        uid = os.getuid()
    script_path = Path(script_path) if script_path is not None else AUDIO_PRIORITY_SCRIPT
    unit_file = Path(unit_file) if unit_file is not None else AUDIO_PRIORITY_UNIT_FILE
    deployed_script = (Path(deployed_script) if deployed_script is not None
                       else PROD_ROOT / "ops" / "audio_priority" / "priority.py")
    deployed_unit = (Path(deployed_unit) if deployed_unit is not None
                     else PROD_ROOT / "ops" / "audio_priority" / "docich-audio-priority.service")

    files = {}
    for label, installed, deployed in (
            ("script", script_path, deployed_script),
            ("unit", unit_file, deployed_unit)):
        installed_sha = _audio_priority_sha256(installed)
        if installed_sha is None:
            # An absent copy carries its whole fact in present=false; the
            # huge-telemetry envelope has no room for null padding.
            files[label] = {"present": False}
            continue
        deployed_sha = _audio_priority_sha256(deployed)
        files[label] = {
            "present": True,
            # 16-hex prefix + the boolean verdict is the evidence; the full
            # digest of the deployed side is reproducible from the repo.
            "sha256_16": installed_sha[:16],
            "matches_deployed": installed_sha == deployed_sha,
        }

    service = {}
    result = _systemctl_system(["is-enabled", AUDIO_PRIORITY_SERVICE])
    if result is not None and result[1] in AUDIO_PRIORITY_ENABLED_STATES:
        service["enabled"] = result[1]
    active = _unit_is_active(AUDIO_PRIORITY_SERVICE, scope="system")
    if active is not None:
        service["active"] = active
    result = _systemctl_system(
        ["show", AUDIO_PRIORITY_SERVICE, "--property=SubState",
         "--property=NRestarts", "--property=ExecMainStartTimestamp"])
    if result is not None and result[0] == 0:
        # systemctl show prints Key=Value lines in its own property order,
        # never the request order, so parse by label -- the positional read
        # mis-assigned SubState ("running") into started_at in production.
        for line in result[1].splitlines():
            key, sep, value = line.partition("=")
            if not sep:
                continue
            key = key.strip()
            value = value.strip()
            if key == "SubState" and re.fullmatch(r"[a-z]+", value):
                service["sub_state"] = value
            elif key == "NRestarts" and re.fullmatch(r"\d{1,7}", value):
                service["n_restarts"] = int(value)
            elif (key == "ExecMainStartTimestamp" and 0 < len(value) <= 64
                  and re.fullmatch(r"[0-9A-Za-z:+\- ]+", value)):
                service["started_at"] = value

    module = _audio_priority_module()
    targets, keeper, mirror = _audio_priority_scan(
        proc_root, uid, module if module is not None else None)

    # Zero counters and empty breakdowns are omitted (absence means zero) so
    # the idle shape stays byte-cheap under the huge-telemetry envelope.
    journal = {"available": False}
    reniced_by_kind = {}
    text = _audio_priority_journal_tail()
    if text is not None:
        journal["available"] = True
        reniced = warnings = scan_failures = restarts = 0
        for line in text.splitlines():
            if line.startswith("audio-priority: reniced "):
                reniced += 1
                match = re.search(r"kind=([a-z-]+)", line)
                kind = match.group(1) if match else "other"
                if kind not in AUDIO_PRIORITY_KINDS:
                    kind = "other"
                reniced_by_kind[kind] = reniced_by_kind.get(kind, 0) + 1
            elif line.startswith("audio-priority: warning"):
                warnings += 1
            elif line.startswith("audio-priority: scan failed"):
                scan_failures += 1
            elif line.startswith("audio-priority: started "):
                restarts += 1
        if reniced:
            journal["reniced_24h"] = reniced
        if warnings:
            journal["warnings_24h"] = warnings
        if scan_failures:
            journal["scan_failures_24h"] = scan_failures
        if restarts:
            journal["restarts_24h"] = restarts
    if reniced_by_kind:
        journal["reniced_by_kind"] = reniced_by_kind

    enabled = service.get("enabled")
    if enabled == "not-found":
        status = "missing"
    elif enabled is None and "active" not in service:
        status = "unknown"
    elif (service.get("active") is True and keeper["present"]
          and files["script"].get("matches_deployed") is True
          and files["unit"].get("matches_deployed") is True):
        status = "ok"
    else:
        status = "degraded"

    # Absent targets/kinds and unknown service fields are omitted rather than
    # serialized as null so the fixed section stays small under the envelope.
    section = {
        "schema_version": 1,
        "status": status,
        "uid": uid,
        "service": service,
        "files": files,
        "targets": {kind: summary for kind, summary in targets.items()
                    if summary["present"]},
        "journal": journal,
    }
    if module is None:
        # The classification is worth its bytes only when it broke.
        section["classification"] = "unavailable"
    if keeper["present"]:
        section["keeper"] = keeper
    if mirror["present"]:
        section["mirror"] = mirror
    return section


BROWSER_OWNER_LABELS = (
    "soren-shared-overlay",
    "soren-game",
    "twica",
    "nethack-tiles",
    "obs",
    "webui",
    "other",
)
BROWSER_TYPE_LABELS = ("browser", "gpu", "renderer", "utility", "other")
BROWSER_CPU_SAMPLE_SEC = 0.5
# The first production samples still classified every Chromium child as
# "browser" even at 16 KiB. Read the full bounded line (64 KiB) and derive the
# type from the raw NUL-separated bytes at an argv boundary.
BROWSER_CMDLINE_BYTES = 65536
_BROWSER_EXECUTABLES = frozenset({
    "chrome",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "headless_shell",
    "chrome-headless-shell",
})
_BROWSER_SKIPPED_EXECUTABLES = frozenset({"chrome_crashpad_handler"})


def _browser_read_cmdline(proc_root, pid, limit=BROWSER_CMDLINE_BYTES):
    """Read argv only to classify; the raw argv never leaves this module."""
    try:
        with open(proc_root / str(pid) / "cmdline", "rb") as fh:
            raw = fh.read(limit)
    except OSError:
        return None
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _browser_read_stat(proc_root, pid):
    try:
        text = (proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    rpar = text.rfind(")")
    if rpar < 0:
        return None
    fields = text[rpar + 2:].split()
    try:
        return {
            "ppid": int(fields[1]),
            "ticks": int(fields[11]) + int(fields[12]),
            "starttime": int(fields[19]),
        }
    except (IndexError, ValueError):
        return None


def _browser_read_rss_kb(proc_root, pid):
    try:
        text = (proc_root / str(pid) / "status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return 0


def _browser_owner_label(argv):
    """Map a process/ancestor argv to a fixed owner label; no raw text escapes."""
    if not argv:
        return None
    joined = "\0".join(argv)
    exe = os.path.basename(argv[0])
    if ("shared_overlay.mjs" in joined
            or "start_shared_overlay_service.sh" in joined
            or "soren-shared-overlay" in joined):
        return "soren-shared-overlay"
    if "soviet_local" in joined:
        return "soren-game"
    if "twica" in joined:
        return "twica"
    if "nethack_tiles" in joined:
        return "nethack-tiles"
    if exe in {"obs", "obs-studio", "obs-browser-page"} or "obs-browser" in joined:
        return "obs"
    if "docich-webui" in joined or "webui" in joined:
        return "webui"
    return None


def _browser_read_type(proc_root, pid, limit=BROWSER_CMDLINE_BYTES):
    """Return (type, type_arg_offset, cmdline_bytes) from the raw cmdline.

    The argv split is not enough: production classified every Chromium child
    as "browser" even at 16 KiB, so the type is derived from the raw
    NUL-separated bytes at an argv boundary. Only the fixed label, offset and
    length leave this function; the bytes themselves never do.
    """
    try:
        with open(proc_root / str(pid) / "cmdline", "rb") as fh:
            raw = fh.read(limit)
    except OSError:
        return "browser", None, 0
    match = re.search(rb"(?:^|\x00)--type=([a-z-]+)", raw)
    if match is None:
        return "browser", None, len(raw)
    value = match.group(1).decode("ascii", "ignore")
    offset = match.start()
    if value == "gpu-process":
        return "gpu", offset, len(raw)
    if "renderer" in value:
        return "renderer", offset, len(raw)
    if "utility" in value:
        return "utility", offset, len(raw)
    return "other", offset, len(raw)


def _browser_owner_for_pid(proc_root, pid, stat, cmdlines):
    # Ancestors win over the Chromium process' own argv: a URL in the page
    # arguments must not re-label a browser launched by a known subsystem.
    current = stat["ppid"]
    for _ in range(6):
        if current <= 1:
            break
        argv = cmdlines.get(current)
        if argv is None:
            argv = _browser_read_cmdline(proc_root, current)
            cmdlines[current] = argv
        owner = _browser_owner_label(argv)
        if owner is not None:
            return owner
        parent_stat = _browser_read_stat(proc_root, current)
        parent = parent_stat["ppid"] if parent_stat else 1
        if parent == current:
            break
        current = parent
    # Only an orphaned Chromium (or one launched directly by an unclassified
    # parent) falls back to its own argv.
    return _browser_owner_label(cmdlines.get(pid)) or "other"


def _collect_browser_processes(proc_root=Path("/proc"), *,
                               sample_sec=BROWSER_CPU_SAMPLE_SEC,
                               sleep=time.sleep, clock=time.monotonic,
                               clk_tck=None):
    """Read-only Chromium inventory by fixed owner/type labels (#970 follow-up).

    Answers "which browser/view is eating CPU" without exposing any cmdline,
    URL, path or PID. Only fixed owner/type labels, counts, CPU percentages,
    RSS and age are emitted. A short two-sample CPU delta separates an idle
    helper from a busy renderer; an unavailable /proc fails open to
    ``unknown`` instead of failing the whole diagnostics operation.
    """
    if clk_tck is None:
        try:
            clk_tck = int(os.sysconf("SC_CLK_TCK"))
        except (AttributeError, ValueError, OSError):
            clk_tck = 100
    if clk_tck <= 0:
        clk_tck = 100
    try:
        pids = [int(name) for name in os.listdir(proc_root) if name.isdigit()]
    except OSError:
        return {"schema_version": 1, "status": "unknown", "scan_complete": 0}

    cmdlines = {}
    rows = []
    for pid in pids:
        argv = _browser_read_cmdline(proc_root, pid)
        cmdlines[pid] = argv
        if not argv:
            continue
        exe = os.path.basename(argv[0])
        if exe in _BROWSER_SKIPPED_EXECUTABLES:
            continue
        if exe not in _BROWSER_EXECUTABLES and "chrom" not in exe.lower():
            continue
        stat = _browser_read_stat(proc_root, pid)
        if stat is None:
            continue
        btype, type_offset, cmdline_bytes = _browser_read_type(proc_root, pid)
        rows.append({
            "pid": pid,
            "type": btype,
            "type_offset": type_offset,
            "cmdline_bytes": cmdline_bytes,
            "owner": _browser_owner_for_pid(proc_root, pid, stat, cmdlines),
            "ticks": stat["ticks"],
            "starttime": stat["starttime"],
            "rss_kb": _browser_read_rss_kb(proc_root, pid),
        })

    if not rows:
        return {
            "schema_version": 1,
            "status": "empty",
            "scan_complete": 1,
            "total": 0,
            "cpu_pct_total": 0.0,
            "rss_mb_total": 0.0,
            "by_type": {},
            "by_owner": {},
            "type_sources": {"type_arg": 0, "no_type_arg": 0},
        }

    start_clock = clock()
    if sample_sec > 0:
        sleep(sample_sec)
    end_clock = clock()
    elapsed = end_clock - start_clock
    if elapsed <= 0:
        elapsed = 0.001
    cpu_cap = 100.0 * max(1, os.cpu_count() or 1)
    for row in rows:
        after = _browser_read_stat(proc_root, row["pid"])
        delta = after["ticks"] - row["ticks"] if after else 0
        row["cpu_pct"] = round(
            min(max(delta, 0) / (elapsed * clk_tck) * 100.0, cpu_cap), 1)

    uptime = None
    try:
        uptime = float((proc_root / "uptime").read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError, UnicodeDecodeError):
        pass

    by_type = {}
    by_owner = {}
    type_sources = {"type_arg": 0, "no_type_arg": 0}
    for row in rows:
        if row["type_offset"] is None:
            type_sources["no_type_arg"] += 1
        else:
            type_sources["type_arg"] += 1
        by_type[row["type"]] = by_type.get(row["type"], 0) + 1
        owner = by_owner.setdefault(row["owner"], {"count": 0, "cpu_pct": 0.0})
        owner["count"] += 1
        owner["cpu_pct"] += row["cpu_pct"]
        if row["type"] == "gpu":
            owner["gpu_cpu_pct"] = owner.get("gpu_cpu_pct", 0.0) + row["cpu_pct"]
        elif row["type"] == "renderer":
            owner["renderer_cpu_pct"] = owner.get("renderer_cpu_pct", 0.0) + row["cpu_pct"]

    for owner in by_owner.values():
        owner["cpu_pct"] = round(owner["cpu_pct"], 1)
        for key in ("gpu_cpu_pct", "renderer_cpu_pct"):
            if key in owner:
                owner[key] = round(owner[key], 1)

    result = {
        "schema_version": 1,
        "status": "ok",
        "scan_complete": 1,
        "total": len(rows),
        "cpu_pct_total": round(sum(row["cpu_pct"] for row in rows), 1),
        "rss_mb_total": round(sum(row["rss_kb"] for row in rows) / 1024.0, 1),
        "by_type": by_type,
        "by_owner": by_owner,
        "type_sources": type_sources,
    }
    if uptime is not None and rows:
        ages = [max(0.0, uptime - row["starttime"] / clk_tck) for row in rows]
        result["oldest_age_sec"] = int(max(ages))
    if rows:
        result["cmdline_bytes_max"] = max(row["cmdline_bytes"] for row in rows)
        offsets = [row["type_offset"] for row in rows if row["type_offset"] is not None]
        if offsets:
            result["max_type_arg_offset"] = max(offsets)
    return result


def _empty_opencode_attribution_bucket():
    return {
        "sessions": 0,
        "messages": 0,
        "message_data_chars": 0,
        "message_max_chars": 0,
        "parts": 0,
        "part_data_chars": 0,
        "part_max_chars": 0,
        "events": 0,
        "event_data_chars": 0,
        "event_max_chars": 0,
    }


def _collect_opencode_session_attribution(
    db_path,
    now,
    *,
    window_sec=OPENCODE_ATTRIBUTION_WINDOW_SEC,
    query_timeout_sec=OPENCODE_ATTRIBUTION_QUERY_TIMEOUT_SEC,
):
    """Read fixed OpenCode caller aggregates without exposing stored content."""

    db_path = Path(db_path)
    result = {
        "version": 1,
        "present": False,
        "scan_complete": False,
        "schema_supported": False,
        "window_sec": int(window_sec),
        "buckets": {
            bucket: _empty_opencode_attribution_bucket()
            for bucket in OPENCODE_CALLER_BUCKETS
        },
        "coverage": {
            "scan_complete": False,
            "all_sessions": 0,
            "attributed_sessions": 0,
            "unattributed_sessions": 0,
            "unattributed_latest_age_sec": 0,
            "unattributed_15m_sessions": 0,
            "unattributed_1h_sessions": 0,
            "unattributed_2h_sessions": 0,
            "unattributed_6h_sessions": 0,
        },
    }
    try:
        st = db_path.lstat()
    except FileNotFoundError:
        result["scan_complete"] = True
        return result
    except OSError:
        return result
    result["present"] = True
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        return result

    con = None
    deadline = time.monotonic() + max(0.05, float(query_timeout_sec))
    try:
        con = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            timeout=min(max(float(query_timeout_sec), 0.05), 1.0),
        )
        con.execute("PRAGMA query_only = ON")
        con.execute("PRAGMA busy_timeout = 500")
        con.set_progress_handler(
            lambda: 1 if time.monotonic() >= deadline else 0,
            1000,
        )

        required = {
            "session": {"id", "title", "time_created"},
            "message": {"id", "session_id", "data"},
            "part": {"id", "session_id", "data"},
            "event": {"id", "aggregate_id", "data"},
        }
        for table, columns in required.items():
            actual = {
                str(row[1])
                for row in con.execute(f"PRAGMA table_info({table})")
                if len(row) > 1
            }
            if not columns.issubset(actual):
                return result
        result["schema_supported"] = True

        cutoff_ms = int((float(now) - max(0, int(window_sec))) * 1000)
        titles = tuple(OPENCODE_CALLER_TITLES)
        placeholders = ",".join("?" for _ in titles)
        params = (cutoff_ms, *titles)

        # Preserve the existing allowlisted detail query: it is intentionally
        # narrow enough to stay within the production diagnostics deadline.
        for title, count in con.execute(
            f"""
            SELECT title, COUNT(*)
              FROM session
             WHERE time_created >= ?
               AND title IN ({placeholders})
             GROUP BY title
            """,
            params,
        ):
            bucket = OPENCODE_CALLER_TITLES.get(str(title))
            if bucket is not None:
                result["buckets"][bucket]["sessions"] = max(0, int(count or 0))

        table_specs = (
            ("message", "session_id", "messages", "message_data_chars", "message_max_chars"),
            ("part", "session_id", "parts", "part_data_chars", "part_max_chars"),
            ("event", "aggregate_id", "events", "event_data_chars", "event_max_chars"),
        )
        for table, session_column, count_key, chars_key, max_key in table_specs:
            sql = f"""
                SELECT s.title,
                       COUNT(x.id),
                       COALESCE(SUM(length(x.data)), 0),
                       COALESCE(MAX(length(x.data)), 0)
                  FROM session AS s
                  JOIN {table} AS x ON x.{session_column} = s.id
                 WHERE s.time_created >= ?
                   AND s.title IN ({placeholders})
                 GROUP BY s.title
            """
            for title, count, chars, max_chars in con.execute(sql, params):
                bucket = OPENCODE_CALLER_TITLES.get(str(title))
                if bucket is None:
                    continue
                item = result["buckets"][bucket]
                item[count_key] = max(0, int(count or 0))
                item[chars_key] = max(0, int(chars or 0))
                item[max_key] = max(0, int(max_chars or 0))

        # Coverage is deliberately session-table-only. Expanding the joins to
        # every unattributed message/part/event made a 7+ GiB production DB hit
        # the global 2s diagnostics deadline (#1334/#1454). Give this cheap,
        # read-only count query its own bounded 1s budget so a coverage timeout
        # never erases the proven fixed-bucket detail above.
        coverage = result["coverage"]
        coverage_deadline = time.monotonic() + min(
            1.0, max(0.05, float(query_timeout_sec))
        )
        con.set_progress_handler(
            lambda: 1 if time.monotonic() >= coverage_deadline else 0,
            1000,
        )
        try:
            all_sessions = max(
                0,
                int(
                    con.execute(
                        "SELECT COUNT(*) FROM session WHERE time_created >= ?",
                        (cutoff_ms,),
                    ).fetchone()[0]
                    or 0
                ),
            )
            attributed_sessions = sum(
                item["sessions"] for item in result["buckets"].values()
            )
            cutoffs_ms = (
                int((float(now) - 15 * 60) * 1000),
                int((float(now) - 60 * 60) * 1000),
                int((float(now) - 2 * 60 * 60) * 1000),
                int((float(now) - 6 * 60 * 60) * 1000),
            )
            row = con.execute(
                f"""
                SELECT COUNT(*),
                       MAX(time_created),
                       COALESCE(SUM(CASE WHEN time_created >= ? THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN time_created >= ? THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN time_created >= ? THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN time_created >= ? THEN 1 ELSE 0 END), 0)
                  FROM session
                 WHERE time_created >= ?
                   AND (title IS NULL OR title NOT IN ({placeholders}))
                """,
                (*cutoffs_ms, cutoff_ms, *titles),
            ).fetchone()
            unattributed_sessions = max(0, int(row[0] or 0))
            if all_sessions != attributed_sessions + unattributed_sessions:
                raise ValueError("attribution coverage mismatch")
            latest_ms = row[1]
            latest_age_sec = 0
            if type(latest_ms) is int and latest_ms >= 0 and unattributed_sessions:
                latest_age_sec = max(0, int(float(now) - latest_ms / 1000.0))
            coverage.update(
                scan_complete=True,
                all_sessions=all_sessions,
                attributed_sessions=attributed_sessions,
                unattributed_sessions=unattributed_sessions,
                unattributed_latest_age_sec=latest_age_sec,
                unattributed_15m_sessions=max(0, int(row[2] or 0)),
                unattributed_1h_sessions=max(0, int(row[3] or 0)),
                unattributed_2h_sessions=max(0, int(row[4] or 0)),
                unattributed_6h_sessions=max(0, int(row[5] or 0)),
            )
        except (sqlite3.Error, TypeError, ValueError):
            pass
        result["scan_complete"] = True
        return result
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return result
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass


VALUE_REDACT_RES = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*"),
    # A key/value pair whose name ends in a secret word, with any prefix
    # (SLACK_BOT_TOKEN=, api_key:) and in JSON form ("api_key": "…").
    re.compile(r"(?i)[A-Za-z0-9_.-]{0,64}(?:api[_-]?key|token|secret|password|passwd"
               r"|stream[_-]?key|credential)[\"']?\s*[:=]\s*[\"']?[^\s\"',}]+"),
    # CLI flag form: --bot-token <v> / --secret=<v>.
    re.compile(r"(?i)--?[A-Za-z0-9-]{0,32}(?:token|secret|password|key)[= ]+[^\s\"']+"),
    # Well-known token shapes, used bare in logs.
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    # Credentials embedded in a URL: scheme://user:pass@host
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@"),
)

_VALUE_REDACT_HINT = re.compile(
    r"[:=]|bearer|xox[baprs]-|github_pat_|gh[pousr]_|AKIA|sk-|://", re.IGNORECASE
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
    "accepted",
    "waiting",
    "boundary",
    "stop_requested",
    "stopping",
    "resume_requested",
})
# Sanitized ack statuses from the lifecycle broker contract
# (soren-integration/lib/game_lifecycle.sh). Only these fixed enums are ever
# emitted; request ids, game names, generations and deadlines are never
# exposed. Terminal stopped parks the bridge until an explicit fresh launch.
LIFECYCLE_ACK_STATUSES = frozenset({
    "accepted",
    "waiting",
    "boundary",
    "stop_requested",
    "stopping",
    "stopped",
    "resume_requested",
    "resumed",
    "cancelled",
    "failed",
    "timeout",
    "unsupported",
})
LIFECYCLE_ACTIVE_STATUSES = frozenset({
    "accepted",
    "waiting",
    "boundary",
    "stop_requested",
    "stopping",
    "resume_requested",
    "stopped",
})
LIFECYCLE_REQUEST_STATUSES = ("absent", "malformed", "present")
LIFECYCLE_WAITING_FOR = (
    "none",
    "lifecycle_handover",
    "fresh_launch",
    "operator_action",
    # An accepted/waiting handover whose request deadline has already passed
    # while its lifecycle pause markers survive (docich #2061).  The broker
    # only rewrites such an ack to ``timeout`` when something polls
    # ``boundary``, so a wedged cleanup keeps reporting ``waiting``; this enum
    # identifies the expired cleanup instead of reading as a live handover.
    "expired_waiting",
    # A pause marker whose ownership record proves a lifecycle request created
    # it, while that request is gone from the broker (consumed).  The #2061 fix
    # deliberately preserves this proof, so the residue the fixed operator
    # releases must not read as an unprovable "unknown" pause.
    "orphaned_lifecycle_pause",
    "unknown",
)
PAUSE_OWNERS = ("lifecycle_owned", "operator_owned", "unknown")
UNREGISTERED_HEALTH = ("alive", "paused", "stale_only", "unknown")


def _redact_text(text, limit=MAX_ERROR_PREVIEW_LEN):
    if not isinstance(text, str):
        text = str(text)
    # Fast reject: the value rules are comparatively expensive and only a
    # line that carries a separator or a known token prefix can hold a
    # secret. Skipping them keeps huge telemetry bounded.
    if _VALUE_REDACT_HINT.search(text):
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


def _pause_record_owner(state_dir, name):
    """The request a pause marker names, when its record proves creation.

    Deliberately independent of whether that request is still live.  This is
    the durable proof the #2061 fix preserves, so an orphaned lifecycle pause
    can be told apart from an operator pause or an unprovable residue.
    Never returns anything but the request id to internal callers; no caller
    emits it.
    """
    marker = state_dir / f"{name}.paused"
    raw = _read_text_capped(marker, 512)
    if raw is None:
        return None
    value = raw.strip()
    if not value.startswith("lifecycle:"):
        return None
    request_id = value[len("lifecycle:"):].strip()
    spec = LIFECYCLE_PAUSE_RECORDS.get(name)
    if not request_id or spec is None:
        return None
    record_name, owner_field = spec
    record = _read_json(state_dir / "game_lifecycle" / record_name)
    if (
        isinstance(record, dict)
        and record.get("request_id") == request_id
        and record.get(owner_field) is True
    ):
        return request_id
    return None


def _pause_owner(state_dir, name, now):
    """Classify one existing pause marker without exposing marker contents."""
    marker = state_dir / f"{name}.paused"
    raw = _read_text_capped(marker, 512)
    if raw is None:
        return "unknown"
    value = raw.strip()
    if value.startswith("lifecycle:"):
        request_id = _pause_record_owner(state_dir, name)
        if request_id and _lifecycle_request_active(state_dir, request_id, now):
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


def _lifecycle_pair_status(state_dir):
    """Sanitize the lifecycle request/ack pair into fixed enums only.

    Returns (request_status, ack_status) where request_status is one of
    LIFECYCLE_REQUEST_STATUSES and ack_status is one of LIFECYCLE_ACK_STATUSES
    plus "absent", "malformed" or "mismatched".  No request id, game name,
    generation, deadline or free-form value is ever returned.
    """
    lifecycle = state_dir / "game_lifecycle"
    request = _read_json(lifecycle / "request.json")
    ack = _read_json(lifecycle / "ack.json")
    if request is None and ack is None:
        return "absent", "absent"
    if not isinstance(request, dict) or request.get("schema") != 1:
        if not isinstance(request, dict):
            if ack is None:
                return "absent", "absent"
            return "absent", "malformed"
        return "malformed", "malformed"
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return "malformed", "malformed"
    if not isinstance(ack, dict) or ack.get("schema") != 1:
        return "present", "malformed" if ack is not None else "absent"
    if not isinstance(ack.get("request_id"), str) or not ack.get("request_id"):
        return "present", "malformed"
    for field in ("game", "generation", "deadline_epoch", "deadline_at"):
        if field not in request or field not in ack or ack.get(field) != request.get(field):
            return "present", "mismatched"
    if ack.get("request_id") != request_id:
        return "present", "mismatched"
    status = ack.get("status")
    if not isinstance(status, str) or status not in LIFECYCLE_ACK_STATUSES:
        return "present", "unknown"
    return "present", status


def _collect_lifecycle_pause(soren, now, workers=None):
    """Sanitized lifecycle pause provenance (read-only).

    Reports whether a lifecycle handover currently governs the supervisor
    pause markers, which of the four lifecycle-managed workers carry a proven
    lifecycle-owned pause, and what a paused worker is waiting for -- all as
    fixed enums.  Never emits request ids, game names, deadlines, paths,
    secrets or free-form text.  Never modifies markers or processes.
    """
    state_dir = soren / "tmp" / "state"
    request_status, ack_status = _lifecycle_pair_status(state_dir)
    details = {}
    if isinstance(workers, dict):
        details = workers.get("details") or {}
    owned = []
    orphaned = []
    operator_paused = False
    any_paused = False
    for name in LIFECYCLE_PAUSE_RECORDS:
        if isinstance(details, dict) and name in details:
            record = details[name] or {}
            is_paused = bool(record.get("paused"))
            owner = record.get("pause_owner")
        else:
            is_paused = (state_dir / f"{name}.paused").is_file()
            owner = _pause_owner(state_dir, name, now) if is_paused else None
        if is_paused:
            any_paused = True
        if owner == "operator_owned" and is_paused:
            operator_paused = True
        if is_paused and owner == "lifecycle_owned":
            owned.append(name)
        elif is_paused and _pause_record_owner(state_dir, name):
            # The record proves a lifecycle request created this marker, but
            # that request is not live: the residue #2061's fix preserves.
            orphaned.append(name)
    owned = sorted(owned)
    orphaned = sorted(orphaned)
    active = False
    expired_waiting = False
    if request_status == "present" and ack_status in LIFECYCLE_ACTIVE_STATUSES:
        if ack_status == "stopped":
            # Terminal park: the bridge intentionally waits for a fresh
            # launch, so no deadline applies.
            active = True
        elif ack_status in LIFECYCLE_DEADLINE_STATUSES:
            request = _read_json(state_dir / "game_lifecycle" / "request.json")
            deadline_raw = request.get("deadline_epoch") if isinstance(request, dict) else None
            if isinstance(deadline_raw, (int, float)) and math.isfinite(deadline_raw):
                active = bool(float(deadline_raw) > float(now))
                expired_waiting = bool(
                    not active and ack_status in {"accepted", "waiting"}
                )
            else:
                active = False
        else:
            # Any remaining live status is deadline-bound too; fail closed
            # rather than report an unproven handover as active.
            active = False
    if active and ack_status == "stopped" and owned:
        waiting_for = "fresh_launch"
    elif active and (owned or ack_status in LIFECYCLE_ACTIVE_STATUSES):
        waiting_for = "lifecycle_handover"
    elif operator_paused:
        waiting_for = "operator_action"
    elif expired_waiting and any_paused:
        # docich #2061: an accepted/waiting handover past its deadline left
        # lifecycle pause markers behind.  Reporting this as
        # "lifecycle_handover" hid the exact cleanup the operator has to
        # cancel, so it gets its own enum (see LIFECYCLE_WAITING_FOR).
        waiting_for = "expired_waiting"
    elif request_status == "absent" and orphaned:
        # The broker has no request at all, yet a pause record still proves a
        # lifecycle request created its marker: the residue a consumed switch
        # left behind, which the fixed operator releases (docich #2061).  It
        # must not read as an unprovable "unknown" pause.
        waiting_for = "orphaned_lifecycle_pause"
    elif any_paused:
        # Paused markers exist but none prove an active lifecycle handover
        # nor an operator webui pause: stale, expired or mismatched.
        waiting_for = "unknown"
    else:
        waiting_for = "none"
    return {
        "active": bool(active),
        "request_status": request_status,
        "ack_status": ack_status,
        "pause_owned_by_lifecycle": owned,
        "waiting_for": waiting_for,
    }


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
    unregistered_flags = {"alive": 0, "stale": 0, "paused": 0}
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
                unregistered_flags["alive"] += int(record["alive"])
                unregistered_flags["stale"] += int(record["stale_pid_file"])
                unregistered_flags["paused"] += int(record["paused"])
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
        "unregistered_flags": unregistered_flags,
        "required_down": sorted(n for n in stopped if n in required),
        "required_stale": sorted(n for n in stale_pid_files if n in required),
        "details": details,
        # Deliberately outside ``details``: the size budget clears that whole
        # block (see _diagnostics_budget), but the registered chat worker's
        # identity is exactly what an owner switching the classifier's provider
        # (#2301) must be able to compare before and after the replacement. A
        # PID and liveness are not secrets and are already published elsewhere
        # in this projection (e.g. webui.main_pid).
        "chat_worker": {
            "pid": (details.get("chat_worker") or {}).get("pid"),
            "alive": bool((details.get("chat_worker") or {}).get("alive")),
            "stale_pid_file": bool((details.get("chat_worker") or {}).get("stale_pid_file")),
        },
    }


# Fixed name allowlist (#882): only these exact variable NAMES are ever read
# from a live worker's environ. Values for the credential names never leave
# _read_allowlisted_environ; docich.semantic_decision.diagnostics.describe()
# converts them to presence/shape-only before this module ever formats output.
# These are exactly the names docich.comment_classifier reads:
# COMMENT_CLASSIFIER_BACKEND (plain enum gate, never a credential, so its
# value is also reported as-is below) and DOCICH_JEV_ROUTE plus that route's
# own credential -- including Clef's account/token pair (#2301), without which
# a selected clef route cannot reach Workers AI at all. The retired
# DOCICH_SEMANTIC_BACKEND is no longer read by anything, so it is deliberately
# not read here either.
SEMANTIC_DECISION_ENV_ALLOWLIST = (
    "DOCICH_JEV_ROUTE",
    "TYPESAFE_API_KEY",
    "DOCICH_JEV_VERCEL_API_KEY",
    "DOCICH_JEV_CLOUDFLARE_ACCOUNT_ID",
    "DOCICH_JEV_CLOUDFLARE_API_TOKEN",
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
    all_failed_components = {component: 0 for component in AI_COMPONENTS}
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
            all_failed_components[_ai_component_bucket(label)] += 1
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
        "all_failed_components": all_failed_components,
        "recent_events_omitted": False,
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


# Fixed enums mirrored from soviet_now strategy/persist.sh. A new upstream
# value is projected as "unknown", never as raw text.
PERSIST_RESULTS = ("ok", "no-change", "no-op", "fail", "unknown")
PERSIST_REASONS = frozenset({
    "none", "clone_missing", "invalid_message", "invalid_branch", "gh_missing",
    "identity_setup_failed", "fetch_failed", "remote_head_failed",
    "checkout_failed", "reset_failed", "copy_failed", "add_failed",
    "commit_failed", "tree_failed", "push_failed", "pr_failed", "unknown",
})
PERSIST_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}")
PERSIST_COMMIT = re.compile(r"[0-9a-f]{7,40}")
PERSIST_LABEL = re.compile(r"[A-Za-z0-9_-]{1,32}")
# A status file older than this while the improve loop runs means persist is
# no longer being called; the 2026-09-17 stalled-clone incident looked exactly
# like this before the file existed at all.
PERSIST_STALE_SEC = 7 * 24 * 60 * 60


def _collect_strategy_persist(soren, now):
    """Project the runtime strategy persist attempt (docich#2489).

    soviet_now's ``strategy/persist.sh`` writes a fixed-schema status document
    after every adoption attempt. Only fixed enums are emitted: the raw status
    message/error can name hosts or paths and never leaves the VM, and no file
    content is executed or evaluated here. A missing clone is the normal
    projection/no-op deployment and is not a failure.
    """
    soren = Path(soren)
    status_path = soren.parent / "soren-persist" / ".git" / "persist-status.json"
    clone_git = soren.parent / "soren-persist" / ".git"
    failure_path = soren / "tmp" / "state" / "strategy_persist_last_failure.json"
    data = _read_json(status_path)
    present = False
    try:
        present = status_path.is_file()
    except OSError:
        present = False
    clone_present = False
    try:
        clone_present = clone_git.is_dir()
    except OSError:
        clone_present = False
    entry = {
        "present": present,
        "clone_present": clone_present,
        "readable": data is not None,
        "result": "unknown",
        "reason": None,
        "rc": None,
        "origin_main": None,
        "last_success_at": None,
        "last_success_age_sec": -1,
        "age_sec": -1,
        "stale": False,
        "branch": None,
        "commit": None,
        "last_failure": None,
    }
    if present:
        try:
            entry["age_sec"] = int(now - status_path.stat().st_mtime)
        except OSError:
            entry["age_sec"] = -1
        entry["stale"] = entry["age_sec"] > PERSIST_STALE_SEC
    if data:
        result = data.get("result")
        entry["result"] = result if result in PERSIST_RESULTS else "unknown"
        reason = data.get("reason")
        if isinstance(reason, str) and reason in PERSIST_REASONS:
            entry["reason"] = reason
        elif entry["result"] == "fail":
            # Older status documents predate the reason field. Keep the
            # failure visible without inventing a cause.
            entry["reason"] = "unknown"
        rc = data.get("rc")
        if type(rc) is int and 0 <= rc <= 255:
            entry["rc"] = rc
        branch = data.get("branch")
        if isinstance(branch, str) and PERSIST_BRANCH.fullmatch(branch):
            entry["branch"] = branch
        commit = data.get("commit")
        if isinstance(commit, str) and PERSIST_COMMIT.fullmatch(commit):
            entry["commit"] = commit
        origin_main = data.get("origin_main")
        if isinstance(origin_main, str) and PERSIST_COMMIT.fullmatch(origin_main):
            entry["origin_main"] = origin_main
        last_success = _parse_epoch(data.get("last_success_at"))
        if last_success is not None:
            entry["last_success_at"] = last_success
            entry["last_success_age_sec"] = (
                int(now - last_success) if last_success <= now else -1
            )

    failure = _read_json(failure_path)
    if failure and failure.get("failure_code") == "strategy_persist_failed":
        reason = failure.get("reason")
        rc = failure.get("rc")
        label = failure.get("label")
        failed_at = _parse_epoch(failure.get("failed_at"))
        entry["last_failure"] = {
            "failure_code": "strategy_persist_failed",
            "reason": reason if isinstance(reason, str) and reason in PERSIST_REASONS else "unknown",
            "rc": rc if type(rc) is int and 0 <= rc <= 255 else None,
            "label": label if isinstance(label, str) and PERSIST_LABEL.fullmatch(label) else "unknown",
            "failed_at": failed_at,
            "age_sec": int(now - failed_at) if failed_at is not None and failed_at <= now else -1,
        }
    return entry


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
        "persist": _collect_strategy_persist(soren, now),
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


STREAM_TITLE_SYNC_MAX_BYTES = 32 * 1024
STREAM_TITLE_SYNC_MAX_LINE_BYTES = 512
STREAM_TITLE_SYNC_MAX_AGE_SEC = 15 * 60
STREAM_TITLE_SYNC_FUTURE_TOLERANCE_SEC = 60
STREAM_TITLE_SYNC_EVENTS = frozenset({"invoked", "started", "result", "skipped"})
STREAM_TITLE_SYNC_SKIP_REASONS = frozenset({
    "category_only", "dry_run", "show_only", "twitch_read_failed",
    "twitch_update_failed", "category_not_configured", "updater_missing",
    "dispatch_failed", "invalid_title",
})
STREAM_TITLE_SYNC_CALL_CONDITIONS = frozenset({
    "unknown", "normal", "category_only", "dry_run", "show_only",
    "title_only", "force",
})
STREAM_TITLE_SYNC_YOUTUBE_RESULTS = frozenset({
    "not_configured", "stream_not_configured", "no_unique_live_broadcast",
    "invalid_video_id", "video_not_found", "invalid_video_snippet",
    "unchanged", "updated", "unconfirmed", "update_failed",
})
STREAM_TITLE_SYNC_KICK_RESULTS = frozenset({
    "not_configured", "invalid_broadcaster", "wrong_broadcaster", "not_live",
    "unchanged", "updated", "unconfirmed", "update_failed",
})
STREAM_TITLE_SYNC_LEGACY_RECORD_FIELDS = frozenset({
    "occurred_at", "event", "skip_reason", "youtube", "kick", "soviet_sha",
})
STREAM_TITLE_SYNC_RECORD_FIELDS = frozenset({
    "occurred_at", "event", "skip_reason", "youtube", "kick", "execution_head",
    "call_condition", "update_stream_game_sha256", "stream_title_sync_sha256",
})
STREAM_TITLE_SYNC_ID_PRESENCE_FIELDS = frozenset({
    "youtube_stream_id_present", "kick_broadcaster_id_present",
})
STREAM_TITLE_SYNC_SHA_RE = re.compile(r"\A[0-9a-f]{64}\Z")


def _stream_title_sync_empty(record_status):
    return {
        "record_status": record_status,
        "occurred_at": None,
        "age_sec": -1,
        "event": "unknown",
        "run_soren_sha": None,
        "execution_soren_head": None,
        "expected_soren_gitlink_sha": None,
        "same_soren_sha": None,
        "same_execution_head_as_gitlink": None,
        "runtime_code_matches_gitlink": None,
        "call_condition": "unknown",
        "youtube_stream_id_present": None,
        "kick_broadcaster_id_present": None,
        "update_stream_game_sha256": None,
        "stream_title_sync_sha256": None,
        "skip_reason": "unknown",
        "youtube": "unknown",
        "kick": "unknown",
        "journal_destination": {
            "root": "unknown",
            "tmp": "not_checked",
            "state": "not_checked",
            "directory": "not_checked",
            "lock_file": "not_checked",
            "events_file": "not_checked",
        },
    }


def _stream_title_sync_unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _stream_title_sync_path_status(info, *, directory=False, private=False):
    if directory:
        valid_type = stat.S_ISDIR(info.st_mode)
    else:
        valid_type = stat.S_ISREG(info.st_mode)
    if not valid_type or info.st_uid != os.getuid():
        return "unsafe"
    if not directory and getattr(info, "st_nlink", 1) != 1:
        return "unsafe"
    if private and info.st_mode & 0o077:
        return "unsafe"
    return "present"


def _stream_title_sync_source_sha256(path):
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 512 * 1024:
            return None
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 512 * 1024:
                return None
            digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None
    finally:
        os.close(fd)


def _stream_title_sync_git_blob_sha256(repo, commit_sha, relative_path):
    if (
        not isinstance(commit_sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", commit_sha)
        or relative_path not in {"update_stream_game.sh", "lib/stream_title_sync.py", "start_all.sh"}
    ):
        return None
    try:
        result = subprocess.run(
            [
                "git", "-C", str(repo), "-c", "core.hooksPath=/dev/null",
                "show", commit_sha + ":" + relative_path,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout) > 512 * 1024:
        return None
    return hashlib.sha256(result.stdout).hexdigest()


def _stream_title_sync_code_versions(soren, tracked_soren_root, expected_soren_sha=None):
    soren = Path(soren)
    tracked_soren_root = Path(tracked_soren_root)
    return {
        "runtime_update_stream_game_sha256": _stream_title_sync_source_sha256(
            soren / "update_stream_game.sh"
        ),
        "runtime_stream_title_sync_sha256": _stream_title_sync_source_sha256(
            soren / "lib" / "stream_title_sync.py"
        ),
        "expected_update_stream_game_sha256": _stream_title_sync_git_blob_sha256(
            tracked_soren_root, expected_soren_sha, "update_stream_game.sh"
        ),
        "expected_stream_title_sync_sha256": _stream_title_sync_git_blob_sha256(
            tracked_soren_root, expected_soren_sha, "lib/stream_title_sync.py"
        ),
    }


def _collect_stream_title_sync(soren, now, expected_soren_sha, expected_code=None):
    """Read one bounded private journal row and report fixed destination states."""
    result = _stream_title_sync_empty("absent")
    destination = result["journal_destination"]
    dir_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    dir_fds = []
    fd = None
    try:
        try:
            current_fd = os.open(soren, dir_flags)
        except FileNotFoundError:
            destination["root"] = "missing"
            return result
        except OSError:
            destination["root"] = "unavailable"
            result["record_status"] = "unreadable"
            return result
        dir_fds.append(current_fd)
        info = os.fstat(current_fd)
        destination["root"] = _stream_title_sync_path_status(info, directory=True)
        if destination["root"] != "present":
            result["record_status"] = "unreadable"
            return result

        for name, key, private in (
            ("tmp", "tmp", False),
            ("state", "state", False),
            ("stream_title_sync", "directory", True),
        ):
            try:
                current_fd = os.open(name, dir_flags, dir_fd=current_fd)
            except FileNotFoundError:
                destination[key] = "missing"
                return result
            except OSError:
                destination[key] = "unavailable"
                result["record_status"] = "unreadable"
                return result
            dir_fds.append(current_fd)
            info = os.fstat(current_fd)
            destination[key] = _stream_title_sync_path_status(
                info, directory=True, private=private
            )
            if destination[key] != "present":
                result["record_status"] = "unreadable"
                return result

        try:
            lock_info = os.stat("lock", dir_fd=current_fd, follow_symlinks=False)
        except FileNotFoundError:
            destination["lock_file"] = "missing"
        except OSError:
            destination["lock_file"] = "unavailable"
        else:
            destination["lock_file"] = _stream_title_sync_path_status(
                lock_info, private=True
            )

        try:
            fd = os.open("events.jsonl", file_flags, dir_fd=current_fd)
        except FileNotFoundError:
            destination["events_file"] = "missing"
            return result
        except OSError:
            destination["events_file"] = "unavailable"
            result["record_status"] = "unreadable"
            return result
        for dir_fd in reversed(dir_fds):
            try:
                os.close(dir_fd)
            except OSError:
                pass
        dir_fds.clear()

        info = os.fstat(fd)
        destination["events_file"] = _stream_title_sync_path_status(info, private=True)
        if destination["events_file"] != "present":
            result["record_status"] = "unreadable"
            return result
        if info.st_size == 0:
            result["record_status"] = "no_record"
            return result
        if info.st_size > STREAM_TITLE_SYNC_MAX_BYTES:
            result["record_status"] = "oversized"
            return result
        chunks = []
        remaining = STREAM_TITLE_SYNC_MAX_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 4096))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > STREAM_TITLE_SYNC_MAX_BYTES:
            result["record_status"] = "oversized"
            return result
    except OSError:
        result["record_status"] = "unreadable"
        return result
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        for dir_fd in reversed(dir_fds):
            try:
                os.close(dir_fd)
            except OSError:
                pass

    if not data.endswith(b"\n"):
        result["record_status"] = "malformed"
        return result
    lines = data[:-1].split(b"\n")
    if not lines or any(not line or len(line) > STREAM_TITLE_SYNC_MAX_LINE_BYTES for line in lines):
        result["record_status"] = "malformed"
        return result
    try:
        row = json.loads(
            lines[-1].decode("utf-8", "strict"),
            object_pairs_hook=_stream_title_sync_unique_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        result["record_status"] = "malformed"
        return result
    if not isinstance(row, dict):
        result["record_status"] = "malformed"
        return result

    row_fields = set(row)
    is_legacy = row_fields == STREAM_TITLE_SYNC_LEGACY_RECORD_FIELDS
    has_id_presence = row_fields == (
        STREAM_TITLE_SYNC_RECORD_FIELDS | STREAM_TITLE_SYNC_ID_PRESENCE_FIELDS
    )
    if not is_legacy and not has_id_presence and row_fields != STREAM_TITLE_SYNC_RECORD_FIELDS:
        result["record_status"] = "malformed"
        return result

    occurred_at = row.get("occurred_at")
    event = row.get("event")
    skip_reason = row.get("skip_reason")
    youtube = row.get("youtube")
    kick = row.get("kick")
    if is_legacy:
        execution_head = row.get("soviet_sha")
        call_condition = "unknown"
        updater_sha256 = None
        helper_sha256 = None
    else:
        execution_head = row.get("execution_head")
        call_condition = row.get("call_condition")
        updater_sha256 = row.get("update_stream_game_sha256")
        helper_sha256 = row.get("stream_title_sync_sha256")

    valid_execution_head = (
        execution_head is None
        or (
            isinstance(execution_head, str)
            and re.fullmatch(r"[0-9a-f]{40}", execution_head)
        )
    )
    valid_source_sha256 = lambda value: value is None or (
        isinstance(value, str) and STREAM_TITLE_SYNC_SHA_RE.fullmatch(value)
    )
    if (
        not isinstance(occurred_at, str)
        or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", occurred_at)
        or not isinstance(event, str)
        or event not in STREAM_TITLE_SYNC_EVENTS
        or not isinstance(skip_reason, str)
        or not isinstance(youtube, str)
        or not isinstance(kick, str)
        or not valid_execution_head
        or (is_legacy and not isinstance(execution_head, str))
        or (
            not is_legacy
            and (
                not isinstance(call_condition, str)
                or call_condition not in STREAM_TITLE_SYNC_CALL_CONDITIONS
            )
        )
        or not valid_source_sha256(updater_sha256)
        or not valid_source_sha256(helper_sha256)
        or (has_id_presence and any(
            type(row[field]) is not bool for field in STREAM_TITLE_SYNC_ID_PRESENCE_FIELDS
        ))
    ):
        result["record_status"] = "malformed"
        return result

    if event == "result":
        if (
            skip_reason != "none"
            or youtube not in STREAM_TITLE_SYNC_YOUTUBE_RESULTS
            or kick not in STREAM_TITLE_SYNC_KICK_RESULTS
        ):
            result["record_status"] = "malformed"
            return result
    elif event in {"invoked", "started"}:
        if skip_reason != "none" or youtube != "not_run" or kick != "not_run":
            result["record_status"] = "malformed"
            return result
    elif (
        skip_reason not in STREAM_TITLE_SYNC_SKIP_REASONS
        or youtube != "not_run"
        or kick != "not_run"
    ):
        result["record_status"] = "malformed"
        return result

    try:
        stamp = dt.datetime.strptime(
            occurred_at, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        result["record_status"] = "malformed"
        return result

    age = int(now - stamp.timestamp())
    expected_sha_valid = (
        isinstance(expected_soren_sha, str)
        and re.fullmatch(r"[0-9a-f]{40}", expected_soren_sha)
    )
    same_head = (
        execution_head == expected_soren_sha
        if execution_head is not None and expected_sha_valid
        else None
    )
    result.update({
        "occurred_at": occurred_at,
        "age_sec": max(0, age),
        "event": event,
        "run_soren_sha": execution_head,
        "execution_soren_head": execution_head,
        "expected_soren_gitlink_sha": expected_soren_sha if expected_sha_valid else None,
        "same_soren_sha": same_head,
        "same_execution_head_as_gitlink": same_head,
        "call_condition": call_condition,
        "update_stream_game_sha256": updater_sha256,
        "stream_title_sync_sha256": helper_sha256,
    })

    if is_legacy:
        if not expected_sha_valid:
            result["record_status"] = "source_unavailable"
            return result
        if not same_head:
            result["record_status"] = "source_mismatch"
            return result
    else:
        expected_updater_sha = (
            expected_code.get("expected_update_stream_game_sha256")
            if isinstance(expected_code, dict)
            else None
        )
        expected_helper_sha = (
            expected_code.get("expected_stream_title_sync_sha256")
            if isinstance(expected_code, dict)
            else None
        )
        code_comparison_available = (
            isinstance(expected_updater_sha, str)
            and STREAM_TITLE_SYNC_SHA_RE.fullmatch(expected_updater_sha)
            and isinstance(expected_helper_sha, str)
            and STREAM_TITLE_SYNC_SHA_RE.fullmatch(expected_helper_sha)
            and isinstance(updater_sha256, str)
            and isinstance(helper_sha256, str)
        )
        code_matches = (
            updater_sha256 == expected_updater_sha
            and helper_sha256 == expected_helper_sha
            if code_comparison_available
            else None
        )
        result["runtime_code_matches_gitlink"] = code_matches
        if not code_comparison_available or not expected_sha_valid:
            result["record_status"] = "source_unavailable"
            return result
        if not code_matches:
            result["record_status"] = "source_mismatch"
            return result

    if age < -STREAM_TITLE_SYNC_FUTURE_TOLERANCE_SEC:
        result["record_status"] = "future"
        return result
    if age > STREAM_TITLE_SYNC_MAX_AGE_SEC:
        result["record_status"] = "stale"
        return result

    result["record_status"] = "fresh"
    if has_id_presence:
        for field in STREAM_TITLE_SYNC_ID_PRESENCE_FIELDS:
            result[field] = row[field]
    result["skip_reason"] = skip_reason
    result["youtube"] = youtube
    result["kick"] = kick
    return result


STREAM_TITLE_LOG_MAX_BYTES = 32 * 1024
STREAM_TITLE_LOG_MAX_LINES = 256
STREAM_TITLE_LOG_MAX_LINE_BYTES = 4096
STREAM_TITLE_LOG_SOURCES = {
    "game": ("soren", ("tmp", "logs", "stream_game.log"), "stream_game"),
    "dispatch": ("state", ("logs", "stream-game.log"), "stream_game"),
    "day": ("soren", ("tmp", "logs", "stream_title_day.log"), "stream_title"),
}


STREAM_TITLE_READ_REJECTIONS = frozenset({
    "root_invalid", "root_owner_mismatch", "root_writable",
    "directory_owner_mismatch", "directory_writable", "not_regular",
    "file_owner_mismatch", "multiple_links", "file_writable",
})


class _StreamTitleUnsafe(ValueError):
    """Carry a fixed metadata-only refusal without paths, IDs or raw errors."""

    def __init__(self, reason):
        super().__init__("unsafe title source")
        self.reason = reason if reason in STREAM_TITLE_READ_REJECTIONS else "unknown"


def _stream_title_open_fixed(root, parts):
    """Open fixed sources through owned, non-writable-by-others directories.

    Keep the parent descriptor for a post-read identity check. No environment
    override, glob, symlink, device, FIFO or multiply linked file is accepted.
    """
    root = Path(root)
    if not root.is_absolute() or ".." in root.parts:
        raise _StreamTitleUnsafe("root_invalid")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root.anchor, directory_flags)
    fd = None
    try:
        # O_NOFOLLOW on a multi-component root protects only its final part.
        # Traverse every component, including ancestors of the configured root.
        for part in root.parts[1:]:
            child = os.open(part, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
        for part in (None, *parts[:-1]):
            if part is not None:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
                os.close(directory)
                directory = child
            info = os.fstat(directory)
            stage = "root" if part is None else "directory"
            if info.st_uid != os.getuid():
                raise _StreamTitleUnsafe(stage + "_owner_mismatch")
            if info.st_mode & 0o022:
                raise _StreamTitleUnsafe(stage + "_writable")
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _StreamTitleUnsafe("not_regular")
        if info.st_uid != os.getuid():
            raise _StreamTitleUnsafe("file_owner_mismatch")
        if info.st_nlink != 1:
            raise _StreamTitleUnsafe("multiple_links")
        if info.st_mode & 0o022:
            raise _StreamTitleUnsafe("file_writable")
        return fd, directory, info
    except Exception:
        if fd is not None:
            os.close(fd)
        os.close(directory)
        raise


def _stream_title_file_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_uid, info.st_mode, info.st_nlink)


def _stream_title_log_observation(root, parts, prefix, now, sync):
    """Report the newest complete log observation, not a current root cause.

    The public producers have neither run IDs nor UTC offsets in these logs.
    Host-local time is only an estimate; even a nearby journal timestamp is
    explicitly time-window-only evidence. Retain stale observations, labelled
    as such, but never search backwards for an old failure after a newer
    success, unknown line, partial write or log rotation.
    """
    result = {"record_status": "unreadable", "observed_outcome": "unknown",
              "observed_reason": "unknown", "age_sec": -1,
              "time_basis": "host_local_unverified",
              "journal_relation": "unknown", "tail_truncated": False,
              "read_rejection": "unknown"}
    fd = directory = None
    try:
        fd, directory, before = _stream_title_open_fixed(root, parts)
        offset = max(0, before.st_size - STREAM_TITLE_LOG_MAX_BYTES)
        os.lseek(fd, offset, os.SEEK_SET)
        raw = bytearray()
        while len(raw) < STREAM_TITLE_LOG_MAX_BYTES + 1:
            chunk = os.read(fd, min(4096, STREAM_TITLE_LOG_MAX_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(fd)
        path_info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        # Also reopen the fixed traversal: replacing any ancestor during the
        # read must not turn a historical inode into the current source.
        check_fd, check_dir, reopened = _stream_title_open_fixed(root, parts)
        os.close(check_fd)
        os.close(check_dir)
        identity = _stream_title_file_identity(before)
        if any(_stream_title_file_identity(item) != identity
               for item in (after, path_info, reopened)):
            result["record_status"] = "changed"
            return result
        result["tail_truncated"] = offset > 0
        if len(raw) > STREAM_TITLE_LOG_MAX_BYTES:
            result["record_status"] = "changed"
            return result
        result["read_rejection"] = "none"
    except FileNotFoundError:
        result["record_status"] = "absent"
        return result
    except _StreamTitleUnsafe as error:
        result.update(record_status="unsafe", read_rejection=error.reason)
        return result
    except ValueError:
        result["record_status"] = "unsafe"
        return result
    except OSError:
        return result
    finally:
        if fd is not None:
            os.close(fd)
        if directory is not None:
            os.close(directory)
    if not raw:
        result["record_status"] = "empty"
        return result
    if not raw.endswith(b"\n"):
        result["record_status"] = "incomplete"
        return result
    if offset:
        cut = raw.find(b"\n")
        raw = raw[cut + 1:] if cut >= 0 else b""
    lines = raw.splitlines()
    if len(lines) > STREAM_TITLE_LOG_MAX_LINES:
        lines = lines[-STREAM_TITLE_LOG_MAX_LINES:]
        result["tail_truncated"] = True
    if not lines or len(lines[-1]) > STREAM_TITLE_LOG_MAX_LINE_BYTES:
        result["record_status"] = "unrecognized"
        return result
    # Copied/rotated logs may append an older failure after a newer success.
    # A timestamp regression in this bounded source makes ordering ambiguous;
    # do not mistake the last physical line for the most recent execution.
    previous_stamp = None
    for item in lines:
        header = re.match(rb"\[" + prefix.encode("ascii") + rb" (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] ", item)
        if header:
            try:
                value = dt.datetime.strptime(header[1].decode("ascii"), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if previous_stamp is not None and value < previous_stamp:
                result["record_status"] = "ambiguous_order"
                return result
            previous_stamp = value
    try:
        line = lines[-1].decode("utf-8", "strict")
        match = re.fullmatch(r"\[" + prefix + r" (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] ([^\r\n\x00]*)", line)
        if not match:
            raise ValueError("unknown producer line")
        stamp = dt.datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S").timestamp()
    except (UnicodeError, ValueError, OverflowError, OSError):
        result["record_status"] = "unrecognized"
        return result
    age = int(now - stamp)
    result["age_sec"] = age
    if age < -STREAM_TITLE_SYNC_FUTURE_TOLERANCE_SEC:
        result["record_status"] = "future"
        return result
    message = match[2]
    reason = "unknown"
    exact_failures = {
        "ERROR: no token (set TWITCH_GAME_TOKEN, TWITCH_TITLE_TOKEN, TWITCH_BOT_TOKEN or TWITCH_PREDICTIONS_TOKEN)": "credential_missing",
        "ERROR: TWITCH_BROADCASTER_ID not set": "broadcaster_missing",
        "ERROR: no valid broadcaster credential with channel:manage:broadcast": "credential_candidates_rejected",
        "ERROR: failed to choose public title body": "body_selection_failed",
        "ERROR: failed to compose public title": "composition_failed",
    }
    starts = {
        "ERROR: no client id (token invalid? resp: ": "credential_validation_failed",
        "ERROR: public title helper not found: ": "helper_missing",
        "ERROR: failed to fetch current channel (resp: ": "channel_read_failed",
        "ERROR: failed to fetch current title (resp: ": "channel_read_failed",
        "ERROR: PATCH failed (HTTP ": "channel_update_failed",
        "ERROR: game toml not found: ": "game_config_missing",
        "ERROR: [twitch].category_id が解決できません ": "category_unresolved",
        "ERROR: failed to compute day N ": "day_computation_failed",
    }
    if message in exact_failures:
        reason = exact_failures[message]
    elif re.fullmatch(r"ERROR: token\(login=[^)\r\n]{1,128}\) lacks 'channel:manage:broadcast' scope\. TWITCH_GAME_TOKEN \(broadcaster の broadcast 付きトークン\) を設定してください。", message):
        reason = "broadcast_scope_missing"
    else:
        reason = next((value for start, value in starts.items() if message.startswith(start)), "unknown")
    outcome = "failure" if reason != "unknown" else "unknown"
    if (message == "already up to date; no change needed"
            or (prefix == "stream_title" and re.fullmatch(r"title already current for day [1-9][0-9]{0,8}; no change needed", message))):
        outcome, reason = "unchanged", "none"
    elif (message.startswith("OK: updated title=")
          or (prefix == "stream_title" and message.startswith("OK: title updated -> "))):
        outcome, reason = "twitch_updated", "none"
    elif message.startswith("current: ") or (prefix == "stream_title" and message.startswith("current title: ")):
        outcome, reason = "channel_read", "none"
    elif message.startswith("desired title: "):
        outcome, reason = "composed", "none"
    elif re.fullmatch(r"using credential: TWITCH_(?:TITLE|PREDICTIONS|BOT)_TOKEN", message):
        outcome, reason = "credential_selected", "none"
    result.update(observed_outcome=outcome, observed_reason=reason)
    result["record_status"] = (
        "unrecognized" if outcome == "unknown"
        else "stale" if age > STREAM_TITLE_SYNC_MAX_AGE_SEC else "fresh"
    )
    try:
        journal_stamp = dt.datetime.strptime(sync["occurred_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
        if sync.get("event") == "invoked":
            result["journal_relation"] = (
                "time_window_only" if 0 <= stamp - journal_stamp <= 120
                else "not_correlated"
            )
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return result


def _stream_title_day_script(soren):
    result = {"record_status": "unreadable", "mode": None, "executable": None,
              "read_rejection": "unknown"}
    fd = directory = None
    try:
        fd, directory, info = _stream_title_open_fixed(soren, ("update_stream_title_day.sh",))
        result.update(record_status="observed", mode=f"{stat.S_IMODE(info.st_mode):04o}",
                      executable=bool(info.st_mode & stat.S_IXUSR), read_rejection="none")
    except FileNotFoundError:
        result["record_status"] = "absent"
    except _StreamTitleUnsafe as error:
        result["read_rejection"] = error.reason
    except (OSError, ValueError):
        pass
    finally:
        if fd is not None:
            os.close(fd)
        if directory is not None:
            os.close(directory)
    return result


def _stream_title_day_unit(scope, kind):
    # Result/exit_status are the manager's LAST execution, whose freshness and
    # relationship to a title log are unproven, not evidence of today's tick.
    result = {"load_state": "unknown", "active_state": "unknown",
              "enabled_state": "unknown", "result": "unknown",
              "exit_status": None, "result_freshness": "unknown"}
    properties = ("LoadState", "ActiveState", "UnitFileState", "Result",
                  "ExecMainCode", "ExecMainStatus")
    answer = _systemctl_scope(scope, ["show", f"soren-title-day.{kind}",
                                    "--property=" + ",".join(properties), "--no-pager"], timeout=1)
    if answer is None or len(answer[1]) > 4096:
        return result
    values = {}
    for line in answer[1].splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in properties or key in values:
            return result
        values[key] = value
    allowed = {
        "load_state": ("LoadState", {"loaded", "not-found", "error", "masked", "bad-setting", "merged", "stub"}),
        "active_state": ("ActiveState", {"active", "inactive", "failed", "activating", "deactivating", "reloading", "maintenance", "refreshing"}),
        "enabled_state": ("UnitFileState", {"enabled", "enabled-runtime", "disabled", "static", "masked", "masked-runtime", "linked", "linked-runtime", "alias", "indirect", "generated", "transient", "not-found", "bad"}),
        "result": ("Result", {"success", "exit-code", "signal", "core-dump", "timeout", "watchdog", "start-limit-hit", "resources", "protocol", "exec-condition", "oom-kill"}),
    }
    for field, (key, choices) in allowed.items():
        if values.get(key) in choices:
            result[field] = values[key]
    exit_status = values.get("ExecMainStatus", "")
    if values.get("ExecMainCode") == "1" and re.fullmatch(r"[0-9]{1,3}", exit_status) and int(exit_status) <= 255:
        result["exit_status"] = int(exit_status)
    return result


def _collect_stream_title_attempts(soren, state_dir, now, sync=None):
    roots = {"soren": soren, "state": state_dir}
    return {
        "schema_version": 1,
        "current_failure": "unknown",
        "logs": {name: _stream_title_log_observation(roots[root], parts, prefix, now, sync)
                 for name, (root, parts, prefix) in STREAM_TITLE_LOG_SOURCES.items()},
        "day_script": _stream_title_day_script(soren),
        "day_units": {scope: {kind: _stream_title_day_unit(scope, kind)
                              for kind in ("service", "timer")}
                      for scope in ("system", "user")},
    }


def _supervisor_directory(path, dir_fd=None):
    flags = (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
             | getattr(os, "O_CLOEXEC", 0))
    return os.open(path, flags, dir_fd=dir_fd)


def _supervisor_read(path, limit, dir_fd=None):
    """Bounded regular-file read; never follow a state/metadata symlink."""
    flags = (os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_CLOEXEC", 0))
    fd = os.open(path, flags, dir_fd=dir_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("unsafe metadata")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            raise ValueError("oversized metadata")
        return data
    finally:
        os.close(fd)


def _supervisor_unit_pid():
    """One fixed system-scope property, not ExecStart/argv/environment."""
    try:
        result = subprocess.run(
            ["systemctl", "show", "soren-runtime.service", "--property=MainPID", "--value"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2, check=False,
        )
        value = result.stdout.strip()
        if result.returncode == 0 and re.fullmatch(rb"[1-9][0-9]{0,9}", value):
            return int(value)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _supervisor_process_start(proc_root, pid, process_fd):
    if os.fstat(process_fd).st_uid != os.getuid():
        raise ValueError("foreign process")
    data = _supervisor_read("stat", 4096, dir_fd=process_fd)
    # comm can itself contain ')' or whitespace; never emit it.
    if not data.startswith(str(pid).encode() + b" ("):
        raise ValueError("invalid process")
    fields = data[data.rfind(b")") + 2:].split()
    if len(fields) < 20 or fields[0] not in (b"R", b"S", b"D", b"T", b"t", b"I"):
        raise ValueError("inactive process")
    if not re.fullmatch(rb"[1-9][0-9]{0,19}", fields[19]):
        raise ValueError("invalid process start")
    ticks = int(fields[19])
    return ticks


def _supervisor_script_fingerprint(path, expected_fd_target=None, dir_fd=None):
    """Hash only fixed start_all.sh or Bash's validated script FD 255.

    Opening a Linux /proc FD creates a separate read description: do not seek
    or read the target process's descriptor itself. Never scan other FDs.
    """
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    if expected_fd_target is None:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    else:
        target = os.readlink(path, dir_fd=dir_fd)
        if target not in (expected_fd_target, expected_fd_target + " (deleted)"):
            raise ValueError("unexpected script descriptor")
    fd = os.open(path, flags, dir_fd=dir_fd)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_size <= 0 or before.st_size > 512 * 1024):
            raise ValueError("unsafe script")
        signature = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                  info.st_mtime_ns, info.st_ctime_ns)
        if expected_fd_target is not None and (
                os.readlink(path, dir_fd=dir_fd) != target
                or signature(before) != signature(os.stat(path, dir_fd=dir_fd))):
            raise ValueError("descriptor changed")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, min(64 * 1024, 512 * 1024 + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > 512 * 1024:
                raise ValueError("oversized script")
            digest.update(chunk)
        if (signature(before) != signature(os.fstat(fd))
                or signature(before) != signature(os.stat(path, dir_fd=dir_fd,
                    follow_symlinks=expected_fd_target is not None))):
            raise ValueError("script changed")
        if expected_fd_target is not None and os.readlink(path, dir_fd=dir_fd) != target:
            raise ValueError("descriptor changed")
        return digest.hexdigest(), int(before.st_mtime), int(before.st_ctime)
    finally:
        os.close(fd)


def _collect_supervisor_identity(soren, expected_sha, proc_root=Path("/proc")):
    """Source/age evidence only: never certify already-defined Bash functions.

    PID/start ticks stay internal for race detection. A matching disk/held-FD
    hash does not prove function memory, and a same-PID exec can reload code
    without changing process start time. No signal or runtime write is used.
    """
    source = Path(soren).absolute() / "start_all.sh"
    expected = _stream_title_sync_git_blob_sha256(
        PROD_ROOT / "games/soviet_now", expected_sha, "start_all.sh"
    )
    result = {
        "status": "unavailable", "reason": "pidfile_unavailable",
        "loaded_functions_status": "unverified",
        "expected_script_sha256": expected, "deployed_script_sha256": None,
        "open_script_sha256": None, "deployed_matches_expected": None,
        "open_script_matches_expected": None, "pidfile_matches_unit": None,
        "identity_stable": None, "process_started_at": None,
        "deployed_script_mtime": None, "deployed_script_ctime": None,
    }
    handles = []
    def pin(path, dir_fd=None):
        fd = _supervisor_directory(path, dir_fd)
        handles.append(fd)
        return fd
    try:
        root_fd = pin(soren)
        try:
            digest, mtime, ctime = _supervisor_script_fingerprint("start_all.sh", dir_fd=root_fd)
            result.update(deployed_script_sha256=digest, deployed_script_mtime=mtime,
                          deployed_script_ctime=ctime)
            if expected:
                result["deployed_matches_expected"] = digest == expected
        except (OSError, ValueError):
            pass
        tmp_fd = pin("tmp", root_fd)
        state_fd = pin("state", tmp_fd)
        value = _supervisor_read("start_all.pid", 32, dir_fd=state_fd).strip()
        if not re.fullmatch(rb"[1-9][0-9]{0,9}", value):
            return result
        pid = int(value)
        unit_pid = _supervisor_unit_pid()
        if unit_pid is None:
            result["reason"] = "unit_unavailable"
            return result
        result["pidfile_matches_unit"] = pid == unit_pid
        if pid != unit_pid:
            result["reason"] = "unit_pid_mismatch"
            return result
        result["reason"] = "process_unavailable"
        # A held /proc/PID directory cannot turn into a recycled PID's
        # directory. All process reads below are relative to this handle.
        process_fd = pin(proc_root / str(pid))
        started = _supervisor_process_start(proc_root, pid, process_fd)
        executable = os.readlink("exe", dir_fd=process_fd)
        if Path(executable).name != "bash":
            result["reason"] = "not_bash"
            return result
        try:
            boot = re.search(rb"(?m)^btime ([0-9]{1,12})$", _supervisor_read(proc_root / "stat", 512 * 1024))
            hz = os.sysconf("SC_CLK_TCK")
            if boot and hz > 0:
                result["process_started_at"] = int(int(boot[1]) + started / hz)
        except (OSError, ValueError):
            pass
        result["reason"] = "script_fd_unavailable"
        try:
            script_dir_fd = pin("fd", process_fd)
            digest, _, _ = _supervisor_script_fingerprint(
                "255", str(source), dir_fd=script_dir_fd
            )
            result["open_script_sha256"] = digest
        except (OSError, ValueError):
            pass
        try:
            current_fd = pin(proc_root / str(pid))
            stable = (_supervisor_process_start(proc_root, pid, process_fd) == started
                      and _supervisor_process_start(proc_root, pid, current_fd) == started
                      and os.fstat(process_fd).st_ino == os.fstat(current_fd).st_ino
                      and os.readlink("exe", dir_fd=process_fd) == executable
                      and _supervisor_read("start_all.pid", 32, dir_fd=state_fd).strip() == value
                      and _supervisor_unit_pid() == pid)
            if stable and result["open_script_sha256"] is not None:
                stable = (_supervisor_script_fingerprint(
                    "255", str(source), dir_fd=script_dir_fd
                )[0] == result["open_script_sha256"])
        except (OSError, ValueError):
            stable = False
        if not stable:
            result.update(reason="identity_changed", identity_stable=False,
                          process_started_at=None, open_script_sha256=None)
            return result
        result["identity_stable"] = True
        if result["open_script_sha256"] is not None:
            result.update(status="observed", reason="source_evidence_only")
            if expected:
                result["open_script_matches_expected"] = result["open_script_sha256"] == expected
                if not result["open_script_matches_expected"]:
                    result["reason"] = "open_script_differs_expected"
    except (OSError, ValueError):
        pass
    finally:
        for fd in reversed(handles):
            os.close(fd)
    return result


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
    runtime_soren_head = _git_text(soren, "rev-parse", "--verify", "HEAD")
    if not runtime_soren_head or not re.fullmatch(r"[0-9a-f]{40}", runtime_soren_head):
        runtime_soren_head = None
    soren_code = _stream_title_sync_code_versions(
        soren, PROD_ROOT / "games" / "soviet_now", soviet_head
    )
    return {
        "generated_at": now,
        "window_sec": DIAG_WINDOW_SEC,
        "soren_root_exists": soren.is_dir(),
        "docich_head": docich_head,
        "soviet_head": soviet_head,
        "soren_gitlink_sha": soviet_head,
        "runtime_soren_head": runtime_soren_head,
        "soren_code": soren_code,
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
    "weather_corner": "weather_corner.json",
    "tsuitate_corner": "tsuitate_corner.json",
    "yoso_corner": "yoso_corner.json",
    "doushi_corner": "doushi_corner.json",
    "mystery_corner": "mystery_corner.json",
    "lawsim_corner": "lawsim_corner.json",
    "retro_corner": "retro_corner.json",
    "paper_corner": "paper_corner.json",
    "paper_corner_manual": "paper_corner_manual.json",
    "nethack_corner": "nethack_corner.json",
    "nethack_corner_manual": "nethack_corner_manual.json",
    "soren91_corner": "soren91_corner.json",
}

# Fixed enum of the switch result a corner recorded in its own state file.
SWITCH_STATUSES = frozenset({"queued", "succeeded", "failed", "rolled_back"})

# These are the remaining sources consulted by rotation's live and retired
# adapters. Do not derive filenames from state, catalog rows or request IDs.
ROTATION_CORNER_FILES = (
    "retro_corner", "retro_corner_manual", "paper_corner", "paper_corner_manual",
    "soren91_corner", "soren91_corner_manual", "nethack_corner", "nethack_corner_manual",
)
# Automatic reservation ownership includes the weather manager. Keep the
# separate manual-owner allowlist above unchanged: its state_file contract is
# intentionally narrower and is not inferred from catalog input.
ROTATION_PENDING_OWNER_FILES = (
    *ROTATION_CORNER_FILES, "weather_corner", "tsuitate_corner", "yoso_corner",
    "doushi_corner", "mystery_corner", "lawsim_corner",
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
# AiTextError.kind is normalized by docich.trading.ai_text before it enters
# PAPER state. Do not expose arbitrary rc suffixes or exception text here.
PAPER_IMPROVE_REASON_CODES = ROTATION_IMPROVE_REASON_CODES | frozenset({
    "gate-disabled", "invalid-timeout", "invocation-error", "empty-output",
    "timeout", "rate-limit", "queue-giveup", "gate-giveup", "provider-failed",
    "invalid-output", "unknown",
    "pending-exists", "baseline-changed", "candidate-invalid", "no-change",
})
PAPER_EXPERIMENT_STATUSES = frozenset({
    "active", "draining", "waiting-candidate", "blocked", "legacy",
})
PAPER_EXPERIMENT_REASON_CODES = frozenset({
    "invalid-active", "evaluation-unavailable", "control-invalid", "control-error",
    "early-stop", "sample-complete", "max-age", "initial-experiment",
    "collecting", "pending-activated", "no-experiment",
})
PAPER_IMPROVE_REASON_CODES |= PAPER_EXPERIMENT_REASON_CODES
PAPER_EVALUATION_STATUSES = frozenset({"ok", "partial", "unavailable", "invalid"})
PAPER_EVALUATION_REASON_CODES = frozenset({
    "evaluated", "ledger_missing", "ledger_read_error", "ledger_invalid_row",
    "ledger_incomplete_basis", "evaluation_invalid_clock", "evaluation_invalid_capital",
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
# Fixed exclusion vocabulary of `CornerRotationManager._eligible()`. An enabled
# manual-only corner is `manual-only`; a disabled or paused one is
# `disabled-or-paused`, so the map distinguishes "enabled for manual reservation"
# from "not enabled at all" without needing a reservation.
ROTATION_EXCLUDED_REASONS = frozenset({
    "disabled-or-paused", "manual-only", "adapter-unavailable",
    # A released owner on this corner's shared state file could not be retired
    # yet, so only that state file waits for the retry (#2354 review).
    "released-owner-unretired",
})
ROTATION_EXCLUDED_LIMIT = 32
# Fixed resolutions of the `failed_corners` ledger rows (#2187). `released`
# means the scheduler settled the failed corner and kept rotating; the other
# value means the shared slot could not be proven free, so the fail-closed
# latch stayed.
ROTATION_FAILED_CORNER_RESOLUTIONS = frozenset({"released-unverified", "recovery-required"})
# Fixed reason codes of those rows, plus the bounded timer restore replay's
# own statuses (#2187). Keep in sync with docich.corner_rotation.
ROTATION_FAILED_CORNER_REASONS = frozenset({
    "failed-corner-slot-released",
    "execution-or-state-unverified",
    "base-game-unrestored",
    "restore-recovery-exhausted",
})
ROTATION_AUTO_RESTORE_STATUSES = frozenset({
    "succeeded", "queued", "failed", "noop", "interrupted", "completed", "unknown", "none",
})
ROTATION_AUTO_RESTORE_ATTEMPT_LIMIT = 3
ROTATION_FAILED_CORNER_MAX = 8

WEATHER_CORNER_STATUSES = frozenset({
    "idle", "starting", "active", "restoring", "completed", "interrupted", "failed",
})
WEATHER_END_REASONS = frozenset({
    "forecast-unavailable-before-start", "forecast-expires-before-start",
    "forecast-fetch-failed-before-start", "operator-stopped-before-start",
    "operator-moved-after-start", "operator-moved-during-weather",
    "operator-moved-before-restore", "switch-terminal-before-corner-active",
    "manual", "forecast-expired", "duration", "audio-completed", "audio-unavailable",
})
WEATHER_RECEIPT_STATUSES = frozenset({
    "allocating", "accepted", "queued", "succeeded", "failed", "rolled_back",
})
WEATHER_RESULT_STATUSES = frozenset({"succeeded", "failed", "rolled_back"})
WEATHER_OPERATIONS = frozenset({"start", "switch", "stop"})
WEATHER_AUDIO_DELIVERY_STATUSES = frozenset({
    "running", "stopping", "completed", "stopped", "failed",
})
_WEATHER_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_WEATHER_GAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_WEATHER_RUNTIME_RE = re.compile(r"^g([1-9][0-9]{0,11})-([a-f0-9]{6,32})$")


def _weather_request_id(value):
    """Return only canonical lowercase UUIDs for fixed receipt lookups."""
    return value if isinstance(value, str) and _WEATHER_UUID_RE.fullmatch(value) else None


def _weather_selection_kind(request_id):
    """Rotation uses UUID5 for automatic picks and UUID4 for manual requests."""
    request_id = _weather_request_id(request_id)
    if request_id is None:
        return "unknown"
    return {"5": "automatic", "4": "manual"}.get(request_id[14], "unknown")


def _weather_game(value):
    return value if isinstance(value, str) and _WEATHER_GAME_RE.fullmatch(value) else None


def _weather_identity(value):
    """Validate the four-field runtime identity without returning it publicly."""
    if not isinstance(value, dict):
        return None
    game = _weather_game(value.get("game"))
    runtime_id = value.get("runtime_id")
    generation = value.get("generation")
    lease_id = _weather_request_id(value.get("lease_id"))
    match = _WEATHER_RUNTIME_RE.fullmatch(runtime_id) if isinstance(runtime_id, str) else None
    if (game is None or match is None or lease_id is None
            or type(generation) is not int or generation < 1
            or int(match.group(1)) != generation):
        return None
    return {
        "game": game,
        "runtime_id": runtime_id,
        "generation": generation,
        "lease_id": lease_id,
    }


def _weather_game_matches(value, expected):
    """Match nullable game fields without treating malformed non-null values as null."""
    return value is None if expected is None else _weather_game(value) == expected


def _weather_receipt_projection(state_dir, request_id, stage, state):
    """Project one owner-bound GameSwitch receipt from its validated fixed UUID path."""
    out = {
        "present": False, "readable": False, "status": "unknown",
        "operation": "unknown", "updated_at": None, "result_status": "unknown",
        "result_matches_owner": None, "runtime_matches_owner": None,
        "generation_matches_owner": None,
    }
    request_id = _weather_request_id(request_id)
    if request_id is None:
        return out
    present, readable, receipt = _rotation_evidence_file(
        state_dir, f"game-switch/requests/{request_id}.json"
    )
    out.update(present=present, readable=readable)
    if not readable:
        return out

    receipt_id = _weather_request_id(receipt.get("request_id"))
    receipt_status = _rotation_enum(receipt.get("status"), WEATHER_RECEIPT_STATUSES)
    operation = _rotation_enum(receipt.get("operation"), WEATHER_OPERATIONS)
    out.update(
        status=receipt_status,
        operation=operation,
        updated_at=_rotation_time(receipt.get("updated_at")),
    )
    if receipt_id != request_id:
        out["result_matches_owner"] = False
        out["runtime_matches_owner"] = False
        out["generation_matches_owner"] = False
        return out

    previous_game_raw = state.get("previous_game")
    previous_game = _weather_game(previous_game_raw)
    previous_game_valid = previous_game_raw is None or previous_game is not None
    expected_operation = ("start" if previous_game_raw is None else "switch") if stage == "start" else (
        "stop" if previous_game_raw is None else "switch"
    )
    expected_target = "weather-view" if stage == "start" else previous_game
    expected_from = previous_game if stage == "start" else "weather-view"
    expected_to = "weather-view" if stage == "start" else previous_game
    result = receipt.get("result")
    if not isinstance(result, dict):
        return out

    result_id = _weather_request_id(result.get("request_id"))
    result_status = _rotation_enum(result.get("status"), WEATHER_RESULT_STATUSES)
    out["result_status"] = result_status
    result_matches = (
        result_id == request_id
        and previous_game_valid
        and operation == expected_operation
        and _weather_game_matches(receipt.get("target"), expected_target)
        and result.get("operation") == expected_operation
        and _weather_game_matches(result.get("from_game"), expected_from)
        and _weather_game_matches(result.get("to_game"), expected_to)
    )
    out["result_matches_owner"] = bool(result_matches)

    result_runtime_raw = result.get("active_runtime")
    result_runtime = _weather_identity(result_runtime_raw)
    if stage == "start":
        expected_runtime = (
            _weather_identity(state.get("weather_runtime_identity"))
            or _weather_identity(state.get("starting_runtime_identity"))
        )
    else:
        expected_runtime = _weather_identity(state.get("restored_runtime_identity"))
    if expected_runtime is not None:
        out["runtime_matches_owner"] = result_runtime == expected_runtime
    elif stage == "restore" and state.get("previous_game") is None and "restored_runtime_identity" in state:
        out["runtime_matches_owner"] = result_runtime_raw is None
    expected_generation_identity = expected_runtime
    if stage == "restore" and state.get("previous_game") is None:
        expected_generation_identity = _weather_identity(state.get("weather_runtime_identity"))
    if expected_generation_identity is not None:
        receipt_generation = receipt.get("generation")
        result_generation = result.get("generation")
        out["generation_matches_owner"] = bool(
            type(receipt_generation) is int
            and type(result_generation) is int
            and receipt_generation == result_generation == expected_generation_identity["generation"]
        )
    return out


def _weather_corner_projection(state_dir, rotation, rotation_readable):
    """Read fixed weather owner state and prove only its reservation/receipt links."""
    present, readable, state = _rotation_evidence_file(
        state_dir, CORNER_STATE_FILES["weather_corner"]
    )
    out = {
        "present": present, "readable": readable, "status": "unknown",
        "rotation_request_matches_pending": None,
        "rotation_request_matches_last_result": None,
        "selection_kind": "unknown", "rotation_result_status": "unknown",
        "rotation_result_at": None, "requested_at": None, "started_at": None,
        "restore_requested_at": None, "completed_at": None, "end_reason": None,
        "previous_game": None, "starting_runtime_present": False,
        "weather_runtime_present": False, "restored_runtime_present": False,
        "start_request_matches_rotation": None,
        "start_receipt": {
            "present": False, "readable": False, "status": "unknown",
            "operation": "unknown", "updated_at": None, "result_status": "unknown",
            "result_matches_owner": None, "runtime_matches_owner": None,
            "generation_matches_owner": None,
        },
        "restore_receipt": {
            "present": False, "readable": False, "status": "unknown",
            "operation": "unknown", "updated_at": None, "result_status": "unknown",
            "result_matches_owner": None, "runtime_matches_owner": None,
            "generation_matches_owner": None,
        },
        "restored_runtime_matches_current": None,
        "audio_delivery_present": False,
        "audio_delivery_status": "unknown",
        "audio_next_index": None,
    }
    if not readable:
        return out

    status = _rotation_enum(state.get("status"), WEATHER_CORNER_STATUSES)
    owner_id = _weather_request_id(state.get("rotation_request_id"))
    start_id = _weather_request_id(state.get("start_request_id"))
    restore_id = _weather_request_id(state.get("restore_request_id"))
    rotation = rotation if rotation_readable and isinstance(rotation, dict) else None
    pending = rotation.get("pending") if rotation and isinstance(rotation.get("pending"), dict) else None
    last = rotation.get("last_result") if rotation and isinstance(rotation.get("last_result"), dict) else None

    pending_match = None
    if rotation is not None and owner_id is not None:
        pending_match = bool(
            pending is not None
            and pending.get("corner") == "weather"
            and _weather_request_id(pending.get("request_id")) == owner_id
        )
    last_match = None
    if rotation is not None and owner_id is not None:
        last_match = bool(
            last is not None
            and last.get("corner") == "weather"
            and _weather_request_id(last.get("request_id")) == owner_id
        )
    selection_kind = "unknown"
    if pending_match:
        source = pending.get("source")
        selection_kind = (
            "manual" if source == "manual" else
            _weather_selection_kind(owner_id) if source is None else "unknown"
        )
    elif last_match:
        selection_kind = _weather_selection_kind(owner_id)

    delivery = state.get("audio_delivery")
    audio_delivery_present = isinstance(delivery, dict)
    audio_delivery_status = (
        _rotation_enum(delivery.get("status"), WEATHER_AUDIO_DELIVERY_STATUSES)
        if audio_delivery_present else "unknown"
    )
    audio_next_index = delivery.get("next_index") if audio_delivery_present else None
    if type(audio_next_index) is not int or not 0 <= audio_next_index <= 13:
        audio_next_index = None

    rotation_result_status = "unknown"
    rotation_result_at = None
    if last_match:
        rotation_result_status = _rotation_enum(
            last.get("status"), {"completed", "failed", "queued", "waiting", "already-running"}
        )
        rotation_result_at = _rotation_time(last.get("at"))

    out.update(
        status=status,
        rotation_request_matches_pending=pending_match,
        rotation_request_matches_last_result=last_match,
        selection_kind=selection_kind,
        rotation_result_status=rotation_result_status,
        rotation_result_at=rotation_result_at,
        requested_at=_rotation_time(state.get("requested_at")),
        started_at=_rotation_time(state.get("started_at")),
        restore_requested_at=_rotation_time(state.get("restore_requested_at")),
        completed_at=_rotation_time(state.get("completed_at")),
        end_reason=(
            state.get("end_reason")
            if isinstance(state.get("end_reason"), str)
            and state.get("end_reason") in WEATHER_END_REASONS else None
        ),
        previous_game=_weather_game(state.get("previous_game")),
        starting_runtime_present=_weather_identity(state.get("starting_runtime_identity")) is not None,
        weather_runtime_present=_weather_identity(state.get("weather_runtime_identity")) is not None,
        restored_runtime_present=(
            _weather_identity(state.get("restored_runtime_identity")) is not None
            or (state.get("previous_game") is None and "restored_runtime_identity" in state
                and state.get("restored_runtime_identity") is None)
        ),
        audio_delivery_present=audio_delivery_present,
        audio_delivery_status=audio_delivery_status,
        audio_next_index=audio_next_index,
        start_request_matches_rotation=(
            start_id == owner_id if start_id is not None and owner_id is not None else None
        ),
    )
    if start_id is not None and start_id == owner_id:
        out["start_receipt"] = _weather_receipt_projection(
            state_dir, start_id, "start", state
        )
    elif start_id is not None:
        out["start_receipt"]["result_matches_owner"] = False
        out["start_receipt"]["runtime_matches_owner"] = False
    restore_is_owned = (
        restore_id is not None
        and (
            (status == "restoring"
             and _weather_request_id(state.get("switch_request_id")) == restore_id)
            or (status in {"completed", "interrupted", "failed"}
                and (state.get("switch_request_id") is None
                     or _weather_request_id(state.get("switch_request_id")) == restore_id))
        )
    )
    if restore_is_owned:
        out["restore_receipt"] = _weather_receipt_projection(
            state_dir, restore_id, "restore", state
        )
    _, current_readable, canonical = _rotation_evidence_file(
        state_dir, CORNER_STATE_FILES["game_switch"]
    )
    current_active = canonical.get("active") if current_readable else None
    current_identity = _weather_identity(current_active)
    restored_identity = _weather_identity(state.get("restored_runtime_identity"))
    if current_readable and status == "completed":
        if state.get("previous_game") is None:
            out["restored_runtime_matches_current"] = (
                canonical.get("phase") == "idle" and current_active is None
            )
        elif restored_identity is not None:
            out["restored_runtime_matches_current"] = current_identity == restored_identity
        else:
            out["restored_runtime_matches_current"] = False
    return out


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
    try:
        return value if math.isfinite(value) else None
    except (OverflowError, ValueError, TypeError):
        return None


def _file_age_sec(path, now):
    try:
        return int(now - path.stat().st_mtime)
    except OSError:
        return -1


AUDIO_QUEUE_DEFAULT = "tmp/.comment_queue"
AUDIO_ITEM_PREFIX = "comment_"
AUDIO_QUEUED_SUFFIX = ".txt"
AUDIO_PLAYING_SUFFIX = ".playing"


def _comment_queue_dir(soren):
    """The queue the audio worker consumes, as far as diagnostics may resolve it.

    ``COMMENT_QUEUE_DIR`` from the collector's own environment wins; otherwise
    the shell default ``tmp/.comment_queue`` below the Soren root. The Soren
    ``.env`` is deliberately not read here: it holds secrets, and production
    uses the default.
    """
    raw = os.environ.get("COMMENT_QUEUE_DIR", "")
    path = Path(raw) if raw else Path(AUDIO_QUEUE_DEFAULT)
    return path if path.is_absolute() else soren / path


def _collect_audio_queue(soren, now):
    """Backlog of the shared comment/audio queue the corners gate on.

    Only counts, fixed enums and ages are surfaced; item names, texts, the
    queue's own logs and the Soren ``.env`` are never read. A missing or
    unreadable queue is "quiet" because the corner boundary does not block on
    it.
    """
    directory = _comment_queue_dir(Path(soren))
    queued = playing = 0
    readable = True
    oldest_age = -1
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                name = entry.name
                if not name.startswith(AUDIO_ITEM_PREFIX):
                    continue
                try:
                    if not entry.is_file():
                        continue
                except OSError:
                    continue
                if name.endswith(AUDIO_QUEUED_SUFFIX):
                    queued += 1
                elif name.endswith(AUDIO_PLAYING_SUFFIX):
                    playing += 1
                else:
                    continue
                age = _file_age_sec(Path(entry.path), now)
                if age >= 0 and (oldest_age < 0 or age > oldest_age):
                    oldest_age = age
    except FileNotFoundError:
        pass  # No queue yet: nothing is queued, and that is not an error.
    except OSError:
        readable = False
    if not readable:
        reason = "unreadable"
    elif queued and playing:
        reason = "queued+playing"
    elif queued:
        reason = "queued"
    elif playing:
        reason = "playing"
    else:
        reason = "quiet"
    return {
        "present": directory.is_dir(),
        "readable": readable,
        "queued": queued,
        "playing": playing,
        "oldest_age_sec": oldest_age,
        "quiet": reason in ("quiet", "unreadable"),
        "reason": reason,
    }


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
    # root is <soren>/tmp/state, so the Soren root is two levels up.
    result["audio"] = _collect_audio_queue(root.parent.parent, now)
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


# Fixed game set whose match-end records back the broadcast score-stats panel.
# Paths are always derived from this tuple, never from state input (#2392).
RETRO_SCORELOG_GAMES = (
    "gnurobots", "ninvaders", "nsnake", "bastet", "moon-buggy", "pacman4console",
)
RETRO_SCORELOG_TAIL_BYTES = 64 * 1024
# Safety cap for pathological (very short) lines; the byte bound is the real
# window.  A real match record is ~70-90 bytes, so production logs stay well
# inside both and are scanned in full (`truncated=false`).
RETRO_SCORELOG_MAX_LINES = 2000
RETRO_SCORELOG_LAST = 5
# Producers today: the CLI wrappers, the resolver brain and the corner-recorded
# live scores. Anything else is counted as "other" instead of echoed.
RETRO_SCORELOG_SOURCES = ("agent", "live", "wrapper")
RETRO_SCORELOG_MAX_SCORE = 10**9


def _collect_retro_scores(state_dir, now):
    """Bounded, sanitized tail of each game's match-end scorelog (#2392).

    A CLI wrapper appends one record per finished match.  The regression this
    evidence exists for is a wrapper that keeps treating a lingering game-over
    pane as "in play" and therefore appends the same confirmed score every
    poll, so the projection has to answer "how many records, and how long is
    the longest run of one repeated score" without exposing the raw log.

    Only the fixed game names are read, only the file tail is scanned, and
    only bounded integers, the enumerated producers and the duplicate-run
    length are emitted.  Other record fields (clip ids, hashes, paths, free
    text) are never projected.

    ``records`` and ``max_same_score_run`` are window-scoped.  While
    ``truncated=false`` the whole log was scanned, so the ``records`` delta
    across a corner run is the exact number of appended records; with
    ``truncated=true`` the count only lower-bounds the log, and acceptance
    reads ``last`` and ``max_same_score_run`` (the repeated-poll signature)
    instead.  ``last`` is oldest-first.
    """
    scores_dir = Path(state_dir) / "scores"
    result = {
        "present": False,
        "game_count": len(RETRO_SCORELOG_GAMES),
        "games": {},
    }
    for game in RETRO_SCORELOG_GAMES:
        entry = {
            "present": False,
            "readable": False,
            "records": 0,
            "invalid": 0,
            "truncated": False,
            "sources": {name: 0 for name in (*RETRO_SCORELOG_SOURCES, "other")},
            "max_same_score_run": 0,
            "last": [],
            "age_sec": -1,
        }
        path = scores_dir / f"{game}.jsonl"
        try:
            if path.is_symlink() or not path.is_file():
                result["games"][game] = entry
                continue
            st = path.stat()
        except OSError:
            result["games"][game] = entry
            continue
        entry["present"] = True
        result["present"] = True
        entry["age_sec"] = _file_age_sec(path, now)
        tail = st.st_size > RETRO_SCORELOG_TAIL_BYTES
        try:
            with open(path, "rb") as handle:
                if tail:
                    handle.seek(st.st_size - RETRO_SCORELOG_TAIL_BYTES)
                raw = handle.read(RETRO_SCORELOG_TAIL_BYTES)
        except OSError:
            result["games"][game] = entry
            continue
        entry["readable"] = True
        entry["truncated"] = tail
        lines = raw.split(b"\n")
        if tail:
            lines = lines[1:]  # drop the partial line the seek landed inside
        lines = [line for line in lines if line.strip()]
        if len(lines) > RETRO_SCORELOG_MAX_LINES:
            lines = lines[-RETRO_SCORELOG_MAX_LINES:]
            entry["truncated"] = True
        scores = []
        last = []
        for line in lines:
            try:
                row = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                row = None
            if not isinstance(row, dict):
                entry["invalid"] += 1
                continue
            score = row.get("score")
            if type(score) is not int or not 0 <= score <= RETRO_SCORELOG_MAX_SCORE:
                entry["invalid"] += 1
                continue
            producer = row.get("source")
            name = producer if producer in RETRO_SCORELOG_SOURCES else "other"
            entry["sources"][name] += 1
            entry["records"] += 1
            scores.append(score)
            last.append({
                "ts": _bounded_time(row.get("ts")),
                "score": score,
                "source": name,
            })
        run = 0
        previous = None
        for score in scores:
            run = run + 1 if score == previous else 1
            previous = score
            if run > entry["max_same_score_run"]:
                entry["max_same_score_run"] = run
        entry["last"] = last[-RETRO_SCORELOG_LAST:]
        result["games"][game] = entry
    return result


_HOTLOAD_STATES = frozenset({"consistent", "mixed", "partial", "unestablished"})
_HOTLOAD_ROLES = ("agent", "corner")
_HOTLOAD_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def _hotload_digest(value):
    return value if isinstance(value, str) and _HOTLOAD_DIGEST.fullmatch(value) else None


def _project_hotload(value):
    """Hanjuku hot-load generation agreement (#1469): state, roles, digests.

    ``state=partial``/``mixed`` means the agent and the corner disagree (or one
    side's durable evidence never landed), and ``grace_exceeded=true`` means the
    bounded hold expired and the stall teardown was taken anyway.  Both are
    otherwise only visible in ``hanjuku_events.jsonl`` and the corner state, so
    the read-only diagnostics carry them explicitly.
    """
    if not isinstance(value, dict):
        return None
    state = value.get("state")
    published = value.get("published")
    return {
        "state": state if state in _HOTLOAD_STATES else None,
        "published": ([role for role in _HOTLOAD_ROLES if role in published]
                      if isinstance(published, list) else []),
        "agent": _hotload_digest(value.get("agent")),
        "corner": _hotload_digest(value.get("corner")),
        "deployed": _hotload_digest(value.get("deployed")),
        "grace_exceeded": (
            value.get("grace_exceeded") if isinstance(value.get("grace_exceeded"), bool) else None
        ),
    }


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
    finish_reason = data.get("finish_reason")
    unchanged_seconds = _finite_number(data.get("screen_unchanged_seconds"))
    screen_readable = data.get("screen_readable")
    return {
        "status": _bounded_str(data.get("status"), 32),
        # Persisted producer reasons only; never infer a boundary from timing.
        "finish_reason": (finish_reason if isinstance(finish_reason, str)
                          and finish_reason in {"terminal", "stalled", "recover", "force_recover"}
                          else None),
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
                       in {"game_over", "screen_stalled", "input_stalled", "manual_saved_stop",
                           "manual_forced_stop",
                           "switch-terminal-before-corner-active", "game-completed",
                           "queue-timeout", "beta-stopped", "manual", "match-timeout",
                           "operator-moved-during-tsuitate",
                           "operator-moved-during-yosou",
                           "operator-moved-during-doushi",
                           "operator-moved-during-mystery",
                           "operator-moved-during-lawsim",
                           "case-completed", "case-error",
                           "duration",
                           "audio-complete",
                           "operator-moved-after-start",
                           "operator-moved-before-restore",
                           "operator-stopped-before-start"} else None),
        "game_result": _project_game_result(data.get("beta_result")),
        "bot_phase": (data.get("bot_phase") if data.get("bot_phase") in {
            "transition", "name", "dialogue", "shop", "field", "field_menu",
            "battle_intro", "battle", "title_or_intro", "title", "month_menu", "concert", "event"} else None),
        "bot_actions_sent": _bounded_int(data.get("bot_actions_sent")),
        "battles_started": _bounded_int(data.get("battles_started")),
        "battles_finished": _bounded_int(data.get("battles_finished")),
        "screen_unchanged_seconds": (unchanged_seconds if unchanged_seconds is not None
                                     and unchanged_seconds >= 0 else None),
        "screen_readable": screen_readable if type(screen_readable) is bool else None,
        "bot_version": (data.get("bot_version") if data.get("bot_version") in {
            "hanjuku-script-v1", "hanjuku-chart-v2",
            "hanjuku-chart-v224-overwhelming-melee-egg-save",
            "hanjuku-chart-v225-merchant-bargains",
            "hanjuku-chart-v226-general-egg-recheck",
            "hanjuku-chart-v227-healthy-egg-margin",
            "hanjuku-chart-v228-bijin-request-decline",
            "hanjuku-chart-v229-bijin-hold-release",
            "hanjuku-chart-v232-low-army-refill-first",
            "hanjuku-chart-v233-boss-card-proven-absent",
            "hanjuku-chart-v234-refill-outcome",
            "hanjuku-chart-v235-boss-card-rebuy",
            "hanjuku-chart-v236-readable-prose-boundary",
            "hanjuku-chart-v237-clash-gauge-dropper-carry",
            "hanjuku-chart-v238-native-map-registration",
            "hanjuku-chart-v239-map-bootstrap-source-guard",
            "hanjuku-chart-v240-map-identity-hold",
            "hanjuku-chart-v241-deploy-endpoint-guard",
            "hanjuku-chart-v238-queen-command-window",
            "hanjuku-chart-v239-concert-picker-escape",
            "hanjuku-chart-v241-sortie-confirm-cancel",
            "hanjuku-chart-v244-payroll-roster-weakest",
            "hanjuku-chart-v245-hero-repair-reservation",
            "hanjuku-chart-v247-house-cancel-diagnostics",
            "hanjuku-chart-v248-bijin-house-policy",
            "hanjuku-chart-v249-shared-egg-plan",
            "hanjuku-chart-v250-low-hp-melee-advantage",
            "hanjuku-chart-v251-shared-egg-plan",
            "hanjuku-chart-v252-current-roster-recruit",
            "hanjuku-chart-v253-bijin-monthly-owner",
            "hanjuku-chart-v254-bijin-stall-bound",
            "hanjuku-chart-v255-native-map-registration",
            "hanjuku-chart-v256-approx-source-map",
            "hanjuku-chart-v257-approx-fallback",
            "hanjuku-chart-v258-approx-default",
            "hanjuku-chart-v259-known-reanchor",
            "hanjuku-chart-v260-reanchor-restart",
            "hanjuku-chart-v261-single-roof-reanchor",
            "hanjuku-chart-v262-status-label-edges",
            "hanjuku-chart-v263-probe-release-text",
            "hanjuku-chart-v264-house-needs-chapter",
            "hanjuku-chart-v265-resume-inherit-chapter",
            "hanjuku-chart-v266-interim-before-search",
            "hanjuku-chart-v267-chapter3-measured",
            "hanjuku-chart-v268-all-chapter-castles",
            "hanjuku-chart-v269-field-text-by-text",
            "hanjuku-chart-v270-battle-menu-open-wait",
            "hanjuku-chart-v271-y-jump-unreadable-bounded",
            "hanjuku-chart-v272-y-jump-stationary-bounded",
            "hanjuku-chart-v273-defense-break-execution",
            "hanjuku-chart-v275-battle-opening-dialogue",
            "hanjuku-chart-v276-chapter-three-relays",
            "hanjuku-chart-v277-nav-motion-continuity",
            "hanjuku-chart-v278-chapter-three-boss-cell",
            "hanjuku-chart-v280-approx-fallback-source-chapters",
            "hanjuku-chart-v281-probe-empty-relay",
            "hanjuku-chart-v282-world-map-sea-cursor",
            "hanjuku-chart-v129-battle-card-progress",
            "hanjuku-chart-v130-summer-cursor-evidence",
            "hanjuku-chart-v131-defense-month-economy",
            "hanjuku-chart-v132-card-damage-gate",
            "hanjuku-chart-v133-measured-egg-fee",
            "hanjuku-chart-v134-month-nav-cursor-guard",
            "hanjuku-chart-v135-month-foreground-state",
            "hanjuku-chart-v136-resource-priority",
            "hanjuku-chart-v137-camp-recheck",
            "hanjuku-chart-v150-summoned-monster-threat",
            "hanjuku-chart-v151-chikujou-scroll",
            "hanjuku-chart-v152-control-melee-followup",
            "hanjuku-chart-v153-visible-castle-levels",
                "hanjuku-chart-v154-nearest-recall",
                "hanjuku-chart-v155-sortie-source-probe",
                "hanjuku-chart-v156-starter-party",
                "hanjuku-chart-v157-eggless-wall-risk",
                "hanjuku-chart-v158-native-starter-pane",
                "hanjuku-chart-v159-egg-recheck-wall-risk",
                "hanjuku-chart-v162-starter-selected-row-cursor",
                "hanjuku-chart-v163-recruit-dialog-eligibility",
                "hanjuku-chart-v164-source-location-recheck",
                "hanjuku-chart-v165-resume-starter-readiness",
                "hanjuku-chart-v166-egg-recovery-receipt",
                "hanjuku-chart-v167-fake-hero-reveal",
                "hanjuku-chart-v168-general-repair-budget",
                "hanjuku-chart-v169-repair-interruption",
                "hanjuku-chart-v170-chapter-two-boss",
                "hanjuku-chart-v173-resume-sortie-menu", "hanjuku-chart-v174-status-reuse-hero-guard", "hanjuku-chart-v175-combat-aware-force", "hanjuku-chart-v176-individual-egg-recovery", "hanjuku-chart-v177-continuous-chikujou", "hanjuku-chart-v178-native-individual-quote", "hanjuku-chart-v179-soldier-team-reading", "hanjuku-chart-v180-disabled-defense-probe", "hanjuku-chart-v181-merchant-reserve", "hanjuku-chart-v182-recovery-list-return", "hanjuku-chart-v184-ritual-foreground", "hanjuku-chart-v185-layered", "hanjuku-chart-v186-unicorn-layered", "hanjuku-chart-v187-ritual-recitation", "hanjuku-chart-v188-restored-terminal-context", "hanjuku-chart-v189-priest-sprite-dialogue", "hanjuku-chart-v190-bound-sortie-execution", "hanjuku-chart-v191-orphan-battle-menu", "hanjuku-chart-v192-conserved-army-reobserve", "hanjuku-chart-v193-wounded-summoner", "hanjuku-chart-v194-army-count-check", "hanjuku-chart-v195-melee-hp-continuation", "hanjuku-chart-v196-observed-menu-feedback", "hanjuku-chart-v197-unread-battle-force", "hanjuku-chart-v198-retry-exhaust-month-step", "hanjuku-chart-v199-no-hero-substitute", "hanjuku-chart-v200-live-summon-shield", "hanjuku-chart-v201-month-exit-bound", "hanjuku-chart-v202-steady-edition", "hanjuku-chart-v203-current-roster-budget", "hanjuku-chart-v204-observed-monster-alias", "hanjuku-chart-v205-unlocated-successor", "hanjuku-chart-v206-enemy-general-command", "hanjuku-chart-v207-recruit-return-ownership", "hanjuku-chart-v208-control-melee-option", "hanjuku-chart-v209-partial-enemy-command", "hanjuku-chart-v210-zero-army-egg-skip", "hanjuku-chart-v211-naruto-damage-priority", "hanjuku-chart-v212-roster-scroll-end", "hanjuku-chart-v213-unknown-text-boundaries", "hanjuku-chart-v214-trade-choice-safety", "hanjuku-chart-v215-roster-known-members", "hanjuku-chart-v216-roster-selected-name", "hanjuku-chart-v218-egg-before-retreat", "hanjuku-chart-v219-boss-and-army-refill", "hanjuku-chart-v220-finishing-resources", "hanjuku-chart-v221-recall-status-proof"} else None),
        "bot_chart": _project_bot_chart(data.get("bot_chart")),
        "narration": _project_counts(data.get("narration"),
                                     ("enqueued", "delivery_failed", "skipped")),
        "overlay_announcements": _project_counts(data.get("overlay_announcements"),
                                                 ("sent", "failed", "skipped")),
        "review": _project_review(data.get("review")),
        "game_audio": _project_game_audio(data.get("game_audio")),
        "hotload": _project_hotload(data.get("hotload")),
    }


_PRESENTATION_FAILURE_STATUSES = frozenset({"viewer_timeout", "viewer_exited"})


def _read_presentation_failure(path):
    """Read and validate a presentation_failure.json file (read-only)."""
    try:
        if path is None or not path.is_file() or path.is_symlink():
            return None
        if path.stat().st_size > 4096:
            return None
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _project_soren91_corner(data, state_dir=None, presentation_failure=None):
    """Meriken/soren91 corner lifecycle plus why its last switch ended.

    The corner keeps the propagated switch error in its own state file. That
    is the only durable evidence for a ``readiness_timeout``: the presenter
    pane dies with its tmux window, so without this the operator sees only
    "failed" and has to reproduce the failure to learn the cause (docich#1221).
    ``switch_status`` stays a fixed enum; the recorded switch error code is a
    bounded token and the detail is redacted and length-capped.

    When the corner is in the interrupted-before-active path (docich#2658) the
    corner state carries no switch error, so the last switch failure is also
    projected from ``presentation_failure.json`` (runtime dir) and/or an
    embedded ``presentation_failure`` dict. Only fixed keys are emitted; unknown
    values become null. Free text, paths and request ids are never exposed.
    """
    entry = _project_corner_state(data)
    status = data.get("switch_status") if isinstance(data, dict) else None
    entry["switch_status"] = (
        status if isinstance(status, str) and status in SWITCH_STATUSES else None
    )
    entry["switch_error_code"] = _bounded_str(
        data.get("last_error_code") if isinstance(data, dict) else None, 64
    )

    # Resolve presentation_failure data from (in order): explicit arg, embedded
    # in data, or the runtime/state directory on disk.
    pf = presentation_failure
    if pf is None and isinstance(data, dict):
        pf = data.get("presentation_failure")
    if pf is None and state_dir is not None:
        state_dir = Path(state_dir)
        # Real writer layout: <state_dir>/runtimes/<runtime_id>/presentation_failure.json
        # (naming.runtime_directory). Enumerate the runtime entries and refuse
        # a symlinked runtime directory *before* descending into it: a glob
        # would follow the directory link and reach outside the runtimes tree.
        # Symlinked files are refused too, and the candidate count is capped.
        candidates = []
        runtimes_dir = state_dir / "runtimes"
        if runtimes_dir.is_dir() and not runtimes_dir.is_symlink():
            for runtime_entry in runtimes_dir.iterdir():
                if runtime_entry.is_symlink() or not runtime_entry.is_dir():
                    continue
                candidates.append(runtime_entry / "presentation_failure.json")
        candidates.sort(key=lambda p: p.stat().st_mtime if p.is_file() else 0.0, reverse=True)
        for candidate in candidates[:32]:
            if candidate.is_symlink():
                continue
            pf = _read_presentation_failure(candidate)
            if pf is not None:
                break

    if not isinstance(pf, dict):
        pf = {}

    pf_status = pf.get("status")
    entry["presentation_failure"] = {
        "status": (
            pf_status if isinstance(pf_status, str)
            and pf_status in _PRESENTATION_FAILURE_STATUSES else None
        ),
        "reason": (
            _bounded_str(pf.get("reason"), 200)
            if isinstance(pf.get("reason"), str) else None
        ),
        "viewer_wait_sec": (
            pf.get("viewer_wait_sec")
            if (isinstance(pf.get("viewer_wait_sec"), int)
                and not isinstance(pf.get("viewer_wait_sec"), bool)
                and pf.get("viewer_wait_sec") > 0) else None
        ),
        "elapsed_s": _finite_number(pf.get("elapsed_s")),
        "viewer_exit_code": (
            pf.get("viewer_exit_code")
            if (isinstance(pf.get("viewer_exit_code"), int)
                and not isinstance(pf.get("viewer_exit_code"), bool)) else None
        ),
    }
    # Flat aliases for callers that expect top-level keys.
    entry["presentation_failure_status"] = entry["presentation_failure"]["status"]
    entry["presentation_failure_reason"] = entry["presentation_failure"]["reason"]
    entry["presentation_failure_viewer_wait_sec"] = entry["presentation_failure"]["viewer_wait_sec"]
    entry["presentation_failure_elapsed_s"] = entry["presentation_failure"]["elapsed_s"]
    entry["presentation_failure_viewer_exit_code"] = entry["presentation_failure"]["viewer_exit_code"]
    return entry


_TSUITATE_SWITCH_ERROR_CODES = frozenset({
    # Fixed GameSwitch ERROR_* values plus Tsuitate's local fallback and the
    # ControlError codes it persists. Never echo a bounded-but-unknown token:
    # arbitrary messages, identifiers or credentials can fit within 64 bytes.
    "busy", "queued", "request_conflict", "invalid_game", "already_active",
    "no_active_game", "invalid_rotation", "prepare_failed", "quiesce_failed",
    "round_boundary_unsupported", "start_failed", "readiness_timeout",
    "agent_start_failed", "rollback_failed", "state_corrupt",
    "recovery_required", "probe_failed", "timeout", "internal",
    "source_fence_lost", "switch_failed", "control_unavailable",
    "control_not_configured", "invalid_beta_operation",
    "token_not_configured", "run_locked", "run_mismatch",
    "control_authentication_failed", "control_timeout", "terminal_unconfirmed",
    "recovery_not_available", "recovery_checkpoint_invalid",
    "terminal_result_unavailable", "terminal_storage_failure",
})


_TSUITATE_SETTLE_REFUSALS = frozenset({
    # Fixed vocabulary of the reason the released-owner settle refused once
    # (docich#1992). Only these are published; an unknown string is null.
    "not-failed", "other-owner", "became-active", "canonical-unstable",
    "canonical-busy", "canonical-owns-view", "receipt-open",
})


def _project_tsuitate_corner(data):
    """Expose the persisted failure category, not a new recovery decision.

    The generic last_error_code field intentionally classifies only
    recovery_required. Preserve that contract while making known Tsuitate
    producer codes observable under a separate field (docich#1992). The
    existing last_error preview remains redacted and length-capped; unknown
    codes are null rather than raw state content.

    ``settle_refused_reason`` is the corner's own fixed reason for refusing to
    retire a released failed owner. It exists so a settle that does not fire is
    diagnosable from diagnostics alone instead of needing another guess cycle.
    """
    entry = _project_corner_state(data)
    code = data.get("last_error_code")
    entry["switch_error_code"] = (
        code if isinstance(code, str) and code in _TSUITATE_SWITCH_ERROR_CODES else None
    )
    refusal = data.get("settle_refused_reason")
    # Only a still-failed owner has a meaningful refusal: after the settle (or
    # any other terminal path) retires it, the persisted reason is stale and
    # must not sit next to `interrupted` as if it still applied.
    entry["settle_refused_reason"] = (
        refusal if (entry.get("status") == "failed"
                    and isinstance(refusal, str)
                    and refusal in _TSUITATE_SETTLE_REFUSALS)
        else None
    )
    return entry


def _project_review(value):
    """Bounded 衝立将棋 感想戦 counters: where the facts came from and the kind.

    The narration text, the opponent's name, the move list and the board are
    never projected; only the fixed vocabulary and the move count.
    """
    if not isinstance(value, dict):
        return None
    source = value.get("source")
    kind = value.get("kind")
    failure = value.get("failure")
    boundary = value.get("boundary")
    model_failure = value.get("model_failure")
    moves = value.get("moves")
    return {
        "source": source if source in {"replay", "result-only"} else None,
        "kind": kind if kind in {"ai", "fallback"} else None,
        # Why the public replay was unusable: a fixed code, never a message.
        "failure": failure if failure in {"ok", "read-failed", "verify-failed",
                                          "invalid-color", "invalid-game-id",
                                          "unavailable"} else None,
        # Whether the 感想戦 waited for the shared audio queue's boundary.
        "boundary": boundary if boundary in {"quiet", "timeout", "unavailable",
                                             "skipped"} else None,
        # Why the model prose was not used, when it was not.
        "model_failure": model_failure if model_failure in {
            "none", "unsafe", "gate", "timeout", "rate_limit", "provider",
            "exception"} else None,
        "moves": moves if type(moves) is int and 0 <= moves <= 100000 else None,
    }


def _project_counts(value, keys):
    if not isinstance(value, dict):
        return None
    return {key: _bounded_int(value.get(key)) for key in keys}


_GAME_OUTCOMES = frozenset({"win", "loss", "draw", "unknown"})
_GAME_REASONS = frozenset({
    "normal", "checkmate", "stalemate", "resign", "timeout", "foul_limit", "repetition",
    "draw", "aborted", "disconnect", "transport_error", "protocol_error", "storage_error",
    "interrupted", "no_move", "unknown"})
_GAME_CONFIDENCE = frozenset({"verified", "unknown"})
_GAME_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z\Z")


def _project_game_result(value):
    """Settled Tsuitate result, enum by enum. Anything unexpected drops to null."""
    if not isinstance(value, dict):
        return None
    outcome = value.get("outcome")
    reason = value.get("reason")
    confidence = value.get("resultConfidence")
    moves = value.get("moveNumber")
    ended = value.get("endedAt")
    if (outcome not in _GAME_OUTCOMES or reason not in _GAME_REASONS
            or confidence not in _GAME_CONFIDENCE
            or isinstance(moves, bool) or not isinstance(moves, int)
            or not 0 <= moves <= 100000
            or not isinstance(ended, str) or not _GAME_TIME.fullmatch(ended)):
        return None
    return {"outcome": outcome, "reason": reason, "resultConfidence": confidence,
            "moveNumber": moves, "endedAt": ended}


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
            "reason_code": _rotation_enum(data.get("reason_code"), PAPER_IMPROVE_REASON_CODES),
            "started_at": _bounded_time(data.get("started_at")),
            "updated_at": _bounded_time(data.get("updated_at")),
            "completed_at": _bounded_time(data.get("completed_at")),
            "changed": changed,
        }
    )
    return entry


def _paper_observed_number(value, *, decimal_string=False, nonnegative=False):
    """Finite diagnostic numbers only; a string can contain no other text."""
    if isinstance(value, str) and decimal_string:
        if (len(value) > 64 or not re.fullmatch(
                r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value)):
            return None
    elif type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(number) or abs(number) > 9007199254740991:
        return None
    return None if nonnegative and number < 0 else number


def _paper_observed_count(value):
    return value if type(value) is int and 0 <= value <= 9007199254740991 else None


def _collect_paper_experiment_evaluation(trading_dir, control, now):
    path = trading_dir / "paper_strategy_evaluation.json"
    present, readable, data = _load_state_file(path)
    entry = {"present": present, "readable": readable, "age_sec": _file_age_sec(path, now)}
    if not readable:
        return entry
    status = _rotation_enum(data.get("status"), PAPER_EVALUATION_STATUSES)
    reason = _rotation_enum(data.get("reason_code"), PAPER_EVALUATION_REASON_CODES)
    matches = None
    if isinstance(control, dict) and isinstance(control.get("experiment_id"), str):
        active_at = _paper_observed_number(control.get("activated_at"), nonnegative=True)
        evaluated_activation = _paper_observed_number(data.get("activated_at"), nonnegative=True)
        matches = bool(
            control["experiment_id"]
            and active_at is not None and evaluated_activation is not None
            and control["experiment_id"] == data.get("experiment_id")
            and control["activated_at"] == data.get("activated_at")
        )
    if matches is False:
        # These separately atomic files can straddle an activation. Never
        # label a different activation's results as the current experiment.
        status, reason = "unavailable", "evaluation_identity_mismatch"
    entry.update(
        status=status, reason_code=reason, matches_active=matches,
        evaluated_at=_paper_observed_number(data.get("evaluated_at"), nonnegative=True),
    )
    usable = status in {"ok", "partial"}
    for key in (
        "closed_sells", "self_entry_closed_sells", "carry_in_closed_sells",
        "mixed_origin_closed_sells", "ignored_unpaired_exits", "open_position_count",
    ):
        entry[key] = _paper_observed_count(data.get(key)) if usable else None
    for key in (
        "realized_pnl_jpy", "self_entry_realized_pnl_jpy", "carry_in_realized_pnl_jpy",
        "profit_factor", "max_realized_drawdown_pct",
    ):
        entry[key] = _paper_observed_number(
            data.get(key), decimal_string=True,
            nonnegative=key in {"profit_factor", "max_realized_drawdown_pct"},
        ) if usable else None
    for key in ("inventory_complete", "promotion_ready"):
        entry[key] = data.get(key) if usable and isinstance(data.get(key), bool) else None
    return entry


def _collect_paper_experiment(state_dir, now):
    """Observe periodic control independently of the end-of-corner AI job."""
    trading_dir = Path(state_dir) / "trading"
    path = trading_dir / "paper_experiment_control.json"
    present, readable, data = _load_state_file(path)
    entry = {"present": present, "readable": readable, "age_sec": _file_age_sec(path, now)}
    if readable:
        entry.update(
            status=_rotation_enum(data.get("status"), PAPER_EXPERIMENT_STATUSES),
            reason_code=_rotation_enum(data.get("reason_code"), PAPER_EXPERIMENT_REASON_CODES),
            entries_allowed=data.get("entries_allowed") if isinstance(data.get("entries_allowed"), bool) else None,
            pending_available=data.get("pending_available") if isinstance(data.get("pending_available"), bool) else None,
            evaluation_status=_rotation_enum(data.get("evaluation_status"), PAPER_EVALUATION_STATUSES),
            open_position_count=_paper_observed_count(data.get("open_position_count")),
            updated_at=_paper_observed_number(data.get("updated_at"), nonnegative=True),
        )
    entry["evaluation"] = _collect_paper_experiment_evaluation(trading_dir, data if readable else None, now)
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


def _rotation_excluded_projection(value):
    """Bounded corner-id -> fixed exclusion reason map from the last rotation tick.

    ``CornerRotationManager._eligible()`` persists why each catalog corner was left
    out of the automatic rotation (``disabled-or-paused`` / ``manual-only`` /
    ``adapter-unavailable``). Projecting it makes catalog changes observable without
    a reservation: an enabled manual-only corner reads ``manual-only``, while a
    disabled one reads ``disabled-or-paused``. Only catalog ids, fixed reasons and
    a bounded count are emitted; unknown values collapse to ``unknown``.
    """
    if not isinstance(value, dict):
        return None
    out = {}
    for corner, reason in sorted(value.items()):
        if len(out) >= ROTATION_EXCLUDED_LIMIT:
            break
        if not isinstance(corner, str) or not corner or len(corner) > 64:
            continue
        out[corner] = reason if isinstance(reason, str) and reason in ROTATION_EXCLUDED_REASONS else "unknown"
    return out or None


def _rotation_failed_corners_projection(data):
    """Bounded, fixed-shape corner failures that no longer stop the rotation.

    Never emits request identity, payloads or exception text: the ledger row's
    corner id, time, fixed resolution, fixed reason code and fixed error kind
    are enough for an operator to find the failing corner and fix it (#2187).
    """
    rows = data.get("failed_corners")
    projected = []
    if isinstance(rows, list):
        for row in rows[-ROTATION_FAILED_CORNER_MAX:]:
            if not isinstance(row, dict):
                continue
            resolution = row.get("resolution")
            reason_code = row.get("reason_code")
            projected.append({
                "corner": _bounded_str(row.get("corner"), 64),
                "at": _bounded_time(row.get("at")),
                "resolution": (
                    resolution if resolution in ROTATION_FAILED_CORNER_RESOLUTIONS
                    else "unknown"
                ),
                "reason_code": (
                    reason_code if reason_code in ROTATION_FAILED_CORNER_REASONS
                    else ("unknown" if reason_code is not None else None)
                ),
                "error_kind": _rotation_error_kind(row.get("error_kind")),
            })
    return {"failed_corners": projected}


def _rotation_auto_restore_projection(data):
    """Bounded view of the timer's restore replay budget (#2187).

    A failed corner end can leave the corner's game on screen; the timer then
    replays the reviewed restore recovery a few times. The operator needs to
    see whether that is still running or already exhausted.
    """
    record = data.get("auto_restore")
    out = {"auto_restore_corner": None, "auto_restore_attempts": None,
           "auto_restore_attempt_limit": ROTATION_AUTO_RESTORE_ATTEMPT_LIMIT,
           "auto_restore_next_at": None, "auto_restore_status": None}
    if not isinstance(record, dict):
        return out
    out["auto_restore_corner"] = _bounded_str(record.get("corner"), 64)
    attempts = record.get("attempts")
    out["auto_restore_attempts"] = attempts if type(attempts) is int and 0 <= attempts <= 100 else None
    out["auto_restore_next_at"] = _bounded_time(record.get("next_attempt_at"))
    status = record.get("last_status")
    out["auto_restore_status"] = (status if status in ROTATION_AUTO_RESTORE_STATUSES
                                  else ("unknown" if status is not None else None))
    return out


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
    for name in ROTATION_PENDING_OWNER_FILES:
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


def _rotation_hanjuku_manual_receipt_projection(state_dir, manual, now):
    """Observe only this fixed manual reservation's UUID receipt, without IDs.

    Missing output means not observed; present=false means the fixed lookup
    found no file in this snapshot. Neither proves historical non-execution.
    This is read-only evidence and never authorizes cancellation or recovery.
    """
    out = {"observed": False, "present": None, "readable": None,
           "status": "unknown", "operation": "unknown",
           "request_matches": None, "target_matches": None,
           "terminal_result_matches": None, "cleanup_pending": None,
           "updated_after_selection": None, "runtime_identity_valid": None,
           "runtime_resources_released": None}
    if (not isinstance(manual, dict) or manual.get("corner") != "hanjuku-hero"
            or manual.get("state_file") != "retro_corner_manual.json"):
        return out
    request_id = _weather_request_id(manual.get("request_id"))
    selected = _rotation_time(manual.get("selected_at"))
    if request_id is None or selected is None or selected > now:
        return out
    present, readable, receipt = _rotation_evidence_file(
        state_dir, f"game-switch/requests/{request_id}.json")
    out.update(observed=True, present=present, readable=readable)
    if not readable:
        return out
    status = _rotation_enum(receipt.get("status"), WEATHER_RECEIPT_STATUSES)
    operation = _rotation_enum(receipt.get("operation"), WEATHER_OPERATIONS)
    matches = receipt.get("request_id") == request_id
    target_matches = receipt.get("target") == "hanjuku-hero"
    out.update(status=status, operation=operation, request_matches=matches,
               target_matches=target_matches)
    updated = _rotation_time(receipt.get("updated_at"))
    if updated is not None:
        out["updated_after_selection"] = updated >= selected
    result = receipt.get("result")
    if isinstance(result, dict):
        if status in WEATHER_RESULT_STATUSES:
            out["terminal_result_matches"] = bool(
                matches and target_matches and operation in {"start", "switch"}
                and result.get("request_id") == request_id
                and result.get("status") == status
                and result.get("operation") == operation)
        if isinstance(result.get("cleanup_pending"), bool):
            out["cleanup_pending"] = result["cleanup_pending"]
    if (not out["terminal_result_matches"] or not target_matches
            or out["cleanup_pending"] is not False):
        return out
    # Reuse the operator's validated runtime and read-only release contract.
    # No canonical/owner decision or cancellation authority comes from this
    # unlocked snapshot; the operator rechecks everything under writer locks.
    from docich.game_switch import validate_receipt
    from docich.hanjuku_manual_cancel import CancelRefused, _ProbeTmux, _released_runtime
    try:
        validate_receipt(receipt, state_dir, expected_request_id=request_id)
        runtime = state_dir / "runtimes" / receipt["runtime_id"]
        if any(parent.is_symlink() for parent in (runtime, *runtime.parents)):
            out["runtime_identity_valid"] = False
            return out
        out["runtime_identity_valid"] = True
        _released_runtime(state_dir, receipt["runtime_id"], _ProbeTmux())
        out["runtime_resources_released"] = True
    except CancelRefused as exc:
        if str(exc) == "runtime_resources_present":
            out["runtime_resources_released"] = False
    except Exception:
        # Invalid identity or a failed/timed-out probe stays unknown. Never
        # expose validation messages, tmux output, identifiers or paths.
        pass
    return out


def _rotation_manual_pending_projection(state_dir, data, now):
    """Observe the manual reservation separately from automatic ``pending``.

    A manual queue return keeps its reservation while automatic pending is
    absent. Only the declared, fixed allowlisted owner file can prove ownership;
    a matching request in a sibling file must not make it look resumable.
    This is evidence, never an instruction to resume or recover a corner.
    """
    from docich.hanjuku_manual_evidence import project as manual_evidence
    out = {
        "manual_pending": isinstance(data.get("manual_pending"), dict),
        "manual_pending_corner": None,
        "manual_pending_state_file": None,
        "manual_pending_age_sec": -1,
        "manual_pending_owner": "absent",
        "manual_pending_owner_status": "unknown",
        "manual_pending_fingerprint": None,
        "manual_pending_receipt": _rotation_hanjuku_manual_receipt_projection(
            state_dir, data.get("manual_pending"), now),
        "manual_pending_evidence": manual_evidence(
            state_dir, data.get("manual_pending"), now, read_fixed=_rotation_evidence_file),
    }
    manual = data.get("manual_pending")
    if manual is None:
        return out
    out["manual_pending_owner"] = "unknown"
    if not isinstance(manual, dict):
        return out
    out["manual_pending_corner"] = _rotation_enum(
        manual.get("corner"), (*ROTATION_IMPROVE_GAMES, "paper", "meriken")
    )
    selected = _rotation_time(manual.get("selected_at"))
    if selected is not None and selected <= now:
        out["manual_pending_age_sec"] = int(now - selected)
    # Never derive a filesystem path from reservation input, even a basename.
    files = {name + ".json": name for name in ROTATION_CORNER_FILES}
    filename = manual.get("state_file")
    name = files.get(filename) if isinstance(filename, str) else None
    request_id = manual.get("request_id")
    # An opaque compare-and-cancel token, never the request identity itself.
    # Only this fixed Hanjuku owner is cancellable by the owner operator.
    if (manual.get("corner") == "hanjuku-hero"
            and filename == "retro_corner_manual.json"
            and selected is not None and selected <= now
            and isinstance(request_id, str)
            and re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", request_id)):
        out["manual_pending_fingerprint"] = hashlib.sha256(json.dumps(
            manual, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if name is None:
        return out
    out["manual_pending_state_file"] = name
    if not isinstance(request_id, str) or not request_id:
        return out
    present, readable, raw = _rotation_evidence_file(state_dir, name + ".json")
    if not present:
        out["manual_pending_owner"] = "none"
    elif readable:
        out["manual_pending_owner"] = "none"
        if raw.get("rotation_request_id") == request_id:
            out["manual_pending_owner"] = name
            out["manual_pending_owner_status"] = _rotation_enum(
                raw.get("status"), ROTATION_STATUSES
            )
    return out


def _collect_hanjuku_checkpoint(state_dir, corner):
    """Fixed receipt and file metadata only; never read/export saved game bytes."""
    if not isinstance(corner, dict) or corner.get("game") != "hanjuku-hero":
        return None
    owner = corner.get("bot_identity")
    if (not isinstance(owner, dict) or owner.get("game") != "hanjuku-hero"
            or type(owner.get("generation")) is not int or owner["generation"] < 1
            or not isinstance(owner.get("lease_id"), str) or not owner["lease_id"]
            or not isinstance(owner.get("runtime_id"), str)
            or not re.fullmatch(r"g[0-9]+-[a-f0-9]{8}", owner["runtime_id"])):
        return None
    runtime = Path(state_dir) / "runtimes" / owner["runtime_id"]
    receipt = runtime / "retroarch_boundary.json"
    if runtime.parent.is_symlink() or runtime.is_symlink() or receipt.is_symlink():
        return None
    present, readable, data = _load_state_file(receipt)
    out = {"receipt_present": present, "receipt_readable": readable,
           "save_status": "unverified", "file_present": None,
           "file_bytes": None, "file_metadata_matches": None}
    if not readable or any(data.get(k) != owner.get(k) for k in
                           ("game", "runtime_id", "generation", "lease_id")):
        return out
    if data.get("outcome") not in {"screen_stalled", "input_stalled", "suspended"}:
        return out
    if data.get("status") == "waiting" and data.get("save_required") is True:
        out["save_status"] = "failed" if data.get("save_error") in {
            "save_timeout", "save_failed"} else "waiting"
    sha = data.get("sha256")
    if (data.get("status") == "reached" and data.get("checkpoint") == "hanjuku-hero.state"
            and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
        out["save_status"] = "saved_receipt"
    # Do not accept a receipt-provided path or read the state contents/hash.
    directory = runtime / "states"
    path = directory / "hanjuku-hero.state"
    try:
        if directory.is_symlink() or path.is_symlink():
            return out
        st = path.stat()
        if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= 64 * 1024 * 1024:
            out["file_present"] = False
            return out
        out.update(file_present=True, file_bytes=st.st_size)
        if (type(data.get("checkpoint_bytes")) is int
                and type(data.get("checkpoint_mtime_ns")) is int):
            out["file_metadata_matches"] = (st.st_size == data["checkpoint_bytes"]
                                           and st.st_mtime_ns == data["checkpoint_mtime_ns"])
    except OSError:
        out["file_present"] = False
    return out


def _collect_corner_files(state_dir, payload, now):
    present, readable, data = _load_state_file(state_dir / CORNER_STATE_FILES["corner_rotation"])
    rotation_data = data if readable else None
    rotation = {"present": present, "readable": readable}
    if readable:
        # Fixed projection only: never emit seed, adapter errors or arbitrary
        # request payloads. Queued ownership remains local for operator recovery.
        status = data.get("status")
        inbox_present, inbox_readable, inbox_data = _load_state_file(
            state_dir / "corner_manual_queue.json"
        )
        queued_record = data.get("queued_manual")
        if not isinstance(queued_record, dict) and inbox_readable:
            queued_record = inbox_data
        trigger_at = (
            _bounded_time(queued_record.get("trigger_at"))
            if isinstance(queued_record, dict) else None
        )
        scheduled_manual = trigger_at is not None
        scheduled_due = now >= trigger_at if scheduled_manual else None
        scheduled_blocked_reason = None
        if scheduled_manual and not scheduled_due:
            scheduled_blocked_reason = "not_due"
        elif scheduled_manual and (
                isinstance(data.get("pending"), dict)
                or isinstance(data.get("manual_pending"), dict)):
            scheduled_blocked_reason = "waiting_slot"
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
            queued_manual=(isinstance(data.get("queued_manual"), dict) or inbox_present),
            scheduled_manual=scheduled_manual,
            scheduled_manual_corner=(
                _bounded_str(queued_record.get("corner"), 64)
                if scheduled_manual and isinstance(queued_record, dict) else None
            ),
            scheduled_manual_trigger_at=trigger_at,
            scheduled_manual_due=scheduled_due,
            scheduled_manual_overdue_sec=(
                min(7 * 86400, max(0, int(now - trigger_at)))
                if scheduled_manual and scheduled_due else None
            ),
            scheduled_manual_blocked_reason=scheduled_blocked_reason,
            error_kind=_rotation_error_kind(data.get("error_kind")),
            excluded=_rotation_excluded_projection(data.get("excluded")),
        )
        rotation.update(_rotation_pending_projection(state_dir, data, now))
        rotation.update(_rotation_manual_pending_projection(state_dir, data, now))
        rotation.update(_rotation_failed_corners_projection(data))
        rotation.update(_rotation_auto_restore_projection(data))
    mode, cooldown = _rotation_policy()
    rotation.update(schedule_mode=mode, cooldown_seconds=cooldown)
    payload["corner_rotation"] = rotation
    present, readable, data = _load_state_file(state_dir / CORNER_STATE_FILES["game_switch"])
    entry = {"present": present, "readable": readable}
    if readable:
        active = data.get("active") if isinstance(data.get("active"), dict) else {}
        last = data.get("last_result") if isinstance(data.get("last_result"), dict) else {}
        deadline_at = data.get("deadline_at")
        deadline_expired = None
        if isinstance(deadline_at, str) and deadline_at:
            try:
                from datetime import datetime, timezone
                deadline_dt = datetime.fromisoformat(deadline_at.replace('Z', '+00:00'))
                deadline_expired = deadline_dt.timestamp() < now
            except (ValueError, TypeError):
                deadline_expired = None
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
                "deadline_at": _bounded_str(deadline_at, 40),
                "deadline_expired": deadline_expired,
            }
        )
    payload["game_switch"] = entry
    payload["weather_corner"] = _weather_corner_projection(
        state_dir, rotation_data, rotation_data is not None
    )
    payload["external_video"] = _collect_external_video(state_dir, now)
    payload["game_switch_fifo"] = _collect_game_switch_fifo(state_dir, now)
    payload["game_switch_watchdog"] = _collect_game_switch_watchdog()
    payload["corner_rotation_timer_alias"] = _collect_corner_rotation_timer_alias()

    for name in (
        "retro_corner",
        "paper_corner",
        "paper_corner_manual",
        "nethack_corner",
        "nethack_corner_manual",
        "tsuitate_corner",
        "yoso_corner",
        "doushi_corner",
        "mystery_corner",
        "lawsim_corner",
        "soren91_corner",
    ):
        present, readable, data = _load_state_file(state_dir / CORNER_STATE_FILES[name])
        entry = {"present": present, "readable": readable}
        if readable:
            if name == "soren91_corner":
                entry.update(_project_soren91_corner(data, state_dir=state_dir))
            elif name == "tsuitate_corner":
                entry.update(_project_tsuitate_corner(data))
            else:
                entry.update(_project_corner_state(data))
            if name == "retro_corner":
                entry["saved_checkpoint"] = _collect_hanjuku_checkpoint(state_dir, data)
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
    payload["paper_experiment"] = _collect_paper_experiment(state_dir, now)
    payload["rotation_evidence"] = _collect_rotation_evidence(state_dir)


def _external_video_process_alive(receiver):
    pid, ticks = receiver.get("pid"), receiver.get("start_ticks")
    if type(pid) is not int or pid <= 1 or type(ticks) is not int or ticks <= 0:
        return False
    root = Path("/proc") / str(pid)
    try:
        before = (root / "stat").read_text().rsplit(") ", 1)[1].split()
        argv = (root / "cmdline").read_bytes().split(b"\0")
        after = (root / "stat").read_text().rsplit(") ", 1)[1].split()
        return (before[0] not in {"Z", "X"} and after[0] not in {"Z", "X"}
                and int(before[19]) == int(after[19]) == ticks
                and b"external-video-corner" in argv and b"receive" in argv
                and receiver["receiver_id"].encode() in argv)
    except (OSError, ValueError, IndexError, KeyError):
        return False


def _collect_external_video(state_dir, now):
    """Fixed enums/health only: no IP, request, PID, file body or argv output."""
    entry = {"receiver_present": False, "receiver_readable": False,
             "receiver_status": "absent", "receiver_alive": None,
             "frame_fresh": None, "frame_age_sec": None, "audio_present": None,
             "corner_status": "absent", "recovery_required": None}
    present, readable, corner = _load_state_file(Path(state_dir) / _REG.EXTERNAL_VIDEO["corner_file"])
    if present:
        entry["corner_status"] = "unknown"
    if readable:
        status = corner.get("status")
        if status in {"waiting", "starting", "active", "restoring", "failed", "completed", "interrupted"}:
            entry["corner_status"] = status
        entry["recovery_required"] = corner.get("recovery_required") is True
    receiver_path = Path(state_dir) / _REG.EXTERNAL_VIDEO["receiver_file"]
    entry["receiver_present"] = receiver_path.exists()
    if not entry["receiver_present"]:
        return entry
    entry["receiver_status"] = "unknown"
    _, readable, receiver = _load_state_file(receiver_path)
    if not readable:
        return entry
    rid = receiver.get("receiver_id")
    expiry = receiver.get("expires_at")
    if (receiver.get("schema_version") != 1 or not isinstance(rid, str)
            or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", rid) is None
            or type(expiry) not in (int, float) or not math.isfinite(expiry)):
        return entry
    alive = _external_video_process_alive(receiver)
    try:
        age = now - (Path(state_dir) / "external-video" / rid / "current.png").stat().st_mtime
    except OSError:
        age = None
    fresh = (alive and receiver.get("status") == "receiving" and now < expiry
             and age is not None and 0 <= age <= _REG.EXTERNAL_VIDEO["frame_max_age_sec"])
    if age is not None and math.isfinite(age) and age >= 0:
        entry["frame_age_sec"] = min(int(age), _REG.EXTERNAL_VIDEO["frame_age_max_sec"])
    entry.update(receiver_readable=True, receiver_alive=alive,
                 frame_fresh=fresh, audio_present=receiver.get("audio_present") is True)
    if receiver.get("status") in {"launching", "waiting", "receiving", "stopped", "failed"}:
        entry["receiver_status"] = receiver["status"]
    return entry


# Encoder heartbeat projection (#AV projection). Only fixed numeric/enum keys
# are copied: the source records also carry stream configuration and the
# reconnect channel name, which must never cross this boundary.
_ENCODER_STATES = frozenset({"running", "stopped", "failed"})
_ENCODER_MAX_FRAMES = 2 ** 53 - 1


def _encoder_count(value):
    if type(value) is not int or not 0 <= value <= _ENCODER_MAX_FRAMES:
        return None
    return value


def _normalize_encoder(kind, data):
    """Return the fixed projection of one heartbeat record, or None."""
    normalized = {
        "state": "unknown",
        "ready": None,
        "frames_sent": None,
        "fps": None,
        "out_time_sec": None,
    }
    if kind == "twica":
        if (type(data.get("schema")) is not int
                or data["schema"] != _REG.ENCODER["schema_version"]):
            return None
        ready = data.get("ready")
        normalized["ready"] = ready if isinstance(ready, bool) else None
        normalized["frames_sent"] = _encoder_count(data.get("frames_sent"))
        fps = data.get("fps")
        if type(fps) in (int, float) and math.isfinite(fps) and 0 <= fps <= 240:
            normalized["fps"] = round(float(fps), 2)
        if normalized["ready"] is True:
            normalized["state"] = "running"
        elif data.get("frame_state") == "closed":
            normalized["state"] = "stopped"
        return normalized
    # Soren direct_stream writes the parsed ffmpeg progress record.
    state = data.get("state")
    if isinstance(state, str) and state in _ENCODER_STATES:
        normalized["state"] = state
    elif data.get("running") is True:
        normalized["state"] = "running"
    elif data.get("running") is False:
        normalized["state"] = "stopped"
    normalized["ready"] = data.get("running") if isinstance(data.get("running"), bool) else None
    normalized["frames_sent"] = _encoder_count(data.get("frame"))
    fps = data.get("fps")
    if type(fps) in (int, float) and math.isfinite(fps) and 0 <= fps <= 240:
        normalized["fps"] = round(float(fps), 2)
    out_time_us = data.get("out_time_us")
    if type(out_time_us) is int and 0 <= out_time_us <= 10 ** 15:
        normalized["out_time_sec"] = out_time_us // 1_000_000
    return normalized


def _collect_encoder(state_dir, soren, now):
    """Read-only encoder heartbeat from the two existing producer paths.

    TwiCa's common adapter owns ``run/twica-common/pipeline.json`` only while
    it holds the output; Soren's ``direct_stream`` owns its own progress record.
    Whichever is fresh wins, and a stale record is reported as ``stale`` rather
    than as a healthy encoder.
    """
    result = {
        "schema_version": _REG.ENCODER["schema_version"],
        "present": False,
        "readable": False,
        "source": "none",
        "state": "unknown",
        "ready": None,
        "frames_sent": None,
        "fps": None,
        "progress_age_sec": None,
        "out_time_sec": None,
        "reason": "absent",
    }
    candidates = (
        ("twica_common", Path(state_dir) / _REG.ENCODER["twica_subpath"],
         _REG.ENCODER["twica_stale_sec"], "twica"),
        ("direct_stream", Path(soren) / _REG.ENCODER["direct_stream_subpath"],
         _REG.ENCODER["direct_stream_stale_sec"], "direct"),
    )
    stale = None
    invalid = None
    for source, path, stale_sec, kind in candidates:
        present, readable, data = _load_state_file(path)
        if not present:
            continue
        if not readable or not isinstance(data, dict):
            invalid = invalid or (source, "unreadable")
            continue
        normalized = _normalize_encoder(kind, data)
        if normalized is None:
            invalid = invalid or (source, "invalid_schema")
            continue
        try:
            raw_age = now - path.stat().st_mtime
            # A clock jump must not make an old record look fresh; an mtime far
            # in the future is treated as stale rather than as age 0.
            age = None if raw_age < -300 else max(0, int(raw_age))
        except OSError:
            age = None
        if age is None or age > stale_sec:
            stale = stale or (source, normalized, age)
            continue
        result.update(normalized)
        result.update(source=source, present=True, readable=True,
                      progress_age_sec=age, reason="ok")
        return result
    if stale is not None:
        source, normalized, age = stale
        result.update(normalized)
        result.update(source=source, present=True, readable=True,
                      progress_age_sec=age, reason="stale")
        return result
    if invalid is not None:
        result.update(source=invalid[0], present=True, readable=False, reason=invalid[1])
    return result


_HANJUKU_PREDICTION_RECORD_LIMIT = 16384
_HANJUKU_PREDICTION_IDENTITY_KEYS = ("game", "runtime_id", "generation", "lease_id")
_HANJUKU_PREDICTION_TERMINAL_REASONS = ("game_over", "screen_stalled", "input_stalled")
_HANJUKU_PREDICTION_BLOCKERS = (
    "runtime-identity-invalid", "runtime-missing", "run-record-missing",
    "run-record-unreadable", "run-record-identity-mismatch",
    "terminal-evidence-malformed", "result-record-unreadable",
    "result-record-mismatch", "progress-record-unreadable",
)


def _bounded_prediction_record(path):
    """(value, present, readable) for one bounded no-follow JSON record."""
    directory_fd = None
    try:
        # Open every parent component without following links. The directory
        # descriptor anchors the record even if a pathname is replaced later.
        path = Path(path)
        parent = path.parent
        directory_fd = _supervisor_directory(parent.anchor or ".")
        components = parent.parts[1:] if parent.anchor else parent.parts
        for component in components:
            previous_fd = directory_fd
            directory_fd = _supervisor_directory(component, dir_fd=previous_fd)
            os.close(previous_fd)
        raw = _supervisor_read(path.name, _HANJUKU_PREDICTION_RECORD_LIMIT,
                               dir_fd=directory_fd)
        value = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        return None, False, False
    except (OSError, ValueError, RecursionError):
        return None, True, False
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    if not isinstance(value, dict):
        return None, True, False
    return value, True, True


def _is_digest(value):
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def _collect_hanjuku_prediction_ownership(state_dir, data, row):
    """Bounded, read-only classification of one owned prediction round.

    ``hanjuku_predictions`` keeps the round owned when an external record stops
    validating, so a permanent mismatch spins in ``error=invalid_state`` with the
    fixed 300 s retry. This reports *which* record is decisive and whether the
    persisted next poll is the retry backoff or the ordinary poll interval, using
    fixed enums, booleans and bounded integers only. No record body, viewer name
    or OAuth material is read out.
    """
    now = time.time()
    next_poll = _finite_number(data.get("next_poll_at") if isinstance(data, dict) else None)
    delay = None
    if next_poll is not None:
        delay = max(0, min(86400, int(next_poll - now)))
    out = {"retry_delay_seconds": delay, "blocker": None}
    if not isinstance(row, dict) or not row:
        return out

    runtime_id = row.get("runtime_id")
    if (not isinstance(runtime_id, str) or not runtime_id or len(runtime_id) > 128
            or "/" in runtime_id or "\\" in runtime_id or ".." in runtime_id):
        out["blocker"] = "runtime-identity-invalid"
        return out
    runtime = Path(state_dir) / "runtimes" / runtime_id
    try:
        present = (not any(parent.is_symlink() for parent in (runtime, *runtime.parents))
                   and runtime.is_dir())
    except OSError:
        present = None
    out["runtime_present"] = present
    if present is False:
        out["blocker"] = "runtime-missing"
        return out

    run_state, run_present, run_readable = _bounded_prediction_record(
        runtime / "hanjuku_run.json")
    out["run_record_present"] = run_present
    out["run_record_readable"] = run_readable
    if not run_present:
        out["blocker"] = "run-record-missing"
        return out
    if not run_readable:
        out["blocker"] = "run-record-unreadable"
        return out

    identity = {key: row.get(key) for key in _HANJUKU_PREDICTION_IDENTITY_KEYS}
    if any(run_state.get(key) != value for key, value in identity.items()):
        out["blocker"] = "run-record-identity-mismatch"
        return out

    reason = run_state.get("terminal_reason")
    reason = reason if reason in _HANJUKU_PREDICTION_TERMINAL_REASONS else None
    out["terminal_reason"] = reason
    if reason is None:
        # No terminal is recorded yet: the round waits rather than failing.
        return out
    if not _is_digest(run_state.get("frame_sha256")):
        out["blocker"] = "terminal-evidence-malformed"
        return out

    result_state, result_present, result_readable = _bounded_prediction_record(
        runtime / "hanjuku_prediction_result.json")
    out["result_record_present"] = result_present
    if result_present and not result_readable:
        out["blocker"] = "result-record-unreadable"
        return out
    if result_readable:
        mismatched = (
            any(result_state.get(key) != value for key, value in identity.items())
            or result_state.get("reason") != reason
            or result_state.get("frame_sha256") != run_state.get("frame_sha256")
        )
        if mismatched:
            out["blocker"] = "result-record-mismatch"
            return out

    _progress, progress_present, progress_readable = _bounded_prediction_record(
        runtime / "hanjuku_progress.json")
    out["progress_record_present"] = progress_present
    if progress_present and not progress_readable:
        out["blocker"] = "progress-record-unreadable"
    return out


def _collect_hanjuku_prediction_health(state_dir, errors):
    """Bounded consecutive-error projection; never expose a record body.

    The counter lives in its own record so it survives the case the durable
    ledger is rejected by ``_load`` and cannot be rewritten (#2216).
    """
    record, present, readable = _bounded_prediction_record(
        Path(state_dir) / "hanjuku_predictions_health.json")
    if not present:
        return None
    record = record if readable and isinstance(record, dict) else {}
    streak = record.get("consecutive_errors")
    last_error = record.get("last_error")
    last_error = last_error if isinstance(last_error, str) and last_error in errors else None
    return {"present": present, "readable": readable,
            "consecutive_errors": (streak if type(streak) is int
                                   and 0 <= streak <= 1_000_000 else None),
            "last_error": last_error,
            "last_ok_at": _finite_number(record.get("last_ok_at"))}


def _collect_hanjuku_predictions(state_dir):
    """Read-only bounded projection; never expose OAuth config or API payloads."""
    present, readable, data = _load_state_file(Path(state_dir) / "hanjuku_predictions.json")
    data = data if isinstance(data, dict) else {}
    row = data.get("round")
    row = row if isinstance(row, dict) else {}
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    modes = {"incompatible_soren", "idle", "disabled", "explore", "unconfigured", "paused", "blocked", "pending",
             "active", "settling", "resolved", "canceled", "known_result", "complete_record", "error"}
    errors = {"transport", "auth", "rate_limited", "rejected", "invalid_response", "configuration",
              "invalid_state", "unexpected", "create_unknown", "remote_missing", "remote_mismatch",
              "clock_regressed", "leaderboard"}
    def chapter(value):
        return value if type(value) is int and 0 <= value <= 12 else None
    def choice(value, allowed):
        return value if isinstance(value, str) and value in allowed else None
    entry = {"present": present, "readable": readable,
             "mode": choice(data.get("mode"), modes),
             "error": choice(data.get("error"), errors),
             "best_cleared": chapter(data.get("best_cleared")),
             "target": chapter(row.get("target")), "middle": chapter(row.get("middle")),
             "window_seconds": (row.get("window") if type(row.get("window")) is int
                                and 1 <= row["window"] <= 1800 else None),
             "status": choice(row.get("status"), {
                 "INTENT", "ACTIVE", "LOCKED", "RESOLVED", "CANCELED"}),
             "cleared": chapter(result.get("cleared")),
             "next_poll_at": _finite_number(data.get("next_poll_at"))}
    health = _collect_hanjuku_prediction_health(state_dir, errors)
    if health is not None:
        entry["health"] = health
    if present and readable and row:
        try:
            ownership = _collect_hanjuku_prediction_ownership(state_dir, data, row)
        except (OSError, ValueError, RecursionError):
            ownership = {"blocker": None, "retry_delay_seconds": None}
        if ownership.get("blocker") not in _HANJUKU_PREDICTION_BLOCKERS:
            ownership["blocker"] = None
        entry["ownership"] = ownership
    return entry


RETRO_PROGRAM_QUEUE_FILES = {
    "scheduled": "retro_corner.json",
    "manual": "retro_corner_manual.json",
}
RETRO_PROGRAM_QUEUE_STATUSES = frozenset({
    "waiting", "waiting_boundary", "waiting_turn", "running",
    "done", "expired", "cancelled", "error",
})


def _collect_retro_program_queues(soren):
    """Two fixed queue snapshots; no identifiers, paths or release authority."""
    queues = {}
    for kind, filename in RETRO_PROGRAM_QUEUE_FILES.items():
        row = {"present": None, "readable": False, "status": "unknown"}
        try:
            # Reuse the collector's descriptor-relative, all-components
            # no-follow reader and its regular-file/256 KiB bound.
            record = _read_hanjuku_scene_record(
                Path(soren) / "tmp/state/docich_program_queue" / filename
            )
        except FileNotFoundError:
            row["present"] = False
        except (ValueError, RecursionError):
            row["present"] = True
        except OSError:
            # An unsafe or inaccessible path cannot prove leaf existence.
            pass
        else:
            row.update(present=True, readable=True,
                       status=_rotation_enum(record.get("status"), RETRO_PROGRAM_QUEUE_STATUSES))
        queues[kind] = row
    return queues


def _parse_iso_utc(value):
    """Epoch seconds for a Twitch ISO-8601 UTC stamp, else None (#2805)."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = dt.datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            continue
        return parsed.replace(tzinfo=dt.timezone.utc).timestamp()
    return None


def _collect_prediction_leaderboard(state_dir, now=None):
    """Counts only; the 予想王 ledger never exposes viewer names or titles.

    The counts are chosen so one envelope can tell the three empty-ranking
    states apart without reading the ledger again (#2805):

    * ``rounds`` > 0 with ``graded_rounds`` 0 makes the hit-rate, won-points and
      lost-points boards empty by contract.
    * ``rounds`` > 0 with ``predictor_rows`` 0 means no participant row was
      recorded at all, so every board is empty for a different reason.
    * ``last_round_ended_at`` and ``last_round_age_sec`` say whether the ledger
      advanced since the measured snapshot, which separates "empty when the
      report was written" from "empty now".

    ``viewers_*`` are per-viewer aggregate counts, never identities. The
    thresholds are the production values recorded in ``min_rounds`` /
    ``min_participations``.
    """
    path = Path(state_dir) / "prediction_leaderboard.json"
    entry = {"present": False, "readable": False, "schema": None, "rounds": 0,
             "graded_rounds": 0, "viewers": 0, "predictor_rows": 0, "canceled": 0,
             "soren_canceled": 0, "viewers_won_gt0": 0,
             "viewers_used_gt0": 0, "viewers_hit_rate_eligible": 0,
             "last_round_ended_at": None, "last_round_age_sec": -1,
             "min_rounds": _PREDICTION_MIN_ROUNDS,
             "min_participations": _PREDICTION_MIN_PARTICIPATIONS}
    try:
        if path.is_symlink() or not path.is_file():
            return entry
    except OSError:
        return entry
    entry["present"] = True
    raw = _read_text_capped(path, 512 * 1024)
    try:
        data = json.loads(raw) if raw is not None else None
    except ValueError:
        data = None
    if (not isinstance(data, dict) or data.get("schema") not in (1, 2)
            or not isinstance(data.get("rounds"), dict)):
        return entry
    schema = data.get("schema")
    viewers = set()
    totals = {}
    ended = []
    canceled = soren_canceled = graded = predictor_rows = 0
    for round_ in data["rounds"].values():
        if not isinstance(round_, dict):
            continue
        status = round_.get("status")
        if status is None and schema == 1:
            # A schema-1 ledger stored graded rounds only (``_upgrade_legacy``).
            status = "RESOLVED"
        if status == "CANCELED":
            # 不成立キャンセルの参加実績（#933 §2.1）。理由・視聴者名は出さない。
            canceled += 1
            if round_.get("corner") == "soren":
                soren_canceled += 1
        elif status == "RESOLVED":
            graded += 1
        round_ended = round_.get("ended_at")
        if isinstance(round_ended, str) and round_ended:
            # ISO-8601 UTC sorts lexicographically, so the max string is the
            # newest round end; only the newest one leaves this projection.
            ended.append(round_ended[:40])
        predictors = round_.get("predictors")
        if not isinstance(predictors, list):
            continue
        for item in predictors:
            if not isinstance(item, dict) or not isinstance(item.get("user_id"), str):
                continue
            viewers.add(item["user_id"])
            predictor_rows += 1
            row = totals.setdefault(item["user_id"],
                                    {"rounds": 0, "used": 0, "won": 0})
            if status == "RESOLVED":
                row["rounds"] += 1
            used = item.get("used")
            if type(used) is int and used > 0:
                row["used"] += used
            won = item.get("won")
            if type(won) is int and won > 0:
                row["won"] += won
    last_round_ended_at = max(ended) if ended else None
    last_round_age_sec = -1
    if last_round_ended_at is not None and now is not None:
        parsed = _parse_iso_utc(last_round_ended_at)
        if parsed is not None:
            last_round_age_sec = _bounded_age(parsed, now)
    entry.update(readable=True, schema=schema,
                 rounds=min(len(data["rounds"]), 2000),
                 graded_rounds=min(graded, 2000),
                 viewers=min(len(viewers), 100000),
                 predictor_rows=min(predictor_rows, 100000),
                 canceled=min(canceled, 2000), soren_canceled=min(soren_canceled, 2000),
                 viewers_won_gt0=min(sum(1 for row in totals.values()
                                        if row["won"] > 0), 100000),
                 viewers_used_gt0=min(sum(1 for row in totals.values()
                                         if row["used"] > 0), 100000),
                 viewers_hit_rate_eligible=min(
                     sum(1 for row in totals.values()
                         if row["rounds"] >= _PREDICTION_MIN_ROUNDS), 100000),
                 last_round_ended_at=last_round_ended_at,
                 last_round_age_sec=last_round_age_sec)
    return entry


def _collect_soren_predictions(soren, state_dir):
    """Presence and bounded counts of the Soren→予想王 handoff; never its rows."""
    entry = {"handoff_present": False, "handoff_bytes": 0, "handoff_rows": 0,
             "handoff_oversized": False, "cursor_present": False}
    try:
        entry["cursor_present"] = (Path(state_dir) / "soren_predictions_cursor.json").is_file()
    except OSError:
        pass
    if soren is None:
        return entry
    path = Path(soren) / "tmp" / "state" / "prediction_resolved.jsonl"
    try:
        if path.is_symlink() or not path.is_file():
            return entry
        size = path.stat().st_size
    except OSError:
        return entry
    entry["handoff_present"] = True
    entry["handoff_bytes"] = min(size, 10 * 1024 * 1024)
    if size > 512 * 1024:
        entry["handoff_oversized"] = True
        return entry
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return entry
    entry["handoff_rows"] = min(sum(1 for line in lines if line.strip()), 1000)
    return entry


def _bounded_age(value, now):
    """Seconds since ``value`` for a plausible timestamp, else -1 (#2805)."""
    if type(value) not in (int, float) or value <= 0:
        return -1
    age = int(now - value)
    return age if 0 <= age <= 10 ** 9 else -1


def _prediction_projection(corners, now):
    """The 予想王 evidence as a top-level section the ceiling drops keep (#2805).

    ``corners`` is dropped whole against the hard ceiling, and the ledger, the
    Soren handoff, the HanJuku round state, the yoso corner run and the
    prediction boundary all live inside it. When the production envelope grew
    past the ceiling, every 予想王 report could only be answered from a run that
    still fitted, so this re-projects the same already-bounded dicts under a key
    that survives.

    This is a *duplicate* projection, not a move: the ``corners.*`` keys keep
    their existing shape and meaning so already documented consumers and the
    regression tests stay honest, while ``prediction`` is the exempt copy. Both
    read the same dict objects, so no file is read twice and the two cannot
    disagree within one envelope. ``state_dir_found`` is repeated here for the
    same reason: it is the only signal that separates "the 予想王 state dir was
    never found" from "the ledger is empty", and it used to disappear with
    ``corners``. No viewer name, login or title is added; the values are counts,
    fixed enums, bounded timestamps and ages only.
    """
    corners = corners if isinstance(corners, dict) else {}

    def _section(name):
        value = corners.get(name)
        return value if isinstance(value, dict) else {}

    leaderboard = _section("prediction_leaderboard")
    handoff = _section("soren_predictions")
    hanjuku = _section("hanjuku_predictions")
    corner = _section("yoso_corner")
    boundary = _section("boundary")
    prediction = boundary.get("prediction")
    prediction = prediction if isinstance(prediction, dict) else {}
    health = hanjuku.get("health")
    health = health if isinstance(health, dict) else {}
    health_ok = None
    if health:
        health_ok = (health.get("present") is True
                     and health.get("readable") is True
                     and health.get("consecutive_errors") == 0
                     and health.get("last_error") is None)

    def _text(value):
        return value if isinstance(value, str) else None

    return {
        "state_dir_found": corners.get("state_dir_found") is True,
        "leaderboard": dict(leaderboard),
        "soren_handoff": dict(handoff),
        "hanjuku": {
            "present": hanjuku.get("present") is True,
            "readable": hanjuku.get("readable") is True,
            "status": _text(hanjuku.get("status")),
            "mode": _text(hanjuku.get("mode")),
            "health_ok": health_ok,
            "last_ok_age_sec": _bounded_age(health.get("last_ok_at"), now),
        },
        "corner": {
            "present": corner.get("present") is True,
            "readable": corner.get("readable") is True,
            "status": _text(corner.get("status")),
            "game": _text(corner.get("game")),
            "end_reason": _text(corner.get("end_reason")),
            "recovery_required": (corner.get("recovery_required")
                                  if isinstance(corner.get("recovery_required"), bool)
                                  else None),
            "completed_at": (corner.get("completed_at")
                             if type(corner.get("completed_at")) in (int, float)
                             else None),
            "age_sec": _bounded_age(corner.get("completed_at"), now),
        },
        "boundary": {
            "present": prediction.get("present") is True,
            "readable": prediction.get("readable") is True,
            "completed_at": (prediction.get("completed_at")
                             if type(prediction.get("completed_at")) in (int, float)
                             else None),
            "age_sec": _bounded_age(prediction.get("completed_at"), now),
        },
    }


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
        "weather_corner": {"present": False, "readable": False},
        "yoso_corner": {"present": False, "readable": False},
        "doushi_corner": {"present": False, "readable": False},
        "mystery_corner": {"present": False, "readable": False},
        "lawsim_corner": {"present": False, "readable": False},
        "soren91_corner": {"present": False, "readable": False},
        "prediction_leaderboard": {"present": False, "readable": False, "rounds": 0, "viewers": 0},
        "soren_predictions": {"handoff_present": False, "handoff_bytes": 0, "handoff_rows": 0,
                              "handoff_oversized": False, "cursor_present": False},
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
        "paper_experiment": {
            "present": False, "readable": False, "age_sec": -1,
            "evaluation": {"present": False, "readable": False, "age_sec": -1},
        },
    }
    if state_dir.is_dir():
        _collect_corner_files(state_dir, payload, now)
    soren = Path(soren)
    payload["prediction_leaderboard"] = _collect_prediction_leaderboard(state_dir, now)
    payload["soren_predictions"] = _collect_soren_predictions(soren, state_dir)
    retro = payload.get("retro_corner")
    game_switch = payload.get("game_switch")
    if (isinstance(retro, dict) and retro.get("readable") is True
            and retro.get("status") == "active"
            and retro.get("game") == "hanjuku-hero"
            and isinstance(game_switch, dict)
            and game_switch.get("active_game") == "hanjuku-hero"):
        retro["narration_playback"] = _collect_hanjuku_narration_playback(soren)
        retro["scene_narration"] = _collect_hanjuku_scene_narration(state_dir)
        retro["decision_plans"] = _collect_hanjuku_decision_plans(state_dir)
    payload["hanjuku_predictions"] = _collect_hanjuku_predictions(state_dir)
    payload["boundary"] = _collect_boundary(soren / "tmp" / "state", now)
    payload["retro_program_queues"] = _collect_retro_program_queues(soren)
    payload["ab"] = _collect_ab(soren, now)
    payload["retro_scores"] = _collect_retro_scores(state_dir, now)
    payload["soren_game"] = _collect_soren_game(soren, now)
    return payload


HANJUKU_PLAYBACK_LOG_MAX_BYTES = 128 * 1024
HANJUKU_PLAYBACK_LOG_MAX_LINES = 2048
_HANJUKU_QUEUE_BASENAME_RE = re.compile(
    r"(?P<name>[^/\s]*_hanjuku(?:_commentary|:commentary)\.(?:txt|playing))(?=$|[\s),])"
)
_HANJUKU_FENCE_REJECTION_RE = re.compile(
    r"^\[say_enqueue [^\]\r\n]+\] 半熟英雄実況を破棄 \(runtime fence失効\) \| "
)
_HANJUKU_SAY_LABEL_RE = re.compile(r"(?:^|\s)label=hanjuku_commentary(?:\s|$)")
_HANJUKU_PLAYBACK_EMPTY = {
    "status": "unavailable",
    "sampled_bytes": None,
    "sampled_lines": None,
    "tail_truncated": None,
    "queue_started": None,
    "queue_completed": None,
    "queue_failed": None,
    "queue_failed_fence_rejection": None,
    "queue_failed_unclassified": None,
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
    across log rotation. Soren also rotates this file upstream, so even an
    untruncated collector sample is not a whole-run total. A fence rejection
    is classified only when the same item's fixed marker precedes failure;
    it does not distinguish expiry from identity or read failures.
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
    fence_rejected = set()
    failed_fence_rejection = 0
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
                fence_rejected.discard(identity)
            elif "再生完了:" in line:
                completed += 1
                fence_rejected.discard(identity)
                if active.get(identity, 0) > 1:
                    active[identity] -= 1
                else:
                    active.pop(identity, None)
            elif "再生失敗:" in line:
                failed += 1
                failed_fence_rejection += identity in fence_rejected
                fence_rejected.discard(identity)
                if active.get(identity, 0) > 1:
                    active[identity] -= 1
                else:
                    active.pop(identity, None)
        elif line.startswith("[say_enqueue ") and _HANJUKU_SAY_LABEL_RE.search(line):
            if _HANJUKU_FENCE_REJECTION_RE.match(line):
                fence_rejected.add(identity)
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
        "queue_failed_fence_rejection": failed_fence_rejection,
        "queue_failed_unclassified": failed - failed_fence_rejection,
        "queue_unmatched_starts": sum(active.values()),
        "external_kill_markers": kill_markers,
        "truncated_playback_suspected": truncated_suspected,
        "partial_audio_retry_suppressed": retry_suppressed,
    })
    return result


HANJUKU_SCENE_RECORD_MAX_BYTES = 256 * 1024
HANJUKU_SCENE_LOG_MAX_BYTES = 128 * 1024
HANJUKU_SCENE_LOG_MAX_LINES = 2048
HANJUKU_SCENE_IDENTITY = ("game", "runtime_id", "generation", "lease_id")
HANJUKU_SCENE_EVENTS = (
    "requested", "skipped", "generate_started", "generate_succeeded",
    "generate_failed", "deliver_enqueued", "deliver_failed",
)
HANJUKU_SCENE_REASONS = (
    "disabled", "no_facts", "duplicate", "cooldown", "in_flight", "stale",
    "scene_changed", "fence_lost", "unavailable", "invalid_output",
    "generation_failed", "timeout", "queue_giveup", "gate_giveup",
    "rate_limit", "backoff", "delivery_failed", "worker_error", "none",
)
HANJUKU_SCENE_ROLES = (
    "BATCH_COMMENTARY_AGENTS", "RADIO_AGENTS", "AI_COMMON_AGENTS",
)


def _open_hanjuku_scene_source(path):
    """Open a fixed runtime source without following any directory symlink."""
    directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        parts = Path(path).absolute().parts[1:]
        for part in parts[:-1]:
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
    finally:
        os.close(directory_fd)


def _read_hanjuku_scene_record(path):
    fd = _open_hanjuku_scene_source(path)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= HANJUKU_SCENE_RECORD_MAX_BYTES:
            raise ValueError("invalid bounded record")
        raw = os.read(fd, HANJUKU_SCENE_RECORD_MAX_BYTES + 1)
        if len(raw) > HANJUKU_SCENE_RECORD_MAX_BYTES:
            raise ValueError("record grew")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("not object")
        return data
    finally:
        os.close(fd)


def _hanjuku_scene_int(value, maximum=1_000_000_000):
    return value if type(value) is int and 0 <= value <= maximum else None


def _hanjuku_scene_latency(value):
    if type(value) not in (int, float) or not 0 <= value <= 3_600_000 or not math.isfinite(value):
        return None
    return round(value, 3)


def _hanjuku_scene_age(value, now):
    if type(value) not in (int, float) or not 0 <= value <= now or not math.isfinite(value):
        return None
    return round(now - value, 3)


def _hanjuku_scene_matches(data, active, *, schema=True):
    return (
        isinstance(data, dict)
        and (not schema or type(data.get("schema")) is int and data["schema"] == 1)
        and all(type(data.get(key)) is type(active[key]) and data.get(key) == active[key]
                for key in HANJUKU_SCENE_IDENTITY)
    )


def _project_hanjuku_scene_record(path, active, now, *, producer=False):
    if producer:
        out = dict(read_status="unavailable", enabled=None, age_sec=None,
                   request_present=None, request_seq=None, request_age_sec=None,
                   request_scope=None, request_unexpired=None, request_matches_scene=None)
    else:
        out = dict(read_status="unavailable", status=None, reason=None, role=None,
                   age_sec=None, last_request_seq=None, counters=None)
    try:
        data = _read_hanjuku_scene_record(path)
        # main's timestamp predates other diagnostics and drops fractional
        # seconds. Compare live records against a clock sampled after this read.
        now = time.time() if now is None else now
        if not _hanjuku_scene_matches(data, active):
            return out
        age = _hanjuku_scene_age(data.get("observed_at" if producer else "at"), now)
        if age is None or producer and type(data.get("enabled")) is not bool:
            return out
        out.update(read_status="available", age_sec=age)
        if producer:
            request = data.get("request")
            out.update(enabled=data["enabled"], request_present=isinstance(request, dict))
            if isinstance(request, dict):
                out["request_seq"] = _hanjuku_scene_int(request.get("seq"))
                out["request_age_sec"] = _hanjuku_scene_age(request.get("at"), now)
                scope = request.get("scope")
                out["request_scope"] = scope if scope in ("history", "scene") else None
                expiry = request.get("expires_at")
                if type(expiry) in (int, float) and math.isfinite(expiry) and expiry >= 0:
                    out["request_unexpired"] = now < expiry
                current_scene = data.get("scene_id")
                request_scene = request.get("scene_id")
                if isinstance(current_scene, str) and current_scene and isinstance(request_scene, str) and request_scene:
                    out["request_matches_scene"] = current_scene == request_scene
        else:
            out.update(
                status=data.get("status") if data.get("status") in HANJUKU_SCENE_EVENTS else None,
                reason=data.get("reason") if data.get("reason") in HANJUKU_SCENE_REASONS else None,
                role=data.get("role") if data.get("role") in HANJUKU_SCENE_ROLES else None,
                last_request_seq=_hanjuku_scene_int(data.get("last_request_seq")),
            )
            counts = data.get("counters")
            if isinstance(counts, dict):
                out["counters"] = {
                    key: _hanjuku_scene_int(counts.get(key, 0))
                    for key in (*HANJUKU_SCENE_EVENTS, *HANJUKU_SCENE_REASONS)
                    if key != "none"
                }
        return out
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return out


def _collect_hanjuku_scene_log(path, active, now):
    out = dict(status="unavailable", sampled_bytes=None, sampled_lines=None,
               tail_truncated=None, matched_records=None, rejected_records=None,
               unknown_reasons=None, event_counts=None, reason_counts=None,
               last_event=None, last_reason=None, last_age_sec=None,
               last_seq=None, last_latency_ms=None, last_char_count=None)
    fd = None
    try:
        fd = _open_hanjuku_scene_source(path)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return out
        offset = max(0, info.st_size - HANJUKU_SCENE_LOG_MAX_BYTES)
        os.lseek(fd, offset, os.SEEK_SET)
        raw = os.read(fd, HANJUKU_SCENE_LOG_MAX_BYTES + 1)
        truncated = offset > 0 or len(raw) > HANJUKU_SCENE_LOG_MAX_BYTES
        raw = raw[:HANJUKU_SCENE_LOG_MAX_BYTES]
        if offset:
            newline = raw.find(b"\n")
            raw = raw[newline + 1:] if newline >= 0 else b""
        lines = raw.decode("utf-8", errors="replace").splitlines()
        if len(lines) > HANJUKU_SCENE_LOG_MAX_LINES:
            lines = lines[-HANJUKU_SCENE_LOG_MAX_LINES:]
            truncated = True
    except (OSError, ValueError, TypeError):
        return out
    finally:
        if fd is not None:
            os.close(fd)

    now = time.time() if now is None else now
    events = dict.fromkeys(HANJUKU_SCENE_EVENTS, 0)
    reasons = dict.fromkeys(HANJUKU_SCENE_REASONS, 0)
    matched = rejected = unknown_reasons = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not _hanjuku_scene_matches(row, active):
                raise ValueError("unattributed event")
            event = row.get("event")
            age = _hanjuku_scene_age(row.get("at"), now)
            seq = _hanjuku_scene_int(row.get("seq"))
            if event not in events or age is None or seq is None and row.get("seq") is not None:
                raise ValueError("invalid event")
        except (ValueError, TypeError, OverflowError, RecursionError):
            rejected += 1
            continue
        matched += 1
        events[event] += 1
        reason = row.get("reason")
        if isinstance(reason, str) and reason in reasons:
            reasons[reason] += 1
        else:
            reason = None
            unknown_reasons += 1
        out.update(last_event=event, last_reason=reason, last_age_sec=age,
                   last_seq=seq, last_latency_ms=_hanjuku_scene_latency(row.get("latency_ms")),
                   last_char_count=_hanjuku_scene_int(row.get("char_count"), 10_000))
    out.update(sampled_bytes=len(raw), sampled_lines=len(lines), tail_truncated=truncated,
               matched_records=matched, rejected_records=rejected, unknown_reasons=unknown_reasons)
    if matched or not rejected and (not truncated or lines):
        out.update(status="partial" if rejected else "available", event_counts=events,
                   reason_counts=reasons)
    return out


def _hanjuku_active_runtime(root):
    """Resolve the current Hanjuku runtime identity from the canonical switch record.

    The identity (game/runtime_id/generation/lease_id) is used only to match
    records and to resolve one runtime directory. It is never emitted.
    """
    canonical = _read_hanjuku_scene_record(Path(root) / "game_switch.json")
    active = canonical.get("active")
    if (canonical.get("phase") != "ready" or not isinstance(active, dict)
            or active.get("game") != "hanjuku-hero"
            or _hanjuku_scene_int(active.get("generation")) is None
            or not isinstance(active.get("runtime_id"), str)
            or not re.fullmatch(r"g[0-9]+-[a-f0-9]{8}", active["runtime_id"])
            or not isinstance(active.get("lease_id"), str) or not active["lease_id"]):
        return None, None
    return active, Path(root) / "runtimes" / active["runtime_id"]


def _collect_hanjuku_scene_narration(state_dir, now=None):
    """Current-runtime scene progress, never facts, text, hashes or identity tokens."""
    empty = dict(status="unavailable", generation=None, producer=None, worker=None, log=None)
    try:
        if now is not None and (type(now) not in (int, float) or not math.isfinite(now) or now < 0):
            return empty
        root = Path(state_dir)
        active, runtime = _hanjuku_active_runtime(root)
        if active is None:
            return empty
        run = _read_hanjuku_scene_record(runtime / "hanjuku_run.json")
        if (not _hanjuku_scene_matches(run, active, schema=False)
                or run.get("playing") is not True
                or run.get("terminal_reason") or run.get("terminal_candidate")):
            return empty
        result = dict(
            status="available", generation=active["generation"],
            producer=_project_hanjuku_scene_record(runtime / "hanjuku_scene.json", active, now, producer=True),
            worker=_project_hanjuku_scene_record(runtime / "hanjuku_scene_worker.json", active, now),
            log=_collect_hanjuku_scene_log(runtime / "hanjuku_scene_commentary.jsonl", active, now),
        )
        again = _read_hanjuku_scene_record(root / "game_switch.json")
        if again.get("phase") != "ready" or again.get("active") != active:
            return {**empty, "status": "identity_changed"}
        return result
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return empty


HANJUKU_DECISION_LOG_MAX_BYTES = 128 * 1024
HANJUKU_DECISION_LOG_MAX_LINES = 1024
# Bounded window of the newest planned-action records the aggregate covers.
HANJUKU_DECISION_WINDOW_PLANS = 128
HANJUKU_DECISION_MAX_ACTIONS = 4
HANJUKU_DECISION_MAX_BUTTONS = 4
HANJUKU_DECISION_TOP_KINDS = 6
HANJUKU_DECISION_MAX_PERIOD = 4
HANJUKU_DECISION_EVENTS = ("action_plan", "decision")
# Owned by hanjuku_screen.classify_text plus the literal screen.kind overrides
# in hanjuku_screen.parse and the layered docich.hanjuku package.
# tests/test_hanjuku_decision_diagnostics.py re-derives that vocabulary from
# source, so a new kind fails closed as "other" instead of being emitted.
HANJUKU_DECISION_SCREEN_KINDS = frozenset({
    "attack_started", "barrier_removed", "battle", "battle_menu",
    "battle_menu_pending", "boss_attack_started", "field_encounter_started", "card_select", "castle_info",
    "castle_menu", "defense_started", "discharge_menu", "egg_battle_menu",
    "egg_choice_menu", "general_list", "gift_request", "main_menu", "map",
    "map_target", "monster_menu", "month_menu", "name_entry", "okunote_menu",
    "sealed_castle", "shop_exit_confirm", "shop_list", "shop_quantity",
    "shop_quantity_prompt", "sortie_confirm", "summer_bonus",
    "summer_bonus_message", "text", "unknown", "world_map", "yes_no",
})
# Pad buttons the reviewed policy can emit; anything else becomes "other".
HANJUKU_DECISION_BUTTONS = frozenset({
    "a", "b", "x", "y", "l", "r", "up", "down", "left", "right", "start",
    "select",
})


def _hanjuku_decision_buttons(value):
    """Normalize planned actions to allowlisted button names.

    An empty list is a real plan that sends nothing; None means the shape was
    not a bounded list of pad actions and must not be reported as an operation.
    """
    if not isinstance(value, list) or len(value) > HANJUKU_DECISION_MAX_ACTIONS:
        return None
    out = []
    for action in value:
        if not isinstance(action, dict) or action.get("type") != "pad":
            return None
        names = action.get("buttons")
        if not isinstance(names, list) or not names or len(names) > HANJUKU_DECISION_MAX_BUTTONS:
            return None
        for name in names:
            if not isinstance(name, str):
                return None
            out.append(name if name in HANJUKU_DECISION_BUTTONS else "other")
    return out


def _hanjuku_decision_signature(kind, buttons):
    return {"screen_kind": kind, "buttons": buttons}


def _hanjuku_decision_tail(window):
    """Trailing whole repetitions of the newest short signature unit.

    A blind confirm/cancel fallback alternates two different plans, so a plain
    "same plan N times" counter would miss it. Period 1 covers one plan pressed
    over and over, period 2-4 the alternating cycles, and period null means the
    runtime is not repeating at the tail of the sampled window.
    """
    best = None
    for period in range(1, HANJUKU_DECISION_MAX_PERIOD + 1):
        if len(window) < 2 * period:
            continue
        unit = window[-period:]
        repeats = 0
        while (len(window) >= (repeats + 1) * period
               and window[-(repeats + 1) * period:len(window) - repeats * period] == unit):
            repeats += 1
        if repeats < 2:
            continue
        covered = period * repeats
        if best is None or covered > best[0] * best[1]:
            best = (period, repeats, unit)
    if best is None:
        kind, buttons = window[-1]
        return {"period": None, "repeats": 1,
                "signatures": [_hanjuku_decision_signature(
                    kind, None if buttons is None else list(buttons))]}
    period, repeats, unit = best
    return {"period": period, "repeats": repeats,
            "signatures": [_hanjuku_decision_signature(
                kind, None if buttons is None else list(buttons))
                for kind, buttons in unit]}


def _collect_hanjuku_decision_plans(state_dir, now=None):
    """Bounded repeat evidence for the current runtime's planned pad actions.

    A corner that keeps answering with the same input is invisible in the
    chart counters (they only move on purchases, battles and months) and never
    trips the stall watchdog while the cursor keeps blinking. This projects the
    repeated operation itself: the fixed screen-kind vocabulary, allowlisted
    button names, counts, ages and the trailing repeating signature unit.

    Decision reasons, chart steps, general/castle names, frame or decision
    hashes, runtime identifiers, planned hold times and log bodies are never
    read out or emitted.
    """
    empty = dict(status="unavailable", sampled_bytes=None, sampled_lines=None,
                 tail_truncated=None, matched_records=None, rejected_records=None,
                 plan_records=None, window_plans=None, distinct_signatures=None,
                 distinct_screen_kinds=None, screen_kind_counts=None,
                 button_counts=None, top_signature=None, trailing_repeat=None,
                 last_plan_age_sec=None)
    try:
        if now is not None and (type(now) not in (int, float) or not math.isfinite(now) or now < 0):
            return empty
        root = Path(state_dir)
        active, runtime = _hanjuku_active_runtime(root)
        if active is None:
            return empty
        run = _read_hanjuku_scene_record(runtime / "hanjuku_run.json")
        if (not _hanjuku_scene_matches(run, active, schema=False)
                or run.get("playing") is not True
                or run.get("terminal_reason") or run.get("terminal_candidate")):
            return empty
        fd = _open_hanjuku_scene_source(runtime / "hanjuku_decisions.jsonl")
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return empty
            offset = max(0, info.st_size - HANJUKU_DECISION_LOG_MAX_BYTES)
            os.lseek(fd, offset, os.SEEK_SET)
            raw = os.read(fd, HANJUKU_DECISION_LOG_MAX_BYTES + 1)
            truncated = offset > 0 or len(raw) > HANJUKU_DECISION_LOG_MAX_BYTES
            raw = raw[:HANJUKU_DECISION_LOG_MAX_BYTES]
            if offset:
                newline = raw.find(b"\n")
                raw = raw[newline + 1:] if newline >= 0 else b""
            lines = raw.decode("utf-8", errors="replace").splitlines()
            if len(lines) > HANJUKU_DECISION_LOG_MAX_LINES:
                lines = lines[-HANJUKU_DECISION_LOG_MAX_LINES:]
                truncated = True
        finally:
            os.close(fd)
        sampled_bytes, sampled_lines = len(raw), len(lines)
        if not lines:
            return {**empty, "sampled_bytes": sampled_bytes,
                    "sampled_lines": sampled_lines, "tail_truncated": truncated}
        # Compare live records against a clock sampled after this read: main's
        # timestamp predates the other collectors and drops fractional seconds.
        clock = time.time() if now is None else now
        matched = rejected = 0
        signatures = []
        last_plan_age = None
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not _hanjuku_scene_matches(row, active):
                    raise ValueError("unattributed event")
                event = row.get("event")
                if event not in HANJUKU_DECISION_EVENTS:
                    raise ValueError("invalid event")
                age = _hanjuku_scene_age(row.get("at"), clock)
                if age is None:
                    raise ValueError("invalid event age")
            except (ValueError, TypeError, OverflowError, RecursionError):
                rejected += 1
                continue
            matched += 1
            if event != "action_plan":
                continue
            kind = row.get("screen_kind")
            if not isinstance(kind, str) or kind not in HANJUKU_DECISION_SCREEN_KINDS:
                kind = "other"
            buttons = _hanjuku_decision_buttons(row.get("planned_actions"))
            last_plan_age = age if last_plan_age is None else min(last_plan_age, age)
            signatures.append((kind, None if buttons is None else tuple(buttons)))
        out = dict(status="unavailable", sampled_bytes=sampled_bytes,
                   sampled_lines=sampled_lines, tail_truncated=truncated,
                   matched_records=matched, rejected_records=rejected,
                   plan_records=len(signatures), window_plans=None,
                   distinct_signatures=None, distinct_screen_kinds=None,
                   screen_kind_counts=None, button_counts=None,
                   top_signature=None, trailing_repeat=None,
                   last_plan_age_sec=last_plan_age)
        if not signatures:
            return out if rejected else {**out, "status": "available"}
        window = signatures[-HANJUKU_DECISION_WINDOW_PLANS:]
        kinds, buttons_seen, repeats = {}, {}, {}
        for signature in window:
            kind, buttons = signature
            kinds[kind] = kinds.get(kind, 0) + 1
            for name in (buttons or ()):
                buttons_seen[name] = buttons_seen.get(name, 0) + 1
            repeats[signature] = repeats.get(signature, 0) + 1
        # Keep unknown buttons distinct from empty plans without comparing None to tuples.
        ranked = sorted(repeats.items(), key=lambda item: (
            -item[1], item[0][0], item[0][1] is None, item[0][1] or ()))
        top_kind, top_buttons = ranked[0][0]
        out.update(
            status="partial" if rejected else "available",
            window_plans=len(window), distinct_signatures=len(repeats),
            distinct_screen_kinds=len(kinds),
            screen_kind_counts=dict(sorted(
                sorted(kinds.items(), key=lambda item: (-item[1], item[0]))
                [:HANJUKU_DECISION_TOP_KINDS])),
            button_counts={name: buttons_seen.get(name, 0)
                           for name in sorted(HANJUKU_DECISION_BUTTONS | {"other"})},
            top_signature={**_hanjuku_decision_signature(
                top_kind, None if top_buttons is None else list(top_buttons)),
                "count": ranked[0][1]},
            trailing_repeat=_hanjuku_decision_tail(window),
        )
        again = _read_hanjuku_scene_record(root / "game_switch.json")
        if again.get("phase") != "ready" or again.get("active") != active:
            return {**empty, "status": "identity_changed"}
        return out
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return empty


def _collect_soren_game(soren, now):
    """Fixed, read-only progress evidence for the active Soren match."""
    soren = Path(soren)
    path = soren / "game_state.json"
    state = _read_json(path)
    runner = _read_json(soren / "tmp/state/main_strategy_runner_active.json")
    result = {"present": path.is_file(), "readable": isinstance(state, dict),
              "state": "unknown", "age_sec": -1, "founding_seen": None,
              "founding_marker_current": None, "founding_marker_stale": None,
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
    # #2345: the raw marker only says that *some* match founded. Operators need
    # to know whether it still belongs to this board; a stale marker used to
    # read as founding evidence and hid a real GAME OVER for ~15h40m.
    if _FOUNDING_MARKER_IS_CURRENT is not None:
        result["founding_marker_current"] = _FOUNDING_MARKER_IS_CURRENT(soren, path)
        result["founding_marker_stale"] = bool(
            result["founding_seen"] and not result["founding_marker_current"])
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


def _systemctl_system(args, timeout=5):
    """Run `systemctl <args>` against the *system* manager (no `--user`).
    Returns (returncode, stdout) or None when the command cannot run at all
    (missing binary, timeout). A non-zero returncode with output (e.g.
    "inactive") is still a valid answer, not an error."""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }
    try:
        proc = subprocess.run(
            ["systemctl", *args],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.returncode, (proc.stdout or "").strip()


def _systemctl_scope(scope, args, timeout=5):
    """Read-only `systemctl` query against the ``user`` or ``system`` manager."""
    if scope == "system":
        return _systemctl_system(args, timeout=timeout)
    return _systemctl_user(args, timeout=timeout)


def _unit_is_active(unit, scope="user"):
    result = _systemctl_scope(scope, ["is-active", unit])
    if result is None:
        return None
    _, out = result
    if out in ("active", "activating", "reloading"):
        return True
    if out in ("inactive", "failed", "deactivating", "unknown"):
        return False
    return None


def _unit_is_enabled(unit, scope="user"):
    result = _systemctl_scope(scope, ["is-enabled", unit])
    if result is None:
        return None
    _, out = result
    if out in ("enabled", "enabled-runtime", "static", "alias", "linked", "linked-runtime"):
        return True
    if out in ("disabled", "masked", "masked-runtime", "not-found", "bad"):
        return False
    return None


# --- resolver improve daemon observation (read-only) ----------------------------
#
# #1286 follow-up: the resolver improve daemons (python -m docich.resolver.improve
# --daemon) are long-lived. If one is installed and running, it keeps using the
# production default tmux server until the next restart — the exact path #1284
# fixed for the corner. This section answers "is one running?" directly.

RESOLVER_IMPROVE_UNITS = (
    "docich-resolver-improve.service",
    "docich-resolver-improve-gnurobots.service",
)
# 2026-10-08 VM observation: both units are installed in the *system* manager
# (/etc/systemd/system, root-owned, WantedBy=default.target). A user-scope
# probe therefore answers "not-found" while the daemon is actually running —
# on 2026-10-01 `docich-resolver-improve.service` had been up since 06:18 JST
# and the user-scope-only projection reported it absent (#1286). Read both
# scopes; a running unit in either manager is a running daemon.
RESOLVER_IMPROVE_SCOPES = ("system", "user")


def _resolver_unit_scopes(unit):
    return {
        scope: {
            "active": _unit_is_active(unit, scope=scope),
            "enabled": _unit_is_enabled(unit, scope=scope),
        }
        for scope in RESOLVER_IMPROVE_SCOPES
    }


def _aggregate_scope_flag(values):
    """True when any scope says True, False when any says False, else None.

    Unknown readings never mask a known one: a unit the system manager runs is
    reported active even when the user-scope probe cannot answer.
    """
    if any(value is True for value in values):
        return True
    if any(value is False for value in values):
        return False
    return None


def _collect_resolver_daemon():
    """Active/enabled state of the resolver improve daemons (#1286 follow-up).

    ``active`` / ``enabled`` aggregate the system and user managers; ``scopes``
    keeps the per-manager readings so a partial read is never flattened into
    "absent".
    """
    units = {}
    for unit in RESOLVER_IMPROVE_UNITS:
        scopes = _resolver_unit_scopes(unit)
        units[unit] = {
            'active': _aggregate_scope_flag(
                [scopes[scope]['active'] for scope in RESOLVER_IMPROVE_SCOPES]),
            'enabled': _aggregate_scope_flag(
                [scopes[scope]['enabled'] for scope in RESOLVER_IMPROVE_SCOPES]),
            'scopes': scopes,
        }
    return {'schema_version': 2, 'units': units}


CC_ADAPTER_DEFAULT_PORT = 4100


def _probe_cc_adapter(port, timeout=1.0):
    """Read-only loopback GET of the CC adapter ``/healthz`` (docich#2894).

    Self-contained on purpose: the gateway copies only a fixed file set into
    its collector checkout, so this must not import ``docich.cc_translation_adapter``.
    Returns ``{"state": "ok", "upstream": ...}`` or a state in
    {"unreachable", "invalid"}; the body is reduced to the upstream name.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as response:
            body = json.loads(response.read(4096).decode("utf-8"))
    except Exception:
        return {"state": "unreachable"}
    if not isinstance(body, dict) or body.get("status") != "ok" or not isinstance(
        body.get("upstream"), str
    ):
        return {"state": "invalid"}
    return {"state": "ok", "upstream": body["upstream"]}


def _collect_cc_translation_adapter():
    """Read-only healthz of the CC translation adapter (docich#2894).

    Reports the probe state and the upstream name the adapter advertises only;
    no prompt, key, or response body is read into the envelope.
    """
    port = CC_ADAPTER_DEFAULT_PORT
    raw_port = os.environ.get("DOCICH_CC_ADAPTER_PORT", "")
    if raw_port.isdigit() and 1 <= int(raw_port) <= 65535:
        port = int(raw_port)
    health = _probe_cc_adapter(port)
    return {"port": port, "state": health["state"], "upstream": health.get("upstream")}


GAME_SWITCH_WATCHDOG_UNIT = "docich-game-switch-fifo.timer"


def _collect_game_switch_watchdog():
    """Active/enabled state of the game-switch FIFO watchdog timer (#1041).

    The watchdog (docich-game-switch-fifo.timer / maintain-fifo) is the
    recovery path for expired draining. If it is not running, an expired
    draining request stays stuck forever.
    """
    result = {
        'timer_active': _unit_is_active(GAME_SWITCH_WATCHDOG_UNIT),
        'timer_enabled': _unit_is_enabled(GAME_SWITCH_WATCHDOG_UNIT),
    }
    # Latest service result from the timer's unit
    show = _systemctl_user(["show", GAME_SWITCH_WATCHDOG_UNIT, "--property", "ExecMainStatus,ExecMainCode,Result,ActiveEnterTimestamp"])
    if show is not None:
        _, out = show
        props = {}
        for line in out.splitlines():
            if '=' in line:
                key, _, value = line.partition('=')
                props[key.strip()] = value.strip()
        result['last_exit_code'] = int(props['ExecMainCode']) if props.get('ExecMainCode', '').lstrip('-').isdigit() else None
        result['last_result'] = props.get('Result') or None
        result['last_active_at'] = props.get('ActiveEnterTimestamp') or None
    return result


CORNER_ROTATION_LEGACY_SERVICE = "docich-retro-corner.service"
CORNER_ROTATION_LEGACY_TIMER = "docich-retro-corner.timer"
CORNER_ROTATION_CANONICAL_SERVICE = "docich-corner-rotation.service"
CORNER_ROTATION_CANONICAL_TIMER = "docich-corner-rotation.timer"


def _collect_corner_rotation_timer_alias():
    """Fixed projection of corner rotation timer alias state (#1092).

    The deploy hook (ensure_corner_rotation_timer.sh) fails with exit 23
    when the legacy alias pair is inconsistent (one side only, or wrong
    target). This projection makes the alias state directly observable in
    diagnostics without publishing paths or unit file contents.
    """
    # The gateway deliberately sets HOME=/tmp. Use the same reviewed user
    # unit root as _rotation_timer_selection(), rather than that scrubbed HOME.
    unit_dir = PROD_ROOT.parent / ".config" / "systemd" / "user"
    legacy_service = unit_dir / CORNER_ROTATION_LEGACY_SERVICE
    legacy_timer = unit_dir / CORNER_ROTATION_LEGACY_TIMER
    service_is_link = legacy_service.is_symlink()
    timer_is_link = legacy_timer.is_symlink()
    service_target = legacy_service.resolve().name if service_is_link else None
    timer_target = legacy_timer.resolve().name if timer_is_link else None
    pair_valid = (service_is_link and timer_is_link
                  and service_target == CORNER_ROTATION_CANONICAL_SERVICE
                  and timer_target == CORNER_ROTATION_CANONICAL_TIMER)
    return {
        'schema_version': 1,
        'legacy_service_alias': service_is_link,
        'legacy_timer_alias': timer_is_link,
        'legacy_service_target': service_target,
        'legacy_timer_target': timer_target,
        'legacy_alias_pair_valid': pair_valid,
        'canonical_service_present': (unit_dir / CORNER_ROTATION_CANONICAL_SERVICE).exists(),
        'canonical_timer_present': (unit_dir / CORNER_ROTATION_CANONICAL_TIMER).exists(),
    }


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
    """Digest of the deployed resource HTML (legacy inline source if absent)."""
    resources = PROD_ROOT / "src" / "docich" / "webui_resources"
    if resources.exists():
        try:
            with (resources / "index.html").open("rb") as stream:
                html = stream.read(WEBUI_MAX_HTML_BYTES + 1)
            with (resources / "manifest.json").open("rb") as stream:
                manifest_bytes = stream.read(4097)
            if len(html) > WEBUI_MAX_HTML_BYTES or len(manifest_bytes) > 4096:
                return None
            manifest = json.loads(manifest_bytes)
            digest = hashlib.sha256(html)
            if manifest.get("schema") != 1 or manifest.get("files", {}).get("index.html") != digest.hexdigest():
                return None
            return digest.digest()
        except (OSError, ValueError, TypeError, AttributeError):
            return None
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


def _severity(workers, queues, ai, improvement, corners=None, hanjuku_resume=None):
    if workers["required_down"] or workers["required_stale"]:
        return "critical"
    # A detached resume claim is an actionable operational condition: raise it
    # through the runtime health alert so the owner is notified within hours
    # (#2870). Normal claimed/queued/verified transitions must not trigger.
    if isinstance(hanjuku_resume, dict) and hanjuku_resume.get("status") == "detached_claim":
        return "warn"
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
    # An adopted strategy that never reached the reviewed lineage (docich#657,
    # #2489) is an actionable regression even while the loop itself keeps
    # running. A later successful/no-change persist overwrites the status and
    # clears the durable failure record.
    persist = improvement.get("persist")
    clone_disappeared = (
        isinstance(persist, dict)
        and persist.get("clone_present") is False
        and (
            bool(improvement.get("running"))
            or persist.get("last_success_at") is not None
        )
    )
    # clone_missing is only actionable while the improve loop is running; a
    # projection deployment without soren-persist is normal when it is idle.
    failure = persist.get("last_failure") if isinstance(persist, dict) else None
    failure_actionable = isinstance(failure, dict) and (
        failure.get("reason") != "clone_missing" or bool(improvement.get("running"))
    )
    if isinstance(persist, dict) and (
        persist.get("result") == "fail"
        or persist.get("stale")
        or (persist.get("present") and not persist.get("readable"))
        or failure_actionable
        or clone_disappeared
    ):
        return "warn"
    return "ok"


# Fixed v1 Soren91 sent-to-sent metrics, never game acceptance or raw records.
DROP_STAGES = ('capture', 'analyze', 'ranking', 'decide', 'input', 'holdInput',
               'cooldown', 'poll', 'overlap', 'unattributed')
DROP_CALLS = DROP_STAGES[:-2]
# Top-level loop stages as written by the v1 `flush()` (no holdInput / overlap /
# unattributed). Used for the open-attempt projection.
DROP_LOOP_STAGES = ('capture', 'analyze', 'ranking', 'decide', 'input',
                    'cooldown', 'poll')
DROP_REASONS = ('unknown-current', 'uncalibrated', 'invalid-board', 'confirm-frame',
                'preview-changed', 'board-moving', 'stable', 'stable-slow-advance',
                'non-move', 'other')
DROP_OUTCOMES = ('observe', 'hold-sent', 'drop-sent', 'error')
# Mirrors SAFE_FAILURE_CLASSES in soviet_now `soren91/loop_metrics.mjs`. A class
# added upstream is reported as the fixed label `unknown`, never as raw text.
DROP_FAILURE_CLASSES = (
    'capture-budget-exhausted', 'capture-session-busy', 'capture-timeout',
    'capture-invalid-geometry', 'capture-invalid-png', 'capture-invalid-jpeg',
    'capture-invalid-image', 'capture-pixel-scale-mismatch',
    'capture-geometry-changed', 'input-stale-observation',
    'input-calibration-mismatch', 'input-geometry-changed', 'other',
)
DROP_INTERVAL_KEYS = ('samples', 'last', 'p50', 'p95', 'max')
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


def _drop_current_summary(data):
    """Bounded projection of the currently open loop attempt in the metrics file.

    The legacy top-level counters are rewritten while the bot loop is alive,
    including while it never reaches a drop. They are the only read-only
    evidence of what the loop is doing now: a plot that sends no drop has no
    completed ``dropProfile.records`` at all, so ``all`` stays empty. Only
    fixed enums and finite numbers are published; every unexpected or
    oversized value degrades to ``null`` / ``unknown`` and raw text, paths,
    error messages and free-form reasons are never emitted.
    """
    outcome = data.get('outcome')
    if outcome not in DROP_OUTCOMES:
        outcome = 'unknown'
    counters = {}
    for key in ('observations', 'holds'):
        value = data.get(key)
        counters[key] = value if _drop_number(value, True) else None
    raw_stages = data.get('stageMs')
    stage_ms = {}
    for stage in DROP_LOOP_STAGES:
        value = raw_stages.get(stage) if isinstance(raw_stages, dict) else None
        stage_ms[stage] = value if _drop_number(value) else None
    raw_reasons = data.get('reasonCounts')
    reason_counts = {}
    for reason in DROP_REASONS:
        value = raw_reasons.get(reason) if isinstance(raw_reasons, dict) else None
        reason_counts[reason] = value if _drop_number(value, True) else None
    last_failure = None
    raw_failure = data.get('lastFailure')
    if isinstance(raw_failure, dict):
        stage = raw_failure.get('stage')
        error_class = raw_failure.get('errorClass')
        last_failure = {
            'stage': stage if stage in DROP_LOOP_STAGES else 'unknown',
            'errorClass': error_class if error_class in DROP_FAILURE_CLASSES else 'unknown',
        }
    raw_intervals = data.get('dropSentIntervalMs')
    intervals = {}
    for key in DROP_INTERVAL_KEYS:
        value = raw_intervals.get(key) if isinstance(raw_intervals, dict) else None
        intervals[key] = value if _drop_number(value, key == 'samples') else None
    return {
        'outcome': outcome,
        'observations': counters['observations'],
        'holds': counters['holds'],
        'stageMs': stage_ms,
        'reasonCounts': reason_counts,
        'lastFailure': last_failure,
        'dropSentIntervals': intervals,
    }


def _drop_profile_summary(data):
    p = data.get('dropProfile')
    current = _drop_current_summary(data)
    # The open attempt stays observable even when the completed-interval
    # schema is unknown/unsupported: it is the only view of a stalled loop.
    invalid = {'profileStatus': 'invalid', 'current': current}
    if p is None:
        return {'profileStatus': 'missing', 'current': current}
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
        return dict(invalid)
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
            return dict(invalid)
        for field, keys, integer in (('stageMs', DROP_STAGES, False),
                                     ('phaseCalls', DROP_CALLS, True),
                                     ('reasonCounts', DROP_REASONS, True)):
            if (not isinstance(r.get(field), dict)
                    or not all(_drop_number(r[field].get(k), integer) for k in keys)):
                return dict(invalid)
        error = r['durationMs'] - sum(r['stageMs'][k] for k in DROP_STAGES)
        if abs(error - r['accountingErrorMs']) > .11:
            return dict(invalid)
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
        'current': current,
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


NETHACK_MANUAL_LOG_DIR = "logs"
NETHACK_MANUAL_LOG_ENTRIES = 256
NETHACK_MANUAL_LOG_TAIL_BYTES = 8192
NETHACK_MANUAL_LOG_LINES = 8
NETHACK_MANUAL_LOG_LINE_LIMIT = 300
NETHACK_MANUAL_LOG_MAX_BYTES = 8 * 1024 * 1024
# ``nethack_corner_operator.launch`` names its private log
# ``nethack-manual-<epoch>-<uuid4 hex8>.log``. Only that exact shape is read;
# any other file in ``logs/`` is ignored.
_NETHACK_MANUAL_LOG_RE = re.compile(r"^nethack-manual-[0-9]{1,12}-[0-9a-f]{8}\.log$")


def _collect_nethack_manual_log(state_dir, now):
    """Bounded, redacted tail of the newest bounded-manual runner log (#2666).

    ``nethack-corner-operator --start`` spawns ``docich-nethack-corner-manual``
    detached and treats an exit inside the one-second startup grace as a
    generic failure (operator exit code 2). The runner's own stderr is the only
    evidence for that failure, and the gateway withholds production stdout, so
    this projection is the read-only way to see why a manual run never started.

    Only the newest file matching the fixed name shape directly under
    ``logs/`` is read: symlinks and non-regular files are refused with
    ``O_NOFOLLOW`` + ``fstat``, and only the tail bytes are decoded.
    """
    entry = {
        "schema_version": 1,
        "collected_at": now,
        "present": False,
        "readable": False,
        "mtime": None,
        "bytes": None,
        "lines": [],
        "tail_truncated": None,
    }
    directory = Path(state_dir) / NETHACK_MANUAL_LOG_DIR
    best = None
    scanned = 0
    try:
        with os.scandir(directory) as entries:
            for item in entries:
                if scanned >= NETHACK_MANUAL_LOG_ENTRIES:
                    break
                scanned += 1
                if _NETHACK_MANUAL_LOG_RE.fullmatch(item.name) is None:
                    continue
                try:
                    info = item.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                if info.st_size > NETHACK_MANUAL_LOG_MAX_BYTES:
                    continue
                if best is None or info.st_mtime > best[0]:
                    best = (info.st_mtime, item.name, info.st_size)
    except OSError:
        return entry
    if best is None:
        return entry
    entry["present"] = True
    entry["mtime"] = int(best[0])
    entry["bytes"] = int(best[2])
    path = directory / best[1]
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                return entry
            size = int(info.st_size)
            truncated = size > NETHACK_MANUAL_LOG_TAIL_BYTES
            if truncated:
                handle.seek(size - NETHACK_MANUAL_LOG_TAIL_BYTES)
            raw = handle.read(NETHACK_MANUAL_LOG_TAIL_BYTES)
    except OSError:
        return entry
    entry["readable"] = True
    entry["tail_truncated"] = truncated
    lines = [line for line in raw.decode("utf-8", errors="replace").splitlines() if line.strip()]
    entry["lines"] = [
        _redact_text(line, NETHACK_MANUAL_LOG_LINE_LIMIT)
        for line in lines[-NETHACK_MANUAL_LOG_LINES:]
    ]
    return entry


NETHACK_PANE_LINES = 14
# NetHack draws its message row -- and therefore ``--More--`` -- on the TOP
# screen row, which the tail-only capture structurally misses (#2271). The
# head rows are captured separately and bounded to the message row plus the
# row the marker is pushed onto: ``more()`` moves it there once the cursor
# reaches ``CO - 8``. Two redacted lines add under a kilobyte.
NETHACK_PANE_HEAD_LINES = 2
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
        "dead_disclosure",
        "capture_failed",
        "unknown",
    }
)
_BOUNDARY_DIAG_OUTCOMES = frozenset({"suspended", "ended", "unknown"})


def _capture_tmux_pane_head(target, *, head_lines=NETHACK_PANE_HEAD_LINES):
    """Read the first visible rows of one pane read-only; never sends input.

    The NetHack message/pager row is the top screen row, so a tail-only
    capture cannot distinguish "a ``--More--`` the brain failed to answer"
    from "a decision loop returning no action" (#2271). Return the leading
    ``head_lines`` rows as-is: blank rows are preserved as empty strings so
    row 0 stays row 0, which makes "no message row displayed" readable.
    """
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
    return [
        _redact_text(line, NETHACK_PANE_LINE_LIMIT)
        for line in proc.stdout.splitlines()[:head_lines]
    ]


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


HANJUKU_TACTICAL_CASTLES = ('アルマムーン', 'キカンドン', 'ナキューメラ', 'ジョンリギ',
           'ゴーメン', 'スペンソニア', 'カストーラ', 'けっかい')
HANJUKU_TACTICAL_KEYS = ('game', 'runtime_id', 'generation', 'lease_id')
HANJUKU_TACTICAL_LIMIT = 256 * 1024

# `tmux -L` server names. The production corner (game, agent, obs, bridge
# sessions) runs on tmux's *default* server — the socket tmux names "default"
# when no -L is given; `docich` is a *session* name inside it, not a server
# name. Evaluation jobs must run on the private `docich-eval` server
# (`docich.tmux.EVAL_SERVER`). Reading `-L default` therefore reads exactly the
# production server; the earlier `-L docich` target never existed on the VM and
# reported the production server as absent (#1286).
TMUX_SERVER_NAMES = ('default', 'docich-eval')
TMUX_SERVER_OUTPUT_LIMIT = 4096


def _tmux_server_session_count(server):
    """Read a count without requesting session names; None means unknown."""
    if server not in TMUX_SERVER_NAMES:
        return None
    try:
        proc = subprocess.run(
            ["tmux", "-L", server, "list-sessions", "-F", "1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    if proc.returncode != 0:
        return None
    output = proc.stdout
    if not isinstance(output, str) or len(output) > TMUX_SERVER_OUTPUT_LIMIT:
        return None
    if any(char not in '1\n' for char in output):
        return None
    markers = output.splitlines()
    if any(marker != '1' for marker in markers):
        return None
    return len(markers)


def _collect_tmux_servers():
    """Fixed projection of tmux server ownership (#1286 follow-up).

    The production corner runs on tmux's default server (session ``docich``);
    evaluation jobs must run on ``docich-eval``. This projection makes the
    separation directly observable in diagnostics instead of inferring it from
    process trees. Read-only: ``list-sessions`` never sends input.
    """
    servers = {}
    for name in TMUX_SERVER_NAMES:
        count = _tmux_server_session_count(name)
        servers[name] = {
            'readable': count is not None,
            'present': bool(count) if count is not None else None,
            'session_count': count,
        }
    return {'schema_version': 2, 'servers': servers}


PULSE_SINK_INPUTS_TIMEOUT = 5
# Bounded so the projection cannot crowd the 36 KiB diagnostics envelope; the
# production session has far fewer streams than this, and the omitted remainder
# is reported rather than silently dropped.
# Bounded detail list; the attribution summary carries the ownership signal
# when this cap (or the output budget) drops the per-stream rows.
PULSE_SINK_INPUTS_MAX = 8
PULSE_SINK_INPUTS_OUTPUT_MAX = 256 * 1024
# ``application.name`` is an externally supplied free-form string, so it is
# never emitted verbatim. These fixed categories still separate the bridge's
# BGM player from a speech worker, which is what the #968 report needs.
PULSE_PLAYER_CATEGORIES = (
    ('ffplay', 'bridge-ffplay'),
    ('retroarch', 'retroarch'),
    ('chrom', 'browser'),
    ('firefox', 'browser'),
    ('speech', 'speech-worker'),
    ('voicevox', 'speech-worker'),
    ('tts', 'speech-worker'),
    ('parec', 'monitor-capture'),
    ('monitor', 'monitor-capture'),
    ('ffmpeg', 'stream-capture'),
    ('stream', 'stream-capture'),
)


def _pulse_player_category(name):
    """Coarse fixed category for a PulseAudio application name."""
    if not isinstance(name, str):
        return None
    lowered = name.lower()
    for needle, category in PULSE_PLAYER_CATEGORIES:
        if needle in lowered:
            return category
    return 'other'


def _pulse_server_argv():
    """Point ``pactl`` at the session's own PulseAudio socket.

    The gateway runs this collector with a scrubbed environment and no
    ``XDG_RUNTIME_DIR``, so ``pactl`` cannot find the server on its own. The
    per-user runtime directory is the only derived path used here; if it is not
    a socket, ``pactl`` falls back to its compiled-in default and the
    projection reports the failure instead of guessing.
    """
    runtime_dir = Path('/run/user') / str(os.getuid())
    socket_path = runtime_dir / 'pulse' / 'native'
    try:
        if stat.S_ISSOCK(socket_path.stat().st_mode):
            return ['--server=unix:' + str(socket_path)]
    except OSError:
        pass
    return []


def _pulse_listing(*args):
    """Return one bounded pactl listing, or a fixed failure reason."""
    try:
        proc = subprocess.run(
            ['pactl', *_pulse_server_argv(), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=PULSE_SINK_INPUTS_TIMEOUT,
            env={**os.environ, 'LC_ALL': 'C'},
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, 'pactl_unavailable'
    if proc.returncode != 0:
        return None, 'pactl_failed'
    output = proc.stdout
    if not isinstance(output, str) or len(output) > PULSE_SINK_INPUTS_OUTPUT_MAX:
        return None, 'unbounded_output'
    return output, None


def _project_pulse_stream(item, names, roles):
    """One sink input as a fixed, sanitized shape.

    ``application.process.id`` and the module/client identifiers are parsed by
    the shared helper but deliberately dropped: a pid is neither stable nor
    needed to tell "the BGM is muted" from "the BGM is missing".
    """
    sink = item.get('sink')
    percents = item.get('volume_percent')
    if not isinstance(percents, list):
        percents = None
    else:
        percents = [p for p in percents if type(p) is int and 0 <= p <= 200] or None
    role = item.get('role')
    return {
        'index': item.get('index') if type(item.get('index')) is int else None,
        'sink': names.get(sink) if isinstance(sink, str) else None,
        # The role vocabulary is fixed in pulse_volume; re-checked here so this
        # projection's contract holds on its own.
        'role': role if role in roles else None,
        'mute': item.get('mute') if isinstance(item.get('mute'), bool) else None,
        # None means the daemon did not report it, never "not corked".
        'corked': item.get('corked') if isinstance(item.get('corked'), bool) else None,
        'volume_percent': percents,
        'player': _pulse_player_category(item.get('application')),
    }


PULSE_PLAYER_CATEGORY_KEYS = tuple(
    dict.fromkeys(category for _needle, category in PULSE_PLAYER_CATEGORIES)
) + ("other", "unknown")


def _pulse_attribution(items, roles):
    """Fixed-axis attribution that survives the output budget.

    ``media.role`` is rarely set by the players, so ``by_player`` is the useful
    axis: it separates the BGM bridge (``bridge-ffplay``), game audio
    (``retroarch``), overlay audio (``browser``) and the encoder's monitor
    capture (``stream-capture``). ``by_role`` keeps only the roles that are
    actually present plus an explicit ``unattributed`` count, so the projection
    stays small and never looks like a role was assumed.
    """
    by_player = {key: 0 for key in PULSE_PLAYER_CATEGORY_KEYS}
    by_role = {"unattributed": 0}
    for item in items:
        category = _pulse_player_category(item.get("application")) or "unknown"
        by_player[category] = by_player.get(category, 0) + 1
        role = item.get("role")
        if isinstance(role, str) and role in roles:
            by_role[role] = by_role.get(role, 0) + 1
        else:
            by_role["unattributed"] += 1
    return {"by_player": by_player, "by_role": by_role}


def _collect_pulse_sink_inputs():
    """Read-only PulseAudio sink-input projection (#968).

    BGM/SE playback silence after a corner switch was previously only visible
    through an ad-hoc owner shell: the bridge's ffplay stream stayed alive with
    a normal volume while its sink input carried ``Mute: yes``, and
    ``module-stream-restore`` re-applied that mute to every new stream with the
    same key. Making mute/volume/sink/role readable here is the observability
    half of that follow-up; the fixed recovery operation is deliberately not
    part of this projection.

    Every field is a fixed key with a bounded value, and an unreachable daemon
    is reported as unreadable rather than as "no muted streams".
    """
    from docich.pulse_volume import MEDIA_ROLES, parse_sink_inputs, sink_names

    listing, failure = _pulse_listing('list', 'sink-inputs')
    if failure is not None:
        return {'schema_version': 1, 'readable': False, 'reason': failure,
                'total': None, 'muted': None, 'streams': [], 'attribution': None}
    # ``list sink-inputs`` reports the sink by index; the name is what makes a
    # muted shared stream recognizable, so the fixed short listing is read too.
    # A missing name mapping is not a failure: mute is still readable.
    short, _short_failure = _pulse_listing('list', 'short', 'sinks')
    try:
        items = parse_sink_inputs(listing)
        # The shared parser deliberately ignores unknown lines. Nonempty
        # output with no recognized stream must not look like a healthy empty
        # daemon (for example when a localized or changed format is returned).
        if listing.strip() and not items:
            raise ValueError('unrecognized sink-input listing')
        names = sink_names(short or '')
    except (TypeError, ValueError):
        return {'schema_version': 1, 'readable': False, 'reason': 'unparsable',
                'total': None, 'muted': None, 'streams': [], 'attribution': None}
    streams = [_project_pulse_stream(item, names, MEDIA_ROLES)
               for item in items[:PULSE_SINK_INPUTS_MAX]]
    return {
        'schema_version': 1,
        'readable': True,
        'reason': None,
        'total': len(items),
        # Count all parsed streams before limiting the per-stream details.
        'muted': sum(1 for item in items if item.get('mute') is True),
        # Bounded list; the omitted remainder is explicit so a full list is
        # never mistaken for a complete one.
        'truncated': len(items) > PULSE_SINK_INPUTS_MAX,
        'streams': streams,
        # Kept even when the budget drops ``streams`` (#AV projection).
        'attribution': _pulse_attribution(items, MEDIA_ROLES),
    }


def _collect_pulse_source_outputs():
    """Read-only PulseAudio source-output (capture/recording) projection (#1964).

    Playback can be audible while the stream capture (encoder input) is
    missing: the encoder's source output may be absent, corked, or pointing at
    the wrong source. This projection makes the capture side independently
    observable, mirroring ``_collect_pulse_sink_inputs`` but for source
    outputs (recording streams). An unreachable daemon is reported as
    unreadable rather than as "no capture streams".
    """
    from docich.pulse_volume import MEDIA_ROLES, parse_source_outputs, sink_names

    listing, failure = _pulse_listing('list', 'source-outputs')
    if failure is not None:
        return {'schema_version': 1, 'readable': False, 'reason': failure,
                'total': None, 'streams': [], 'attribution': None}
    # ``list source-outputs`` reports the source by index; resolve names via
    # the short source listing. A missing name mapping is not a failure.
    short, _short_failure = _pulse_listing('list', 'short', 'sources')
    try:
        items = parse_source_outputs(listing)
        if listing.strip() and not items:
            raise ValueError('unrecognized source-output listing')
        names = sink_names(short or '')
    except (TypeError, ValueError):
        return {'schema_version': 1, 'readable': False, 'reason': 'unparsable',
                'total': None, 'streams': [], 'attribution': None}

    def _project(item):
        source = item.get('source')
        percents = item.get('volume_percent')
        if not isinstance(percents, list):
            percents = None
        else:
            percents = [p for p in percents if type(p) is int and 0 <= p <= 200] or None
        role = item.get('role')
        return {
            'index': item.get('index') if type(item.get('index')) is int else None,
            'source': names.get(source) if isinstance(source, str) else None,
            'role': role if role in MEDIA_ROLES else None,
            'mute': item.get('mute') if isinstance(item.get('mute'), bool) else None,
            'corked': item.get('corked') if isinstance(item.get('corked'), bool) else None,
            'volume_percent': percents,
            'player': _pulse_player_category(item.get('application')),
        }

    streams = [_project(item) for item in items[:PULSE_SINK_INPUTS_MAX]]
    return {
        'schema_version': 1,
        'readable': True,
        'reason': None,
        'total': len(items),
        'truncated': len(items) > PULSE_SINK_INPUTS_MAX,
        'streams': streams,
        'attribution': _pulse_attribution(items, MEDIA_ROLES),
    }


# Aliases for alternate naming used by callers/tests.
_collect_pulse_capture = _collect_pulse_source_outputs
_collect_pulse_source_output = _collect_pulse_source_outputs
_collect_capture = _collect_pulse_source_outputs
_collect_stream_capture = _collect_pulse_source_outputs


BGM_REASONS = frozenset({
    'eligible', 'audio_missing', 'canonical_unreadable', 'request_unreadable',
    'ack_unreadable', 'pause_unreadable', 'invalid_state', 'switch_not_ready',
    'invalid_game', 'excluded_game', 'native_stop_unconfirmed', 'invalid_request',
    'invalid_generation', 'invalid_deadline', 'ack_mismatch', 'handover_changed',
    'policy_unavailable', 'worker_stopped',
})


def _bgm_owned_player(pid, worker_pid, proc_root=Path('/proc')):
    """Require the tagged ffplay to be the systemd worker's direct child."""
    if type(pid) is not int or pid <= 0 or not worker_pid:
        return False
    try:
        root = proc_root / str(pid)
        args = _supervisor_read(root / 'cmdline', 8192).split(b'\0')
        fields = _supervisor_read(root / 'stat', 4096).rsplit(b')', 1)[1].split()
        return (fields[0] != b'Z' and int(fields[1]) == worker_pid
                and _supervisor_read(root / 'comm', 64).strip() == b'ffplay'
                and any(args[i:i + 2] == [b'-window_title', b'soren-bgm-loop']
                        for i in range(len(args) - 1)))
    except (OSError, ValueError, IndexError):
        return False


def _collect_corner_bgm(soren, now):
    """Small independent BGM summary, retained when generic Pulse rows drop.

    Live ownership and the worker heartbeat are distinct from audibility.
    No environment, arbitrary command line, player stderr or audio is emitted.
    """
    result = dict(service='unknown', worker_present=None, telemetry='missing',
                  state='unknown', reason='unknown', age_sec=None,
                  player_present=None, player_exits=None, last_exit=None,
                  pulse_readable=False, identified_streams=None,
                  owned_streams=None, streams=[])
    unit = _systemctl_system(['show', _REG.CORNER_BGM['unit'],
                             '--property=ActiveState,MainPID'])
    worker_pid = None
    if unit and unit[0] == 0:
        props = dict(line.split('=', 1) for line in unit[1].splitlines() if '=' in line)
        if props.get('ActiveState') in {'active', 'inactive', 'failed', 'activating', 'deactivating'}:
            result['service'] = props['ActiveState']
        value = props.get('MainPID', '')
        if value.isdigit() and len(value) <= 10:
            worker_pid = int(value)
            result['worker_present'] = worker_pid > 0
    try:
        data = json.loads(_supervisor_read(Path(soren) / _REG.CORNER_BGM['state_file'], 4096))
        if not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != 1:
            raise ValueError('invalid snapshot')
        observed = data.get('observed_at')
        if type(observed) not in (int, float) or not math.isfinite(observed) or observed > now:
            raise ValueError('invalid timestamp')
        result['age_sec'] = min(int(now - observed), 2**31 - 1)
        fresh = result['age_sec'] <= _REG.CORNER_BGM['heartbeat_max_age_sec']
        matches = type(data.get('worker_pid')) is int and data['worker_pid'] == worker_pid and bool(worker_pid)
        result['telemetry'] = 'current' if fresh and matches else 'stale'
        # Never present a previous worker's decision as the live worker's state.
        if result['telemetry'] == 'current':
            if data.get('state') in {'blocked', 'starting', 'running', 'restarting', 'stopped'}:
                result['state'] = data['state']
            if isinstance(data.get('reason'), str) and data['reason'] in BGM_REASONS:
                result['reason'] = data['reason']
            result['player_present'] = _bgm_owned_player(data.get('player_pid'), worker_pid)
            for key, upper in (('player_exits', 2**31 - 1), ('last_exit', 255)):
                value = data.get(key)
                if type(value) is int and 0 <= value <= upper:
                    result[key] = value
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        result['telemetry'] = 'unreadable'
    from docich.pulse_volume import MEDIA_ROLES, parse_sink_inputs, sink_names
    listing, failure = _pulse_listing('list', 'sink-inputs')
    if failure is not None:
        return result
    items = parse_sink_inputs(listing)
    if listing.strip() and not items:
        return result
    identified = [s for s in items if s.get('corner_bgm_id') and s.get('corner_bgm_restore')]
    owned = [s for s in identified if _bgm_owned_player(s.get('pid'), worker_pid)]
    result.update(pulse_readable=True, identified_streams=len(identified), owned_streams=len(owned))
    short, _ = _pulse_listing('list', 'short', 'sinks')
    names = sink_names(short or '')
    # Only the fixed target name crosses this additional boundary.
    for item in owned[:2]:
        projected = _project_pulse_stream(item, names, MEDIA_ROLES)
        result['streams'].append({key: projected[key] for key in ('mute', 'corked', 'volume_percent')} |
                                 {'on_target_sink': projected['sink'] == 'soren_null' if projected['sink'] else None})
    return result


def _read_hanjuku_record(path):
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    file_fd = None
    try:
        parts = Path(path).absolute().parts[1:]
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= HANJUKU_TACTICAL_LIMIT:
            raise ValueError('invalid bounded record')
        raw = os.read(file_fd, HANJUKU_TACTICAL_LIMIT + 1)
        if len(raw) > HANJUKU_TACTICAL_LIMIT:
            raise ValueError('record grew')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('not object')
        return data, info.st_mtime
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(fd)


HANJUKU_DAMAGE_CARDS = frozenset({
    'イッテツーン', 'ダイチスイム', 'ブラッキー', 'フットバース', 'ダンスライン',
    'グリンボー', 'カルゲンジー', 'ピッグローラー', 'ラピニアール', 'カンケリン',
    'デッドガン', 'ノリウツール', 'ブレイコウ', 'クースカン', 'ブンシーン', 'ゼンマイン',
    'バルムンク', 'ミックミー', 'ファイアーボイス', 'グルミー', 'ブラックホール',
    'シュプレボイス', 'エンジェリン', 'ころぼぐんだん', 'バグストーム', 'リューキーシ',
    'ドデカヘー', 'マグネガキン', 'キャトルミュー', 'ビッグウェイブ', 'ハリケーン', 'ファバード',
})
HANJUKU_DAMAGE_REASONS = frozenset({
    'single_card_lethal', 'healing_not_a_kill', 'no_autonomous_egg_trigger',
    'egg_drop_candidate_not_a_kill', 'observed_control_chain_not_a_kill',
    'summon_already_observed', 'inspect_live_inventory_before_selection',
    'control_has_no_observed_followup', 'nonlethal_egg_risk',
    'damage_or_egg_risk_unclassified',
})


def _project_hanjuku_card_assessment(value, tick):
    """One numerical prediction, not a card-use or victory receipt."""
    if (not isinstance(value, dict) or type(tick) is not int or tick < 0
            or type(value.get('tick')) is not int or not 0 <= value['tick'] <= tick):
        return None
    def bounded(value, upper):
        return value if type(value) is int and 0 <= value <= upper else None
    def enum(value, allowed):
        return value if type(value) is str and value in allowed else None
    out = {'prediction_only': True, 'age_ticks': bounded(tick-value['tick'], 10000000),
           'card': enum(value.get('card'), HANJUKU_DAMAGE_CARDS),
           'target_kind': enum(value.get('target_kind'), {
               'general', 'boss_general', 'egg_monster', 'boss_monster', 'unknown'}),
           'reason': enum(value.get('reason'), HANJUKU_DAMAGE_REASONS)}
    for key in ('enemy_hp', 'raw_damage_min', 'enemy_soldier_hp_upper',
                'damage_lower_bound', 'remaining_hp_upper'):
        out[key] = bounded(value.get(key), 9999)
    for key in ('ally_soldiers', 'enemy_soldiers'):
        out[key] = bounded(value.get(key), 6)
    for key in ('lethal', 'egg_drop_fit', 'allowed'):
        out[key] = value.get(key) if type(value.get(key)) is bool else None
    return out


def _collect_hanjuku_projection_recovery(state_dir):
    """Current adopted mirror/guard births only; no identifiers or argv emitted."""
    result = {'status': 'unavailable', 'present': False, 'identity_matches': None,
              'guard_alive': None, 'player_alive': None}
    try:
        root = Path(state_dir)
        canonical, _ = _read_hanjuku_record(root / 'game_switch.json')
        active = canonical.get('active')
        if (not isinstance(active, dict) or active.get('game') != 'hanjuku-hero'
                or type(active.get('generation')) is not int
                or not isinstance(active.get('runtime_id'), str)
                or not re.fullmatch(r'g[1-9][0-9]*-[a-f0-9]{8}', active['runtime_id'])
                or active['generation'] != int(active['runtime_id'].split('-')[0][1:])
                or not isinstance(active.get('lease_id'), str) or not active['lease_id']):
            return result
        runtime = root / 'runtimes' / active['runtime_id']
        if runtime.is_symlink():
            return result
        path = runtime / _REG.HANJUKU_PROJECTION_RECOVERY['receipt_file']
        if not path.exists():
            return result
        result['present'] = True
        record, _ = _read_hanjuku_record(path)
        expected = {k: active[k] for k in ('game', 'runtime_id', 'generation', 'lease_id')}
        result['identity_matches'] = record.get('identity') == expected
        if not result['identity_matches']:
            result['status'] = 'unverified'
            return result
        def alive(entry, role):
            if (not isinstance(entry, dict) or type(entry.get('pid')) is not int
                    or entry['pid'] < 2 or not isinstance(entry.get('start'), str)
                    or not entry['start'].isdecimal()):
                return False
            try:
                raw = (Path('/proc') / str(entry['pid']) / 'stat').read_text()
                fields = raw[raw.rfind(')')+2:].split()
                if fields[0] == 'Z' or fields[19] != entry['start']:
                    return False
                argv_raw = (Path('/proc') / str(entry['pid']) / 'cmdline').read_bytes()
                if len(argv_raw) > 8192:
                    return False
                argv = [word.decode() for word in argv_raw.split(b'\0') if word]
                if role == 'guard':
                    owner=record.get('game_owner') or {}
                    return argv[1:] == ['-m', _REG.HANJUKU_PROJECTION_RECOVERY['module'], 'guard',
                                        active['runtime_id'],str(owner.get('pid')),str(owner.get('start'))]
                digest = record.get('player_argv_sha256')
                if not argv or Path(argv[0]).name != 'ffplay' or not isinstance(digest, str):
                    return False
                argv[0] = 'ffplay'
                return hashlib.sha256(json.dumps(argv).encode()).hexdigest() == digest
            except (OSError, ValueError, IndexError):
                return False
        result['guard_alive'] = alive(record.get('guard'), 'guard')
        result['player_alive'] = alive(record.get('player'), 'player')
        status = record.get('status')
        result['status'] = status if status in {'prepared', 'stopped', 'failed', 'cleanup_failed'} else 'unverified'
        if status == 'ready' and result['guard_alive'] and result['player_alive']:
            presentation, _ = _read_hanjuku_record(runtime / 'presentation.json')
            recovery = presentation.get('recovery')
            if (presentation.get('status') == 'ready' and presentation.get('projection') == record.get('projection')
                    and isinstance(recovery, dict) and recovery.get('identity') == expected
                    and recovery.get('player') == record.get('player') and recovery.get('guard') == record.get('guard')):
                result['status'] = 'healthy'
        again, _ = _read_hanjuku_record(root / 'game_switch.json')
        if again != canonical:
            result.update(status='unverified', identity_matches=False)
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {**result, 'status': 'unverified'}


def _collect_hanjuku_resume(state_dir):
    """Fixed metadata projection; does not read checkpoint/ROM bytes."""
    unavailable = {'status': 'unavailable', 'basis': 'resume_receipt'}
    try:
        root = Path(state_dir)
        canonical, _ = _read_hanjuku_record(root / 'game_switch.json')
        request, _ = _read_hanjuku_record(root / 'hanjuku-resume/hanjuku_resume.json')
        status = request.get('status')
        if status in {'prepared', 'queued'}:
            ledger = {}
            try:
                ledger, _ = _read_hanjuku_record(root / 'corner_rotation.json')
            except (OSError, ValueError):
                pass
            inbox = None
            try:
                inbox, _ = _read_hanjuku_record(root / 'corner_manual_queue.json')
            except (OSError, ValueError):
                pass
            choices = [inbox, ledger.get('queued_manual'), ledger.get('pending')]
            if any(isinstance(q, dict) and q.get('resume_latest') is True
                   and q.get('request_id') == request.get('request_id')
                   and q.get('corner') == 'hanjuku-hero' for q in choices):
                return {**unavailable, 'status': 'queued'}
            return {**unavailable, 'status': 'prepared' if status == 'prepared' else 'unverified'}
        owner = canonical.get('candidate') if status == 'claimed' else canonical.get('active')
        keys = ('game', 'runtime_id', 'generation', 'lease_id', 'request_id')
        active = canonical.get('active')
        if active is not None and not isinstance(active, dict):
            return unavailable
        if (status == 'claimed' and request.get('game') == 'hanjuku-hero'
                and request.get('corner') == 'hanjuku-hero'
                and isinstance(request.get('runtime_id'), str)
                and re.fullmatch(r'g[0-9]+-[a-f0-9]{8}', request['runtime_id'])
                and canonical.get('phase') in {'ready', 'idle'}
                and not any(canonical.get(k) for k in ('candidate', 'previous', 'retiring',
                                                       'operation', 'request_id'))
                and (active or {}).get('game') != 'hanjuku-hero'):
            receipt, _ = _read_hanjuku_record(
                root / 'runtimes' / request['runtime_id'] / 'hanjuku_resume.json')
            request_again, _ = _read_hanjuku_record(root / 'hanjuku-resume/hanjuku_resume.json')
            again, _ = _read_hanjuku_record(root / 'game_switch.json')
            if again != canonical or request_again != request:
                return {**unavailable, 'status': 'identity_changed'}
            if receipt != request:
                return {**unavailable, 'status': 'unverified'}
            # Metadata only: this is NOT proof that recovery is safe. The
            # operator rechecks rollback, gameplay, resources and sealed bytes
            # under writer locks before accepting this reservation fingerprint.
            digest = hashlib.sha256(json.dumps(request, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            return {**unavailable, 'status': 'detached_claim',
                    'reservation_fingerprint': digest}
        if (not isinstance(owner, dict) or owner.get('game') != 'hanjuku-hero'
                or not isinstance(owner.get('runtime_id'), str)
                or not re.fullmatch(r'g[0-9]+-[a-f0-9]{8}', owner['runtime_id'])
                or any(request.get(k) != owner.get(k) for k in keys[:-1])):
            return unavailable
        receipt, _ = _read_hanjuku_record(root / 'runtimes' / owner['runtime_id'] / 'hanjuku_resume.json')
        if any(receipt.get(k) != request.get(k) for k in keys) or receipt.get('status') != status:
            return {**unavailable, 'status': 'unverified'}
        again, _ = _read_hanjuku_record(root / 'game_switch.json')
        if again != canonical:
            return {**unavailable, 'status': 'identity_changed'}
        if status == 'claimed':
            return {**unavailable, 'status': 'claimed'}
        chapter = receipt.get('chapter')
        if status == 'verified' and type(chapter) is int and 1 <= chapter <= 12:
            return {**unavailable, 'status': 'verified_receipt', 'chapter': chapter,
                    'core_roundtrip_record': True}
        return {**unavailable, 'status': 'unverified'}
    except (OSError, ValueError, TypeError):
        return unavailable


_HANJUKU_REFILL_ENUMS = {
    'scope': frozenset({'current', 'unknown'}),
    'count': frozenset({'zero', 'low', 'below_target', 'enough', 'unknown'}),
    'basis': frozenset({'screen_total', 'observed_plus_paid_refill', 'unread_total',
                        'unverified_refill', 'unknown'}),
    'check': frozenset({'none', 'opening', 'requested', 'quantity_requested',
                        'closing', 'measured', 'unverified', 'unknown'}),
    'check_result': frozenset({'none', 'measured', 'unverified', 'unknown'}),
    'outcome': frozenset({'none', 'cancelled', 'insufficient_funds', 'unverified',
                          'attempt_limit', 'not_attempted', 'unknown'}),
    'receipt': frozenset({'none', 'pending', 'paid', 'unverified', 'unknown'}),
    'repair': frozenset({'none', 'hero', 'general', 'both', 'unknown'}),
    'planned': frozenset({'zero', 'positive', 'unknown'}),
    # Release condition of the hero repair reserve (#2601); wounded/moving are the only writer codes.
    'hero_deferred': frozenset({'none', 'wounded', 'moving', 'unknown'}),
    'roster': frozenset({'unavailable', 'partial', 'complete_no_pending',
                          'complete_all_seen', 'complete_missing', 'unknown'}),
    'recruit': frozenset({'none', 'pending', 'check', 'opened', 'done', 'unverified',
                         'deferred_army', 'deferred_egg', 'deferred_cash', 'deferred_roster',
                         'deferred_economy', 'not_affordable', 'not_needed', 'unknown'}),
}
_HANJUKU_REFILL_BOOLS = ('payroll_pending', 'low_priority', 'done', 'egg_hold', 'recruit_hold')
# Bounded gold amounts of the repair reserves (#2601); bools are excluded.
_HANJUKU_REFILL_INTS = ('hero_reserve', 'general_reserve')


def _compact_hanjuku_refill(value):
    """Only fixed enums/bools/bounded ints survive: never a writer's reason or names."""
    if not isinstance(value, dict) or value.get('scope') != 'current':
        return {'scope': 'unknown'}
    out = {key: value[key] if isinstance(value.get(key), str) and value[key] in allowed
           else 'unknown' for key, allowed in _HANJUKU_REFILL_ENUMS.items()}
    out.update({key: value.get(key) if type(value.get(key)) is bool else None
                for key in _HANJUKU_REFILL_BOOLS})
    out.update({key: value.get(key) if type(value.get(key)) is int and 0 <= value[key] <= 1_000_000
                else None for key in _HANJUKU_REFILL_INTS})
    return out


def _hanjuku_refill_roster(mem, same_identity, chapter, month):
    """Validate stored roster receipts without importing unverified bot code.

    Mirrors roster.fresh's raw/completed-survey choice, with exact scope types.
    Contract tests compare both paths with the strategy's read-only reader.
    """
    now = mem.get('tick')
    if type(now) is not int:
        return None

    def valid(receipt, *, surveyed=False):
        if not isinstance(receipt, dict):
            return False
        names, tick = receipt.get('names'), receipt.get('tick')
        return (same_identity(receipt.get('identity'))
                and type(receipt.get('chapter')) is int and receipt['chapter'] == chapter
                and type(receipt.get('month')) is str and receipt['month'] == month
                and type(tick) is int
                and (0 <= tick <= now if surveyed else 0 <= now - tick < 400)
                and isinstance(names, list) and 1 <= len(names) <= 32
                and all(isinstance(n, str) and n and
                        (not surveyed or len(n) <= 32 and '\ufffd' not in n) for n in names)
                and len(set(names)) == len(names))

    house = mem.get('house')
    if house is not None and not isinstance(house, dict):
        return None
    survey, house = mem.get('roster_survey'), house or {}
    if isinstance(house, dict) and 'roster_started' not in house and isinstance(survey, dict):
        receipt, scope = survey.get('receipt'), survey.get('scope')
        statuses, dirty = survey.get('statuses'), survey.get('dirty')
        if (type(survey.get('schema')) is int and survey['schema'] == 1
                and same_identity(survey.get('identity'))
                and isinstance(scope, list) and len(scope) == 2
                and type(scope[0]) is int and scope[0] == chapter
                and type(scope[1]) is str and scope[1] == month
                and valid(receipt, surveyed=True) and receipt.get('complete') is True
                and isinstance(statuses, dict) and set(statuses) == set(receipt['names'])
                and isinstance(dirty, dict) and set(dirty) <= set(receipt['names'])
                and all(isinstance(fields, list) and all(isinstance(f, str) for f in fields)
                        and set(fields) <= {'hp', 'location', 'egg'} for fields in dirty.values())
                and all(isinstance(row, dict) and row.get('general') == name
                        and row.get('month') == month
                        and type(row.get('observed_chapter')) is int and row['observed_chapter'] == chapter
                        and type(row.get('observed_tick')) is int
                        and receipt['tick'] <= row['observed_tick'] <= now
                        and type(row.get('broken')) is bool for name, row in statuses.items())
                and all(isinstance(survey.get(key, {}), dict)
                        and set(survey.get(key, {})) <= set(receipt['names'])
                        and all(type(v) is int and 0 <= v <= 10**6
                                for v in survey.get(key, {}).values())
                        for key in ('dirty_revisions', 'read_revisions'))):
            return receipt
    receipt = mem.get('recruit_roster')
    return receipt if valid(receipt) else None


def _project_hanjuku_refill(mem, active):
    """Current-month recorded gates only, not a newly computed spend decision.

    Caller already pins bot/run identity and a <=30s bot mtime. No extra file
    or log is read. The writer's unverified receipt/ended attempt stays so.
    """
    identity = {key: active[key] for key in HANJUKU_TACTICAL_KEYS}
    def same_identity(value):
        return (isinstance(value, dict) and value.keys() == identity.keys()
                and all(type(value[key]) is type(expected) and value[key] == expected
                        for key, expected in identity.items()))
    chapter, month, shop = mem.get('chapter'), mem.get('month'), mem.get('shop')
    if (not same_identity(mem.get('_run_identity')) or type(chapter) is not int
            or not 1 <= chapter <= 12 or not isinstance(month, str)
            or re.fullmatch(r'[1-9][0-9]*-(?:[1-9]|1[0-2])', month) is None
            or not isinstance(shop, dict) or shop.get('key') != month):
        return {'scope': 'unknown'}
    def same_scope(value):
        return (isinstance(value, list) and len(value) == 3 and same_identity(value[0])
                and type(value[1]) is int and value[1] == chapter
                and type(value[2]) is str and value[2] == month)
    count = mem.get('soldiers_seen')
    current = (same_scope(mem.get('soldiers_seen_scope')) and mem.get('soldiers_seen_key') == month
               and type(count) is int and 0 <= count <= 1_000_000)
    check, receipt = mem.get('army_count_check'), shop.get('soldier_receipt')
    check_current = isinstance(check, dict) and same_scope(check.get('scope'))
    low, pending = shop.get('low_army_refill'), mem.get('recruit_payroll_pending')
    hero, general = shop.get('hero_repair_reserve', 0), shop.get('general_repair_reserve', 0)
    repair_valid = all(type(n) is int and 0 <= n <= 1_000_000 for n in (hero, general))
    planned = shop.get('soldiers')
    outcome = shop.get('refill_outcome')
    # Use the same read-only freshness contract as recruitment. Only the
    # relationship is public; names and historical membership stay private.
    roster_gate = 'unknown'
    try:
        seen = _hanjuku_refill_roster(mem, same_identity, chapter, month)
        valid_pending = (pending is None or isinstance(pending, list)
                         and len(pending) <= 32
                         and all(isinstance(n, str) and n for n in pending))
        if (seen is None or not same_identity(seen.get('identity'))
                or type(seen.get('chapter')) is not int or seen['chapter'] != chapter
                or type(seen.get('month')) is not str or seen['month'] != month):
            roster_gate = 'unavailable'
        elif seen.get('complete') is not True:
            roster_gate = 'partial'
        elif valid_pending:
            roster_gate = ('complete_no_pending' if not pending else
                           'complete_all_seen' if set(pending) <= set(seen['names'])
                           else 'complete_missing')
    except (ImportError, ValueError, TypeError, KeyError, RecursionError):
        pass
    out = {'scope': 'current', 'roster': roster_gate,
           'count': ('unknown' if not current else 'zero' if count == 0 else
                     'low' if count <= 20 else 'below_target' if count < 50 else 'enough'),
           'basis': mem.get('soldiers_seen_basis') if current else 'unknown',
           'check': ('none' if check is None else check.get('stage') if isinstance(check, dict)
                     and check_current else 'unknown'),
           'check_result': ('none' if check is None else
                            check.get('result', 'none') if check_current else 'unknown'),
           'outcome': 'none' if outcome is None else outcome,
           'receipt': ('none' if receipt is None or receipt is False else receipt.get('status')
                       if isinstance(receipt, dict) and receipt.get('key') == month else 'unknown'),
           'repair': ('unknown' if not repair_valid else 'both' if hero and general else
                      'hero' if hero else 'general' if general else 'none'),
           'hero_reserve': hero if repair_valid else None,
           'general_reserve': general if repair_valid else None,
           'hero_deferred': 'none' if shop.get('hero_repair_deferred') is None else shop['hero_repair_deferred'],
           'planned': ('unknown' if type(planned) is not int or not 0 <= planned <= 99 else
                       'positive' if planned else 'zero'),
           'recruit': 'none' if shop.get('recruit') is None else shop['recruit'],
           'payroll_pending': (False if pending is None else bool(pending) if isinstance(pending, list)
                               and len(pending) <= 32 and all(isinstance(x, str) for x in pending) else None),
           'low_priority': (False if low is None else low.get('active') if isinstance(low, dict)
                            and type(low.get('version')) is int and low['version'] == 1
                            and same_scope(low.get('scope')) else None),
           'done': shop.get('soldiers_done')}
    for field, key in (('egg_hold', 'reserve'), ('recruit_hold', 'recruit_reserve')):
        value = shop.get(key, 0)
        out[field] = bool(value) if type(value) is int and 0 <= value <= 1_000_000 else None
    return _compact_hanjuku_refill(out)


def _project_hanjuku_house_deferral(mem, active):
    """Recent recorded cancellation, not proof of the physical reason or outcome."""
    record = mem.get('last_house_deferral')
    if not isinstance(record, dict):
        return None
    for identity in (mem.get('_run_identity'), record.get('identity')):
        if (not isinstance(identity, dict) or any(
                type(identity.get(k)) is not type(active.get(k)) or identity.get(k) != active.get(k)
                for k in HANJUKU_TACTICAL_KEYS)):
            return None
    tick, observed = mem.get('tick'), record.get('tick')
    if (type(tick) is not int or type(observed) is not int or observed < 0
            or not 0 <= tick - observed <= 400
            or type(record.get('chapter')) is not int or record['chapter'] != mem.get('chapter')
            or not isinstance(mem.get('month'), str)
            or not re.fullmatch(r'[1-9][0-9]*-(?:[1-9]|1[0-2])', mem['month'])
            or record.get('month') != mem['month']):
        return None
    return _compact_hanjuku_house_deferral({
        'age_ticks': tick - observed, 'code': record.get('code'), 'phase': record.get('phase')})


def _compact_hanjuku_house_deferral(value):
    if not isinstance(value, dict) or type(value.get('age_ticks')) is not int or not 0 <= value['age_ticks'] <= 400:
        return None
    code = value.get('code')
    phase = value.get('phase')
    return {'age_ticks': value['age_ticks'],
            'code': code if isinstance(code, str) and code in (
                'position_unread', 'alignment_limit', 'route_unverified', 'other') else 'unknown',
            'phase': phase if isinstance(phase, str) and phase in (
                'castle_confirm', 'house_view', 'house_confirm') else 'other'}


def _recent_hanjuku_decisions(runtime, active, clock, *, limit=8):
    """Last fixed decision/event names from the active runtime's own log.

    Only allowlisted tokens, fixed screen kinds and ages leave this reader.
    Reasons, chart steps, general/castle names, frame hashes and identities
    remain private; a malformed or unattributed line is skipped.
    """
    out = []
    try:
        fd = _open_hanjuku_scene_source(Path(runtime) / "hanjuku_decisions.jsonl")
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return out
            offset = max(0, info.st_size - 64 * 1024)
            os.lseek(fd, offset, os.SEEK_SET)
            raw = os.read(fd, 64 * 1024 + 1)[:64 * 1024]
            if offset:
                newline = raw.find(b"\n")
                raw = raw[newline + 1:] if newline >= 0 else b""
            lines = raw.decode("utf-8", errors="replace").splitlines()[-256:]
        finally:
            os.close(fd)
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not _hanjuku_scene_matches(row, active):
                    continue
                event = row.get("event")
                decision = row.get("decision")
                if not isinstance(event, str) or not re.fullmatch(r"[a-z_]{1,48}", event):
                    continue
                if decision is not None and (
                        not isinstance(decision, str)
                        or not re.fullmatch(r"[a-z_]{1,64}", decision)):
                    continue
                age = _hanjuku_scene_age(row.get("at"), clock)
                if age is None:
                    continue
                kind = row.get("screen_kind")
                kind = kind if kind in HANJUKU_DECISION_SCREEN_KINDS else "other"
                out.append({"event": event, "decision": decision,
                            "screen_kind": kind, "age_sec": age})
            except (ValueError, TypeError, OverflowError, RecursionError):
                continue
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return []
    return out[-limit:]


def _compact_hanjuku_policy(mem, state=None):
    """Small map/route summary that survives the tactical detail drop.

    The full tactical section is dropped when the diagnostics envelope is
    large, which hid chapter/active/orders exactly when a sortie had stopped.
    Keep only fixed names, bounded counts and status tokens; no free text,
    identities, frame hashes or decision reasons.
    """
    if not isinstance(mem, dict):
        return None
    def text(value, limit=64):
        return value if isinstance(value, str) and len(value) <= limit else None
    def names(value, limit=12):
        return ([item for item in value if isinstance(item, str)][:limit]
                if isinstance(value, list) else None)
    orders = mem.get('orders') if isinstance(mem.get('orders'), dict) else {}
    garrison = mem.get('garrison') if isinstance(mem.get('garrison'), dict) else {}
    sorties = mem.get('sorties') if isinstance(mem.get('sorties'), dict) else {}
    discovery = mem.get('map_discovery') if isinstance(mem.get('map_discovery'), dict) else {}
    registration = mem.get('map_registration') if isinstance(mem.get('map_registration'), dict) else {}
    chart_adjust = mem.get('chart_adjust') if isinstance(mem.get('chart_adjust'), dict) else {}
    battle = mem.get('battle') if isinstance(mem.get('battle'), dict) else {}
    monster = mem.get('monster_panel') if isinstance(mem.get('monster_panel'), dict) else {}
    cursor = mem.get('cursor')
    tick = mem.get('tick')
    state = state if isinstance(state, dict) else {}
    return {
        'chapter': mem.get('chapter') if type(mem.get('chapter')) is int else None,
        'screen_kind': text(state.get('screen_kind'), 32),
        'phase': text(state.get('phase'), 32),
        'step': state.get('step') if type(state.get('step')) is int else None,
        'no_input_streak': (state.get('no_input_streak')
                            if type(state.get('no_input_streak')) is int else None),
        'variant': text(mem.get('variant'), 32),
        'active': text(mem.get('active'), 32),
        'tick': tick if type(tick) is int and tick >= 0 else None,
        'month': text(mem.get('month'), 16),
        'orders': {text(step, 32) or '?': text(status, 32) or '?'
                   for step, status in list(orders.items())[:20]
                   if isinstance(step, str) and isinstance(status, str)},
        'captured_count': (len(mem['captured']) if isinstance(mem.get('captured'), list)
                           and len(mem['captured']) <= 64 else None),
        'garrison': {text(castle, 32) or '?': len(present) if isinstance(present, list) else None
                     for castle, present in list(garrison.items())[:12] if isinstance(castle, str)},
        'sorties': {text(step, 32) or '?': {'status': text(value.get('status'), 32),
                                            'target': text(value.get('target'), 32)}
                    for step, value in list(sorties.items())[:12]
                    if isinstance(value, dict)},
        'cursor': ([item for item in cursor[:2] if type(item) is int]
                   if isinstance(cursor, (list, tuple)) and len(cursor) == 2 else None),
        'uncertain': mem.get('uncertain') is True,
        'nav_search': mem.get('nav_search') is True,
        'y_jump': ({'mode': text((mem.get('y_jump') or {}).get('mode'), 16),
                    'goal': text((mem.get('y_jump') or {}).get('goal'), 32)}
                   if isinstance(mem.get('y_jump'), dict) else None),
        'approx_map': mem.get('approx_map') is True,
        'approx_fallback': mem.get('approx_fallback') is True,
        'approx_localize_miss': (mem.get('approx_localize_miss')
                                 if type(mem.get('approx_localize_miss')) is int else None),
        'map_discovery': ({'candidate': text(discovery.get('candidate'), 32),
                           'attempts': discovery.get('attempts')
                           if type(discovery.get('attempts')) is int else None}
                          if discovery else None),
        'map_registration_castles': (len(registration.get('castles'))
                                     if isinstance(registration.get('castles'), dict) else None),
        'chart_adjust': ({'interim_wanted': chart_adjust.get('interim_wanted') is True,
                          'has_plan': isinstance(mem.get('chart_plan'), dict)}
                         if chart_adjust else None),
        # A long battle looks like a frozen month/capture count. Project the
        # bounded HP evidence so diagnostics can say whether it is progressing.
        'battle': ({'enemy': text(battle.get('enemy'), 32),
                    'ally': text(battle.get('ally'), 32),
                    'start_enemy_hp': (battle.get('start_enemy_hp')
                                       if type(battle.get('start_enemy_hp')) is int else None),
                    'start_ally_hp': (battle.get('start_ally_hp')
                                      if type(battle.get('start_ally_hp')) is int else None),
                    'egg_battle': battle.get('egg_battle') is True}
                   if battle else None),
        'monster_panel': ({'ally': text(monster.get('ally'), 32),
                           'ally_hp': (monster.get('ally_hp')
                                       if type(monster.get('ally_hp')) is int else None),
                           'enemy': text(monster.get('enemy'), 32),
                           'enemy_hp': (monster.get('enemy_hp')
                                        if type(monster.get('enemy_hp')) is int else None)}
                          if monster else None),
    }


def _collect_hanjuku_tactical(state_dir, now):
    """Fresh fixed castle, card-prediction and monthly-refill records.

    These are the bot's recorded beliefs, not independently verified ownership
    or roster/payment. Castle labels remain chapter-1 only; refill gates do not
    imply an input was received. An absent garrison remains unknown, not empty.
    """
    out = {'status': 'unavailable', 'basis': 'bot_record', 'age_sec': None}
    try:
        if type(now) not in (int, float) or not math.isfinite(now):
            return out
        root = Path(state_dir)
        canonical, _ = _read_hanjuku_record(root / 'game_switch.json')
        active = canonical.get('active')
        if (canonical.get('phase') != 'ready' or not isinstance(active, dict)
                or active.get('game') != 'hanjuku-hero'
                or type(active.get('generation')) is not int
                or not isinstance(active.get('lease_id'), str) or not active['lease_id']
                or not isinstance(active.get('runtime_id'), str)
                or not re.fullmatch(r'g[0-9]+-[a-f0-9]{8}', active['runtime_id'])):
            return out
        runtime = root / 'runtimes' / active['runtime_id']
        bot, modified = _read_hanjuku_record(runtime / 'hanjuku_bot.json')
        run, _ = _read_hanjuku_record(runtime / 'hanjuku_run.json')
        trace = bot.get('decision_trace')
        if (not isinstance(trace, dict)
                or any(trace.get(k) != active.get(k) or run.get(k) != active.get(k) for k in HANJUKU_TACTICAL_KEYS)
                or run.get('terminal_reason') or run.get('terminal_candidate')):
            return out
        age = now - modified
        if not 0 <= age <= 30:
            return {**out, 'status': 'stale'}
        mem = bot.get('policy')
        if (not isinstance(mem, dict) or type(mem.get('chapter')) is not int
                or not 1 <= mem['chapter'] <= 12):
            return {**out, 'status': 'unsupported_chapter'}
        assessment = _project_hanjuku_card_assessment(mem.get('last_card_assessment'), mem.get('tick'))
        refill = _project_hanjuku_refill(mem, active)
        house_deferral = _project_hanjuku_house_deferral(mem, active)
        if mem['chapter'] != 1:
            again, _ = _read_hanjuku_record(root / 'game_switch.json')
            if again.get('phase') != 'ready' or again.get('active') != active:
                return {**out, 'status': 'identity_changed'}
            return {**out, 'status': 'unsupported_chapter', 'chapter': mem['chapter'],
                    'age_sec': int(age), 'policy': {**_compact_hanjuku_policy(mem, bot),
                               'recent_decisions': _recent_hanjuku_decisions(runtime, active, now)},
                    'last_card_assessment': assessment, 'refill': refill, 'last_house_deferral': house_deferral}
        captured = mem.get('captured', [])
        garrison = mem.get('garrison', {})
        sorties = mem.get('sorties', {})
        tick = mem.get('tick')
        unknown = mem.get('general_location_unknown', [])
        if (not isinstance(captured, list) or not isinstance(garrison, dict)
                or not isinstance(sorties, dict) or not isinstance(unknown, list)
                or type(tick) is not int or tick < 0):
            return out
        busy = {g for g in unknown if isinstance(g, str)}
        reserved = set()
        for entry in sorties.values():
            if (not isinstance(entry, dict)
                    or entry.get('status') not in ('en_route', 'launched_unconfirmed')
                    or type(entry.get('tick')) is not int or not 0 <= tick-entry['tick'] < 400):
                continue
            if isinstance(entry.get('general'), str):
                busy.add(entry['general'])
            if entry.get('target') in HANJUKU_TACTICAL_CASTLES:
                reserved.add(entry['target'])
        rows = []
        for castle in HANJUKU_TACTICAL_CASTLES[:-1]:
            names = garrison.get(castle)
            known = isinstance(names, list) and len(names) <= 64 and all(isinstance(g, str) for g in names)
            idle = len(set(names) - busy) if known else None
            rows.append({'castle': castle, 'captured_record': castle in captured,
                         'garrison_known': known, 'idle_generals_record': idle,
                         'target_reserved_record': castle in reserved})
        again, _ = _read_hanjuku_record(root / 'game_switch.json')
        if again.get('phase') != 'ready' or again.get('active') != active:
            return {**out, 'status': 'identity_changed'}
        out.update(status='ok', age_sec=int(age), chapter=1,
                   policy={**_compact_hanjuku_policy(mem, bot),
                           'recent_decisions': _recent_hanjuku_decisions(runtime, active, now)},
                   remaining_castles=[c for c in HANJUKU_TACTICAL_CASTLES[1:-1] if c not in captured],
                   castles=rows, home_lost_record=mem.get('home_lost') is True,
                   last_card_assessment=assessment, refill=refill, last_house_deferral=house_deferral)
        return out
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return out



class _NethackEvidenceError(ValueError):
    """Only fixed codes leave this read-only evidence reader."""


class _NethackEvidenceReader:
    """Pin every path component; never create a directory, lock or state file."""
    FILE_BYTES = 65536
    RUNTIME_LIMIT = 4096

    def __init__(self, root):
        self.root = Path(root).absolute()
        self.records = {}
        self.runtime_listing = None

    def directory(self, parts=()):
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in (*self.root.parts[1:], *parts):
                if part in {'.', '..'} or '/' in part:
                    raise _NethackEvidenceError('unsafe_path')
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=fd)
                os.close(fd)
                fd = following
            return fd
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise _NethackEvidenceError('invalid_record')
            result[key] = value
        return result

    def read(self, parts):
        parts = tuple(parts)
        try:
            directory = self.directory(parts[:-1])
            try:
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
            finally:
                os.close(directory)
            with os.fdopen(fd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > self.FILE_BYTES:
                    raise _NethackEvidenceError('invalid_record')
                raw = stream.read(self.FILE_BYTES + 1)
                after = os.fstat(stream.fileno())
                if ((before.st_ino, before.st_size, before.st_mtime_ns) !=
                        (after.st_ino, after.st_size, after.st_mtime_ns)):
                    raise _NethackEvidenceError('evidence_changed')
                if len(raw) > self.FILE_BYTES:
                    raise _NethackEvidenceError('invalid_record')
            value = json.loads(raw, object_pairs_hook=self.unique_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(
                                   _NethackEvidenceError('invalid_record')))
            if not isinstance(value, dict):
                raise _NethackEvidenceError('invalid_record')
        except FileNotFoundError:
            value, raw = None, None
        if parts in self.records and self.records[parts] != raw:
            raise _NethackEvidenceError('evidence_changed')
        self.records[parts] = raw
        return value

    def restored_runtime(self, generation):
        if type(generation) is not int or not 0 < generation < 10**9:
            raise _NethackEvidenceError('invalid_identity')
        directory = self.directory(('runtimes',))
        try:
            with os.scandir(directory) as entries:
                names = []
                for index, entry in enumerate(entries):
                    if index >= self.RUNTIME_LIMIT:
                        raise _NethackEvidenceError('scan_limit')
                    names.append(entry.name)
        finally:
            os.close(directory)
        names = sorted(names)
        if self.runtime_listing is not None and names != self.runtime_listing:
            raise _NethackEvidenceError('evidence_changed')
        self.runtime_listing = names
        matches = [name for name in names if name.startswith(f'g{generation}-')]
        if len(matches) != 1:
            raise _NethackEvidenceError('runtime_ambiguous')
        return _nethack_evidence_runtime(matches[0])

    def recheck(self):
        for parts in tuple(self.records):
            self.read(parts)
        if self.runtime_listing is not None:
            # Reuse one known generation only to verify the whole listing.
            name = next((n for n in self.runtime_listing
                         if re.fullmatch(r'g[1-9][0-9]{0,8}-[a-f0-9]{6,32}', n)), None)
            if name is None:
                raise _NethackEvidenceError('invalid_identity')
            self.restored_runtime(int(name.split('-')[0][1:]))


# Admission observations are deliberately separate from recovery authority.
# Keep this mapping fixed: never turn a state-provided corner/path into a read.
PROGRAM_ADMISSION_OWNERS = {
    **{name: 'retro_corner' for name in (
        'ninvaders', 'nsnake', 'bastet', 'moon-buggy', 'pacman4console', 'hanjuku-hero')},
    'nethack': 'nethack_corner', 'paper': 'paper_corner', 'meriken': 'soren91_corner',
    'tsuitate': 'tsuitate_corner', 'weather': 'weather_corner',
    'yosou-oh': 'yoso_corner', 'doushi-card': 'doushi_corner',
    'mystery': 'mystery_corner',
    'communist-law-simulator': 'lawsim_corner',
}
PROGRAM_ADMISSION_QUEUE_STATUSES = frozenset({
    'waiting', 'waiting_turn', 'waiting_boundary', 'running', 'done', 'error',
    'expired', 'cancelled',
})


PROGRAM_ADMISSION_FAILURE_STAGES = frozenset({
    'none', 'pending', 'owner', 'queue', 'lock', 'registry', 'fifo',
    'prediction', 'audio', 'recheck_owners', 'recheck_runtime', 'recheck_audio',
})
PROGRAM_ADMISSION_FAILURE_CODES = frozenset({
    'none', 'missing', 'unsafe_path', 'non_regular', 'oversize', 'json_invalid',
    'schema_invalid', 'queue_mismatch', 'scan_limit', 'evidence_changed', 'read_error',
})


class _ProgramAdmissionFailure(ValueError):
    """Only a reviewed fixed code, never exception text, reaches diagnostics."""
    def __init__(self, code):
        self.code = code if code in PROGRAM_ADMISSION_FAILURE_CODES else 'schema_invalid'
        super().__init__(self.code)


def _program_admission_failure_code(exc):
    if isinstance(exc, _ProgramAdmissionFailure):
        return exc.code
    if isinstance(exc, OSError):
        return ('unsafe_path' if exc.errno in {errno.ELOOP, errno.ENOTDIR}
                else 'missing' if exc.errno == errno.ENOENT
                else 'non_regular' if exc.errno == errno.EISDIR else 'read_error')
    if isinstance(exc, _NethackEvidenceError):
        # This shared reader/UUID validator uses only fixed internal codes.
        return 'unsafe_path' if exc.args == ('unsafe_path',) else 'schema_invalid'
    return 'schema_invalid'


class _ProgramAdmissionReader(_NethackEvidenceReader):
    """Bounded no-follow reads, including same-byte replacement detection.

    Rechecking is an optimistic observation window, not an atomic snapshot or
    a lease. Missing files and directory listings are rechecked as well.
    """
    SCAN_LIMIT = 128

    def __init__(self, root, *, audio_only=False):
        super().__init__(root)
        self.samples = {}
        self.audio_only = audio_only is True

    @staticmethod
    def fingerprint(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns)

    def sample(self, parts, mode='json'):
        parts = tuple(parts)
        try:
            directory = self.directory(parts[:-1])
            try:
                if mode == 'presence':
                    info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        raise _ProgramAdmissionFailure(
                            'unsafe_path' if stat.S_ISLNK(info.st_mode) else 'non_regular')
                    signature, value = self.fingerprint(info), True
                else:
                    fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
                    with os.fdopen(fd, 'rb') as stream:
                        before = os.fstat(stream.fileno())
                        if not stat.S_ISREG(before.st_mode):
                            raise _ProgramAdmissionFailure('non_regular')
                        if before.st_size > self.FILE_BYTES:
                            raise _ProgramAdmissionFailure('oversize')
                        raw = stream.read(self.FILE_BYTES + 1)
                        after = os.fstat(stream.fileno())
                    if self.fingerprint(before) != self.fingerprint(after):
                        raise _ProgramAdmissionFailure('evidence_changed')
                    if len(raw) > self.FILE_BYTES:
                        raise _ProgramAdmissionFailure('oversize')
                    signature = (self.fingerprint(after), raw)
                    value = raw
                    if mode == 'json':
                        try:
                            value = json.loads(raw, object_pairs_hook=self.unique_object,
                                               parse_constant=lambda _: (_ for _ in ()).throw(
                                                   _ProgramAdmissionFailure('json_invalid')))
                        except (ValueError, RecursionError) as exc:
                            raise _ProgramAdmissionFailure('json_invalid') from exc
                        if not isinstance(value, dict):
                            raise _ProgramAdmissionFailure('schema_invalid')
            finally:
                os.close(directory)
        except FileNotFoundError:
            signature, value = None, None
        key = (parts, mode)
        if key in self.samples and self.samples[key] != signature:
            raise _ProgramAdmissionFailure('evidence_changed')
        self.samples[key] = signature
        return value

    def listing(self, parts):
        parts = tuple(parts)
        try:
            directory = self.directory(parts)
            try:
                before = self.fingerprint(os.fstat(directory))
                rows = []
                with os.scandir(directory) as entries:
                    for index, entry in enumerate(entries):
                        if index >= self.SCAN_LIMIT:
                            raise _ProgramAdmissionFailure('scan_limit')
                        name = entry.name
                        if self.audio_only and not (
                                name.startswith(AUDIO_ITEM_PREFIX) and
                                name.endswith((AUDIO_QUEUED_SUFFIX, AUDIO_PLAYING_SUFFIX))):
                            # Keep every name in the bounded/rechecked listing,
                            # but never stat, follow or descend into non-audio.
                            rows.append((name, None))
                            continue
                        info = entry.stat(follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode):
                            raise _ProgramAdmissionFailure(
                                'unsafe_path' if stat.S_ISLNK(info.st_mode) else 'non_regular')
                        rows.append((entry.name, self.fingerprint(info)))
                after = self.fingerprint(os.fstat(directory))
                if before != after:
                    raise _ProgramAdmissionFailure('evidence_changed')
                value = tuple(sorted(rows))
                signature = (after, value)
            finally:
                os.close(directory)
        except FileNotFoundError:
            signature, value = None, None
        key = (parts, 'listing')
        if key in self.samples and self.samples[key] != signature:
            raise _ProgramAdmissionFailure('evidence_changed')
        self.samples[key] = signature
        return value

    def recheck(self):
        for parts, mode in tuple(self.samples):
            if mode == 'listing':
                self.listing(parts)
            else:
                self.sample(parts, mode)


def _program_admission_empty(reason='unavailable', *, stage='none', code='none'):
    return {
        'status': 'unknown', 'reason': reason, 'recovery_authority': False,
        # Method vocabulary alone is not proof that recheck was reached.
        'observation': 'rechecked_non_atomic', 'recheck_completed': False,
        'failure_stage': stage if stage in PROGRAM_ADMISSION_FAILURE_STAGES else 'none',
        'failure_code': code if code in PROGRAM_ADMISSION_FAILURE_CODES else 'schema_invalid',
        'queue_identity_basis': 'fixed_owner_and_selected_at_only',
        'pending_queue_matches': None,
        'owner_created_for_request': None, 'queue_status': 'unknown',
        'program_lock': {'state': 'unknown', 'present': None},
        'registry_owner_free': None, 'fifo_scan_complete': None,
        'earlier_waiter': None,
        'prediction': {'worker_running': None, 'local_active': None,
                       'boundary_after_request': None, 'waiting': None},
        'audio': {'readable': None, 'queued': None, 'playing': None,
                  'quiet': None, 'runtime_source_verified': False,
                  'source_basis': 'collector_env_or_default'},
    }


def _program_admission_owner_free(runtime, owners, expected):
    registry = runtime.sample(('docich_program_active.json',))
    if registry is None:
        return True
    previous = registry.get('owner_state')
    if previous == str(owners.root / (expected + '.json')):
        return True
    allowed = {str(owners.root / (name + '.json')): name
               for name in ROTATION_PENDING_OWNER_FILES}
    if not isinstance(previous, str) or previous not in allowed:
        return None
    name = allowed[previous]
    state = owners.sample((name + '.json',))
    if state is None:
        return True
    status = state.get('status')
    if not isinstance(status, str) or status not in ROTATION_STATUSES:
        return None
    # These views have a canonical restoration exception. Do not reproduce it
    # incompletely or call them occupied/free without its additional evidence.
    # lawsim は例外集合に入れない: 実行側（rotation/admission）は failed の
    # lawsim を「占有中（要回収）」と判定するため、診断だけ unknown にすると
    # 不一致になる（PR #2502 の独立検証で再現）。mystery と同じ扱い。
    if status == 'failed' and name in {'paper_corner', 'paper_corner_manual', 'doushi_corner'}:
        return None
    return status not in {'starting', 'active', 'restoring', 'recovery_required', 'failed'}


def _program_admission_worker(runtime):
    if runtime.sample(('prediction_worker.paused',), 'presence'):
        return False
    raw = runtime.sample(('prediction_worker.pid',), 'raw')
    if raw is None or len(raw) > 256:
        return None
    try:
        pid = next((int(word) for word in raw.decode('utf-8').split()
                    if word.isdigit()), None)
        if pid is None or pid <= 0:
            return None
        os.kill(pid, 0)  # Existence probe only, exactly as corner_boundary.
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, OverflowError, UnicodeError):
        return None


def _collect_program_admission_evidence(state_dir, soren, now):
    """Observe pre-owner admission gates; never grant recovery or acquire locks.

    A queue has no request UUID, so a match means only fixed-owner/time
    correspondence. No deadline is inferred from selected_at. Manual sibling
    reservations and unfamiliar schemas stay unknown. Runtime fail-open paths
    remain unknown here when their inputs cannot be safely observed.
    """
    result = _program_admission_empty()
    stage = 'pending'
    try:
        owners = _ProgramAdmissionReader(Path(state_dir))
        runtime = _ProgramAdmissionReader(Path(soren) / 'tmp' / 'state')
        ledger = owners.sample(('corner_rotation.json',))
        if not isinstance(ledger, dict) or ledger.get('manual_pending'):
            return _program_admission_empty('pending_unavailable', stage=stage,
                                            code='missing' if ledger is None else 'schema_invalid')
        pending = ledger.get('pending')
        if (not isinstance(pending, dict) or pending.get('phase') != 'dispatched'
                or not isinstance(pending.get('corner'), str)
                or pending['corner'] not in PROGRAM_ADMISSION_OWNERS):
            return _program_admission_empty('pending_unavailable', stage=stage, code='schema_invalid')
        requested = _finite_number(pending.get('selected_at'))
        request = _nethack_evidence_uuid(pending.get('request_id'))
        if requested is None or not 0 <= requested <= now:
            return _program_admission_empty('pending_unavailable', stage=stage, code='schema_invalid')
        expected = PROGRAM_ADMISSION_OWNERS[pending['corner']]
        stage = 'owner'
        owner = owners.sample((expected + '.json',))
        owner_request = owner.get('rotation_request_id') if owner is not None else None
        result['owner_created_for_request'] = (False if owner is None else
            owner_request == request if isinstance(owner_request, str) and re.fullmatch(
                r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
                owner_request) else None)
        stage = 'queue'
        queue = runtime.sample(('docich_program_queue', expected + '.json'))
        if (queue is None or _finite_number(queue.get('requested_at')) != requested
                or queue.get('status') not in PROGRAM_ADMISSION_QUEUE_STATUSES):
            return _program_admission_empty('pending_queue_unverified', stage=stage,
                                            code='missing' if queue is None else 'queue_mismatch')
        result['pending_queue_matches'] = True
        result['queue_status'] = queue['status']
        stage = 'lock'
        result['program_lock']['present'] = bool(runtime.sample(('docich_program.lock',), 'presence'))
        stage = 'registry'
        result['registry_owner_free'] = _program_admission_owner_free(runtime, owners, expected)
        stage = 'fifo'
        rows = runtime.listing(('docich_program_queue',))
        if rows is None:
            raise _ProgramAdmissionFailure('missing')
        earlier = False
        for name, _ in rows:
            if not name.endswith('.json') or name == expected + '.json':
                continue
            entry = runtime.sample(('docich_program_queue', name))
            if entry is None or entry.get('status') not in PROGRAM_ADMISSION_QUEUE_STATUSES:
                raise _ProgramAdmissionFailure('schema_invalid')
            if entry['status'] in {'waiting', 'waiting_turn'}:
                stamp = _finite_number(entry.get('requested_at'))
                if stamp is None or stamp < 0 or stamp > now:
                    raise _ProgramAdmissionFailure('schema_invalid')
                earlier |= stamp < requested  # No age cutoff or lexical tie-break.
        result['fifo_scan_complete'], result['earlier_waiter'] = True, earlier
        stage = 'prediction'
        prediction = runtime.sample(('current_prediction.json',))
        local_status = prediction.get('status') if prediction is not None else None
        local_active = (local_status in {'ACTIVE', 'LOCKED'} if local_status in {
            'ACTIVE', 'LOCKED', 'RESOLVED', 'CANCELED', 'CANCELLED', 'CLOSED', 'NONE', 'IDLE'} else None)
        boundary = runtime.sample(('corner_boundary_prediction.json',))
        stamp = _finite_number(boundary.get('completed_at')) if boundary is not None else None
        fresh = requested <= stamp <= now if stamp is not None else None
        worker = _program_admission_worker(runtime)
        waiting = (False if worker is False or local_active is False else
                   not fresh if worker is True and local_active is True and fresh is not None else None)
        result['prediction'].update(worker_running=worker, local_active=local_active,
                                    boundary_after_request=fresh, waiting=waiting)
        # Reuse the existing resolver, suffixes and count/quiet semantics.
        # Do not invoke the legacy unbounded scan a second time: every scan
        # in this projection is bounded and no-follow. No bodies or .env reads.
        stage = 'audio'
        audio_path = _comment_queue_dir(Path(soren))
        audio_reader = _ProgramAdmissionReader(audio_path, audio_only=True)
        audio_rows = audio_reader.listing(())
        counts = [0, 0]
        for name, _ in audio_rows or ():
            if name.startswith(AUDIO_ITEM_PREFIX):
                counts[0] += name.endswith(AUDIO_QUEUED_SUFFIX)
                counts[1] += name.endswith(AUDIO_PLAYING_SUFFIX)
        # Neither the collector environment nor current .env existence proves
        # the running worker's inherited environment. Do not read either the
        # worker environment or .env; runtime source verification stays false.
        result['audio'].update(readable=True, queued=counts[0], playing=counts[1],
                               quiet=not any(counts))
        stage = 'prediction'
        worker_again = _program_admission_worker(runtime)
        if worker_again != worker:
            raise _ProgramAdmissionFailure('evidence_changed')
        for stage, reader in (('recheck_owners', owners), ('recheck_runtime', runtime),
                              ('recheck_audio', audio_reader)):
            reader.recheck()
        result['recheck_completed'] = True
        result.update(status='observed', reason=(
            'pre_owner' if result['owner_created_for_request'] is False else
            'owner_already_created' if result['owner_created_for_request'] is True else
            'owner_identity_unverified'))
        return result
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError,
            _NethackEvidenceError) as exc:
        return _program_admission_empty('evidence_unverified', stage=stage,
                                        code=_program_admission_failure_code(exc))


def _nethack_evidence_uuid(value):
    if not isinstance(value, str) or re.fullmatch(
            r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value) is None:
        raise _NethackEvidenceError('invalid_identity')
    return value


def _nethack_evidence_runtime(value):
    if not isinstance(value, str) or re.fullmatch(r'g[1-9][0-9]{0,8}-[a-f0-9]{6,32}', value) is None:
        raise _NethackEvidenceError('invalid_identity')
    return value


def _nethack_evidence_time(value):
    if not isinstance(value, str):
        raise _NethackEvidenceError('invalid_record')
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.timestamp() < 0:
        raise _NethackEvidenceError('invalid_record')
    return parsed.timestamp()


def _nethack_evidence_player():
    """Read the fixed reviewed game definition, never a boundary-selected path."""
    try:
        import tomllib
        reader = _NethackEvidenceReader(PROD_ROOT)
        directory = reader.directory(('config', 'games'))
        try:
            fd = os.open('nethack.toml', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
                return None
            raw = stream.read(16385)
        if len(raw) > 16384:
            return None
        value = tomllib.loads(raw.decode('utf-8')).get('nethack', {}).get('player_name')
        return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_]{1,31}', value) else None
    except (OSError, ValueError, TypeError, AttributeError, ImportError):
        return None


def _nethack_evidence_receipt(reader, request):
    request = _nethack_evidence_uuid(request)
    receipt = reader.read(('game-switch', 'requests', request + '.json'))
    if receipt is None:
        return None
    result = receipt.get('result')
    generation = receipt.get('generation')
    runtime = _nethack_evidence_runtime(receipt.get('runtime_id'))
    if (type(receipt.get('schema_version')) is not int or receipt['schema_version'] != 1
            or receipt.get('request_id') != request or receipt.get('operation') != 'switch'
            or receipt.get('target') != 'sorengame' or not isinstance(result, dict)
            or receipt.get('status') not in {'succeeded', 'failed', 'rolled_back'}
            or result.get('request_id') != request or result.get('operation') != 'switch'
            or result.get('status') != receipt['status']
            or result.get('from_game') != 'nethack' or result.get('to_game') != 'sorengame'
            or type(generation) is not int or generation != int(runtime.split('-')[0][1:])
            or type(result.get('generation')) is not int or result['generation'] != generation
            or receipt.get('adapter_session') != f'docich-game-g{generation}'
            or receipt.get('game_window') != f'game-g{generation}'
            or receipt.get('agent_window') != f'agent-g{generation}'
            or receipt.get('runtime_dir') != str(reader.root / 'runtimes' / runtime)
            or (result.get('cleanup_pending') is not None
                and type(result['cleanup_pending']) is not bool)
            or _nethack_evidence_time(receipt.get('created_at')) >
               _nethack_evidence_time(receipt.get('updated_at'))):
        raise _NethackEvidenceError('receipt_mismatch')
    return receipt


def _nethack_evidence_boundary(reader, runtime, receipt, player, *, active_started=None):
    boundary = reader.read(('runtimes', runtime, 'nethack_boundary.json'))
    result = dict(present=boundary is not None, identity_matches=None,
                  request_matches=None, player_matches=None, ended=None, time_matches=None)
    if boundary is None:
        return result
    result.update(
        identity_matches=(type(boundary.get('schema_version')) is int
                          and boundary['schema_version'] == 1
                          and boundary.get('game') == 'nethack'
                          and boundary.get('runtime_id') == runtime
                          and type(boundary.get('generation')) is int
                          and boundary['generation'] == int(runtime.split('-')[0][1:])),
        request_matches=boundary.get('request_id') == receipt['request_id'],
        player_matches=bool(player) and boundary.get('player_name') == player,
        ended=boundary.get('outcome') == 'ended',
        time_matches=(_nethack_evidence_time(receipt['created_at']) <=
                      _nethack_evidence_time(boundary.get('recorded_at')) <=
                      (active_started if active_started is not None else
                       _nethack_evidence_time(receipt['updated_at']))),
    )
    return result


def _nethack_evidence_source(receipt, runtime):
    """A present modern source must agree with the selected NetHack boundary."""
    if 'source_runtime' not in receipt['result']:
        return
    source = receipt['result']['source_runtime']
    if (not isinstance(source, dict)
            or set(source) != {'game', 'adapter', 'runtime_id', 'generation', 'lease_id'}
            or source.get('game') != 'nethack' or source.get('adapter') != 'cli'
            or source.get('runtime_id') != runtime
            or type(source.get('generation')) is not int
            or source['generation'] != int(runtime.split('-')[0][1:])
            or source['generation'] >= receipt['generation']):
        raise _NethackEvidenceError('source_mismatch')
    _nethack_evidence_uuid(source.get('lease_id'))


def _nethack_evidence_tmux_absent(generation):
    # Only exact-generation read-only targets. No recovery/teardown helper,
    # input, shell, new-window or signal is invoked.
    from docich.hanjuku_manual_cancel import _ProbeTmux
    probe = _ProbeTmux()
    return (not probe.window_target_exists(f'docich:game-g{generation}', strict=True)
            and not probe.window_target_exists(f'docich:agent-g{generation}', strict=True)
            and not probe.session_target_exists(f'docich-game-g{generation}', strict=True))


def _nethack_evidence_resources(reader, runtime, probe):
    generation = int(runtime.split('-')[0][1:])
    presentation = reader.read(('runtimes', runtime, 'presentation.json'))
    tiles = reader.read(('runtimes', runtime, 'nethack_tiles.json'))
    result = dict(presentation_stopped=None, tiles_identity_matches=None,
                  tiles_cleanup_complete=None, tmux_absent=None,
                  all_resources_released=None, coverage='release_records_and_tmux',
                  unknown_reasons=[])
    if presentation is None:
        result['unknown_reasons'].append('presentation_record_missing')
    if presentation is not None:
        result['presentation_stopped'] = presentation.get('status') == 'stopped'
    if tiles is not None:
        matches = (type(tiles.get('schema_version')) is int and tiles['schema_version'] == 1
                   and tiles.get('runtime_id') == runtime
                   and type(tiles.get('generation')) is int and tiles['generation'] == generation
                   and tiles.get('adapter_session') == f'docich-game-g{generation}'
                   and tiles.get('game_window') == f'game-g{generation}')
        result['tiles_identity_matches'] = matches
        if matches and type(tiles.get('cleanup_complete')) is bool:
            result['tiles_cleanup_complete'] = (tiles['cleanup_complete']
                                               and tiles.get('status') == 'stopped')
        elif matches:
            result['unknown_reasons'].append('tiles_cleanup_unproven')
    else:
        result['unknown_reasons'].append('tiles_record_missing')
    try:
        absent = probe(generation)
        if type(absent) is bool:
            result['tmux_absent'] = absent
    except Exception:
        pass
    if result['tmux_absent'] is None:
        result['unknown_reasons'].append('tmux_probe_unavailable')
    result['unknown_reasons'] += ['detached_children_unobserved', 'unregistered_workers_unobserved']
    # A stopped presenter / empty tmux cannot prove detached game children or
    # an unregistered improvement worker gone. Preserve unknown; positive
    # contrary evidence, however, can prove incomplete cleanup.
    if any(result[k] is False for k in ('presentation_stopped', 'tiles_identity_matches',
                                       'tiles_cleanup_complete', 'tmux_absent')):
        result['all_resources_released'] = False
    return result


def _nethack_evidence_dispatch_history(owner, ledger, now):
    """Observe the legacy producer checks in the already-read ledger only."""
    unknown = ('invalid', 'invalid')
    history = ledger.get('history')
    if not isinstance(history, list):
        return unknown
    if len(history) > 512:
        return ('scan_limit', 'scan_limit')

    def stamp(value):
        if isinstance(value, str):
            return _nethack_evidence_time(value)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('invalid timestamp')
        return value

    try:
        selected = stamp(ledger['pending']['selected_at'])
        started = stamp(owner['started_at'])
        if not selected <= started <= now or stamp(ledger['last_seen_at']) > now:
            return unknown
        automatic, manual = False, False
        for row in history:
            if not isinstance(row, dict):
                return unknown
            recorded = stamp(row.get('at'))
            if recorded > now:
                return unknown
            if row.get('corner') == 'nethack' and recorded >= selected:
                manual |= row.get('source') == 'manual-reservation'
                automatic |= row.get('source') == 'reservation' and recorded <= started
        return ('matched' if automatic else 'missing', 'present' if manual else 'absent')
    except (KeyError, ValueError, TypeError, OverflowError):
        return unknown


def _nethack_evidence_contract_conditions(owner, ledger, original, landed, now):
    """Fixed input observations, never a recovery verdict or a historical error."""
    def category(key, expected):
        if key not in owner:
            return 'absent'
        value = owner[key]
        if value is None:
            return 'null'
        if not isinstance(value, str):
            return 'invalid'
        return expected if value == expected else 'other'

    automatic, manual = _nethack_evidence_dispatch_history(owner, ledger, now)
    result = original['result']
    cleanup = result.get('cleanup_pending')
    return dict(
        previous_game=category('previous_game', 'sorengame'),
        finish_reason=category('finish_reason', 'terminal'),
        pending_source_absent='source' not in ledger['pending'],
        original_source_absent='source_runtime' not in result,
        original_restored_identity_absent='restored_runtime' not in result,
        return_source_absent='source_runtime' not in landed['result'],
        restore_request_distinct=original['request_id'] != ledger['pending']['request_id'],
        original_cleanup_pending=('absent' if 'cleanup_pending' not in result else
                                  'null' if cleanup is None else 'pending' if cleanup else 'clear'),
        automatic_dispatch_history=automatic,
        manual_reservation_since_selection=manual,
    )


def _collect_nethack_rotation_evidence(state_dir, now, *, player=None, probe=None):
    """Observe one automatic failed owner; evidence is never recovery authority.

    No locks/writes or manager constructors. Re-read every input after probes;
    stable bytes are an observation, not an atomic snapshot or a future fence.
    """
    result = dict(schema_version=1, status='unavailable', reason='missing_evidence',
                  recovery_authority=False, snapshot_stable=None,
                  owner_matches=None, owner_terminal=None, legacy_contract_applicable=None,
                  contract_conditions=None,
                  restore_recovery_present=None, cleanup_record_present=None,
                  adopted_return_record_present=None,
                  cleanup_attempt_present=None, chronology_matches=None, terminal_chain_matches=None,
                  manual_clear=None, original_receipt=None, original_boundary=None,
                  return_receipt=None, return_boundary=None, canonical=None,
                  original_resources=None, rollback_resources=None)
    reader = _NethackEvidenceReader(state_dir)
    try:
        ledger = reader.read(('corner_rotation.json',))
        if ledger is None:
            return result
        pending = ledger.get('pending')
        if not isinstance(pending, dict) or pending.get('corner') != 'nethack':
            result.update(status='not_applicable', reason='no_automatic_nethack_pending')
            return result
        slot = _nethack_evidence_uuid(pending.get('request_id'))
        owner = reader.read(('nethack_corner.json',))
        canonical = reader.read(('game_switch.json',))
        manual = reader.read(('nethack_corner_manual.json',))
        if owner is None or canonical is None:
            return result
        result['owner_matches'] = (type(ledger.get('schema_version')) is int
                                   and ledger['schema_version'] == 1
                                   and ledger.get('status') == 'recovery_required'
                                   and pending.get('phase') == 'dispatched'
                                   and owner.get('rotation_request_id') == slot
                                   and type(owner.get('schema_version')) is int
                                   and owner['schema_version'] == 1 and owner.get('game') == 'nethack')
        result['owner_terminal'] = owner.get('status') in {'failed', 'interrupted'}
        result.update(adopted_return_record_present='nethack_adopted_return' in owner,
                      restore_recovery_present='restore_recovery' in owner,
                      cleanup_record_present='restore_cleanup' in owner,
                      cleanup_attempt_present='restore_cleanup_attempt' in owner)
        result['legacy_contract_applicable'] = (owner.get('previous_game', 'sorengame') == 'sorengame'
            and owner.get('finish_reason') == 'terminal'
            and all(k not in owner for k in ('restore_recovery', 'restore_cleanup', 'restore_cleanup_attempt', 'nethack_adopted_return')))
        result['manual_clear'] = (ledger.get('manual_pending') is None
            and (manual is None or (type(manual.get('schema_version')) is int
                                   and manual['schema_version'] == 1
                                   and manual.get('status') in {'idle', 'completed', 'interrupted', 'expired'})))
        if result['owner_matches'] is not True or result['owner_terminal'] is not True:
            raise _NethackEvidenceError('owner_mismatch')
        original = _nethack_evidence_receipt(reader, owner.get('switch_request_id'))
        if original is None:
            raise _NethackEvidenceError('missing_evidence')
        result['original_receipt'] = dict(status=original['status'], request_matches=True,
            cleanup_clear=original['result'].get('cleanup_pending') is False,
            source_identity_present=isinstance(original['result'].get('source_runtime'), dict))
        source = original['result'].get('source_runtime')
        source_runtime = owner.get('rotation_runtime_id')
        if isinstance(source, dict):
            if source_runtime is not None and source.get('runtime_id') != source_runtime:
                raise _NethackEvidenceError('source_mismatch')
            source_runtime = source.get('runtime_id')
        runtime = _nethack_evidence_runtime(source_runtime)
        _nethack_evidence_source(original, runtime)
        result['original_boundary'] = _nethack_evidence_boundary(reader, runtime, original, player)
        last = canonical.get('last_result')
        if not isinstance(last, dict):
            raise _NethackEvidenceError('missing_evidence')
        landed = _nethack_evidence_receipt(reader, last.get('request_id'))
        if landed is None:
            raise _NethackEvidenceError('missing_evidence')
        active = canonical.get('active')
        identity = landed['result'].get('active_runtime')
        identity_fields = ('game', 'runtime_id', 'generation', 'lease_id')
        identity_complete = isinstance(active, dict) and isinstance(identity, dict)
        active_started = None
        if identity_complete:
            _nethack_evidence_runtime(active.get('runtime_id'))
            _nethack_evidence_uuid(active.get('lease_id'))
            active_started = _nethack_evidence_time(active.get('started_at'))
            identity_complete = (active.get('game') == 'sorengame' and active.get('adapter') == 'soren'
                and type(active.get('generation')) is int
                and active['generation'] == int(active['runtime_id'].split('-')[0][1:])
                and active.get('adapter_session') == f"docich-game-g{active['generation']}"
                and active.get('game_window') == f"game-g{active['generation']}"
                and active.get('agent_window') == f"agent-g{active['generation']}"
                and _nethack_evidence_time(landed['created_at']) <= active_started <=
                    _nethack_evidence_time(landed['updated_at']) <=
                    _nethack_evidence_time(canonical.get('updated_at')) <= now)
        result['return_receipt'] = dict(status=landed['status'],
            distinct_request=landed['request_id'] not in {slot, original['request_id']},
            result_matches=(json.dumps(last, sort_keys=True) == json.dumps(landed['result'], sort_keys=True)))
        result['legacy_contract_applicable'] = (result['legacy_contract_applicable']
            and 'source' not in pending
            and all(k not in original['result'] for k in ('source_runtime', 'restored_runtime'))
            and 'source_runtime' not in landed['result'])
        result['canonical'] = dict(
            identity_complete=identity_complete,
            lease_matches=(identity_complete and all(type(active.get(k)) is type(identity.get(k))
                                                   and active.get(k) == identity.get(k) for k in identity_fields)
                           and active['runtime_id'] == landed['runtime_id']
                           and active['generation'] == landed['generation']),
            record_clear=(type(canonical.get('schema_version')) is int and canonical['schema_version'] == 2
                   and canonical.get('phase') == 'ready'
                   and all(k in canonical and canonical[k] is None
                           for k in ('request_id', 'operation', 'candidate', 'previous', 'deadline_at'))
                   and canonical.get('retiring') == []
                   and type(canonical.get('revision')) is int and canonical['revision'] >= 0
                   and _nethack_evidence_time(canonical.get('updated_at')) <= now
                   and landed['result'].get('cleanup_pending') in (None, False)),
        )
        restored = reader.restored_runtime(original['result'].get('restored_generation'))
        _nethack_evidence_source(landed, restored)
        result['return_boundary'] = _nethack_evidence_boundary(
            reader, restored, landed, player, active_started=active_started)
        result['chronology_matches'] = (
            _nethack_evidence_time(owner.get('started_at')) <=
            _nethack_evidence_time(owner.get('completed_at')) <=
            _nethack_evidence_time(original['created_at']) <=
            _nethack_evidence_time(original['updated_at']) <=
            _nethack_evidence_time(landed['created_at']) <=
            _nethack_evidence_time(landed['updated_at']) <= now
            and int(runtime.split('-')[0][1:]) < original['generation']
                < original['result']['restored_generation'] < landed['generation'])
        result['contract_conditions'] = _nethack_evidence_contract_conditions(
            owner, ledger, original, landed, now)
        probe = probe or _nethack_evidence_tmux_absent
        result['original_resources'] = _nethack_evidence_resources(reader, runtime, probe)
        result['rollback_resources'] = _nethack_evidence_resources(reader, restored, probe)
        checks = [result['owner_matches'], result['owner_terminal'], result['manual_clear'],
                  result['chronology_matches'], original['status'] == 'rolled_back',
                  landed['status'] == 'succeeded', result['return_receipt']['distinct_request'],
                  result['return_receipt']['result_matches'], result['canonical']['record_clear'],
                  result['canonical']['identity_complete'], result['canonical']['lease_matches']]
        checks += [v for b in (result['original_boundary'], result['return_boundary'])
                   for v in b.values()]
        result['terminal_chain_matches'] = (False if False in checks else
                                            None if None in checks else True)
        reader.recheck()
        result.update(status='observed',
                      reason=('resource_coverage_incomplete' if result['terminal_chain_matches'] is True
                              else 'terminal_chain_unproven'), snapshot_stable=True)
    except _NethackEvidenceError as exc:
        # Any failed read/recheck can straddle a changed record. Never retain
        # affirmative fields from an incomplete or unverified observation.
        for key in tuple(result):
            if key not in {'schema_version', 'recovery_authority', 'status', 'reason'}:
                result[key] = None
        result.update(status='unavailable', reason=str(exc), snapshot_stable=False)
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        for key in tuple(result):
            if key not in {'schema_version', 'recovery_authority', 'status', 'reason'}:
                result[key] = None
        result.update(status='unavailable', reason='unreadable_or_invalid_evidence',
                      snapshot_stable=False)
    return result


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


_PRESENTATION_STATUSES = ("starting", "ready", "presentation_failed", "cleanup_failed", "stopped")


def _presentation_record_nofollow(path):
    """Bounded JSON object and mtime from one pinned regular record."""
    directory_fd = record_fd = None
    try:
        path = Path(path)
        parent = path.parent
        directory_fd = _supervisor_directory(parent.anchor or ".")
        components = parent.parts[1:] if parent.anchor else parent.parts
        for component in components:
            previous_fd = directory_fd
            directory_fd = _supervisor_directory(component, dir_fd=previous_fd)
            os.close(previous_fd)
        flags = (os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
                 | getattr(os, "O_CLOEXEC", 0))
        record_fd = os.open(path.name, flags, dir_fd=directory_fd)
        info = os.fstat(record_fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("unsafe presentation record")
        with os.fdopen(record_fd, "rb", closefd=False) as stream:
            raw = stream.read(65536 + 1)
        if len(raw) > 65536:
            raise ValueError("oversized presentation record")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid presentation record")
        return True, True, value, info.st_mtime
    except FileNotFoundError:
        return False, False, None, None
    except (OSError, ValueError, RecursionError):
        return True, False, None, None
    finally:
        if record_fd is not None:
            os.close(record_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _collect_runtime_presentation(state_dir, now):
    """Bounded view of the *active* runtime's projector state (#2097).

    Reads only ``<state_dir>/runtimes/<runtime_id>/presentation.json`` for the
    runtime that currently owns the slot, and projects fixed enums plus the
    projection geometry and the record's mtime. A shared display restart must be
    observable (``presentation_failed`` -> ``ready``) without ever exposing
    pids, argv, paths or pane text.
    """
    result = {
        "present": False,
        "readable": False,
        "reason": None,
        "active_runtime": False,
        "status": None,
        "display": None,
        "window": None,
        "width": None,
        "height": None,
        "age_sec": None,
    }
    _, readable, data, _ = _presentation_record_nofollow(
        Path(state_dir) / "game_switch.json"
    )
    if not readable:
        result["reason"] = "active_unreadable"
        return result
    active = data.get("active")
    if not isinstance(active, dict):
        result["reason"] = "no_active_runtime"
        return result
    runtime_id = active.get("runtime_id")
    if (not isinstance(runtime_id, str) or runtime_id in (".", "..")
            or not _RUNTIME_ID_RE.fullmatch(runtime_id)):
        result["reason"] = "runtime_id_invalid"
        return result
    result["active_runtime"] = True
    path = Path(state_dir) / "runtimes" / runtime_id / "presentation.json"
    present, readable, state, modified = _presentation_record_nofollow(path)
    result["present"] = present
    result["readable"] = readable
    if not present:
        result["reason"] = "record_absent"
        return result
    if not readable or not isinstance(state, dict):
        result["reason"] = "record_unreadable"
        return result
    status = state.get("status")
    result["reason"] = "status_unknown"
    if isinstance(status, str) and status in _PRESENTATION_STATUSES:
        result["status"] = status
        result["reason"] = "ok"
    display = state.get("display")
    if isinstance(display, str) and re.fullmatch(r":[0-9]{1,3}", display):
        result["display"] = display
    window = state.get("window")
    if isinstance(window, str) and window.isdigit() and len(window) <= 12:
        result["window"] = window
    for key in ("width", "height"):
        value = state.get(key)
        if type(value) is int and 0 < value <= 4096:
            result[key] = value
    if 0 <= modified <= now + 86400:
        result["age_sec"] = max(0, int(now - modified))
    return result


def _collect_nethack_panes(state_dir, now):
    """Bounded read-only view of the committed NetHack runtime's tmux windows.

    A corner that is active but not progressing is otherwise invisible through
    this channel. The NetHack TTY lives in the runtime's birth/process window,
    not the presentation xterm window, so capture every window by name plus the
    resolved process and agent panes. Never sends tmux input.

    ``game_head`` carries the leading rows of the same game pane so the
    message/pager row -- structurally outside the 14-line tail -- is visible
    enough to tell an unanswered ``--More--`` from a zero-action decision loop
    (#2271).
    """
    result = {
        "present": False,
        "phase": None,
        "active_game": None,
        "windows": [],
        "game": [],
        "game_head": [],
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
        result["game_head"] = _capture_tmux_pane_head(f"{session}:{candidates[0]}")
    result["agent"] = _capture_tmux_pane(f"{session}:{agent_window}")
    return result


# NetHack evidence only: never invoke the game/retrospective/provider or probe locks.
NETHACK_HISTORY_SCAN_LIMIT = 128
NETHACK_HISTORY_FILE_BYTES = 65536
NETHACK_TERMINAL = frozenset({'dead', 'ascended', 'ended', 'ended_unknown'})
# Compact per-run depth/turn digest that survives the record budget (#2666).
# ``records`` is the first part of this projection the 36KiB envelope drops, so
# "did the run ever leave Dlvl 1?" was unanswerable from diagnostics exactly
# when the envelope was full. Five rows stay well under 1 KiB.
NETHACK_RECENT_RUN_LIMIT = 5
# Fixed producer codes from ``docich.nethack_run`` ``_terminal_records_since``;
# duplicated here (see test_nethack_boundary_diag.py precedent) so the
# diagnostics projection can aggregate them without importing game modules.
NETHACK_ANALYSIS_ERRORS = frozenset({
    'xlogfile-missing', 'xlogfile-truncated', 'no-new-player-xlog-record'})
NETHACK_LESSONS = frozenset({'repeated_death', 'survival_signal', 'food_survival',
                            'proposal_drift', 'evidence_gap', 'terminal_evidence',
                            'progress_stall'})


def _nethack_number(value):
    return value if type(value) is int and 0 <= value <= 10**12 else None


def _nethack_time(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        epoch = parsed.timestamp()
        return epoch if math.isfinite(epoch) and 0 <= epoch <= 253402300799 else None
    except (ValueError, OverflowError, OSError):
        return None


def _nethack_progress(raw):
    if not isinstance(raw, dict):
        return {'status': 'missing'}
    result = {'status': _rotation_enum(raw.get('status'),
              {'ok', 'empty', 'missing', 'error', 'too_large', 'invalid_run_id'})}
    for key in ('sample_count', 'malformed_lines', 'first_turn', 'last_turn',
                'max_turn', 'max_depth', 'same_frame_sent_pairs', 'max_same_frame_sent_streak',
                'same_frame_hold_pairs', 'max_same_frame_hold_streak'):
        result[key] = _nethack_number(raw.get(key))
    for key in ('first_ts', 'last_ts', 'min_hp_ratio'):
        value = raw.get(key)
        result[key] = value if type(value) in (int, float) and 0 <= value <= 10**12 and math.isfinite(value) else None
    result['truncated'] = raw.get('truncated') if type(raw.get('truncated')) is bool else None
    result['phase_counts'] = {key: _nethack_number(raw.get('phase_counts', {}).get(key))
                             for key in ('sent', 'hold')} if isinstance(raw.get('phase_counts'), dict) else {}
    result['prompt_counts'] = {
        key: _nethack_number(raw.get('prompt_counts', {}).get(key))
        for key in ('none', 'more', 'yes_no', 'direction', 'selection', 'text', 'unknown')
        if _nethack_number(raw.get('prompt_counts', {}).get(key)) is not None
    } if isinstance(raw.get('prompt_counts'), dict) else {}
    return result


def _nethack_record(raw, daily):
    if type(raw.get('schema_version')) is not int or raw['schema_version'] != 1:
        return None
    if daily:
        if _rotation_enum(raw.get('status'), {'review_ready', 'no_new_runs'}) == 'unknown':
            return None
        date = raw.get('date')
        if not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            return None
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            return None
        if raw.get('policy_effect') != 'none' or raw.get('automatic_promotion') is not False:
            return None
        count = _nethack_number(raw.get('run_count'))
        if count is None or count > 8:
            return None
        candidates = raw.get('candidates')
        if not isinstance(candidates, list) or len(candidates) > 100:
            return None
        categories = {}
        for item in candidates:
            category = _rotation_enum(item.get('category') if isinstance(item, dict) else None, NETHACK_LESSONS)
            categories[category] = categories.get(category, 0) + 1
        result = {'date': date, 'status': raw['status'], 'run_count': count,
                  'candidate_state': _rotation_enum(raw.get('candidate_state'), {'no_change', 'pending_canary_evaluation'}),
                  'candidate_categories': categories, 'policy_effect': 'none', 'automatic_promotion': False}
        timestamp = _nethack_time(raw.get('generated_at'))
    else:
        if _rotation_enum(raw.get('status'), NETHACK_TERMINAL) == 'unknown':
            return None
        # The run producer persists session closure on the root under
        # last_finished_at; ended_at exists only inside individual sessions.
        timestamp = _nethack_time(raw.get('last_finished_at'))
        retrospective = raw.get('retrospective')
        retrospective = retrospective if (
            isinstance(retrospective, dict)
            and type(retrospective.get('schema_version')) is int
            and retrospective['schema_version'] == 1
            and retrospective.get('source') == 'p5a_retrospective'
            and retrospective.get('run_id') == raw.get('run_id')
            and retrospective.get('terminal_status') == raw['status']
        ) else {}
        result = {'terminal_status': raw['status'], 'started_at': _nethack_time(raw.get('started_at')),
                  'expedition': _nethack_number(raw.get('expedition')),
                  'retrospective_present': bool(retrospective),
                  'retrospective_generated_at': _nethack_time(retrospective.get('generated_at')),
                  'same_death_total_count': _nethack_number(retrospective.get('same_death_total_count')),
                  'progress': _nethack_progress(retrospective.get('progress_evidence'))}
        for key in ('score', 'turns', 'max_depth'):
            result[key] = _nethack_number(raw.get(key))
        # ``terminal.analysis_error`` is the run store's own fixed reason code
        # for "game ended but xlog evidence was missing" (#753). Reviewed codes
        # are projected verbatim; any other producer value collapses to the
        # fixed token ``other`` so it is still countable without being exposed.
        # The type guard also keeps unhashable producer values (list/dict)
        # away from the frozenset membership test (#2515 review).
        terminal = raw.get('terminal')
        error = terminal.get('analysis_error') if isinstance(terminal, dict) else None
        if isinstance(error, str) and error in NETHACK_ANALYSIS_ERRORS:
            result['analysis_error'] = error
        else:
            result['analysis_error'] = 'other' if error else None
    if timestamp is None:
        return None
    result['generated_at' if daily else 'ended_at'] = timestamp
    return result


def _nethack_run_digest(record):
    """Fixed-shape per-run depth/turn row for the budget-proof summary.

    ``_nethack_history_budget`` trims ``records`` first, so on a full envelope
    the only surviving depth evidence is this digest inside ``summary``
    (#2666). Values are the same nullable projections ``records`` already
    carries: no run id, death reason, path or free text is added.
    """
    progress = record.get('progress')
    progress = progress if isinstance(progress, dict) else {}
    return {
        'ended_at': record.get('ended_at'),
        'terminal_status': record.get('terminal_status'),
        'max_depth': record.get('max_depth'),
        'turns': record.get('turns'),
        'progress_max_depth': progress.get('max_depth'),
        'progress_last_turn': progress.get('last_turn'),
    }


def _nethack_history(state_dir, daily):
    """Open each directory relative to a pinned fd: no symlink traversal/races."""
    result = {'status': 'unavailable', 'scan_complete': False, 'scanned_entries': 0,
              'invalid_records': 0, 'excluded_active': 0, 'omitted_records': 0, 'records': []}
    # Summary counters cover every valid terminal record seen during the scan,
    # not just the bounded ``records`` projection, so they survive the output
    # budget reductions that trim old records away (#753).
    summary = (None if daily else {
        'terminal_status_counts': {status: 0 for status in sorted(NETHACK_TERMINAL)},
        'score_present_count': 0,
        'analysis_error_counts': {code: 0 for code in sorted(NETHACK_ANALYSIS_ERRORS)}
        | {'other': 0},
        # Survives the record trimming below, so depth progress stays readable
        # from a full envelope (#2666).
        'recent_runs': [],
    })
    directory = Path(state_dir) / 'nethack' / ('daily-improvements' if daily else 'runs')
    fd = None
    try:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        for component in directory.absolute().parts[1:]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        records = []
        with os.scandir(fd) as entries:
            for entry in entries:
                if result['scanned_entries'] >= NETHACK_HISTORY_SCAN_LIMIT:
                    break
                result['scanned_entries'] += 1
                pattern = r'\d{4}-\d{2}-\d{2}\.json' if daily else r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.json'
                if not re.fullmatch(pattern, entry.name):
                    continue
                try:
                    file_fd = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    with os.fdopen(file_fd, 'rb') as handle:
                        info = os.fstat(handle.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_size > NETHACK_HISTORY_FILE_BYTES:
                            raise ValueError('bounded file required')
                        content = handle.read(NETHACK_HISTORY_FILE_BYTES + 1)
                    if len(content) > NETHACK_HISTORY_FILE_BYTES:
                        raise ValueError('bounded file required')
                    raw = json.loads(content)
                    if not isinstance(raw, dict):
                        raise ValueError('object required')
                    if not daily and _rotation_enum(raw.get('status'), {'active', 'starting', 'suspended', 'saved'}) != 'unknown':
                        result['excluded_active'] += 1
                        continue
                    record = _nethack_record(raw, daily)
                    if record is None or (daily and raw['date'] + '.json' != entry.name) or (
                        not daily and raw.get('run_id') != entry.name[:-5]
                    ):
                        raise ValueError('invalid evidence')
                    record['file_mtime'] = info.st_mtime
                    records.append(record)
                    if summary is not None:
                        status = record.get('terminal_status')
                        if status in summary['terminal_status_counts']:
                            summary['terminal_status_counts'][status] += 1
                        if type(record.get('score')) is int:
                            summary['score_present_count'] += 1
                        error = record.get('analysis_error')
                        if error is not None:
                            summary['analysis_error_counts'][error] += 1
                        summary['recent_runs'].append(_nethack_run_digest(record))
                except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
                    # A hostile run file must cost one invalid_records entry,
                    # never the whole read-only diagnostics scan (#2515 review).
                    result['invalid_records'] += 1
            else:
                result['scan_complete'] = True
        key = 'generated_at' if daily else 'ended_at'
        records.sort(key=lambda item: item[key], reverse=True)
        limit = 7 if daily else 8
        result['records'] = records[:limit]
        result['omitted_records'] = max(0, len(records) - limit)
        result['status'] = 'partial' if not result['scan_complete'] or result['invalid_records'] else ('ok' if records else 'empty')
        if summary is not None:
            summary['recent_runs'].sort(key=lambda item: item['ended_at'], reverse=True)
            summary['recent_runs'] = summary['recent_runs'][:NETHACK_RECENT_RUN_LIMIT]
            result['summary'] = summary
    except FileNotFoundError:
        result['status'] = 'missing'
    except OSError:
        pass
    finally:
        if fd is not None:
            os.close(fd)
    return result


def _collect_nethack_history(state_dir, now):
    return {'schema_version': 1, 'collected_at': now, 'scan_limit_per_source': NETHACK_HISTORY_SCAN_LIMIT,
            'file_byte_limit': NETHACK_HISTORY_FILE_BYTES,
            'daily': _nethack_history(state_dir, True), 'completed_runs': _nethack_history(state_dir, False)}


def _nethack_history_budget(payload, *, keep_latest=False):
    """Trim oldest history first, keeping the latest of each source if possible."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    history = payload.get("nethack_history")
    if not isinstance(history, dict):
        # A section the hard ceiling already dropped has nothing to trim. Return
        # the envelope unchanged so the drop order stays total instead of
        # raising on the next pass (#2533).
        return text
    while len(text.encode("utf-8")) > MAX_JSON_BYTES:
        sources = [history[name] for name in ("daily", "completed_runs")
                   if isinstance(history.get(name), dict)
                   and len(history[name].get("records") or []) > int(keep_latest)]
        if not sources:
            break  # Existing diagnostics retain their original budget handling.
        # Each source is newest-first. Exhaust older records before removing
        # either source's latest record; never mark an empty/missing source.
        heaviest = max(sources, key=lambda item: len(item["records"]))
        heaviest["records"].pop()
        heaviest["omitted_records"] = heaviest.get("omitted_records", 0) + 1
        heaviest["output_omitted"] = True
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return text


# ---------------------------------------------------------------------------
# NetHack xlogfile writer diagnostics (#753, read-only).
#
# ``src/docich/nethack_run.py`` treats the machine-readable xlogfile as the
# canonical source of terminal facts. NetHack 3.6.x on Linux appends a record
# at game end only when ``link(xlogfile, xlogfile_lock)`` succeeds: the file
# must already exist (ENOENT silently skips the write and never creates it)
# and a leftover lock hard link (EEXIST) silently skips every later write
# after 10 retries. Both failures are invisible to the run store, so this
# projection stats the fixed contract files and derived counters only. It
# never follows symlinks, never writes, never outputs xlog content, player
# names, or death text, and resolves paths only from the reviewed production
# game config.
# ---------------------------------------------------------------------------

NETHACK_XLOG_TAIL_BYTES = 65536
NETHACK_XLOG_DIR_SCAN_LIMIT = 128
NETHACK_XLOG_PROCESS_LIMIT = 4
NETHACK_XLOG_SYSCONF_BYTES = 65536
NETHACK_XLOG_SYSCONF_KEYS = frozenset({'xlogfile', 'logfile', 'dumplogfile'})
NETHACK_XLOG_SYSCONF_PATH = Path('/etc/nethack/sysconf')
NETHACK_XLOG_PACKAGES = frozenset({'nethack-common', 'nethack-console'})
# Fixed launcher / alternate-path candidates from the #753 investigation
# checklist; never discovered from the environment at runtime.
NETHACK_XLOG_LAUNCHERS = (Path('/usr/games/nethack'), Path('/usr/local/bin/nethack'))
NETHACK_XLOG_ALTERNATES = (Path('/var/lib/nethack/xlogfile'),
                           Path('/usr/games/lib/nethackdir/xlogfile'))
NETHACK_XLOG_LAUNCHER_BYTES = 4096
NETHACK_XLOG_LAUNCHER_KEYS = frozenset({'hackdir', 'nethackplayground',
                                        'playground', 'xlogfile'})
NETHACK_XLOG_BINARY_BYTES = 8 * 1024 * 1024
NETHACK_XLOG_SIBLING_LIMIT = 8


def _nethack_xlog_stat(path):
    """Bounded lstat projection of one fixed path; symlinks are never followed.

    Missing paths stay minimal: absence is reported as ``{"exists": false}``
    rather than a full dict of nulls, so the fixed envelope is spent on real
    evidence.
    """
    try:
        info = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return {"exists": False}
    except OSError:
        return {"exists": None}
    result = {
        "exists": True,
        "symlink": stat.S_ISLNK(info.st_mode),
        "regular": stat.S_ISREG(info.st_mode),
        "dir": stat.S_ISDIR(info.st_mode),
        "size": int(info.st_size),
        "mtime": info.st_mtime,
        "mode": stat.S_IMODE(info.st_mode),
        "uid": int(info.st_uid),
        "gid": int(info.st_gid),
        "nlink": int(info.st_nlink),
    }
    return result


def _nethack_xlog_same_inode(path_a, path_b):
    """True/False only when both fixed paths stat; otherwise None (unknown)."""
    try:
        info_a = os.stat(path_a, follow_symlinks=False)
        info_b = os.stat(path_b, follow_symlinks=False)
    except OSError:
        return None
    return (info_a.st_dev, info_a.st_ino) == (info_b.st_dev, info_b.st_ino)


def _nethack_xlog_game_paths():
    """Resolve the reviewed production game paths; never arbitrary input."""
    resolved = {
        "player": "docich",
        "save_dir": Path("/var/games/nethack/save"),
        "xlogfile": Path("/var/games/nethack/xlogfile"),
        "dump_dir": Path("/var/games/nethack/dumps"),
    }
    try:
        import tomllib
        raw = _read_text_capped(
            PROD_ROOT / "config" / "games" / "nethack.toml", 16384)
        table = tomllib.loads(raw or "").get("nethack")
    except (OSError, ValueError, TypeError, ImportError):
        table = None
    source = "defaults"
    if isinstance(table, dict):
        player = table.get("player_name")
        if isinstance(player, str) and re.fullmatch(r"[A-Za-z0-9_]{1,31}", player):
            resolved["player"] = player
            source = "production-config"
        for key in ("save_dir", "xlogfile", "dump_dir"):
            value = table.get(key)
            if (isinstance(value, str) and value.startswith("/")
                    and 0 < len(value) <= 4096 and "\x00" not in value
                    and ".." not in Path(value).parts):
                resolved[key] = Path(value)
                source = "production-config"
    resolved["source"] = source
    return resolved


def _nethack_xlog_save_files(save_dir, player):
    """Count the player's save files through a pinned dirfd; names stay local."""
    result = {"present": False, "scan_complete": False, "scanned_entries": 0,
              "player_saves": 0, "newest_player_save_mtime": None}
    pattern = re.compile(
        rf"\d+{re.escape(player)}(?:\.[A-Za-z0-9]+)?\Z", re.IGNORECASE)
    fd = None
    try:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        for component in Path(save_dir).absolute().parts[1:]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
            os.close(fd)
            fd = next_fd
        result["present"] = True
        with os.scandir(fd) as entries:
            for entry in entries:
                if result["scanned_entries"] >= NETHACK_XLOG_DIR_SCAN_LIMIT:
                    break
                result["scanned_entries"] += 1
                if pattern.fullmatch(entry.name) is None:
                    continue
                try:
                    info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                result["player_saves"] += 1
                newest = result["newest_player_save_mtime"]
                if newest is None or info.st_mtime > newest:
                    result["newest_player_save_mtime"] = info.st_mtime
        result["scan_complete"] = result["scanned_entries"] < NETHACK_XLOG_DIR_SCAN_LIMIT
    except (FileNotFoundError, NotADirectoryError):
        pass
    except OSError:
        pass
    finally:
        if fd is not None:
            os.close(fd)
    return result


def _nethack_xlog_processes():
    """Effective uid/gid of live nethack processes; no argv is ever read."""
    result = {"count": 0, "entries": []}
    try:
        candidates = [name for name in os.listdir('/proc') if name.isdigit()]
    except OSError:
        return result
    for name in candidates[:256]:
        if len(result["entries"]) >= NETHACK_XLOG_PROCESS_LIMIT:
            break
        base = Path('/proc') / name
        try:
            if base.joinpath('comm').read_text(
                    encoding='utf-8', errors='replace').strip() != 'nethack':
                continue
            entry = {"pid": int(name), "euid": None, "egid": None, "start_ticks": None}
            for line in base.joinpath('status').read_text(
                    encoding='utf-8', errors='replace').splitlines():
                if not (line.startswith('Uid:') or line.startswith('Gid:')):
                    continue
                fields = line.split()
                if len(fields) >= 3 and fields[1].isdigit() and fields[2].isdigit():
                    entry['euid' if line.startswith('Uid:') else 'egid'] = int(fields[2])
            stat_text = base.joinpath('stat').read_text(
                encoding='utf-8', errors='replace')
            tail = stat_text[stat_text.rindex(')') + 2:].split()
            if len(tail) > 19 and tail[19].isdigit():
                entry["start_ticks"] = int(tail[19])
            result["entries"].append(entry)
            result["count"] += 1
        except (OSError, ValueError):
            continue
    return result


def _nethack_xlog_packages():
    """dpkg metadata for the two fixed Debian package names; nothing else."""
    result = {"available": False, "entries": []}
    try:
        proc = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package}\t${Version}\t${Status}\n",
             *sorted(NETHACK_XLOG_PACKAGES)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return result
    for line in (proc.stdout or "").splitlines():
        fields = (line.split("\t") + ["", "", ""])[:3]
        package, version, status = fields
        if package not in NETHACK_XLOG_PACKAGES:
            continue
        result["available"] = True
        result["entries"].append({
            "package": package,
            "version": version[:64] if version else None,
            "installed": status == "install ok installed",
        })
    return result


def _nethack_xlog_sysconf():
    """Project only the three allowlisted xlog-related sysconf keys."""
    result = {"present": False, "readable": False, "configured": [], "values": {}}
    try:
        fd = os.open(NETHACK_XLOG_SYSCONF_PATH,
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return result
    except OSError:
        result["present"] = True
        return result
    result["present"] = True
    try:
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > NETHACK_XLOG_SYSCONF_BYTES:
                return result
            raw = stream.read(NETHACK_XLOG_SYSCONF_BYTES + 1)
        if len(raw) > NETHACK_XLOG_SYSCONF_BYTES:
            return result
    except OSError:
        return result
    result["readable"] = True
    for line in raw.decode('utf-8', errors='replace').splitlines():
        text = line.strip()
        if not text or text.startswith('#'):
            continue
        for separator in ('=', ':'):
            if separator in text:
                key, _, value = text.partition(separator)
                break
        else:
            continue
        name = key.strip().casefold()
        if name not in NETHACK_XLOG_SYSCONF_KEYS or name in result["values"]:
            continue
        result["configured"].append(name)
        cleaned = value.strip().strip('"').strip()
        if re.fullmatch(r"[A-Za-z0-9._/%{}-]{1,200}", cleaned or ""):
            result["values"][name] = cleaned
        else:
            result["values"][name] = None
    return result


def _nethack_xlog_parse_line(line):
    """Minimal copy of ``docich.nethack_run.parse_xlog_line`` (import-light)."""
    record = {}
    for fragment in line.rstrip("\r\n").split("\t"):
        if "=" not in fragment:
            continue
        key, _, value = fragment.partition("=")
        if key:
            record[key] = value
    return record


def _nethack_xlog_int(record, key, base=10):
    raw = record.get(key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw, base)
    except ValueError:
        return None


def _nethack_xlog_record_fields(record, player):
    """Allowlisted numeric projection of one parsed xlog record; no text."""
    return {
        "name_matches_player": (record.get("name") == player) if player else None,
        "starttime": _nethack_xlog_int(record, "starttime"),
        "endtime": _nethack_xlog_int(record, "endtime"),
        "points": _nethack_xlog_int(record, "points"),
        "turns": _nethack_xlog_int(record, "turns"),
        "maxlvl": _nethack_xlog_int(record, "maxlvl"),
        "achieve": _nethack_xlog_int(record, "achieve", base=0),
    }


def _nethack_xlog_tail(xlogfile, player):
    """Derived counters from the bounded xlog tail; content never leaves.

    ``record_span`` (oldest record's birth time, newest record's end time,
    complete-line count) is the #753 timeline evidence and stays small enough
    to survive the soft budget reductions. An unreadable xlogfile reports
    only ``{"readable": false}`` so absence never spends envelope bytes.
    """
    result = {"readable": False}
    try:
        fd = os.open(xlogfile, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (FileNotFoundError, OSError):
        return result
    head_trimmed = False
    try:
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return result
            size = int(info.st_size)
            start = max(0, size - NETHACK_XLOG_TAIL_BYTES)
            if start:
                # Peek one byte back: when the window happens to start on a
                # line boundary the first line is complete and must survive
                # (#753 review). Only a real mid-line cut is trimmed below.
                stream.seek(start - 1)
                raw = stream.read(NETHACK_XLOG_TAIL_BYTES + 1)
                head_trimmed = raw[:1] != b'\n'
                raw = raw[1:]
            else:
                raw = stream.read(NETHACK_XLOG_TAIL_BYTES)
    except OSError:
        return result
    result["size"] = size
    result["tail_complete_lines"] = 0
    result["tail_head_truncated"] = bool(start)
    result["record_span"] = None
    result["first_record"] = None
    result["last_record"] = None
    result["readable"] = True
    if head_trimmed:
        # The window may begin mid-line; only bytes after the first newline
        # are complete records (#2515 review). With no newline in the window
        # there is no complete line at all.
        newline = raw.find(b'\n')
        raw = raw[newline + 1:] if newline != -1 else b''
    raw = raw[:raw.rfind(b'\n') + 1]
    lines = [line for line in raw.split(b'\n') if line]
    result["tail_complete_lines"] = len(lines)
    if not lines:
        return result
    first = _nethack_xlog_record_fields(
        _nethack_xlog_parse_line(lines[0].decode('utf-8', errors='replace')), player)
    last = _nethack_xlog_record_fields(
        _nethack_xlog_parse_line(lines[-1].decode('utf-8', errors='replace')), player)
    result["first_record"] = first
    result["last_record"] = last
    result["record_span"] = {
        "first_starttime": first["starttime"],
        "last_endtime": last["endtime"],
        "complete_lines": len(lines),
    }
    return result


def _nethack_xlog_safe_value(text, cap=200):
    """Project a config-derived value only when it is plain path-like text."""
    if not isinstance(text, str) or not text:
        return None
    cleaned = text.strip().strip('"').strip()
    pattern = r"[A-Za-z0-9._/{}%+-]{1," + str(int(cap)) + "}"
    if re.fullmatch(pattern, cleaned or ""):
        return cleaned
    return None


def _nethack_xlog_launcher(path):
    """Stat one fixed launcher and project only allowlisted playground vars."""
    entry = {"stat": _nethack_xlog_stat(path), "target": None, "vars": {}}
    if not entry["stat"]["exists"]:
        return entry
    if entry["stat"]["symlink"]:
        try:
            entry["target"] = _nethack_xlog_safe_value(os.readlink(path))
        except OSError:
            entry["target"] = None
        return entry
    if not entry["stat"]["regular"] or entry["stat"]["size"] > 16384:
        return entry
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(NETHACK_XLOG_LAUNCHER_BYTES)
    except OSError:
        return entry
    for line in raw.decode('utf-8', errors='replace').splitlines():
        text = line.strip()
        if text.startswith('export '):
            text = text[len('export '):].strip()
        for separator in ('=', ':'):
            if separator in text:
                key, _, value = text.partition(separator)
                break
        else:
            continue
        name = key.strip().casefold()
        if name not in NETHACK_XLOG_LAUNCHER_KEYS or name in entry["vars"]:
            continue
        entry["vars"][name] = _nethack_xlog_safe_value(value)
    return entry


def _nethack_xlog_siblings(playground):
    """Names/size/mtime of bounded xlogfile* siblings; no other entries."""
    result = {"present": False, "scan_complete": False, "scanned_entries": 0,
              "entries": []}
    fd = None
    try:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        for component in Path(playground).absolute().parts[1:]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
            os.close(fd)
            fd = next_fd
        result["present"] = True
        with os.scandir(fd) as entries:
            matched = []
            for entry in entries:
                if result["scanned_entries"] >= NETHACK_XLOG_DIR_SCAN_LIMIT:
                    break
                result["scanned_entries"] += 1
                name = entry.name
                if not name.startswith('xlogfile'):
                    continue
                info = entry.stat(follow_symlinks=False)
                matched.append({
                    "name": _nethack_xlog_safe_value(name, cap=64) or 'xlogfile',
                    "size": int(info.st_size), "mtime": info.st_mtime,
                })
        matched.sort(key=lambda item: item["name"])
        result["entries"] = matched[:NETHACK_XLOG_SIBLING_LIMIT]
        result["scan_complete"] = result["scanned_entries"] < NETHACK_XLOG_DIR_SCAN_LIMIT
    except (FileNotFoundError, NotADirectoryError, OSError):
        pass
    finally:
        if fd is not None:
            os.close(fd)
    return result


def _nethack_xlog_binary_has_xlog_string(path):
    """Scan a fixed binary for the compiled-in ``xlogfile`` name (read-only).

    Reads at most ``NETHACK_XLOG_BINARY_BYTES`` bytes and reports whether the
    scan was complete, so a capped probe is never mistaken for a full
    negative (#2515 review). The needle is also matched across read-chunk
    boundaries by carrying an overlap of ``len(needle) - 1`` bytes.
    """
    needle = b'xlogfile'
    overlap = b''
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (FileNotFoundError, OSError):
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        total = int(info.st_size)
        planned = min(total, NETHACK_XLOG_BINARY_BYTES)
        consumed = 0
        while consumed < planned:
            chunk = os.read(fd, min(256 * 1024, planned - consumed))
            if not chunk:
                # The file shrank under the plan; the scan did not complete.
                break
            consumed += len(chunk)
            data = overlap + chunk
            if needle in data:
                return {"present": True, "complete_scan": True}
            overlap = data[1 - len(needle):]
        # ``complete_scan`` is true only when the whole file was examined;
        # a capped or short probe is never a full negative (#2515 review).
        return {"present": False, "complete_scan": consumed == planned == total}
    except OSError:
        return None
    finally:
        os.close(fd)


def _nethack_xlog_binaries(launcher_targets):
    """Locate the real nethack binaries via dpkg -L and fixed launcher targets."""
    result = {"available": False, "complete_scan": True, "entries": []}
    candidates = []
    try:
        proc = subprocess.run(
            ["dpkg-query", "-L", *sorted(NETHACK_XLOG_PACKAGES)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    if proc is not None:
        for line in (proc.stdout or "").splitlines():
            path = line.strip()
            if path.startswith('/') and path.rpartition('/')[2] == 'nethack':
                candidates.append(("dpkg", path))
    for launcher, target in launcher_targets:
        if target and target.startswith('/'):
            candidates.append((launcher, target))
    seen = set()
    for origin, path in candidates:
        if len(result["entries"]) >= 2:
            result["complete_scan"] = False
            break
        if path in seen:
            continue
        seen.add(path)
        probe = _nethack_xlog_binary_has_xlog_string(path)
        if probe is None:
            continue
        result["available"] = True
        result["entries"].append({
            "origin": origin,
            "xlog_string_present": probe["present"],
            "binary_scan_complete": probe["complete_scan"],
        })
    return result


def _collect_nethack_xlog(now):
    settings = _nethack_xlog_game_paths()
    xlogfile = settings["xlogfile"]
    playground = settings["save_dir"].parent
    xlog_lock = xlogfile.parent / (xlogfile.name + "_lock")
    logfile = playground / "logfile"
    logfile_lock = playground / (logfile.name + "_lock")
    record = playground / "record"
    launchers = {str(path): _nethack_xlog_launcher(path)
                 for path in NETHACK_XLOG_LAUNCHERS}
    launcher_targets = [(origin, entry["target"]) for origin, entry in launchers.items()
                        if entry["target"]]
    paths = {
        "xlogfile": _nethack_xlog_stat(xlogfile),
        "xlogfile_lock": _nethack_xlog_stat(xlog_lock),
        "logfile": _nethack_xlog_stat(logfile),
        "logfile_lock": _nethack_xlog_stat(logfile_lock),
        "record": _nethack_xlog_stat(record),
    }
    paths["xlogfile_lock"]["same_inode_as_xlogfile"] = _nethack_xlog_same_inode(
        xlogfile, xlog_lock)
    paths["logfile_lock"]["same_inode_as_logfile"] = _nethack_xlog_same_inode(
        logfile, logfile_lock)
    nlink = paths["xlogfile"].get("nlink")
    return {
        "schema_version": 1,
        "collected_at": now,
        "config": {"source": settings["source"], "player": settings["player"]},
        "paths": paths,
        "dirs": {
            "playground": _nethack_xlog_stat(playground),
            "save_dir": _nethack_xlog_stat(settings["save_dir"]),
            "dump_dir": _nethack_xlog_stat(settings["dump_dir"]),
        },
        "launchers": launchers,
        "alternates": {str(path): _nethack_xlog_stat(path)
                       for path in NETHACK_XLOG_ALTERNATES},
        "xlog_siblings": _nethack_xlog_siblings(playground),
        "binaries": _nethack_xlog_binaries(launcher_targets),
        "save_dir_entries": _nethack_xlog_save_files(
            settings["save_dir"], settings["player"]),
        "processes": _nethack_xlog_processes(),
        "packages": _nethack_xlog_packages(),
        "sysconf": _nethack_xlog_sysconf(),
        "xlog": _nethack_xlog_tail(xlogfile, settings["player"]),
        "findings": {
            "xlog_lock_present": paths["xlogfile_lock"]["exists"] is True,
            "xlog_lock_shares_inode": paths["xlogfile_lock"]["same_inode_as_xlogfile"],
            "xlog_file_shared_hardlink": (nlink > 1) if nlink is not None else None,
        },
    }


def _nethack_xlog_budget(payload):
    """Yield the xlog projection's optional detail under envelope pressure.

    The stat-level lock/permission evidence and the ``record_span`` timeline
    (oldest birth time / newest end time / line count) are the incident's
    first-order proof, so they go last. Optional detail (first/last record
    field detail, binaries, sibling list, process entries, sysconf values) is
    dropped first, and each drop is visible through an ``*_omitted`` marker.
    """
    section = payload.get("nethack_xlog")
    if not isinstance(section, dict):
        return payload
    xlog = section.get("xlog")
    if isinstance(xlog, dict):
        for key in ("first_record", "last_record"):
            if xlog.get(key) is not None:
                xlog[key] = None
                xlog[key + "_omitted"] = True
    for key in ("binaries", "processes", "xlog_siblings", "sysconf"):
        part = section.get(key)
        detail_key = "values" if key == "sysconf" else "entries"
        if isinstance(part, dict) and part.get(detail_key):
            part["values_omitted" if key == "sysconf" else "entries_omitted"] = True
            part[detail_key] = {} if key == "sysconf" else []
    return payload


def _nethack_xlog_drop(payload):
    """Collapse the xlog projection to its first-order evidence as the last resort.

    Preserve the stat-level lock/permission evidence (``paths``/``dirs``), the
    ``findings`` summary and the ``xlog.record_span`` timeline even at the hard
    ceiling, so #753 recurrence monitoring stays visible. Everything else is
    dropped and marked ``output_omitted``.
    """
    section = payload.get("nethack_xlog")
    if not isinstance(section, dict):
        return payload
    xlog = section.get("xlog")
    record_span = xlog.get("record_span") if isinstance(xlog, dict) else None
    dropped = {
        "schema_version": section.get("schema_version"),
        "collected_at": section.get("collected_at"),
        "status": "output_omitted",
    }
    for key in ("paths", "dirs", "findings"):
        value = section.get(key)
        if value is not None:
            dropped[key] = value
    if record_span is not None:
        dropped["xlog"] = {"record_span": record_span,
                           "record_span_omitted": False}
    payload["nethack_xlog"] = dropped
    return payload


def _cpu_profile_budget(payload):
    """Keep the busiest profiler rows when the fixed envelope is still full.

    The profiler report is the largest optional section left after the worker
    and AI reductions, and it is already ordered busiest-first by the
    producer, so dropping its tail frees room for the current game's evidence
    instead of forcing it into ``output_omitted``. The dropped row counts stay
    visible: an omitted row is never reported as absent.
    """
    profile = payload.get("cpu_profile")
    if not isinstance(profile, dict):
        return payload
    for key, limit in (("components", CPU_PROFILE_BUDGET_COMPONENTS),
                       ("spawns", CPU_PROFILE_BUDGET_SPAWNS)):
        rows = profile.get(key)
        if isinstance(rows, list) and len(rows) > limit:
            profile[f"{key}_omitted"] = len(rows) - limit
            profile[key] = rows[:limit]
    return payload


def _cpu_profile_detail_budget(payload):
    """Trim per-component rows to a small floor under pressure.

    Host totals are always retained; the busiest few rows survive too, so the
    sanitized projection can still name the top CPU consumers and spawn paths
    (azumag/docich#970) instead of degrading to host totals only. The producer
    already orders both lists busiest-first, and the dropped-row counts stay
    visible in ``*_omitted``.
    """
    profile = payload.get("cpu_profile")
    if not isinstance(profile, dict):
        return payload
    for key, floor in (("components", CPU_PROFILE_FLOOR_COMPONENTS),
                       ("spawns", CPU_PROFILE_FLOOR_SPAWNS)):
        rows = profile.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        kept = rows[:floor]
        dropped = len(rows) - len(kept)
        profile[key] = kept
        if not dropped:
            continue
        omitted_key = f"{key}_omitted"
        previous = profile.get(omitted_key, 0)
        if type(previous) is not int or previous < 0:
            previous = 0
        profile[omitted_key] = previous + dropped
    return payload


def _cpu_profile_detail_drop(payload):
    """Drop the remaining per-component rows as the last optional detail.

    The floor kept by ``_cpu_profile_detail_budget`` is still optional detail:
    it must go before the latest NetHack result, which the reviewed priority
    order ranks above CPU/audio detail. Host totals survive, and the dropped
    rows move into ``*_omitted`` so an omitted row is never reported as absent.
    """
    profile = payload.get("cpu_profile")
    if not isinstance(profile, dict):
        return payload
    for key in ("components", "spawns"):
        rows = profile.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        omitted_key = f"{key}_omitted"
        previous = profile.get(omitted_key, 0)
        if type(previous) is not int or previous < 0:
            previous = 0
        profile[omitted_key] = previous + len(rows)
        profile[key] = []
    return payload


def _host_metrics_hard_trim(payload, text):
    """Shed the oldest ring samples before any whole section is dropped.

    The ring is the only read-only evidence of the hour before a guest-OS hang
    (#2278) and cannot be recreated after the reboot, but it must not evict an
    existing section either: ``corners`` alone is ~23 KB, so a few hundred bytes
    of overflow would otherwise trade the whole game evidence block for the last
    minutes of host metrics. Trimming only under the hard ceiling -- never under
    the softer ``MAX_JSON_BYTES`` target, which the production envelope already
    exceeds on its own (#2533 measured 50,280 B) -- keeps both: drop the oldest
    projected points one at a time, down to the floor of the newest point plus
    the retained boot-boundary points (at most ``HOST_METRICS_BOUNDARY_BOOTS``
    of them). The floor itself is never crossed: when even the floor cannot
    fit, a whole section is dropped by the cascade regardless, so burning the
    pre-hang boundary for a trim that cannot help would only destroy evidence
    (#2578 review N1/N2). ``samples_omitted`` stays accurate either way.
    """
    section = payload.get("host_metrics")
    if (not isinstance(section, dict)
            or not isinstance(section.get("samples"), list)):
        return text
    samples = section["samples"]
    floor = _host_metrics_trim_samples(samples, 1)
    if len(samples) <= len(floor):
        return text
    original_samples = samples
    section["samples"] = floor
    floor_text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    section["samples"] = original_samples
    if len(floor_text.encode("utf-8")) > HARD_JSON_BYTES:
        # The floor does not fit: some section is dropped below whatever this
        # one sheds, so keep the full evidence instead of burning the boundary.
        return text
    while len(text.encode("utf-8")) > HARD_JSON_BYTES and len(samples) > len(floor):
        # The floor members (the newest point plus each retained previous-boot
        # boundary) are exactly the samples ``_host_metrics_trim_samples``
        # keeps, so they are protected as a set and re-resolved every
        # iteration: an older boundary must not be mistaken for droppable.
        protected = {id(item) for item in _host_metrics_trim_samples(samples, 1)}
        candidates = [
            index for index, sample in enumerate(samples)
            if id(sample) not in protected
        ]
        if not candidates:
            break
        samples.pop(candidates[0])
        omitted = section.get("samples_omitted")
        section["samples_omitted"] = (
            (omitted if type(omitted) is int and omitted > 0 else 0) + 1)
        section["points"] = len(samples)
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return text


def _diagnostics_budget(payload):
    """Keep latest history through existing detail reductions, then bound it."""
    text = _nethack_history_budget(payload, keep_latest=True)
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        payload["ai"]["recent_events"] = []
        payload["ai"]["recent_events_omitted"] = True
        payload["workers"]["details"] = {}
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if len(text.encode("utf-8")) > MAX_JSON_BYTES:
            payload["ai"]["anomalous_components"] = {}
            text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    # The profiler lists are the largest optional section left. Reducing them
    # before any game evidence keeps a full envelope from pushing the current
    # corner's read-only projections into output_omitted.
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        payload = _cpu_profile_budget(payload)
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
    # Scene narration is optional detail; keep tactical game evidence ahead of
    # its event histograms if the fixed diagnostics envelope is still full.
    corners = payload.get("corners")
    retro = corners.get("retro_corner") if isinstance(corners, dict) else None
    if (len(text.encode("utf-8")) > MAX_JSON_BYTES and isinstance(retro, dict)
            and "scene_narration" in retro):
        retro["scene_narration"] = {"status": "output_omitted"}
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    # Keep current game evidence through the older detail reductions first.
    if len(text.encode("utf-8")) > MAX_JSON_BYTES and "hanjuku_tactical" in payload:
        refill = payload['hanjuku_tactical'].get('refill') if isinstance(payload['hanjuku_tactical'], dict) else None
        house_deferral = (payload['hanjuku_tactical'].get('last_house_deferral')
                          if isinstance(payload['hanjuku_tactical'], dict) else None)
        summary = payload['hanjuku_tactical']
        policy = summary.get('policy') if isinstance(summary, dict) else None
        chapter = summary.get('chapter') if isinstance(summary, dict) else None
        age_sec = summary.get('age_sec') if isinstance(summary, dict) else None
        payload["hanjuku_tactical"] = {"status": "output_omitted", "basis": "bot_record"}
        if isinstance(policy, dict):
            payload['hanjuku_tactical']['policy'] = policy
        if type(chapter) is int:
            payload['hanjuku_tactical']['chapter'] = chapter
        if type(age_sec) is int:
            payload['hanjuku_tactical']['age_sec'] = age_sec
        if house_deferral is not None:
            payload['hanjuku_tactical']['last_house_deferral'] = _compact_hanjuku_house_deferral(house_deferral)
        if refill is not None:
            payload['hanjuku_tactical']['refill'] = _compact_hanjuku_refill(refill)
    # Keep the bounded CPU overview (meta/host) and PulseAudio counts ahead of
    # their per-component/per-stream details and the latest NetHack result.
    # ``encoder`` and ``pulse_sink_inputs.attribution`` are never reduced here
    # (#AV projection): they are the only read-only evidence for "is the encoder
    # still sending" and "which player owns this stream", and each is bounded
    # well under 1 KiB. Only ``streams`` yields below.
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    pulse = payload.get("pulse_sink_inputs")
    if (len(text.encode("utf-8")) > MAX_JSON_BYTES and isinstance(pulse, dict)
            and isinstance(pulse.get("streams"), list) and pulse["streams"]):
        payload["pulse_sink_inputs"] = {
            key: value for key, value in pulse.items() if key != 'streams'
        } | {'streams': [], 'truncated': True, 'output_omitted': True}
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    pulse2 = payload.get("pulse_source_outputs")
    if (len(text.encode("utf-8")) > MAX_JSON_BYTES and isinstance(pulse2, dict)
            and isinstance(pulse2.get("streams"), list) and pulse2["streams"]):
        payload["pulse_source_outputs"] = {
            key: value for key, value in pulse2.items() if key != 'streams'
        } | {'streams': [], 'truncated': True, 'output_omitted': True}
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        payload = _cpu_profile_detail_budget(payload)
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    # The #753 xlog projection is small and is the incident's first-order
    # evidence, but its optional detail still yields with the other optional
    # details before any history record goes.
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        payload = _nethack_xlog_budget(payload)
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    # Old history is still the first part of the NetHack projection to go. The
    # latest result is allowed to go only after optional CPU/audio detail.
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        text = _nethack_history_budget(payload, keep_latest=True)
    # The CPU floor is still optional detail: drop it entirely before the latest
    # NetHack result, preserving the reviewed priority order (review of #2406).
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        payload = _cpu_profile_detail_drop(payload)
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        text = _nethack_history_budget(payload)
    # The audio keeper summary and the audio-gap projection (docich#1811)
    # deliberately survive the soft budget: the production envelope routinely
    # sits above MAX_JSON_BYTES, so a soft-level stub here would blank them
    # from every run and break the audio_gap_measure operation's read-back.
    # Both sections are compact (a few hundred bytes) and yield only at the
    # hard ceiling below, where the largest-first fallback drops them last.
    # The repeated-operation projection deliberately survives the soft budget.
    # It is bounded well under 1 KiB and is the only evidence that separates
    # "corner alive and looping" from "corner progressing" -- the chart
    # counters and the stall watchdog stay quiet in that state. It is dropped
    # only against the gateway's hard ceiling, where keeping it would fail the
    # whole diagnostics operation instead of one field.
    text = _nethack_history_budget(payload)
    if (len(text.encode("utf-8")) > HARD_JSON_BYTES and isinstance(retro, dict)
            and "decision_plans" in retro):
        retro["decision_plans"] = {"status": "output_omitted"}
        # Re-measure: the omission above may already bring the envelope back
        # under the hard ceiling, and the retro_scores branch below must not
        # decide on a stale size.
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    # The scorelog tail is the only read-only evidence for "one score record per
    # match" (#2392). It stays under a few KiB, so it yields only against the
    # gateway's hard ceiling rather than the softer budget, and only if the
    # envelope is still over that ceiling after the reductions above.
    if (len(text.encode("utf-8")) > HARD_JSON_BYTES and isinstance(corners, dict)
            and "retro_scores" in corners):
        corners["retro_scores"] = {"status": "output_omitted"}
    # The xlog stat evidence is the last NetHack projection to go, and only
    # against the hard ceiling: collapsing it hides the lock/permission facts
    # that #753 needs, so it must survive every softer reduction.
    if len(text.encode("utf-8")) > HARD_JSON_BYTES:
        payload = _nethack_xlog_drop(payload)
    text = _nethack_history_budget(payload)
    # #2578: the ring samples are the only evidence of the hour before a
    # guest-OS hang and cannot be recreated after the reboot. Trim only against
    # the hard ceiling (and only the oldest samples) before any whole section is
    # dropped, so this section can never evict `corners` for a small overflow.
    text = _host_metrics_hard_trim(payload, text)
    # Hard ceiling guarantee (#2533). Every reduction above is reviewed and
    # preserves the evidence its own incident review depends on, but none of them
    # is unconditional: a payload that was still over the ceiling after them was
    # returned as-is, and the gateway rejected the whole diagnostics operation
    # closed ("diagnostics output too large") -- which is exactly how #2533 took
    # the read-only diagnostics path down. Drop whole sections heaviest-first
    # until the envelope fits, so the collector never hands the gateway a
    # document it must reject. Each dropped section is marked, so the omission is
    # visible in the envelope instead of looking like absent data.
    for key in _HARD_DROP_ORDER:
        if len(text.encode("utf-8")) <= HARD_JSON_BYTES:
            break
        if not isinstance(payload.get(key), dict):
            continue
        payload[key] = {"status": "output_omitted", "reason": "hard_ceiling"}
        text = _nethack_history_budget(payload)
    # Pathological fallback: a payload whose surviving sections are individually
    # small but numerous. Drop the remaining top-level sections largest-first,
    # keeping ``meta`` (the deployed SHA and collection status) so the envelope
    # still identifies what it came from, and ``prediction`` so the 予想王 counts
    # stay observable (#2805).
    if len(text.encode("utf-8")) > HARD_JSON_BYTES:
        for key, value in sorted(
                payload.items(),
                key=lambda item: -len(json.dumps(item[1], sort_keys=True))):
            if key in _HARD_KEEP or not isinstance(value, dict):
                continue
            payload[key] = {"status": "output_omitted", "reason": "hard_ceiling"}
            text = _nethack_history_budget(payload)
            if len(text.encode("utf-8")) <= HARD_JSON_BYTES:
                break
    # JSON whitespace is not evidence. Keep the new bounded summary without
    # pushing previously fitting envelopes over their byte budget; all values
    # and the existing conservative detail-reduction order stay unchanged.
    return json.dumps(json.loads(text), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def _collect_nethack_tiles(state_dir, now):
    """Project the fixed, generation-owned tile supervisor health fields.

    The private manifest contains ownership PIDs and paths for local cleanup;
    the diagnostics boundary deliberately exposes neither. Raw browser/server
    output, argv, and exception details are never part of this projection.
    """
    statuses = {
        "starting", "tiles_active", "fallback_starting", "fallback_tty",
        "failed", "stopped", "cleanup_failed",
    }
    reasons = {
        "server_start_failed", "server_stopped", "browser_start_failed",
        "browser_exited", "browser_window_missing", "projection_failed",
        "reader_failed", "reader_unavailable", "fallback_start_failed",
        "fallback_exited", "fallback_window_missing", "startup_failed",
    }
    result = {
        "present": False,
        "status": "unknown",
        "mode": "unknown",
        "reason": None,
        "active_runtime": False,
        "stale_runtime": False,
        "age_sec": None,
        "cleanup_complete": None,
    }
    try:
        switch = json.loads((Path(state_dir) / "game_switch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    if not isinstance(switch, dict):
        return result
    active = switch.get("active")
    if not isinstance(active, dict) or active.get("game") != "nethack":
        return result
    runtime_id = active.get("runtime_id")
    generation = active.get("generation")
    session = active.get("adapter_session")
    game_window = active.get("game_window")
    if (
        not isinstance(runtime_id, str)
        or re.fullmatch(r"[A-Za-z0-9._-]{1,96}", runtime_id) is None
        or type(generation) is not int
        or generation < 1
        or not isinstance(session, str)
        or not isinstance(game_window, str)
    ):
        return result
    path = Path(state_dir) / "runtimes" / runtime_id / "nethack_tiles.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    if not isinstance(manifest, dict):
        result["present"] = True
        return result
    result["present"] = True
    matches = (
        type(manifest.get("schema_version")) is int
        and manifest.get("schema_version") == 1
        and type(manifest.get("generation")) is int
        and manifest.get("runtime_id") == runtime_id
        and manifest.get("generation") == generation
        and manifest.get("adapter_session") == session
        and manifest.get("game_window") == game_window
    )
    result["active_runtime"] = bool(matches)
    result["stale_runtime"] = not matches
    if not matches:
        return result
    status = manifest.get("status")
    if isinstance(status, str) and status in statuses:
        result["status"] = status
    mode = manifest.get("mode")
    if isinstance(mode, str) and mode in {"tiles", "tty"}:
        result["mode"] = mode
    reason = manifest.get("reason")
    if isinstance(reason, str) and reason in reasons:
        result["reason"] = reason
    cleanup_complete = manifest.get("cleanup_complete")
    if isinstance(cleanup_complete, bool):
        result["cleanup_complete"] = cleanup_complete
    updated_at = manifest.get("updated_at")
    if type(updated_at) in (int, float) and 0 <= updated_at <= now + 86400:
        result["age_sec"] = max(0, int(now - updated_at))
    return result


SHARED_RUNTIME_ROLES = ("shared_stream", "encoder", "audio")
SHARED_RUNTIME_ROLE_LABELS = {
    "shared_stream": "shared stream / distribution",
    "encoder": "stream encoder",
    "audio": "audio worker",
}
SHARED_RUNTIME_MAX_PIDS = 64


def _shared_runtime_boot_id(proc_root):
    """Read only the fixed kernel boot UUID, with no symlink traversal."""
    fd = None
    try:
        root = Path(proc_root).absolute()
        fd = _supervisor_directory(root.anchor)
        for part in (*root.parts[1:], 'sys', 'kernel', 'random'):
            child = _supervisor_directory(part, dir_fd=fd)
            os.close(fd)
            fd = child
        value = _supervisor_read('boot_id', 64, dir_fd=fd).decode('ascii').strip()
        if re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value):
            return value
    except (OSError, ValueError, UnicodeError):
        pass
    finally:
        if fd is not None:
            os.close(fd)
    return None


class _SharedRuntimeSourceError(ValueError):
    """Fixed producer and refusal enums; never publish raw errors or paths."""
    def __init__(self, source, reason):
        super().__init__('runtime source unavailable')
        self.source = source if source in {'stream', 'audio', 'identities'} else 'unknown'
        allowed = STREAM_TITLE_READ_REJECTIONS | {
            'missing', 'permission_denied', 'io_error', 'changed', 'oversized',
            'invalid_json', 'not_running', 'invalid_pid', 'unknown'}
        self.reason = reason if reason in allowed else 'unknown'


def _shared_runtime_sources(soren):
    values = []
    for source, parts in (('stream', ('tmp', 'state', 'direct_stream', 'status.json')),
                          ('audio', ('tmp', 'state', 'audio_worker.pid'))):
        fd = directory = None
        try:
            fd, directory, before = _stream_title_open_fixed(soren, parts)
            raw = os.read(fd, 262145)
            after = os.fstat(fd)
            linked = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            if len(raw) > 262144:
                raise _SharedRuntimeSourceError(source, 'oversized')
            if (_stream_title_file_identity(before) != _stream_title_file_identity(after)
                    or _stream_title_file_identity(after) != _stream_title_file_identity(linked)):
                raise _SharedRuntimeSourceError(source, 'changed')
            values.append(raw)
        except _StreamTitleUnsafe as exc:
            raise _SharedRuntimeSourceError(source, exc.reason) from None
        except FileNotFoundError:
            raise _SharedRuntimeSourceError(source, 'missing') from None
        except PermissionError:
            raise _SharedRuntimeSourceError(source, 'permission_denied') from None
        except OSError:
            raise _SharedRuntimeSourceError(source, 'io_error') from None
        finally:
            if fd is not None:
                os.close(fd)
            if directory is not None:
                os.close(directory)
    try:
        stream = json.loads(values[0])
    except (ValueError, UnicodeError):
        raise _SharedRuntimeSourceError('stream', 'invalid_json') from None
    if not isinstance(stream, dict) or stream.get('running') is not True:
        raise _SharedRuntimeSourceError('stream', 'not_running')
    audio = values[1].strip()
    if not re.fullmatch(rb'[1-9][0-9]{0,9}', audio):
        raise _SharedRuntimeSourceError('audio', 'invalid_pid')
    pids = {'shared_stream': stream.get('pid'), 'encoder': stream.get('ffmpeg_pid'),
            'audio': int(audio)}
    if (any(type(pid) is not int or not 1 < pid <= 2**31-1 for pid in pids.values())
            or len(set(pids.values())) != len(pids)):
        raise _SharedRuntimeSourceError('identities', 'invalid_pid')
    return pids


def _shared_runtime_process(soren, proc_root, role, pid):
    root = Path(proc_root).absolute()
    directory = _supervisor_directory(root.anchor)
    process = None
    try:
        for part in root.parts[1:]:
            child = _supervisor_directory(part, dir_fd=directory)
            os.close(directory)
            directory = child
        process = _supervisor_directory(str(pid), dir_fd=directory)
        info = os.fstat(process)
        ticks = _supervisor_process_start(proc_root, pid, process)
        if not 0 < ticks <= 2**63-1:
            raise ValueError('invalid process start')
        raw = _supervisor_read('cmdline', 4096, dir_fd=process)
        if not raw.endswith(b'\0'):
            raise ValueError('incomplete command')
        argv = raw[:-1].split(b'\0')
        exe = os.readlink('exe', dir_fd=process)
        executable = Path(exe).name
        cwd = os.readlink('cwd', dir_fd=process)
        fields = _supervisor_read('stat', 4096, dir_fd=process).rsplit(b')', 1)[1].split()
        parent = int(fields[1])
        process_kind = role
        python = re.fullmatch(r'python3(?:\.[0-9]+)?', executable) is not None
        if role in ('encoder', 'guardian'):
            if cwd != str(soren):
                raise ValueError('unexpected encoder cwd')
            module = b'docich.twica_encoder' if role == 'guardian' else b'docich.twica_ffmpeg'
            if python and len(argv) >= 3 and argv[1:3] == [b'-m', module]:
                process_kind = 'guardian' if role == 'guardian' else 'adapter'
            elif (role == 'encoder' and executable == 'ffmpeg' and argv
                  and Path(os.fsdecode(argv[0])).name == 'ffmpeg'
                  and b'x11grab' in argv and b'-progress' in argv):
                process_kind = 'native'
            else:
                raise ValueError('unexpected encoder')
        else:
            expected = 'lib/direct_stream.py' if role == 'shared_stream' else 'workers/audio_worker.sh'
            if (role == 'shared_stream' and not re.fullmatch(r'python3(?:\.[0-9]+)?', executable)
                    or role == 'audio' and executable != 'bash' or len(argv) < 2):
                raise ValueError('unexpected interpreter')
            script = Path(os.fsdecode(argv[1]))
            cwd = os.readlink('cwd', dir_fd=process)
            if not script.is_absolute():
                if cwd != str(soren):
                    raise ValueError('unexpected working directory')
                script = Path(soren) / script
            if script != Path(soren) / expected:
                raise ValueError('unexpected script')
        again = os.stat(str(pid), dir_fd=directory, follow_symlinks=False)
        if ((info.st_dev, info.st_ino, info.st_uid) != (again.st_dev, again.st_ino, again.st_uid)
                or ticks != _supervisor_process_start(proc_root, pid, process)
                or raw != _supervisor_read('cmdline', 4096, dir_fd=process)
                or exe != os.readlink('exe', dir_fd=process)
                or cwd != os.readlink('cwd', dir_fd=process)
                or parent != int(_supervisor_read('stat', 4096, dir_fd=process).rsplit(b')', 1)[1].split()[1])):
            raise ValueError('changed process identity')
        return {'pid': pid, 'start_ticks': ticks, 'parent': parent, 'kind': process_kind}
    finally:
        if process is not None:
            os.close(process)
        os.close(directory)


def _shared_runtime_pipeline(docich_root, adapter, boot_id):
    parts = ('twica-common', 'pipeline.json')
    fd, directory, before = _stream_title_open_fixed(Path(docich_root) / 'run', parts)
    try:
        raw = os.read(fd, 4097)
        after = os.fstat(fd)
        linked = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        if (len(raw) > 4096 or _stream_title_file_identity(before) != _stream_title_file_identity(after)
                or _stream_title_file_identity(after) != _stream_title_file_identity(linked)):
            raise ValueError('changed pipeline')
        data = json.loads(raw)
        now_ns = time.monotonic_ns()  # Sample after the atomic heartbeat read.
        if (not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != 1
                or type(data.get('pid')) is not int or data['pid'] != adapter['pid']
                or data.get('identity') != f"{boot_id}:{adapter['start_ticks']}"
                or type(data.get('monotonic_ns')) is not int
                or not 0 <= now_ns - data['monotonic_ns'] < 3_000_000_000
                or data.get('ready') is not True
                or type(data.get('encoder_pid')) is not int or not 1 < data['encoder_pid'] <= 2**31-1):
            raise ValueError('unverified pipeline')
        # Health counters may advance between reads; identity may not.
        return data['encoder_pid']
    finally:
        os.close(fd)
        os.close(directory)


def _shared_runtime_chain(soren, proc_root, docich_root, pids, boot_id, probe=None):
    # Only collector-owned literal stage names may reach the public receipt.
    probe = {} if probe is None else probe
    probe['failure_stage'] = 'stream_process'
    runner = _shared_runtime_process(soren, proc_root, 'shared_stream', pids['shared_stream'])
    probe['failure_stage'] = 'audio_process'
    audio = _shared_runtime_process(soren, proc_root, 'audio', pids['audio'])
    probe['failure_stage'] = 'encoder_process'
    child = _shared_runtime_process(soren, proc_root, 'encoder', pids['encoder'])
    probe['failure_stage'] = 'encoder_parent'
    if child['parent'] != runner['pid']:
        raise ValueError('unrelated encoder')
    shared = [runner]
    if child['kind'] == 'adapter':
        probe['failure_stage'] = 'pipeline_receipt'
        native_pid = _shared_runtime_pipeline(docich_root, child, boot_id)
        probe['failure_stage'] = 'native_encoder_process'
        native = _shared_runtime_process(soren, proc_root, 'encoder', native_pid)
        if native['kind'] != 'native':
            raise ValueError('native encoder missing')
        probe['failure_stage'] = 'guardian_process'
        guardian = _shared_runtime_process(soren, proc_root, 'guardian', native['parent'])
        probe['failure_stage'] = 'guardian_parent'
        if guardian['parent'] != child['pid']:
            raise ValueError('unrelated guardian')
        shared.extend([child, guardian])
    else:
        native = child
    records = {'shared_stream': shared, 'encoder': [native], 'audio': [audio]}
    all_pids = [row['pid'] for rows in records.values() for row in rows]
    probe['failure_stage'] = 'distinct_processes'
    if len(set(all_pids)) != len(all_pids):
        raise ValueError('overlapping runtime identities')
    return records


def _collect_shared_runtime_pids(soren=Path('/home/ubuntu/soren'), proc_root=Path('/proc'),
                                 docich_root=PROD_ROOT):
    """Fixed producer chain only; never enumerate processes or emit argv."""
    roles = {role: {'count': 0, 'pids': [], 'entries': []} for role in SHARED_RUNTIME_ROLES}
    boot_id = _shared_runtime_boot_id(proc_root)
    section = {'schema_version': 2, 'scan_complete': False, 'boot_id': boot_id, 'roles': roles,
               'failure_stage': 'boot_identity'}
    try:
        if boot_id is None:
            return section
        soren = Path(soren).absolute()
        section['failure_stage'] = 'producer_sources'
        pids = _shared_runtime_sources(soren)
        entries = _shared_runtime_chain(soren, proc_root, docich_root, pids, boot_id, section)
        section['failure_stage'] = 'source_recheck'
        if pids != _shared_runtime_sources(soren):
            return section
        section['failure_stage'] = 'identity_recheck'
        again = _shared_runtime_chain(soren, proc_root, docich_root, pids, boot_id)
        if entries != again or boot_id != _shared_runtime_boot_id(proc_root):
            return section
        for role, rows in entries.items():
            clean = [{'pid': row['pid'], 'start_ticks': row['start_ticks']}
                     for row in sorted(rows, key=lambda row: row['pid'])]
            roles[role] = {'count': len(clean), 'pids': [row['pid'] for row in clean], 'entries': clean}
        section['scan_complete'] = True
        section.pop('failure_stage')
    except _SharedRuntimeSourceError as exc:
        section['failure_source'] = exc.source
        section['failure_reason'] = exc.reason
    except (OSError, ValueError, TypeError, OverflowError, RecursionError, IndexError):
        pass
    return section


def _shared_runtime_pid_set(section):
    """Validate the whole v2 receipt before comparing nonempty required roles."""
    if (not isinstance(section, dict)
            or set(section) != {'schema_version', 'boot_id', 'scan_complete', 'roles'}
            or type(section.get('schema_version')) is not int or section['schema_version'] != 2
            or section.get('scan_complete') is not True):
        return None
    boot = section.get('boot_id')
    if not isinstance(boot, str) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot):
        return None
    roles = section.get('roles')
    if not isinstance(roles, dict) or set(roles) != set(SHARED_RUNTIME_ROLES):
        return None
    result, seen = {}, set()
    for role in SHARED_RUNTIME_ROLES:
        data = roles[role]
        if not isinstance(data, dict) or set(data) != {'count', 'pids', 'entries'}:
            return None
        count, pids, entries = data['count'], data['pids'], data['entries']
        if (type(count) is not int or not 1 <= count <= SHARED_RUNTIME_MAX_PIDS
                or not isinstance(pids, list) or not isinstance(entries, list)
                or len(pids) != count or len(entries) != count):
            return None
        values, projected = set(), []
        for item in entries:
            if not isinstance(item, dict) or set(item) not in (
                    {'pid', 'start_ticks'}, {'pid', 'start_ticks', 'started_at'}):
                return None
            pid, ticks = item['pid'], item['start_ticks']
            if (type(pid) is not int or not 1 < pid <= 2**31-1 or pid in seen
                    or type(ticks) is not int or not 0 < ticks <= 2**63-1):
                return None
            started = item.get('started_at')
            if 'started_at' in item and (type(started) is not int or not 0 < started <= 2**63-1):
                return None
            seen.add(pid)
            projected.append(pid)
            values.add((boot, pid, ticks, started))
        if (any(type(pid) is not int for pid in pids)
                or len(set(pids)) != count or set(pids) != set(projected)):
            return None
        result[role] = values
    return result


def compare_shared_runtime_pids(before, after):
    """Fail-closed before/after comparison of shared runtime PID sets.

    Accepts either full diagnostics dicts (with a ``shared_runtime_pids`` key)
    or the section itself. Returns a dict with ``match`` (bool), the list of
    mismatched roles, and a short ``reason``. Unknown/partial/missing sides
    never count as a match.
    """
    if isinstance(before, dict) and "shared_runtime_pids" in before:
        before = before["shared_runtime_pids"]
    if isinstance(after, dict) and "shared_runtime_pids" in after:
        after = after["shared_runtime_pids"]
    before_set = _shared_runtime_pid_set(before)
    after_set = _shared_runtime_pid_set(after)
    if before_set is None or after_set is None:
        return {
            "match": False,
            "mismatched_roles": list(SHARED_RUNTIME_ROLES),
            "reason": "unavailable_or_partial",
        }
    mismatched = []
    for role in SHARED_RUNTIME_ROLES:
        if before_set[role] != after_set[role]:
            mismatched.append(role)
    if mismatched:
        return {
            "match": False,
            "mismatched_roles": mismatched,
            "reason": "pid_set_changed",
        }
    return {"match": True, "mismatched_roles": [], "reason": "ok"}


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
    meta = _collect_meta(soren, now)
    workers = _collect_workers(soren, now)
    queues = _collect_queues(soren, now)
    ai = _collect_ai(soren, now)
    improvement = _collect_improvement(soren, now)
    corners = _collect_programs(_program_state_dir(), soren, now)
    hanjuku_resume = _collect_hanjuku_resume(_program_state_dir())
    title_sync = _collect_stream_title_sync(
        soren, now, meta.get("soren_gitlink_sha"), meta.get("soren_code")
    )
    payload = {
        "status": _severity(workers, queues, ai, improvement, corners, hanjuku_resume),
        "meta": meta,
        "stream_title_sync": title_sync,
        "twitch_oauth": _collect_twitch_oauth(now),
        "stream_title_attempts": _collect_stream_title_attempts(
            soren, _program_state_dir(), now, title_sync
        ),
        "tracked_drift": _collect_tracked_drift(PROD_ROOT),
        # The ring lives in the runtime user's home. Resolve it with the same
        # rule the sampler uses (the passwd entry), not the checkout location
        # and not a config/state_dir value (#2578: reboot-persistent evidence).
        "host_metrics": _collect_host_metrics(_host_metrics_home(), now),
        "workers": workers,
        "lifecycle_pause": _collect_lifecycle_pause(soren, now, workers),
        "supervisor_identity": _collect_supervisor_identity(soren, meta.get("soren_gitlink_sha")),
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
        "prediction": _prediction_projection(corners, now),
        "hanjuku_resume": hanjuku_resume,
        "hanjuku_tactical": _collect_hanjuku_tactical(_program_state_dir(), time.time()),
        "hanjuku_projection_recovery": _collect_hanjuku_projection_recovery(_program_state_dir()),
        "tmux_servers": _collect_tmux_servers(),
        "pulse_sink_inputs": _collect_pulse_sink_inputs(),
        "pulse_source_outputs": _collect_pulse_source_outputs(),
        "encoder": _collect_encoder(_program_state_dir(), soren, now),
        "corner_bgm": _collect_corner_bgm(soren, time.time()),
        "resolver_daemon": _collect_resolver_daemon(),
        "nethack_history": _collect_nethack_history(_program_state_dir(), now),
        "nethack_xlog": _collect_nethack_xlog(now),
        "nethack_agent": _collect_nethack_agent_log(_program_state_dir(), now),
        "nethack_manual_log": _collect_nethack_manual_log(_program_state_dir(), now),
        "nethack_boundary": _collect_nethack_boundary(_program_state_dir(), now),
        "runtime_presentation": _collect_runtime_presentation(_program_state_dir(), now),
        "program_admission_evidence": _collect_program_admission_evidence(
            _program_state_dir(), soren, now),
        "nethack_rotation_evidence": _collect_nethack_rotation_evidence(
            _program_state_dir(), now, player=_nethack_evidence_player()),
        "nethack_panes": _collect_nethack_panes(_program_state_dir(), now),
        "nethack_tiles": _collect_nethack_tiles(_program_state_dir(), now),
        "market_paper": _collect_market_paper(_program_state_dir(), now),
        "webui": _collect_webui(),
        "cpu_profile": _collect_cpu_profile(now),
        "browser_processes": _collect_browser_processes(),
        "audio_priority": _collect_audio_priority(),
        "audio_gaps": _collect_audio_gaps(now),
        "shared_runtime_pids": _collect_shared_runtime_pids(soren),
        "storage_breakdown": _collect_storage_breakdown(soren, PROD_ROOT),
        "opencode_session_attribution": _collect_opencode_session_attribution(
            Path(soren).parent / ".local" / "share" / "opencode" / "opencode.db",
            now,
        ),
        "opencode_retention": _collect_opencode_retention(soren, now),
        "storage_artifacts": _collect_tmp_shared_objects(now),
        "soren91_drop_profile": _collect_soren91_drop_profile(soren),
        "cc_translation_adapter": _collect_cc_translation_adapter(),
    }
    retention = payload["opencode_retention"]
    attempt = retention["attempt"]
    if payload["status"] == "ok" and attempt.get("present") and (
        not attempt.get("readable") or attempt.get("stale")
        or attempt.get("status") in {"failed", "deferred", "gate_timeout", "unknown"}
        or retention["timer"].get("active") is not True
    ):
        payload["status"] = "warn"
    text = _diagnostics_budget(payload)
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
