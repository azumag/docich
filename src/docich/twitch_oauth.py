"""Keep the broadcaster's Twitch user token alive by refreshing it in Soren's .env.

Implicit-grant tokens cannot be refreshed and silently expire after ~60 days,
which stopped every title/category update on 2026-10-19.  This module uses the
Authorization Code Grant instead: one manual consent stores a refresh token,
and a timer then swaps the short-lived access token into Soren's ``.env``
before it expires.  Soren's scripts already read ``.env`` on every call.

No token, secret or authorization code is ever printed or logged; status
output only carries fixed codes, the login name, scope coverage and expiry.

Subcommands (see ``docs/operations/twitch-oauth-refresh.md``):

* ``authorize-url`` prints the consent URL and remembers its ``state``.
* ``exchange`` reads the redirected ``http://localhost/?code=...`` URL on
  stdin and stores the new access/refresh tokens.
* ``refresh`` (timer) refreshes when the access token is missing, invalid,
  inconsistent across the managed keys or close to expiry.
* ``status`` prints the last recorded status.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
REDIRECT_URI = "http://localhost"

# Union of what Soren's Twitch features need from the broadcaster token:
# title/category, chat send, clips, polls, predictions and Creator Goals.
REQUIRED_SCOPES = (
    "channel:manage:broadcast",
    "chat:edit",
    "chat:read",
    "user:write:chat",
    "clips:edit",
    "channel:manage:polls",
    "channel:manage:predictions",
    "channel:read:goals",
)
# Every key the refreshed access token is written to.  They were separate
# implicit tokens of the same account; one token with the union of scopes
# replaces them all so no key is left to expire on its own.
MANAGED_KEYS = (
    "TWITCH_BOT_TOKEN",
    "TWITCH_PREDICTIONS_TOKEN",
    "TWITCH_POLLS_TOKEN",
    "TWITCH_CLIP_TOKEN",
)
REFRESH_KEY = "TWITCH_OAUTH_REFRESH_TOKEN"
SECRET_KEY = "TWITCH_CLIENT_SECRET"
CLIENT_KEY = "TWITCH_CLIENT_ID"
BROADCASTER_KEY = "TWITCH_BROADCASTER_ID"

REFRESH_MARGIN_SEC = 3600
STATE_TTL_SEC = 900
ENV_MAX_BYTES = 1 << 20

EXIT_OK = 0
EXIT_TRANSIENT = 1
EXIT_REAUTH = 3

Http = Callable[..., "tuple[int, dict]"]


class TwitchOAuthError(RuntimeError):
    """A fixed, secret-free reason code."""

    def __init__(self, code: str, exit_code: int = EXIT_TRANSIENT):
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


def _http(method: str, url: str, *, headers: dict | None = None,
          form: dict | None = None, timeout: float = 15) -> tuple[int, dict]:
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TwitchOAuthError("network_error") from exc
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        body = {}
    return status, body if isinstance(body, dict) else {}


# --- .env ---------------------------------------------------------------

def _parse_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


def read_env(path: Path) -> dict[str, str]:
    """Literal ``KEY=value`` pairs; the file is never sourced or evaluated."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > ENV_MAX_BYTES:
        raise TwitchOAuthError("env_unreadable", EXIT_REAUTH)
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, raw = line.partition("=")
        if sep and key.strip() and not key.lstrip().startswith("#"):
            values[key.strip()] = _parse_value(raw)
    return values


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Replace (or append) ``updates`` in ``path`` atomically, keeping its mode.

    The file is re-read under the lock right before writing so that unrelated
    edits made since :func:`read_env` are kept.
    """
    path = Path(path)
    for value in updates.values():
        if not value or any(c.isspace() or c in "'\"#$`\\" for c in value):
            raise TwitchOAuthError("token_format_rejected")
    lock_path = path.with_name(".env.twitch-oauth.lock")
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.is_symlink() or not path.is_file():
            raise TwitchOAuthError("env_unreadable", EXIT_REAUTH)
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        pending = dict(updates)
        for index, line in enumerate(lines):
            stripped = line.lstrip()
            export = stripped.startswith("export ")
            body = stripped[7:].lstrip() if export else stripped
            key = body.partition("=")[0].strip()
            if body.partition("=")[1] and key in updates:
                lines[index] = f"{'export ' if export else ''}{key}={updates[key]}\n"
                pending.pop(key, None)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.extend(f"{key}={value}\n" for key, value in pending.items())
        mode = path.stat().st_mode & 0o777
        tmp = path.with_name(f".env.twitch-oauth.{os.getpid()}.tmp")
        tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            with os.fdopen(tfd, "w", encoding="utf-8") as handle:
                handle.writelines(lines)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


# --- Twitch calls -------------------------------------------------------

def validate(token: str, broadcaster_id: str, http: Http = _http) -> dict:
    """Secret-free facts about ``token``; ``valid`` means usable for Soren."""
    status, body = http("GET", VALIDATE_URL, headers={"Authorization": f"OAuth {token}"})
    if status == 401:
        return {"valid": False, "reason": "token_invalid"}
    if status != 200:
        raise TwitchOAuthError("validate_unavailable")
    scopes = body.get("scopes") if isinstance(body.get("scopes"), list) else []
    missing = sorted(set(REQUIRED_SCOPES) - set(scopes))
    owner_ok = str(body.get("user_id") or "") == broadcaster_id
    expires_in = body.get("expires_in")
    return {
        "valid": owner_ok and not missing,
        "reason": None if owner_ok and not missing else ("not_broadcaster" if not owner_ok else "missing_scopes"),
        "login": str(body.get("login") or "")[:64],
        "missing_scopes": missing,
        "expires_in": int(expires_in) if isinstance(expires_in, int) else None,
    }


def _token_request(form: dict, http: Http) -> tuple[str, str]:
    status, body = http("POST", TOKEN_URL, form=form)
    if status in (400, 401, 403):
        raise TwitchOAuthError("reauth_required", EXIT_REAUTH)
    if status != 200:
        raise TwitchOAuthError("token_endpoint_unavailable")
    access = body.get("access_token")
    refresh = body.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise TwitchOAuthError("token_response_invalid")
    return access, refresh if isinstance(refresh, str) else ""


# --- status -------------------------------------------------------------

def _now_iso(now: float) -> str:
    return dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="seconds")


def write_status(path: Path, status: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _status(now: float, outcome: str, *, action: str | None = None,
            facts: dict | None = None) -> dict:
    facts = facts or {}
    expires_in = facts.get("expires_in")
    return {
        "status": outcome,
        "action": action,
        "checked_at": _now_iso(now),
        "login": facts.get("login") or None,
        "missing_scopes": facts.get("missing_scopes") or [],
        "expires_at": _now_iso(now + expires_in) if isinstance(expires_in, int) else None,
    }


# --- operations ---------------------------------------------------------

def _credentials(env: dict[str, str]) -> tuple[str, str, str]:
    client, secret, broadcaster = (env.get(CLIENT_KEY, ""), env.get(SECRET_KEY, ""),
                                   env.get(BROADCASTER_KEY, ""))
    return client, secret, broadcaster


def _store(env_path: Path, access: str, refresh: str, facts: dict) -> None:
    if not facts["valid"]:
        raise TwitchOAuthError(f"new_token_{facts['reason']}", EXIT_REAUTH)
    updates = {key: access for key in MANAGED_KEYS}
    if refresh:
        updates[REFRESH_KEY] = refresh
    write_env(env_path, updates)


def refresh(env_path: Path, *, http: Http | None = None, now: float | None = None,
            margin: int = REFRESH_MARGIN_SEC) -> dict:
    http = http or _http
    now = time.time() if now is None else now
    env = read_env(env_path)
    client, secret, broadcaster = _credentials(env)
    refresh_token = env.get(REFRESH_KEY, "")
    if not (client and secret and broadcaster and refresh_token):
        return _status(now, "not_configured")
    current = env.get(MANAGED_KEYS[0], "").removeprefix("oauth:")
    consistent = all(env.get(key, "").removeprefix("oauth:") == current for key in MANAGED_KEYS)
    if current and consistent:
        try:
            facts = validate(current, broadcaster, http)
        except TwitchOAuthError:
            # Validation being down must not stop a refresh that may be due.
            facts = {"valid": False}
        if facts["valid"] and (facts["expires_in"] or 0) > margin:
            return _status(now, "ok", action="no_change", facts=facts)
    access, new_refresh = _token_request({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client,
        "client_secret": secret,
    }, http)
    facts = validate(access, broadcaster, http)
    _store(env_path, access, new_refresh, facts)
    return _status(now, "ok", action="refreshed", facts=facts)


def authorize_url(env_path: Path, state_path: Path, *, now: float | None = None) -> str:
    now = time.time() if now is None else now
    client = read_env(env_path).get(CLIENT_KEY, "")
    if not client:
        raise TwitchOAuthError("client_id_missing", EXIT_REAUTH)
    state = secrets.token_urlsafe(24)
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"state": state, "created_at": now}, handle)
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(REQUIRED_SCOPES),
        "state": state,
        "force_verify": "true",
    })


def exchange(env_path: Path, state_path: Path, redirected_url: str, *,
             http: Http | None = None, now: float | None = None) -> dict:
    http = http or _http
    now = time.time() if now is None else now
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(redirected_url.strip()).query)
    if query.get("error"):
        raise TwitchOAuthError("consent_denied", EXIT_REAUTH)
    code = (query.get("code") or [""])[0]
    state = (query.get("state") or [""])[0]
    try:
        saved = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise TwitchOAuthError("state_missing", EXIT_REAUTH) from None
    if (not code or not hmac.compare_digest(state, str(saved.get("state", "")))
            or now - float(saved.get("created_at", 0)) > STATE_TTL_SEC):
        raise TwitchOAuthError("state_mismatch_or_expired", EXIT_REAUTH)
    Path(state_path).unlink(missing_ok=True)
    env = read_env(env_path)
    client, secret, broadcaster = _credentials(env)
    if not (client and secret and broadcaster):
        raise TwitchOAuthError("client_credentials_missing", EXIT_REAUTH)
    access, refresh_token = _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client,
        "client_secret": secret,
        "redirect_uri": REDIRECT_URI,
    }, http)
    if not refresh_token:
        raise TwitchOAuthError("refresh_token_missing", EXIT_REAUTH)
    facts = validate(access, broadcaster, http)
    _store(env_path, access, refresh_token, facts)
    return _status(now, "ok", action="authorized", facts=facts)


def _paths(config: Path) -> tuple[Path, Path, Path]:
    from .config import load_global
    from .trading.soren_output import resolve_soren_root

    g = load_global(Path(__file__).resolve().parents[2], config)
    state_dir = Path(g.state_dir) / "twitch_oauth"
    return resolve_soren_root(g) / ".env", state_dir / "status.json", state_dir / "authorize_state.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docich-twitch-oauth")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("command", choices=("authorize-url", "exchange", "refresh", "status"))
    args = parser.parse_args(argv)
    env_path, status_path, state_path = _paths(args.config)
    now = time.time()
    if args.command == "status":
        try:
            print(status_path.read_text(encoding="utf-8").strip())
        except OSError:
            print(json.dumps({"status": "unknown"}))
        return EXIT_OK
    if args.command == "authorize-url":
        try:
            print(authorize_url(env_path, state_path, now=now))
        except TwitchOAuthError as exc:
            print(json.dumps({"status": exc.code}))
            return exc.exit_code
        return EXIT_OK
    try:
        if args.command == "exchange":
            result = exchange(env_path, state_path, sys.stdin.read(), now=now)
        else:
            result = refresh(env_path, now=now)
        code = EXIT_OK
    except TwitchOAuthError as exc:
        result, code = _status(now, exc.code), exc.exit_code
    except OSError:
        result, code = _status(now, "io_error"), EXIT_TRANSIENT
    write_status(status_path, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
