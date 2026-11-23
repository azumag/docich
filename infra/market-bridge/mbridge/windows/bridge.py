"""Minimal, loopback-only HTTP bridge (stdlib http.server).

Exactly five routes exist.  There is no proxy, no arbitrary URL, no order or
account endpoint.  Every /v1 request requires the market-scoped bridge key.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from ..common.auth import verify_headers
from ..common.contract import encode_candidate, encode_quote, symbol_ok
from ..common.timeutil import Caller, DeadlineExceeded

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
ROUTES = frozenset({
    "/v1/health", "/v1/readiness", "/v1/snapshot", "/v1/scanner", "/v1/subscriptions",
})
GET_ROUTES = ROUTES - {"/v1/subscriptions"}
MAX_BODY_BYTES = 64 * 1024


class BridgeServer:
    def __init__(self, *, market: str, adapter: Any, secret: bytes,
                 host: str = "127.0.0.1", port: int = 0,
                 request_deadline_s: float = 3.0, clock=time.time,
                 caller: Caller | None = None) -> None:
        if host not in LOOPBACK_HOSTS:
            raise ValueError("bridge must bind loopback only")
        if market not in ("stocks", "fx"):
            raise ValueError("invalid_market")
        if len(secret) != 32:
            raise ValueError("invalid_bridge_key")
        self.market = market
        self.adapter = adapter
        self.secret = secret
        self.host = host
        self.port = port
        self.deadline = request_deadline_s
        self.clock = clock
        self._caller = caller or Caller(workers=4, name="mbridge-http")
        self._owns_caller = caller is None
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._httpd is None:
            raise RuntimeError("bridge not started")
        return self._httpd.server_address[0], self._httpd.server_address[1]

    def start(self) -> "BridgeServer":
        """Serve in a background thread; tests use a loopback ephemeral port."""
        self._httpd = self._build()
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        kwargs={"poll_interval": 0.05}, daemon=True,
                                        name="mbridge-httpd")
        self._thread.start()
        return self

    def serve(self) -> None:
        """Serve in the current thread (production entry point)."""
        self._httpd = self._build()
        self._httpd.serve_forever(poll_interval=0.05)

    def _build(self) -> ThreadingHTTPServer:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                return  # never log headers, bodies or credentials

            def do_GET(self) -> None:
                self._dispatch("GET")

            def do_PUT(self) -> None:
                self._dispatch("PUT")

            def do_POST(self) -> None:
                self._dispatch("POST")

            def do_DELETE(self) -> None:
                self._dispatch("DELETE")

            def _send(self, code: int, payload: dict) -> None:
                body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _dispatch(self, method: str) -> None:
                path = urlsplit(self.path).path
                ok, reason = verify_headers(self.headers, secret=outer.secret, now=outer.clock())
                if not ok:
                    self._send(401, {"status": "unauthorized", "reason": reason})
                    return
                if path not in ROUTES:
                    self._send(404, {"status": "not_found", "reason": "unknown_route"})
                    return
                if (method == "GET" and path in GET_ROUTES) or (method == "PUT" and path == "/v1/subscriptions"):
                    pass
                else:
                    self._send(405, {"status": "method_not_allowed", "reason": "method_not_allowed"})
                    return
                try:
                    if path == "/v1/health":
                        self._send(200, {"status": "alive", "market": outer.market})
                        return
                    if path == "/v1/readiness":
                        reading = outer._read()
                        self._send(200, {"status": reading.status, "reason": reading.reason,
                                         "market_state": reading.market_state})
                        return
                    if path == "/v1/snapshot":
                        reading = outer._read()
                        snap = outer._snapshot(reading)
                        if reading.status == "unavailable":
                            self._send(503, {"status": "unavailable", "reason": reading.reason})
                            return
                        self._send(200, snap)
                        return
                    if path == "/v1/scanner":
                        if outer.market != "stocks":
                            self._send(404, {"status": "not_found", "reason": "unknown_route"})
                            return
                        reading = outer._read()
                        if reading.status == "unavailable":
                            self._send(503, {"status": "unavailable", "reason": reading.reason})
                            return
                        self._send(200, {"status": "ok", "generation": outer.adapter.generation,
                                         "candidates": [encode_candidate(c) for c in reading.candidates]})
                        return
                    self._put_subscriptions()
                except DeadlineExceeded:
                    self._send(503, {"status": "unavailable", "reason": "adapter_timeout"})
                except (ValueError, TypeError):
                    self._send(400, {"status": "invalid_request", "reason": "invalid_request"})
                except Exception:
                    # Upstream exception text may contain broker details.
                    self._send(503, {"status": "unavailable", "reason": "upstream_error"})

            def _put_subscriptions(self) -> None:
                length = self.headers.get("Content-Length", "0")
                try:
                    size = int(length)
                except (TypeError, ValueError):
                    raise ValueError("invalid_length")
                if size <= 0 or size > MAX_BODY_BYTES:
                    raise ValueError("invalid_length")
                data = json.loads(self.rfile.read(size).decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("invalid_subscriptions")
                symbols = data.get("symbols")
                if not isinstance(symbols, list) or len(symbols) > 50 or len(set(symbols)) != len(symbols):
                    raise ValueError("invalid_subscriptions")
                if outer.market == "stocks" and not all(symbol_ok(s, "stocks") for s in symbols):
                    raise ValueError("invalid_subscriptions")
                if outer.market == "fx" and not all(symbol_ok(s, "fx") for s in symbols):
                    raise ValueError("invalid_subscriptions")
                outer.adapter.set_subscriptions(tuple(symbols))
                self._send(200, {"status": "ok", "count": len(symbols)})

        httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        httpd.daemon_threads = True
        return httpd

    def _read(self):
        return self._caller.call(lambda: self.adapter.read(self.clock()), timeout_s=self.deadline)

    def _snapshot(self, reading) -> dict:
        return {
            "market": self.market,
            "source": getattr(self.adapter, "source", ""),
            "source_epoch": getattr(self.adapter, "source_epoch", ""),
            "generation": int(getattr(self.adapter, "generation", 0)),
            "sequence": int(reading.sequence),
            "status": reading.status, "reason": reading.reason,
            "market_state": reading.market_state,
            "source_ts": reading.source_ts, "received_ts": reading.received_ts,
            "quotes": [encode_quote(q) for q in reading.quotes],
            "candidates": [],
        }

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            if self._thread is not None:
                self._thread.join(timeout=2)
            self._httpd = None
            self._thread = None
        if self._owns_caller:
            self._caller.close(wait=False)
