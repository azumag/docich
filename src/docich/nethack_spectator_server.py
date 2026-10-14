"""Loopback-only, latest-frame HTTP surface for the NetHack presentation.

This module is a library only. It does not install or start a system service;
the generation-scoped tile presentation supervisor owns it when tiles mode is
explicitly selected.
"""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

from .nethack_spectator import render_live_shell
from .nethack_spectator_live import (
    MAX_FRAME_JSON_BYTES,
    NethackSpectatorLiveError,
    SnapshotStore,
    encode_snapshot_json,
)

MAX_REQUEST_BYTES = 65_536
MAX_HTML_BYTES = 131_072


class _SingleRequestServer(HTTPServer):
    request_queue_size = 1
    allow_reuse_address = False
    daemon_threads = False


class NethackSpectatorFrameServer:
    """Serve a fixed shell and the current sanitized frame on a reserved socket."""

    def __init__(
        self,
        *,
        snapshots: SnapshotStore,
        presentation_epoch: str,
        window_title: str = "NetHack",
        poll_interval_ms: int = 500,
        stale_after_ms: int = 3000,
        now_monotonic=None,
    ) -> None:
        if not isinstance(presentation_epoch, str) or not re.fullmatch(
            r"[A-Za-z0-9._-]{1,96}", presentation_epoch
        ):
            raise ValueError("presentation_epoch is invalid")
        if type(stale_after_ms) is not int or not 1000 <= stale_after_ms <= 10_000:
            raise ValueError("stale_after_ms must be between 1000 and 10000")
        self.snapshots = snapshots
        self.presentation_epoch = presentation_epoch
        initial = self.snapshots.latest()
        if initial.presentation_epoch != presentation_epoch:
            raise ValueError("snapshot store epoch does not match the server")
        self.stale_after_ms = stale_after_ms
        self._now_monotonic = now_monotonic
        self._html = render_live_shell(
            runtime_id=initial.runtime.runtime_id,
            generation=initial.runtime.generation,
            presentation_epoch=presentation_epoch,
            window_title=window_title,
            poll_interval_ms=poll_interval_ms,
            stale_after_ms=stale_after_ms,
        ).encode("utf-8")
        if len(self._html) > MAX_HTML_BYTES:
            raise ValueError("NetHack frame shell exceeds its size bound")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "DocichFrameServer/1"
            sys_version = ""

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(2.0)

            def log_message(self, _format: str, *args) -> None:
                # Never log request contents or client supplied headers.
                return

            def _reply(
                self,
                status: int,
                content_type: str,
                body: bytes,
                *,
                head_only: bool = False,
            ) -> None:
                if len(body) > MAX_FRAME_JSON_BYTES and content_type == "application/json; charset=utf-8":
                    status = 503
                    body = b'{"schema_version":1,"state":"unavailable","reason":"frame_too_large"}'
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Connection", "close")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                    "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
                    "base-uri 'none'; frame-ancestors 'none'",
                )
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)
                self.close_connection = True

            def _guard_request(self) -> bool:
                if self.client_address[0] != "127.0.0.1":
                    self._reply(403, "text/plain; charset=utf-8", b"forbidden")
                    return False
                expected_host = f"127.0.0.1:{owner.port}"
                if self.headers.get("Host", "") != expected_host:
                    self._reply(403, "text/plain; charset=utf-8", b"forbidden")
                    return False
                origin = self.headers.get("Origin")
                if origin is not None and origin != f"http://{expected_host}":
                    self._reply(403, "text/plain; charset=utf-8", b"forbidden")
                    return False
                length = self.headers.get("Content-Length")
                if length not in (None, "0") or self.headers.get("Transfer-Encoding"):
                    self._reply(413, "text/plain; charset=utf-8", b"request body rejected")
                    return False
                return True

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                if not self._guard_request():
                    return
                if len(self.path.encode("utf-8", errors="replace")) > MAX_REQUEST_BYTES:
                    self._reply(414, "text/plain; charset=utf-8", b"request target too long")
                    return
                parsed = urlsplit(self.path)
                if parsed.query or parsed.fragment or parsed.netloc:
                    self._reply(404, "text/plain; charset=utf-8", b"not found")
                    return
                if parsed.path == "/":
                    self._reply(
                        200,
                        "text/html; charset=utf-8",
                        owner._html,
                        head_only=self.command == "HEAD",
                    )
                    return
                if parsed.path == "/frame":
                    try:
                        body = encode_snapshot_json(
                            owner.snapshots.latest(),
                            now_monotonic=owner._now(),
                            stale_after_ms=owner.stale_after_ms,
                        )
                    except NethackSpectatorLiveError:
                        body = b'{"schema_version":1,"state":"unavailable","frame_kind":"placeholder","reason":"frame_invalid"}'
                        self._reply(503, "application/json; charset=utf-8", body)
                        return
                    self._reply(
                        200,
                        "application/json; charset=utf-8",
                        body,
                        head_only=self.command == "HEAD",
                    )
                    return
                if parsed.path == "/health":
                    payload = owner.snapshots.latest().public_dict(
                        now_monotonic=owner._now(),
                        stale_after_ms=owner.stale_after_ms,
                    )
                    safe = {
                        key: payload[key]
                        for key in (
                            "schema_version",
                            "runtime_id",
                            "generation",
                            "presentation_epoch",
                            "capture_seq",
                            "capture_age_ms",
                            "state",
                            "frame_kind",
                            "reason",
                        )
                    }
                    body = json.dumps(safe, separators=(",", ":")).encode("utf-8")
                    self._reply(
                        200,
                        "application/json; charset=utf-8",
                        body,
                        head_only=self.command == "HEAD",
                    )
                    return
                self._reply(404, "text/plain; charset=utf-8", b"not found")

            def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                self.do_GET()

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                if self._guard_request():
                    self._reply(405, "text/plain; charset=utf-8", b"method not allowed")

            def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                self.do_POST()

            def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                self.do_POST()

        self._server = _SingleRequestServer(("127.0.0.1", 0), Handler)
        self._server.timeout = 2.0
        self.host, self.port = self._server.server_address[:2]
        if self.host != "127.0.0.1":
            self._server.server_close()
            raise OSError("frame server did not bind to IPv4 loopback")

    def _now(self) -> float:
        if self._now_monotonic is None:
            import time

            return time.monotonic()
        return self._now_monotonic()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def serve_forever(self) -> None:
        self._server.serve_forever(poll_interval=0.25)

    def shutdown(self) -> None:
        self._server.shutdown()

    def close(self) -> None:
        self._server.server_close()
