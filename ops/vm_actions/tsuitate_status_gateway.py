#!/usr/bin/env python3
"""Installed-only read path for one Tsuitate background run receipt.

The operation never executes repository code and never forwards PlayerView,
move/decision history, tokens, URLs, or raw remote errors.  A game id is
published only after the requested run is terminal and has exactly one game.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OPERATION = "tsuitate_status_query"
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
RUN_ID_RE = re.compile(r"hourly-[0-9]{10}\Z")
TEXT_RE = re.compile(r"[A-Za-z0-9_.:+/-]{1,128}\Z")
CONTROL_HOST_RE = re.compile(r"docich-tsuitate-bot\.[a-z0-9-]+\.workers\.dev\Z")
PREFIX = b"beta-control-v1\nPOST\n/beta-control\n"
CONFIG = Path("/etc/azumag-vm-ops.json")
PRODUCTION = Path("/home/ubuntu/docich")
ENV_FILE = Path("/home/ubuntu/.config/docich/webui.env")
INSTALL = Path("/usr/local/libexec/azumag-vm-ops")
MAX_ENV = 64 * 1024
MAX_RESPONSE = 8192
# git status on the production checkout maps the pack/index and needs headroom
# above 256 MiB; 512 MiB keeps the query bounded while allowing that read.
MAX_ADDRESS_SPACE = 512 * 1024 * 1024
INSTALLED_FILES = ("gateway_entry.py", "tsuitate_status_gateway.py")
CAPABILITY_KEYS = ("DOCICH_BETA_CONTROL_SECRET", "DOCICH_BETA_CONTROL_URL")
FORBIDDEN_KEYS = ("DOCICH_WEBUI_TOKEN", "DOCICH_WEBUI_READONLY_TOKEN")
STATES = {"stopped", "queued", "playing", "draining", "finished", "queue_timeout", "paused"}
ERRORS = {
    "token_not_configured", "run_locked", "run_mismatch", "control_not_configured",
    "control_authentication_failed", "control_timeout", "control_unavailable",
    "terminal_unconfirmed", "recovery_not_available", "recovery_checkpoint_invalid",
    "terminal_result_unavailable", "terminal_storage_failure",
}
OUTCOMES = {"win", "loss", "draw", "unknown"}
REASONS = {
    "normal", "checkmate", "stalemate", "resign", "timeout", "foul_limit",
    "repetition", "draw", "aborted", "disconnect", "transport_error",
    "protocol_error", "storage_error", "interrupted", "no_move", "unknown",
}
CONFIDENCE = {"verified", "unknown"}
TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z\Z")


class QueryError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise QueryError("redirect_rejected")


def parse_command(command: str) -> tuple[str, str]:
    if not isinstance(command, str) or len(command) > 192:
        raise QueryError("invalid_command")
    parts = command.split(" ")
    if (len(parts) != 5 or parts[:3] != [OPERATION, "docich", "production"]
            or not SHA_RE.fullmatch(parts[3]) or not RUN_ID_RE.fullmatch(parts[4])):
        raise QueryError("invalid_command")
    return parts[3], parts[4]


def _read_regular(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise QueryError("source_unavailable") from exc
    try:
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_size > limit
                or meta.st_uid not in {0, os.getuid()} or meta.st_mode & 0o022):
            raise QueryError("untrusted_source")
        raw = b""
        while len(raw) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(raw)))
            if not chunk:
                break
            raw += chunk
        if len(raw) > limit:
            raise QueryError("source_too_large")
        return raw
    finally:
        os.close(fd)


def _read_control_environment() -> tuple[str, str]:
    try:
        text = _read_regular(ENV_FILE, MAX_ENV).decode("utf-8", "strict")
    except UnicodeError as exc:
        raise QueryError("invalid_environment") from exc
    allowed = set(CAPABILITY_KEYS + FORBIDDEN_KEYS)
    values: dict[str, str] = {}
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("export "):
            raw = raw[7:].lstrip()
        key, separator, value = raw.partition("=")
        if not separator or key not in allowed:
            continue
        value = value.strip()
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise QueryError("invalid_environment")
            value = value[1:-1]
        if not value or any(ch in value for ch in "\r\n\x00"):
            raise QueryError("invalid_environment")
        values[key] = value
    secret = values.get("DOCICH_BETA_CONTROL_SECRET", "")
    url = values.get("DOCICH_BETA_CONTROL_URL", "")
    if not 32 <= len(secret.encode("utf-8")) <= 4096:
        raise QueryError("control_not_configured")
    if secret in {values.get(key) for key in FORBIDDEN_KEYS if values.get(key)}:
        raise QueryError("control_secret_reused")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise QueryError("control_not_configured") from exc
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"} or port not in {None, 443}
            or not CONTROL_HOST_RE.fullmatch(parsed.hostname or "")):
        raise QueryError("control_not_configured")
    return secret, url.rstrip("/")


def _signed_request(url: str, secret: str, run_id: str) -> urllib.request.Request:
    payload = json.dumps({"action": "run", "runId": run_id}, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"), PREFIX + timestamp.encode("ascii") + b"." + payload, hashlib.sha256
    ).hexdigest()
    return urllib.request.Request(url + "/beta-control", data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "X-Beta-Control-Timestamp": timestamp,
        "X-Beta-Control-Signature": "sha256=" + signature,
        "User-Agent": "docich-tsuitate-status-query/1.0",
    })


def _text(value, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not TEXT_RE.fullmatch(value):
        raise QueryError("invalid_status")
    return value


def _project_result(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise QueryError("invalid_status")
    outcome, reason, ended_at = raw.get("outcome"), raw.get("reason"), raw.get("endedAt")
    move_number, confidence = raw.get("moveNumber"), raw.get("resultConfidence")
    if (outcome not in OUTCOMES or reason not in REASONS
            or not isinstance(ended_at, str) or not TIME_RE.fullmatch(ended_at)
            or type(move_number) is not int or not 0 <= move_number <= 100000
            or confidence not in CONFIDENCE):
        raise QueryError("invalid_status")
    return {
        "outcome": outcome,
        "reason": reason,
        "endedAt": ended_at,
        "moveNumber": move_number,
        "resultConfidence": confidence,
    }


def project_status(data, expected_run_id: str) -> dict:
    if not isinstance(data, dict) or data.get("state") not in STATES:
        raise QueryError("invalid_status")
    if data.get("runId") != expected_run_id:
        raise QueryError("run_mismatch")
    game_id = _text(data.get("gameId"), optional=True)
    brain = _text(data.get("brainVersion"), optional=True)
    completed = data.get("completedGames")
    reserved = data.get("reservedGames")
    ready = data.get("readyForNextRun")
    if type(completed) is not int or completed not in {0, 1}:
        raise QueryError("invalid_status")
    if type(reserved) is not int or reserved not in {0, 1} or type(ready) is not bool:
        raise QueryError("invalid_status")
    error = data.get("errorCode") if data.get("errorCode") in ERRORS else None
    terminal = data["state"] == "finished" and completed == 1
    result = _project_result(data.get("terminalResult")) if terminal else None
    if terminal and game_id is None:
        raise QueryError("terminal_identity_missing")
    return {
        "schema": 1,
        "status": "ok",
        "runId": expected_run_id,
        "state": data["state"],
        # Never identify a live/paused match to the GitHub control plane.
        "gameId": game_id if terminal else None,
        "completedGames": completed,
        "reservedGames": reserved,
        "readyForNextRun": ready,
        "brainVersion": brain,
        "errorCode": error,
        "gameResult": result,
    }


def _call_control(secret: str, url: str, run_id: str) -> dict:
    request = _signed_request(url, secret, run_id)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=5) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise QueryError("response_too_large")
        return project_status(json.loads(raw), run_id)
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(MAX_RESPONSE + 1)
            error = json.loads(raw).get("error") if len(raw) <= MAX_RESPONSE else None
        except Exception:
            error = None
        finally:
            exc.close()
        raise QueryError(error if error == "run_mismatch" else "control_unavailable") from None
    except (QueryError, json.JSONDecodeError):
        raise
    except Exception as exc:
        raise QueryError("control_unavailable") from exc


def _verify_installed_source(sha: str) -> None:
    for name in INSTALLED_FILES:
        installed = _read_regular(INSTALL / name, 256 * 1024)
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(PRODUCTION), "-c", "core.hooksPath=/dev/null",
             "show", f"{sha}:ops/vm_actions/{name}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "GIT_OPTIONAL_LOCKS": "0"},
        )
        if (result.returncode != 0 or len(result.stdout) > 256 * 1024
                or not hmac.compare_digest(hashlib.sha256(result.stdout).digest(), hashlib.sha256(installed).digest())):
            raise QueryError("installed_source_mismatch")


@contextmanager
def _operations_lock(core, cfg):
    lock_path = core.state_root(cfg) / "vm-operations.lock"
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags)
    except OSError as exc:
        raise QueryError("gateway_busy") from exc
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
            raise QueryError("unsafe_lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise QueryError("gateway_busy") from exc
        yield
    finally:
        os.close(fd)


def _ready(core, cfg, sha: str) -> None:
    result = core.status_result(cfg, "docich", "production", sha)
    if result.get("status") != "configured" or result.get("sha") != sha:
        raise QueryError("production_unverified")


def query(core, config_path: str, command: str) -> bytes:
    sha, run_id = parse_command(command)
    if Path(config_path) != CONFIG:
        raise QueryError("invalid_configuration")
    cfg = core.load_config(Path(config_path))
    with _operations_lock(core, cfg):
        _ready(core, cfg, sha)
        _verify_installed_source(sha)
        secret, url = _read_control_environment()
        result = _call_control(secret, url, run_id)
        _ready(core, cfg, sha)
    payload = json.dumps(result, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if not 0 < len(payload) <= MAX_RESPONSE:
        raise QueryError("response_too_large")
    return payload


def main(core, config_path: str) -> int:
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        resource.setrlimit(resource.RLIMIT_AS, (MAX_ADDRESS_SPACE, MAX_ADDRESS_SPACE))
        signal.alarm(20)
        os.environ["PATH"] = "/usr/bin:/bin"
        os.environ["GIT_OPTIONAL_LOCKS"] = "0"
        payload = query(core, config_path, os.environ.get("SSH_ORIGINAL_COMMAND", ""))
        signal.alarm(0)
    except Exception:
        print("Tsuitate status query rejected", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
    return 0
