"""Read-only dashboard for the news PAPER lane (N07).

Two fixed GET routes:

* ``/`` — server-rendered HTML (all values escaped; no scripts, no links);
* ``/api/news-paper/status`` — the :func:`status.build_status` allow-list as
  compact JSON.

The server is loopback-only (validated host), refuses every non-GET/HEAD
request with 405, and sends ``Cache-Control: no-store`` plus a restrictive
CSP. It never mutates trading state: every request builds a fresh read-only
projection. The worker's raw material (article bodies, prompts, model output,
URLs, secrets) is not part of the projection and cannot be requested.
"""

from __future__ import annotations

import argparse
import html
import json
import signal
import socket
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Mapping

from .status import LIVE_ELIGIBLE, MODE, build_status

DASHBOARD_SCHEMA_VERSION = 1
STATUS_ROUTE = "/api/news-paper/status"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_CSP = (
    "default-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'; img-src 'none'"
)
_FALLBACK_HTML = (
    "<!doctype html><html lang=\"ja\" translate=\"no\"><head>"
    "<meta charset=\"utf-8\"><meta name=\"google\" content=\"notranslate\">"
    "<title>news paper</title></head><body><p>状態を取得できませんでした</p></body></html>"
)


class DashboardConfigError(ValueError):
    """Invalid dashboard configuration; ``code`` is a fixed error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DashboardConfig:
    state_dir: Path
    run_id: str
    scenario_id: str
    account_id: str
    host: str = "127.0.0.1"
    port: int = 0


def _require(block: Mapping[str, object], key: str) -> object:
    if key not in block:
        raise DashboardConfigError("VALUE_OUT_OF_RANGE")
    return block[key]


def load_dashboard_config(path: str | Path) -> DashboardConfig | None:
    """Parse the ``[dashboard]`` section; absent/false means disabled."""

    import tomllib

    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise DashboardConfigError("VALUE_OUT_OF_RANGE") from exc
    dashboard = raw.get("dashboard")
    if dashboard is None:
        return None
    if not isinstance(dashboard, dict):
        raise DashboardConfigError("TYPE_INVALID")
    allowed = frozenset({"enabled", "host", "port", "state_dir", "run_id", "scenario_id", "account_id"})
    if set(dashboard) - allowed:
        raise DashboardConfigError("UNKNOWN_KEY")
    enabled = dashboard.get("enabled")
    if enabled is None or enabled is False:
        return None
    if enabled is not True:
        raise DashboardConfigError("TYPE_INVALID")
    state_dir = Path(str(_require(dashboard, "state_dir")))
    if not state_dir.is_absolute() or ".." in state_dir.parts:
        raise DashboardConfigError("STATE_PATH_FORBIDDEN")
    host = dashboard.get("host", "127.0.0.1")
    if not isinstance(host, str) or host not in _LOOPBACK_HOSTS:
        # The dashboard is never exposed beyond an approved owner route.
        raise DashboardConfigError("VALUE_OUT_OF_RANGE")
    port = dashboard.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise DashboardConfigError("VALUE_OUT_OF_RANGE")
    for key in ("run_id", "scenario_id", "account_id"):
        value = _require(dashboard, key)
        if not isinstance(value, str) or not value:
            raise DashboardConfigError("TYPE_INVALID")
    return DashboardConfig(
        state_dir=state_dir,
        run_id=str(dashboard["run_id"]),
        scenario_id=str(dashboard["scenario_id"]),
        account_id=str(dashboard["account_id"]),
        host=host,
        port=port,
    )


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _show(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _row(label: str, value: object) -> str:
    return (
        f"<dt>{html.escape(label)}</dt>"
        f"<dd>{html.escape(_show(value))}</dd>"
    )


def render_html(status: Mapping[str, object]) -> str:
    """Render the allow-listed projection as escaped, script-free HTML."""

    receipts = status.get("receipts") if isinstance(status.get("receipts"), dict) else {}
    orders = status.get("orders") if isinstance(status.get("orders"), dict) else {}
    fills = status.get("fills") if isinstance(status.get("fills"), dict) else {}
    valuation = status.get("valuation") if isinstance(status.get("valuation"), dict) else {}
    ages = status.get("ages") if isinstance(status.get("ages"), dict) else {}
    pending = status.get("pending_work") if isinstance(status.get("pending_work"), dict) else {}
    worker = status.get("worker") if isinstance(status.get("worker"), dict) else {}
    rows = [
        _row("state", status.get("state")),
        _row("mode", status.get("mode")),
        _row("live_eligible", status.get("live_eligible")),
        _row("run", status.get("run_id")),
        _row("scenario", status.get("scenario_id")),
        _row("account", status.get("account_id")),
        _row("phase", status.get("phase")),
        _row("entries_disabled", status.get("entries_disabled")),
        _row("heartbeat_age_seconds", ages.get("heartbeat_seconds")),
        _row("market_age_seconds", ages.get("market_seconds")),
        _row("news_age_seconds", ages.get("news_seconds")),
        _row("classifier_age_seconds", ages.get("classifier_seconds")),
        _row("accepted", receipts.get("counts", {}).get("accepted") if isinstance(receipts.get("counts"), dict) else None),
        _row("held", receipts.get("counts", {}).get("held") if isinstance(receipts.get("counts"), dict) else None),
        _row("rejected", receipts.get("counts", {}).get("rejected") if isinstance(receipts.get("counts"), dict) else None),
        _row("pending_buy", orders.get("pending_buy")),
        _row("pending_sell", orders.get("pending_sell")),
        _row("fills", fills.get("count")),
        _row("valuation_as_of", valuation.get("as_of")),
        _row("valuation_equity", valuation.get("total_equity")),
        _row("valuation_stale", valuation.get("stale")),
        _row("pending_total", pending.get("total")),
        _row("entries_disabled (worker)", worker.get("entries_disabled")),
        _row("worker_status", worker.get("status")),
        _row("last_error_code", worker.get("last_error_code")),
    ]
    return (
        "<!doctype html><html lang=\"ja\" translate=\"no\"><head>"
        "<meta charset=\"utf-8\"><meta name=\"google\" content=\"notranslate\">"
        "<title>news paper — read-only status</title></head><body>"
        "<h1>news paper — read-only status</h1>"
        "<dl>" + "".join(rows) + "</dl>"
        "<p>GET only. Values are an allow-listed projection; article bodies, "
        "prompts, model output, URLs and secrets are never shown.</p>"
        "</body></html>"
    )


def _status_payload(status: Mapping[str, object]) -> bytes:
    return json.dumps(status, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    status_provider: Callable[[], Mapping[str, object]] = staticmethod(lambda: {"state": "disabled"})

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._respond(include_body=True)

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._respond(include_body=False)

    def _respond(self, *, include_body: bool) -> None:
        try:
            if self.path == "/":
                status = self.status_provider()
                body = render_html(status).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            elif self.path == STATUS_ROUTE:
                body = _status_payload(self.status_provider())
                content_type = "application/json"
            else:
                body = b'{"state":"not_found"}'
                self._send(404, "application/json", body, include_body=include_body)
                return
        except Exception:  # noqa: BLE001 - never leak exception text
            body = _FALLBACK_HTML.encode("utf-8")
            self._send(503, "text/html; charset=utf-8", body, include_body=include_body)
            return
        self._send(200, content_type, body, include_body=include_body)

    def _send(self, code: int, content_type: str, body: bytes, *, include_body: bool) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def _read_only(self) -> None:
        body = b'{"state":"method_not_allowed"}'
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = _read_only  # noqa: N815 - BaseHTTPRequestHandler API
    do_PUT = _read_only  # noqa: N815
    do_DELETE = _read_only  # noqa: N815
    do_PATCH = _read_only  # noqa: N815
    do_OPTIONS = _read_only  # noqa: N815
    do_TRACE = _read_only  # noqa: N815
    do_CONNECT = _read_only  # noqa: N815

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - API
        return


def make_server(
    config: DashboardConfig, *, status_provider: Callable[[], Mapping[str, object]] | None = None
) -> HTTPServer:
    """Create the loopback HTTP server (not started)."""

    if config.host not in _LOOPBACK_HOSTS:
        raise DashboardConfigError("VALUE_OUT_OF_RANGE")

    def provider() -> Mapping[str, object]:
        return build_status(
            config.state_dir, config.run_id, config.scenario_id, config.account_id
        )

    handler = type(
        "Handler",
        (_Handler,),
        {"status_provider": staticmethod(provider if status_provider is None else status_provider)},
    )
    server_class = HTTPServer
    if ":" in config.host:
        server_class = type("HTTPServer6", (HTTPServer,), {"address_family": socket.AF_INET6})
    return server_class((config.host, config.port), handler)


def serve(
    config: DashboardConfig, *, status_provider: Callable[[], Mapping[str, object]] | None = None
) -> None:
    """Serve until SIGINT/SIGTERM (blocking)."""

    server = make_server(config, status_provider=status_provider)
    stopping = {"stop": False}

    def _stop(_signum: int, _frame: object) -> None:
        stopping["stop"] = True

    previous = signal.signal(signal.SIGTERM, _stop) if hasattr(signal, "SIGTERM") else None
    try:
        while not stopping["stop"]:
            server.timeout = 0.5
            server.handle_request()
    finally:
        server.server_close()
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docich-news-paper-dashboard")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_dashboard_config(args.config)
    except DashboardConfigError as exc:
        print(json.dumps({"state": "config_invalid", "reason_code": exc.code}, ensure_ascii=False))
        return 2
    if config is None:
        print(json.dumps({"state": "disabled", "mode": MODE, "live_eligible": LIVE_ELIGIBLE}))
        return 0
    print(json.dumps({"state": "serving", "host": config.host, "port": config.port}, ensure_ascii=False))
    try:
        serve(config)
    except OSError:
        print(json.dumps({"state": "failed", "reason_code": "STORE_UNAVAILABLE"}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
