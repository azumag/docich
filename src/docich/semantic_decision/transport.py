"""Bounded HTTPS transport shared by fixed semantic decision routes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

if __package__:
    from .contracts import (
        MAX_REQUEST_BYTES,
        MAX_RESPONSE_BYTES,
        RouteProfile,
        MAX_TIMEOUT_MS,
        SemanticDecisionError,
        TransportResult,
    )
    from .routes import resolve_route
    from .validator import strict_loads
else:  # pragma: no cover - exercised only by the isolated HTTP child.
    from contracts import (  # type: ignore[no-redef]
        MAX_REQUEST_BYTES,
        MAX_RESPONSE_BYTES,
        RouteProfile,
        MAX_TIMEOUT_MS,
        SemanticDecisionError,
        TransportResult,
    )
    from routes import resolve_route  # type: ignore[no-redef]
    from validator import strict_loads  # type: ignore[no-redef]


_CHILD_STATUSES = frozenset(
    {
        "ok",
        "auth_error",
        "rate_limited",
        "overloaded",
        "server_error",
        "http_error",
        "redirect_forbidden",
        "network_error",
        "timeout",
        "invalid_response",
        "input_limit",
    }
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Provider redirects are a contract error, never an implicit hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_status(code: int) -> str:
    if code in (301, 302, 303, 307, 308):
        return "redirect_forbidden"
    if code in (401, 403):
        return "auth_error"
    if code == 429:
        return "rate_limited"
    if code == 529:
        return "overloaded"
    if 500 <= code <= 599:
        return "server_error"
    return "http_error"


def _retry_after(headers: Any) -> int:
    try:
        value = int(headers.get("Retry-After", "0"))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(300, value))


def http_worker(request: Mapping[str, Any], profile: RouteProfile, timeout_s: float) -> dict[str, Any]:
    """Perform one request and return only sanitized status/data."""

    try:
        raw_request = json.dumps(
            request, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(raw_request) > MAX_REQUEST_BYTES:
            return {"status": "input_limit"}
        key = os.environ.get(profile.credential_env, "")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        req = urllib.request.Request(
            profile.endpoint,
            data=raw_request,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with opener.open(req, timeout=timeout_s) as response:
            if response.status != 200:
                return {
                    "status": _http_status(response.status),
                    "retry_after": _retry_after(response.headers),
                }
            raw_response = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw_response) > MAX_RESPONSE_BYTES:
            return {"status": "invalid_response"}
        data = strict_loads(raw_response)
        if not isinstance(data, Mapping):
            return {"status": "invalid_response"}
        return {"status": "ok", "data": dict(data)}
    except urllib.error.HTTPError as exc:
        try:
            return {
                "status": _http_status(exc.code),
                "retry_after": _retry_after(exc.headers),
            }
        finally:
            exc.close()
    except (TimeoutError, socket.timeout):
        return {"status": "timeout"}
    except urllib.error.URLError as exc:
        return {
            "status": "timeout" if isinstance(exc.reason, TimeoutError) else "network_error"
        }
    except (SemanticDecisionError, TypeError, ValueError, UnicodeError):
        return {"status": "invalid_response"}
    except Exception:
        return {"status": "network_error"}


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=1)
    except (subprocess.TimeoutExpired, OSError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            process.communicate()
        except OSError:
            pass


def bounded_process(
    argv: list[str],
    *,
    data: bytes,
    timeout_s: float,
    env: Mapping[str, str],
) -> bytes:
    """Run a detached child with a hard wall bound and guaranteed reap."""

    if timeout_s <= 0:
        raise subprocess.TimeoutExpired(argv, timeout_s)
    process: subprocess.Popen[bytes] | None = None
    completed = False
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(env),
            start_new_session=True,
        )
        out, _ = process.communicate(data, timeout=timeout_s)
        if process.returncode != 0:
            raise ValueError("transport child failed")
        completed = True
        return out
    finally:
        if process is not None and not completed:
            _kill_and_reap(process)


def _fixed_profile(profile: RouteProfile) -> bool:
    try:
        return resolve_route({"DOCICH_SEMANTIC_BACKEND": "jev", "DOCICH_JEV_ROUTE": profile.name}) == profile
    except SemanticDecisionError:
        return False


def request_once(
    request: Mapping[str, Any],
    profile: RouteProfile,
    credential: str,
    timeout_s: float,
    *,
    runner: Callable[..., bytes] = bounded_process,
    clock: Callable[[], float] = time.monotonic,
) -> TransportResult:
    """Send exactly one bounded request; no automatic retry is performed."""

    if not _fixed_profile(profile):
        return TransportResult("invalid_config", attempted=False)
    if not isinstance(credential, str) or not credential:
        return TransportResult("missing_key", attempted=False)
    if len(credential) > 4096 or not credential.isascii() or any(
        char.isspace() or ord(char) < 33 for char in credential
    ):
        return TransportResult("invalid_config", attempted=False)
    try:
        raw_request = json.dumps(
            request, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return TransportResult("input_limit", attempted=False)
    if len(raw_request) > MAX_REQUEST_BYTES:
        return TransportResult("input_limit", attempted=False)
    if (
        type(timeout_s) not in (int, float)
        or timeout_s <= 0
        or timeout_s > MAX_TIMEOUT_MS / 1000
    ):
        return TransportResult("invalid_config", attempted=False)
    started = clock()
    argv = [
        sys.executable,
        # Ignore Python path configuration while retaining the fixed module
        # directory so the sibling contract modules can be imported.
        "-E",
        str(Path(__file__).resolve()),
        "--http-worker",
        profile.name,
        f"{timeout_s:.6f}",
    ]
    child_env = {"LANG": "C.UTF-8", profile.credential_env: credential}
    try:
        remaining = timeout_s - (clock() - started)
        raw_result = runner(
            argv,
            data=raw_request,
            timeout_s=max(0.001, remaining),
            env=child_env,
        )
        if len(raw_result) > MAX_RESPONSE_BYTES:
            return TransportResult("invalid_response", attempted=True)
        parsed = strict_loads(raw_result)
        if not isinstance(parsed, Mapping) or parsed.get("status") not in _CHILD_STATUSES:
            return TransportResult("invalid_response", attempted=True)
        status = parsed["status"]
        if status == "ok":
            data = parsed.get("data")
            if not isinstance(data, Mapping):
                return TransportResult("invalid_response", attempted=True)
            return TransportResult("ok", attempted=True, data=dict(data))
        retry_after = parsed.get("retry_after", 0)
        if type(retry_after) is not int or not 0 <= retry_after <= 300:
            retry_after = 0
        return TransportResult(status, attempted=True, retry_after=retry_after)
    except subprocess.TimeoutExpired:
        return TransportResult("timeout", attempted=True)
    except (SemanticDecisionError, TypeError, ValueError, UnicodeError):
        return TransportResult("invalid_response", attempted=True)
    except Exception:
        return TransportResult("network_error", attempted=True)


def _main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] != "--http-worker":
        return 1
    try:
        profile = resolve_route({"DOCICH_JEV_ROUTE": sys.argv[2]})
        timeout_s = float(sys.argv[3])
        raw_request = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw_request) > MAX_REQUEST_BYTES:
            result = {"status": "input_limit"}
        else:
            request = strict_loads(raw_request)
            if not isinstance(request, Mapping):
                result = {"status": "invalid_response"}
            else:
                result = http_worker(request, profile, timeout_s)
    except Exception:
        result = {"status": "invalid_response"}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - child process entrypoint.
    raise SystemExit(_main())
