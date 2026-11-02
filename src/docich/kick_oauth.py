"""Keep the broadcaster's Kick user token alive by refreshing it in Soren's .env.

Kick user tokens are short-lived and the title fan-out helper
(``lib/stream_title_sync.py`` in soviet_now) reads ``KICK_ACCESS_TOKEN`` and
``KICK_BROADCASTER_USER_ID`` from ``.env`` on every run, so a timer can keep a
valid token in place without touching the Soren code path.  This module uses
the Authorization Code Grant with PKCE (Kick requires ``code_challenge``):
one manual consent stores a refresh token, and a timer then swaps the
short-lived access token into Soren's ``.env`` before it expires.

No token, secret, authorization code or PKCE verifier is ever printed or
logged; status output only carries fixed codes, the broadcaster id, scope
coverage and expiry.

Subcommands (see ``docs/operations/kick-oauth-refresh.md``):

* ``authorize-url`` prints the consent URL and remembers its ``state`` and
  PKCE ``code_verifier``.
* ``exchange`` reads the redirected ``http://localhost/?code=...`` URL on
  stdin and stores the new access/refresh tokens.  When
  ``KICK_BROADCASTER_USER_ID`` is still unset it is learned from the
  authenticated principal's own channel read and stored at the same time.
* ``refresh`` (timer) refreshes when the access token is missing, inactive,
  or close to expiry.
* ``status`` prints the last recorded status.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import fcntl
import hashlib
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

AUTHORIZE_URL = "https://id.kick.com/oauth/authorize"
TOKEN_URL = "https://id.kick.com/oauth/token"
INTROSPECT_URL = "https://id.kick.com/oauth/token/introspect"
CHANNELS_URL = "https://api.kick.com/public/v1/channels"
REDIRECT_URI = "http://localhost"

# The title fan-out PATCHes stream_title (channel:write) after a principal
# read (channel:read).  Ask for both scopes in one consent.
REQUIRED_SCOPES = (
    "channel:read",
    "channel:write",
)
# Every key the refreshed access token is written to.
MANAGED_KEYS = (
    "KICK_ACCESS_TOKEN",
)
REFRESH_KEY = "KICK_OAUTH_REFRESH_TOKEN"
SECRET_KEY = "KICK_CLIENT_SECRET"
CLIENT_KEY = "KICK_CLIENT_ID"
BROADCASTER_KEY = "KICK_BROADCASTER_USER_ID"

REFRESH_MARGIN_SEC = 3600
STATE_TTL_SEC = 900
ENV_MAX_BYTES = 1 << 20
PKCE_VERIFIER_BYTES = 48

EXIT_OK = 0
EXIT_TRANSIENT = 1
EXIT_REAUTH = 3

Http = Callable[..., "tuple[int, dict]"]


class KickOAuthError(RuntimeError):
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
        raise KickOAuthError("network_error") from exc
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
        raise KickOAuthError("env_unreadable", EXIT_REAUTH)
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
            raise KickOAuthError("token_format_rejected")
    lock_path = path.with_name(".env.kick-oauth.lock")
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.is_symlink() or not path.is_file():
            raise KickOAuthError("env_unreadable", EXIT_REAUTH)
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
        tmp = path.with_name(f".env.kick-oauth.{os.getpid()}.tmp")
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


# --- Kick calls ---------------------------------------------------------

def _introspect(token: str, http: Http, now: float) -> dict:
    """Secret-free facts from ``/oauth/token/introspect``."""
    status, body = http(
        "POST", INTROSPECT_URL, headers={"Authorization": f"Bearer {token}"})
    if status == 401:
        return {"active": False, "reason": "token_invalid"}
    if status != 200:
        raise KickOAuthError("validate_unavailable")
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    scopes = str(data.get("scope") or "").split()
    exp = data.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, str)):
        expires_in = None
    else:
        try:
            expires_in = max(0, int(exp) - int(now))
        except ValueError:
            expires_in = None
    return {
        "active": data.get("active") is True,
        "reason": None if data.get("active") is True else "token_inactive",
        "token_type": str(data.get("token_type") or ""),
        "scopes": scopes,
        "expires_in": expires_in,
    }


def _principal_channel(token: str, http: Http) -> dict:
    """The authenticated principal's own channel row (channel:read)."""
    status, body = http(
        "GET", CHANNELS_URL, headers={"Authorization": f"Bearer {token}"})
    if status == 401:
        return {"reachable": True, "broadcaster_user_id": None, "unauthorized": True}
    if status != 200:
        raise KickOAuthError("validate_unavailable")
    rows = body.get("data")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return {"reachable": True, "broadcaster_user_id": None}
    user_id = rows[0].get("broadcaster_user_id")
    return {
        "reachable": True,
        "broadcaster_user_id": str(user_id) if isinstance(user_id, int) else None,
    }


def validate(token: str, broadcaster_id: str, http: Http = _http,
             now: float | None = None) -> dict:
    """Secret-free facts about ``token``; ``valid`` means usable for Soren.

    The broadcaster id is verified (or learned, when unconfigured) through the
    authenticated principal's own channel read, not through introspection,
    because the introspect response carries no user identity.
    """
    now = time.time() if now is None else now
    info = _introspect(token, http, now)
    if not info["active"]:
        return {"valid": False, "reason": info["reason"]}
    if info["token_type"] != "user":
        return {"valid": False, "reason": "not_user_token"}
    missing = sorted(set(REQUIRED_SCOPES) - set(info["scopes"]))
    if missing:
        return {"valid": False, "reason": "missing_scopes",
                "missing_scopes": missing, "expires_in": info["expires_in"]}
    principal = _principal_channel(token, http)
    learned = principal["broadcaster_user_id"]
    if principal.get("unauthorized") or learned is None:
        return {"valid": False, "reason": "principal_unreadable"}
    if broadcaster_id and learned != broadcaster_id:
        return {"valid": False, "reason": "not_broadcaster",
                "broadcaster_user_id": learned}
    return {
        "valid": True,
        "reason": None,
        "broadcaster_user_id": learned,
        "missing_scopes": [],
        "expires_in": info["expires_in"],
    }


def _token_request(form: dict, http: Http) -> tuple[str, str]:
    status, body = http("POST", TOKEN_URL, form=form)
    if status in (400, 401, 403):
        raise KickOAuthError("reauth_required", EXIT_REAUTH)
    if status != 200:
        raise KickOAuthError("token_endpoint_unavailable")
    access = body.get("access_token")
    refresh = body.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise KickOAuthError("token_response_invalid")
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
        "broadcaster_user_id": facts.get("broadcaster_user_id") or None,
        "missing_scopes": facts.get("missing_scopes") or [],
        "expires_at": _now_iso(now + expires_in) if isinstance(expires_in, int) else None,
    }


# --- operations ---------------------------------------------------------

def _credentials(env: dict[str, str]) -> tuple[str, str, str]:
    client, secret, broadcaster = (env.get(CLIENT_KEY, ""), env.get(SECRET_KEY, ""),
                                   env.get(BROADCASTER_KEY, ""))
    return client, secret, broadcaster


def _store(env_path: Path, access: str, refresh: str, facts: dict,
           broadcaster_id: str) -> None:
    if not facts["valid"]:
        raise KickOAuthError(f"new_token_{facts['reason']}", EXIT_REAUTH)
    updates = {key: access for key in MANAGED_KEYS}
    if refresh:
        updates[REFRESH_KEY] = refresh
    if not broadcaster_id:
        updates[BROADCASTER_KEY] = facts["broadcaster_user_id"]
    write_env(env_path, updates)


def refresh(env_path: Path, *, http: Http | None = None, now: float | None = None,
            margin: int = REFRESH_MARGIN_SEC) -> dict:
    http = http or _http
    now = time.time() if now is None else now
    env = read_env(env_path)
    client, secret, broadcaster = _credentials(env)
    refresh_token = env.get(REFRESH_KEY, "")
    if not (client and secret and refresh_token):
        return _status(now, "not_configured")
    current = env.get(MANAGED_KEYS[0], "")
    if current:
        try:
            facts = validate(current, broadcaster, http, now)
        except KickOAuthError:
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
    facts = validate(access, broadcaster, http, now)
    _store(env_path, access, new_refresh, facts, broadcaster)
    return _status(now, "ok", action="refreshed", facts=facts)


def authorize_url(env_path: Path, state_path: Path, *, now: float | None = None) -> str:
    now = time.time() if now is None else now
    client = read_env(env_path).get(CLIENT_KEY, "")
    if not client:
        raise KickOAuthError("client_id_missing", EXIT_REAUTH)
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(PKCE_VERIFIER_BYTES)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"state": state, "code_verifier": verifier, "created_at": now}, handle)
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(REQUIRED_SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def exchange(env_path: Path, state_path: Path, redirected_url: str, *,
             http: Http | None = None, now: float | None = None) -> dict:
    http = http or _http
    now = time.time() if now is None else now
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(redirected_url.strip()).query)
    if query.get("error"):
        raise KickOAuthError("consent_denied", EXIT_REAUTH)
    code = (query.get("code") or [""])[0]
    state = (query.get("state") or [""])[0]
    try:
        saved = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KickOAuthError("state_missing", EXIT_REAUTH) from None
    verifier = saved.get("code_verifier")
    if (not code or not isinstance(verifier, str) or not verifier
            or not hmac.compare_digest(state, str(saved.get("state", "")))
            or now - float(saved.get("created_at", 0)) > STATE_TTL_SEC):
        raise KickOAuthError("state_mismatch_or_expired", EXIT_REAUTH)
    Path(state_path).unlink(missing_ok=True)
    env = read_env(env_path)
    client, secret, broadcaster = _credentials(env)
    if not (client and secret):
        raise KickOAuthError("client_credentials_missing", EXIT_REAUTH)
    access, refresh_token = _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client,
        "client_secret": secret,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }, http)
    if not refresh_token:
        raise KickOAuthError("refresh_token_missing", EXIT_REAUTH)
    facts = validate(access, broadcaster, http, now)
    _store(env_path, access, refresh_token, facts, broadcaster)
    return _status(now, "ok", action="authorized", facts=facts)


def _paths(config: Path) -> tuple[Path, Path, Path]:
    from .config import load_global
    from .trading.soren_output import resolve_soren_root

    g = load_global(Path(__file__).resolve().parents[2], config)
    state_dir = Path(g.state_dir) / "kick_oauth"
    return resolve_soren_root(g) / ".env", state_dir / "status.json", state_dir / "authorize_state.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docich-kick-oauth")
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
        except KickOAuthError as exc:
            print(json.dumps({"status": exc.code}))
            return exc.exit_code
        return EXIT_OK
    try:
        if args.command == "exchange":
            result = exchange(env_path, state_path, sys.stdin.read(), now=now)
        else:
            result = refresh(env_path, now=now)
        code = EXIT_OK
    except KickOAuthError as exc:
        result, code = _status(now, exc.code), exc.exit_code
    except OSError:
        result, code = _status(now, "io_error"), EXIT_TRANSIENT
    write_status(status_path, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
