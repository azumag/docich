"""Loopback-only read-only runtime for the graphical NetHack spectator."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .game_switch import atomic_write_json
from .nethack_spectator_state import parse_terminal_snapshot, read_run_summary
from .nethack_spectator_ui import HTML_PAGE
from .tmux import Tmux

MARKER_FILENAME = "nethack_spectator.json"


class SpectatorError(RuntimeError):
    """The presentation-only spectator cannot start safely."""


def find_browser() -> str | None:
    """Find one reviewed Chromium/Chrome executable without widening PATH."""
    for name in (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "/snap/bin/chromium",
    ):
        found = shutil.which(name)
        if found:
            return found
    return None


class SnapshotSource:
    def __init__(self, *, tmux_session: str, target: str, state_dir: Path):
        self.tmux = Tmux(tmux_session)
        self.target = target
        self.state_dir = state_dir

    def snapshot(self) -> dict[str, Any]:
        text = self.tmux.capture_pane_checked(self.target)
        return {
            "snapshot": parse_terminal_snapshot(text),
            "run": read_run_summary(self.state_dir),
            "captured_at": dt.datetime.now(dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }


class _Handler(BaseHTTPRequestHandler):
    server_version = "DocichNetHackSpectator/1"

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib API name
        if self.path in {"/", "/index.html"}:
            self._send(
                HTTPStatus.OK,
                HTML_PAGE.encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if self.path == "/healthz":
            body = json.dumps(
                {"ok": True, "runtime_id": self.server.runtime_id},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")
            return
        if self.path == "/api/snapshot":
            try:
                payload = self.server.source.snapshot()
            except Exception as exc:
                body = json.dumps(
                    {
                        "error": "snapshot-unavailable",
                        "detail": str(exc)[:160],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                self._send(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    body,
                    "application/json; charset=utf-8",
                )
                return
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")
            return
        self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")

    def log_message(self, format: str, *args: object) -> None:
        return


class SpectatorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, source: SnapshotSource, runtime_id: str):
        super().__init__(address, _Handler)
        self.source = source
        self.runtime_id = runtime_id


def _browser_command(
    browser: str,
    url: str,
    profile: Path,
    width: int,
    height: int,
) -> list[str]:
    profile.mkdir(parents=True, exist_ok=True)
    return [
        browser,
        f"--app={url}",
        f"--window-size={width},{height}",
        "--window-position=0,0",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-scrollbars",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-features=Translate,BackForwardCache",
        f"--user-data-dir={profile}",
    ]


def _viewer_command(args: argparse.Namespace, url: str) -> list[str]:
    browser = find_browser()
    if browser is None:
        raise SpectatorError("chromium/chrome が見つかりません")
    native_width = max(960, args.width)
    native_height = max(540, args.height)
    browser_cmd = _browser_command(
        browser,
        url,
        args.runtime_dir / "nethack-spectator-profile",
        native_width,
        native_height,
    )
    if args.width > 0 and args.height > 0:
        return [
            sys.executable,
            str(Path(__file__).resolve().with_name("presentation.py")),
            "--display",
            args.display,
            "--title",
            f"docich-present-{args.runtime_id}",
            "--x",
            str(args.x),
            "--y",
            str(args.y),
            "--width",
            str(args.width),
            "--height",
            str(args.height),
            "--",
            *browser_cmd,
        ]
    return browser_cmd


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich.nethack_spectator")
    parser.add_argument("--tmux-session", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--runtime-id", required=True)
    parser.add_argument("--display", required=True)
    parser.add_argument("--x", type=int, default=0)
    parser.add_argument("--y", type=int, default=0)
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    return parser


def _stop_child(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.runtime_dir.mkdir(parents=True, exist_ok=True)
    source = SnapshotSource(
        tmux_session=args.tmux_session,
        target=args.target,
        state_dir=args.state_dir,
    )
    # Validate the target before presenting a graphical window.  A foreign or
    # vanished tmux target must never yield a misleadingly healthy dashboard.
    source.snapshot()

    server = SpectatorHTTPServer(("127.0.0.1", 0), source, args.runtime_id)
    port = int(server.server_address[1])
    url = f"http://127.0.0.1:{port}/"
    atomic_write_json(
        args.runtime_dir / MARKER_FILENAME,
        {
            "schema_version": 1,
            "runtime_id": args.runtime_id,
            "host": "127.0.0.1",
            "port": port,
            "target": args.target,
            "started_at": dt.datetime.now(dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        },
    )
    thread = threading.Thread(
        target=server.serve_forever,
        name="nethack-spectator-http",
        daemon=True,
    )
    thread.start()

    child: subprocess.Popen[Any] | None = None
    stopping = False

    def _stop(_sig, _frame):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _stop)

    try:
        command = _viewer_command(args, url)
        env = dict(os.environ)
        env["DISPLAY"] = args.display
        env.pop("TMUX", None)
        child = subprocess.Popen(command, env=env)
        while not stopping and child.poll() is None:
            time.sleep(0.2)
        if stopping:
            return 0
        return child.returncode or 1
    finally:
        server.shutdown()
        server.server_close()
        _stop_child(child)
        try:
            (args.runtime_dir / MARKER_FILENAME).unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
