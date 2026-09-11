"""Read-only local HTTP server for the PAPER HTML/canvas dashboard (Issue #198).

Serves the static dashboard page plus allowlisted JSON endpoints built from the
trading state directory and bitbank public market data. It binds to loopback
only, serves GET/HEAD only (POST/others are refused), and never mutates trading
state. The program view shows this page in a browser window sized to
``(0,90,960,540)`` on the stream display; the FFmpeg/x11grab path is unchanged.
"""
from __future__ import annotations

import argparse
import http.server
import ipaddress
import json
import socketserver
import time
from pathlib import Path

from .dashboard_live import LiveMarketSampler
from .dashboard_snapshot import build_dashboard_snapshot

ASSETS = Path(__file__).resolve().parent / "dashboard_assets"
_CSS = "text/css; charset=utf-8"
_JS = "application/javascript; charset=utf-8"
_HTML = "text/html; charset=utf-8"
_JSON = "application/json; charset=utf-8"
_TEXT = "text/plain; charset=utf-8"


class _ReusableServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def make_handler(trading_dir: Path, *, live_sampler=None):
    trading_dir = Path(trading_dir)
    sampler = live_sampler if live_sampler is not None else LiveMarketSampler(trading_dir)

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "docich-paper-dashboard/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep the corner stream clean
            return

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload: object) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._send(200, body, _JSON)

        def _file(self, name: str, ctype: str) -> None:
            try:
                body = (ASSETS / name).read_bytes()
            except OSError:
                return self._send(404, b"not found", _TEXT)
            self._send(200, body, ctype)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._file("index.html", _HTML)
            if path == "/dashboard.css":
                return self._file("dashboard.css", _CSS)
            if path == "/dashboard.js":
                return self._file("dashboard.js", _JS)
            if path == "/api/trading/dashboard":
                try:
                    payload = build_dashboard_snapshot(trading_dir, now=time.time())
                except Exception:
                    payload = {"schema_version": 1, "error": "snapshot unavailable"}
                return self._json(payload)
            if path == "/api/trading/live":
                try:
                    payload = sampler.snapshot(now=time.time())
                except Exception:
                    payload = {
                        "schema_version": 1,
                        "available": False,
                        "error": "live refresh unavailable",
                    }
                return self._json(payload)
            return self._send(404, b"not found", _TEXT)

        def do_HEAD(self):  # noqa: N802
            return self.do_GET()

        def do_POST(self):  # noqa: N802
            return self._send(405, b"read-only", _TEXT)

        do_PUT = do_POST
        do_DELETE = do_POST
        do_PATCH = do_POST

    return Handler


def _require_loopback(host: str) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("dashboard host must be a loopback IP address") from exc
    if not address.is_loopback:
        raise ValueError("dashboard host must be a loopback IP address")


def serve(*, trading_dir: Path, host: str = "127.0.0.1", port: int = 8799) -> None:
    _require_loopback(host)
    handler = make_handler(Path(trading_dir))
    with _ReusableServer((host, port), handler) as httpd:
        httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docich trading dashboard-server")
    parser.add_argument("--state-dir", required=True, help="trading state directory (<state>/trading)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args(argv)
    serve(trading_dir=Path(args.state_dir), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
