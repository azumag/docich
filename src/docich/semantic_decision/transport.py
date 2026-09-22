"""One bounded stdlib HTTP path for direct and explicit Vercel choice requests.

POSIX/main-thread only, matching the existing classifier's cancellation boundary.
No consumer imports, state writes, retries, fallback, logging or runtime activation.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

# The isolated child uses this reviewed file, not cwd/PYTHONPATH/site customisation.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from docich.semantic_decision.routes import resolve_route
from docich.semantic_decision.validator import (
    MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, dumps, encode_request, strict_json,
    validate_response,
)

_ERRORS = frozenset({"auth_error", "rate_limited", "overloaded", "server_error",
                     "network_error", "timeout", "invalid_response", "http_error"})


def http_status(code: int) -> str:
    if code in (401, 403):
        return "auth_error"
    if code == 429:
        return "rate_limited"
    if code == 529:
        return "overloaded"
    return "server_error" if 500 <= code <= 599 else "http_error"


def _valid_key(key) -> bool:
    return (type(key) is str and 0 < len(key) <= 4096
            and all(33 <= ord(char) < 127 for char in key))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_worker(request, route: str, timeout: float, env) -> dict:
    """The sole HTTP implementation; never return exception text/headers/bodies."""
    try:
        profile = resolve_route(route)
        payload = encode_request(request, profile)
        key = env.get(profile.credential_env, "")
        if not _valid_key(key):
            return {"status": "auth_error"}
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        req = urllib.request.Request(
            profile.endpoint, data=payload, method="POST",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        )
        with opener.open(req, timeout=timeout) as response:
            if response.status != 200:
                return {"status": http_status(response.status)}
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            return {"status": "invalid_response"}
        return {"status": "ok", "data": validate_response(strict_json(raw), request, profile)}
    except urllib.error.HTTPError as exc:
        try:
            retry = int(exc.headers.get("Retry-After", "0"))
        except (ValueError, TypeError, AttributeError):
            retry = 0
        status = http_status(exc.code)
        exc.close()
        return {"status": status, "retry_after": max(0, min(300, retry))}
    except (TimeoutError, socket.timeout):
        return {"status": "timeout"}
    except urllib.error.URLError as exc:
        return {"status": "timeout" if isinstance(exc.reason, TimeoutError) else "network_error"}
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        return {"status": "invalid_response"}
    except Exception:
        return {"status": "network_error"}


def _kill_and_reap(process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.communicate()


def _bounded_process(argv, *, data: bytes, timeout: float, env) -> bytes:
    """Defer TERM/INT while acquiring/reaping a detached process group."""
    if os.name != "posix":
        raise ValueError("unsupported_platform")
    started = time.monotonic()
    process, cancelled, completed = None, None, False
    handlers = {}

    def cancel(signum, _frame):
        nonlocal cancelled
        cancelled = signum

    try:
        # Fails before spawn outside the main thread; do not silently drop cleanup.
        for signum in (signal.SIGTERM, signal.SIGINT):
            handlers[signum] = signal.signal(signum, cancel)
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, start_new_session=True,
        )
        while True:
            if cancelled is not None:
                raise SystemExit(128 + cancelled)
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                out, _ = process.communicate(data, timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                data = None  # communicate retains the pending input on retry.
                continue
            if process.returncode:
                raise ValueError("process_error")
            completed = True
            return out
    finally:
        try:
            if process is not None and (not completed or cancelled is not None):
                _kill_and_reap(process)
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
        if cancelled is not None:
            raise SystemExit(128 + cancelled)


def request_once(request, *, route=None, env=None, timeout_ms: int | None = None) -> dict:
    """Internal client seam, not a new production CLI or enable/disable control.

    Unset route uses DOCICH_JEV_ROUTE, then direct; timeout uses the canonical
    DOCICH_JEV_TIMEOUT_MS or 1500ms. Endpoint/model overrides and
    unselected keys are never read. Missing/invalid configuration means no child.
    The existing consumer remains responsible for its heuristic fallback.
    """
    started = time.monotonic()
    profile = None

    def finish(status, *, data=None, retry_after=None):
        meta = {"route": profile.name if profile else None,
                "requested_model": profile.requested_model if profile else None,
                "resolved_model": data["model"] if data else None,
                "latency_ms": round((time.monotonic() - started) * 1000, 3),
                "retry_count": 0, "usage": data["usage"] if data else None,
                "cost_usd": None}
        result = {"status": status, "meta": meta}
        if data is not None:
            result["data"] = data
        if retry_after is not None:
            result["retry_after"] = retry_after
        return result

    env = os.environ if env is None else env
    try:
        profile = resolve_route(env.get("DOCICH_JEV_ROUTE", "direct") if route is None else route)
        if os.name != "posix" or threading.current_thread() is not threading.main_thread():
            return finish("invalid_config")
        if timeout_ms is None:
            configured_timeout = env.get("DOCICH_JEV_TIMEOUT_MS", "1500")
            if type(configured_timeout) is not str or not configured_timeout.isascii() or not configured_timeout.isdecimal():
                return finish("invalid_config")
            try:
                timeout_ms = int(configured_timeout)
            except ValueError:
                return finish("invalid_config")
        if type(timeout_ms) is not int or not 50 <= timeout_ms <= 5000:
            return finish("invalid_config")
        key = env.get(profile.credential_env, "")
        if not _valid_key(key):
            return finish("missing_key")
        try:
            payload = encode_request(request, profile)
            snapshot = strict_json(payload)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return finish("input_limit")
        remaining = timeout_ms / 1000 - (time.monotonic() - started)
        if remaining <= 0:
            return finish("timeout")
        raw = _bounded_process(
            [sys.executable, "-I", str(Path(__file__).resolve()), "--http-worker",
             profile.name, str(remaining)],
            data=payload, timeout=remaining,
            env={profile.credential_env: key, "LANG": "C.UTF-8"},
        )
        if time.monotonic() - started > timeout_ms / 1000:
            return finish("timeout")
        if len(raw) > MAX_RESPONSE_BYTES:
            return finish("invalid_response")
        result = strict_json(raw)
        if type(result) is not dict or result.get("status") not in {"ok", *_ERRORS}:
            return finish("invalid_response")
        if result["status"] == "ok":
            return finish("ok", data=validate_response(result.get("data"), snapshot, profile))
        retry = result.get("retry_after", 0)
        if type(retry) is not int or not 0 <= retry <= 300:
            return finish("invalid_response")
        return finish(result["status"], retry_after=retry)
    except subprocess.TimeoutExpired:
        return finish("timeout")
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        return finish("invalid_config" if profile is None else "invalid_response")
    except Exception:
        return finish("network_error")


def _main() -> int:
    try:
        if len(sys.argv) != 4 or sys.argv[1] != "--http-worker":
            return 1
        route, timeout = sys.argv[2], float(sys.argv[3])
        if not 0 < timeout <= 5:
            return 1
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            return 1
        result = _http_worker(strict_json(raw), route, timeout, os.environ)
        output = dumps(result).encode("utf-8")
        if len(output) > MAX_RESPONSE_BYTES:
            output = b'{"status":"invalid_response"}'
        sys.stdout.buffer.write(output)
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
