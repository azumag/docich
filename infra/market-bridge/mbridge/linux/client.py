"""Authenticated, deadline-bounded HTTP client for one market bridge."""
from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from ..common.auth import build_headers
from ..common.timeutil import Caller, DeadlineExceeded

MAX_RESPONSE_BYTES = 256 * 1024


class BridgeError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BridgeClient:
    def __init__(self, *, url: str, secret: bytes,
                 connect_timeout_s: float = 1.0, deadline_s: float = 3.0,
                 opener=None, clock=time.time, caller: Caller | None = None,
                 allow_insecure_http: bool = False) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("invalid_bridge_url")
        if parsed.scheme == "http" and not allow_insecure_http:
            raise ValueError("bridge_url_must_be_https")
        if not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("invalid_bridge_url")
        if len(secret) != 32:
            raise ValueError("invalid_bridge_key")
        self.base = url.rstrip("/")
        self.secret = secret
        self.connect_timeout_s = connect_timeout_s
        self.deadline_s = deadline_s
        self._opener = opener or urllib.request.build_opener()
        self.clock = clock
        self._caller = caller or Caller(workers=2, name="mbridge-client")
        self._owns_caller = caller is None

    def close(self) -> None:
        if self._owns_caller:
            self._caller.close(wait=False)

    def get(self, path: str) -> dict:
        return self._request("GET", path)

    def put(self, path: str, body: dict) -> dict:
        return self._request("PUT", path, body)

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        if path not in ("/v1/health", "/v1/readiness", "/v1/snapshot",
                        "/v1/scanner", "/v1/subscriptions"):
            raise BridgeError("unknown_route")
        now = int(self.clock())
        headers = build_headers(self.secret, ts=now, nonce=secrets.token_hex(8))
        headers["Accept"] = "application/json"
        payload = None
        if body is not None:
            payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=payload,
                                         headers=headers, method=method)

        def do() -> dict:
            with self._opener.open(request, timeout=self.connect_timeout_s) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise BridgeError("payload_too_large")
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise BridgeError("invalid_schema") from exc
            if not isinstance(parsed, dict):
                raise BridgeError("invalid_schema")
            return parsed

        try:
            return self._caller.call(do, timeout_s=self.deadline_s)
        except DeadlineExceeded as exc:
            raise BridgeError("adapter_timeout") from exc
        except urllib.error.HTTPError as exc:
            raise BridgeError(_http_reason(exc)) from exc
        except urllib.error.URLError as exc:
            raise BridgeError("bridge_unreachable") from exc
        except OSError as exc:
            raise BridgeError("bridge_unreachable") from exc
        except BridgeError:
            raise
        except Exception as exc:
            raise BridgeError("upstream_error") from exc


def _http_reason(exc: urllib.error.HTTPError) -> str:
    if exc.code == 401:
        return "auth_failed"
    if exc.code == 404:
        return "unknown_route"
    if exc.code == 405:
        return "method_not_allowed"
    try:
        body = json.loads(exc.read().decode("utf-8"))
        reason = body.get("reason") if isinstance(body, dict) else None
        if isinstance(reason, str) and len(reason) <= 64:
            return reason
    except Exception:
        pass
    return "upstream_unavailable"
