"""Runtime-owned NetHack tile presentation with a one-way TTY fallback.

This process runs inside ``presentation.py``'s private X server.  It owns only
the loopback frame server, browser, and optional read-only xterm for one
generation.  It does not launch NetHack or send game input.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.request import urlopen
from uuid import uuid4

if __package__:
    from .game_switch import GameSwitchStore, atomic_write_json
    from .nethack_spectator_live import (
        ActiveRuntime,
        NethackFrameReader,
        SnapshotStore,
        active_nethack_runtime,
    )
    from .nethack_spectator_server import NethackSpectatorFrameServer
else:  # production invokes this file as a script through presentation.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from docich.game_switch import GameSwitchStore, atomic_write_json
    from docich.nethack_spectator_live import (
        ActiveRuntime,
        NethackFrameReader,
        SnapshotStore,
        active_nethack_runtime,
    )
    from docich.nethack_spectator_server import NethackSpectatorFrameServer


MANIFEST_SCHEMA_VERSION = 1
FAILURE_REASONS = frozenset(
    {
        "server_start_failed",
        "server_stopped",
        "browser_start_failed",
        "browser_exited",
        "browser_window_missing",
        "projection_failed",
        "reader_failed",
        "reader_unavailable",
        "fallback_start_failed",
        "fallback_exited",
        "fallback_window_missing",
        "startup_failed",
    }
)
_RUNTIME_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
_TMUX_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WINDOW_TITLE_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_BROWSER_NAMES = (
    "google-chrome-stable",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "/snap/bin/chromium",
)
_POLL_INTERVAL_S = 0.5
_WINDOW_WAIT_S = 8.0
_READER_GRACE_S = 12.0


def browser_binary(which: Callable[[str], str | None] = shutil.which) -> str | None:
    """Resolve only the reviewed system browser names; config cannot pick argv."""
    for name in _BROWSER_NAMES:
        resolved = which(name)
        if resolved:
            return resolved
    return None


def presentation_window_pattern(window_title: str) -> str:
    """Match the exact owned app title with only known Chromium title suffixes."""
    if not isinstance(window_title, str) or not _WINDOW_TITLE_RE.fullmatch(window_title):
        raise ValueError("window_title is invalid")
    suffix = r"( - (Google Chrome|Chromium( Web Browser)?))?"
    # The allowlist leaves only dot as a POSIX extended-regex metacharacter.
    escaped_title = window_title.replace(".", r"\.")
    return f"^{escaped_title}{suffix}$"


def _proc_start_ticks(pid: int) -> int | None:
    """Linux process identity supplement, omitted on platforms without procfs."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        # comm is parenthesized and may itself contain spaces or ')'.
        fields = raw[raw.rfind(")") + 2 :].split()
        return int(fields[19])  # field 22 (starttime), after pid + comm
    except (OSError, ValueError, IndexError):
        return None


def manifest_matches(
    value: object,
    *,
    runtime: ActiveRuntime,
) -> bool:
    """Check the exact runtime identity before consuming the private manifest."""
    return (
        isinstance(value, dict)
        and type(value.get("schema_version")) is int
        and value.get("schema_version") == MANIFEST_SCHEMA_VERSION
        and value.get("runtime_id") == runtime.runtime_id
        and type(value.get("generation")) is int
        and value.get("generation") == runtime.generation
        and value.get("adapter_session") == runtime.adapter_session
        and value.get("game_window") == runtime.game_window
    )


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class NethackTilesSupervisor:
    """Own one optional browser presentation and its single TTY fallback."""

    def __init__(
        self,
        *,
        state_dir: Path,
        runtime_dir: Path,
        manifest_path: Path,
        presentation_state_path: Path,
        runtime: ActiveRuntime,
        cols: int = 80,
        rows: int = 24,
        font: str = "monospace",
        font_size: int = 18,
        window_title: str | None = None,
        which: Callable[[str], str | None] = shutil.which,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not _RUNTIME_TOKEN_RE.fullmatch(runtime.runtime_id):
            raise ValueError("runtime_id is invalid")
        if type(runtime.generation) is not int or runtime.generation < 1:
            raise ValueError("generation is invalid")
        if not _TMUX_NAME_RE.fullmatch(runtime.adapter_session):
            raise ValueError("adapter_session is invalid")
        if not _TMUX_NAME_RE.fullmatch(runtime.game_window):
            raise ValueError("game_window is invalid")
        if type(cols) is not int or not 1 <= cols <= 80:
            raise ValueError("cols is invalid")
        if type(rows) is not int or not 3 <= rows <= 24:
            raise ValueError("rows is invalid")
        if type(font_size) is not int or not 6 <= font_size <= 48:
            raise ValueError("font_size is invalid")

        self.state_dir = Path(state_dir)
        self.runtime_dir = Path(runtime_dir).resolve()
        self.manifest_path = Path(manifest_path)
        self.presentation_state_path = Path(presentation_state_path)
        if self.manifest_path.resolve().parent != self.runtime_dir:
            raise ValueError("manifest must be inside runtime_dir")
        if self.presentation_state_path.resolve().parent != self.runtime_dir:
            raise ValueError("presentation state must be inside runtime_dir")
        self.runtime = runtime
        self.cols = cols
        self.rows = rows
        self.font = font
        self.font_size = font_size
        self.window_title = window_title or f"docich-present-{runtime.runtime_id}"
        if not _RUNTIME_TOKEN_RE.fullmatch(self.window_title):
            raise ValueError("window_title is invalid")
        self._which = which
        self._popen = popen
        self._clock = clock
        self._sleep = sleep
        self._stop = threading.Event()
        self._reader_stop = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._server_thread: threading.Thread | None = None
        self._reader: NethackFrameReader | None = None
        self._snapshots: SnapshotStore | None = None
        self._server: NethackSpectatorFrameServer | None = None
        self._browser: subprocess.Popen | None = None
        self._tty: subprocess.Popen | None = None
        self._profile_path = self.runtime_dir / "nethack-tiles-profile"
        self._presentation_epoch = f"p-{uuid4().hex}"
        self._previous_manifest = _load_json(self.manifest_path)
        self._status = "starting"
        self._mode = "tiles"
        self._reason: str | None = None
        self._fallback_attempted = False
        self._window_seen_at: float | None = None
        self._active_runtime_seen_at: float | None = None
        self._reader_error = False
        self._write_manifest()

    def _write_manifest(self, *, cleanup_complete: bool = False) -> None:
        payload: dict[str, object] = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "status": self._status,
            "mode": self._mode,
            "runtime_id": self.runtime.runtime_id,
            "generation": self.runtime.generation,
            "adapter_session": self.runtime.adapter_session,
            "game_window": self.runtime.game_window,
            "presentation_epoch": self._presentation_epoch,
            "owner_pid": os.getpid(),
            "owner_start_ticks": _proc_start_ticks(os.getpid()),
            "frame_port": self._server.port if self._server is not None else None,
            "browser_pid": self._browser.pid if self._browser is not None else None,
            "tty_pid": self._tty.pid if self._tty is not None else None,
            "reason": self._reason if self._reason in FAILURE_REASONS else None,
            "updated_at": time.time(),
            "cleanup_complete": cleanup_complete,
        }
        atomic_write_json(self.manifest_path, payload)

    def _load_state(self) -> dict[str, object]:
        store = GameSwitchStore(self.state_dir)
        return store.canonical.load()[0]

    def _reader_loop(self) -> None:
        assert self._reader is not None and self._snapshots is not None
        while not self._reader_stop.wait(_POLL_INTERVAL_S):
            try:
                self._snapshots.publish(self._reader.read_once())
            except Exception:  # sanitized as a single health bit in the manifest
                self._reader_error = True
                return

    def _start_frame_service(self) -> None:
        self._reader = NethackFrameReader(
            expected_runtime=self.runtime,
            state_loader=self._load_state,
            cols=self.cols,
            rows=self.rows,
            presentation_epoch=self._presentation_epoch,
        )
        # Do not wait for canonical readiness here. The coordinator publishes
        # the active generation only after this viewer becomes ready.
        self._snapshots = SnapshotStore(self._reader.read_once())
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="nethack-tiles-reader", daemon=True
        )
        self._reader_thread.start()
        self._server = NethackSpectatorFrameServer(
            snapshots=self._snapshots,
            presentation_epoch=self._presentation_epoch,
            window_title=self.window_title,
        )
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="nethack-tiles-server",
            daemon=True,
        )
        self._server_thread.start()
        deadline = self._clock() + 3.0
        while self._clock() < deadline:
            if not self._server_thread.is_alive():
                raise RuntimeError("server_start_failed")
            try:
                with urlopen(self._server.url + "health", timeout=0.5) as response:
                    health = json.loads(response.read(4096))
                if (
                    response.status == 200
                    and health.get("runtime_id") == self.runtime.runtime_id
                    and health.get("generation") == self.runtime.generation
                    and health.get("presentation_epoch") == self._presentation_epoch
                ):
                    return
            except Exception:
                self._sleep(0.05)
        raise RuntimeError("server_start_failed")

    def _window_id(self) -> str | None:
        xdotool = self._which("xdotool")
        if not xdotool:
            return None
        try:
            found = subprocess.run(
                [
                    xdotool,
                    "search",
                    "--onlyvisible",
                    "--name",
                    presentation_window_pattern(self.window_title),
                ],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if found.returncode != 0 or not found.stdout.strip():
            return None
        windows = found.stdout.split()
        return windows[0] if len(windows) == 1 else None

    def _wait_window(self, process: subprocess.Popen, *, timeout_s: float) -> bool:
        deadline = self._clock() + timeout_s
        while self._clock() < deadline and not self._stop.is_set():
            if process.poll() is not None:
                return False
            if self._window_id() is not None:
                return True
            self._sleep(0.1)
        return False

    @staticmethod
    def _window_wait_failure_reason(process: subprocess.Popen) -> str:
        if process.poll() is not None:
            return "browser_exited"
        return "browser_window_missing"

    def _prepare_profile(self) -> Path:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._profile_path.lstat()
        except FileNotFoundError:
            self._profile_path.mkdir(mode=0o700)
            return self._profile_path
        if not self._profile_path.is_dir() or self._profile_path.is_symlink():
            raise RuntimeError("browser_start_failed")
        previous = self._previous_manifest
        if not manifest_matches(previous, runtime=self.runtime):
            raise RuntimeError("browser_start_failed")
        if previous.get("cleanup_complete") is not True:
            raise RuntimeError("browser_start_failed")
        shutil.rmtree(self._profile_path)
        self._profile_path.mkdir(mode=0o700)
        return self._profile_path

    def _browser_command(self, port: int) -> list[str]:
        browser = browser_binary(self._which)
        if not browser:
            raise RuntimeError("browser_start_failed")
        profile = self._prepare_profile()
        return [
            browser,
            f"--app=http://127.0.0.1:{port}/",
            f"--user-data-dir={profile}",
            "--window-size=960,540",
            "--window-position=0,0",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--disable-extensions",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--hide-scrollbars",
        ]

    def _start_browser(self) -> None:
        assert self._server is not None and self._server_thread is not None
        command = self._browser_command(self._server.port)
        try:
            self._browser = self._popen(
                command,
                env=dict(os.environ),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise RuntimeError("browser_start_failed") from exc
        self._write_manifest()
        if not self._wait_window(self._browser, timeout_s=_WINDOW_WAIT_S):
            raise RuntimeError(self._window_wait_failure_reason(self._browser))
        if not self._server_thread.is_alive():
            raise RuntimeError("server_stopped")

    def _start_tty(self) -> None:
        xterm = self._which("xterm")
        if not xterm:
            raise RuntimeError("fallback_start_failed")
        command = [
            xterm,
            "-fa", self.font,
            "-fs", str(self.font_size),
            "-bg", "black",
            "-fg", "grey90",
            "-geometry", f"{self.cols}x{self.rows}+0+0",
            "-T", self.window_title,
            "-e", "tmux", "attach-session", "-r", "-t", self.runtime.adapter_session,
        ]
        try:
            self._tty = self._popen(
                command,
                env=dict(os.environ),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise RuntimeError("fallback_start_failed") from exc
        self._write_manifest()
        if not self._wait_window(self._tty, timeout_s=_WINDOW_WAIT_S):
            raise RuntimeError("fallback_start_failed")

    @staticmethod
    def _stop_process(process: subprocess.Popen | None) -> bool:
        if process is None:
            return True
        try:
            # start_new_session=True gave this exact Popen a private process
            # group, so its group id remains our ownership boundary even if
            # Chromium's original launcher has already exited.
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            return False
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                return False
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return True
            except OSError:
                return False
            time.sleep(0.05)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return True
            except OSError:
                return False
            time.sleep(0.05)
        return False

    def _stop_frame_service(self) -> bool:
        self._reader_stop.set()
        ok = True
        if self._server is not None:
            try:
                if self._server_thread is not None and self._server_thread.is_alive():
                    self._server.shutdown()
                self._server.close()
            except Exception:
                ok = False
        if self._server_thread is not None:
            self._server_thread.join(timeout=3.0)
            ok = ok and not self._server_thread.is_alive()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
            ok = ok and not self._reader_thread.is_alive()
        return ok

    def _fallback(self, reason: str) -> bool:
        if self._fallback_attempted:
            self._status = "failed"
            self._reason = reason if reason in FAILURE_REASONS else "fallback_start_failed"
            self._write_manifest()
            return False
        self._fallback_attempted = True
        self._mode = "tty"
        self._reason = reason if reason in FAILURE_REASONS else "startup_failed"
        self._status = "fallback_starting"
        self._write_manifest()
        browser_stopped = self._stop_process(self._browser)
        self._browser = None
        service_stopped = self._stop_frame_service()
        if not browser_stopped or not service_stopped:
            self._status = "failed"
            self._reason = "fallback_start_failed"
            self._write_manifest()
            return False
        try:
            self._start_tty()
        except Exception:
            self._status = "failed"
            self._reason = "fallback_start_failed"
            self._write_manifest()
            return False
        self._status = "fallback_tty"
        self._write_manifest()
        return True

    def _runtime_frame_failure(self) -> bool:
        try:
            active = active_nethack_runtime(self._load_state())
        except Exception:
            return True
        if active != self.runtime:
            return False
        now = self._clock()
        if self._active_runtime_seen_at is None:
            self._active_runtime_seen_at = now
        snapshot = self._snapshots.latest() if self._snapshots is not None else None
        if (
            snapshot is not None
            and snapshot.state == "active"
            and snapshot.captured_monotonic is not None
            and now - snapshot.captured_monotonic <= 3.0
        ):
            return False
        if self._reader_error:
            return True
        return now - self._active_runtime_seen_at >= _READER_GRACE_S

    def _failure_reason(self) -> str | None:
        if self._status == "tiles_active":
            if self._browser is None or self._browser.poll() is not None:
                return "browser_exited"
            if self._server_thread is None or not self._server_thread.is_alive():
                return "server_stopped"
            presentation = _load_json(self.presentation_state_path)
            if presentation.get("status") == "presentation_failed":
                return "projection_failed"
            if self._window_id() is None:
                self._window_seen_at = self._window_seen_at or self._clock()
                if self._clock() - self._window_seen_at >= 1.0:
                    return "browser_window_missing"
            else:
                self._window_seen_at = None
            if self._runtime_frame_failure():
                return "reader_failed" if self._reader_error else "reader_unavailable"
        elif self._status == "fallback_tty":
            if self._tty is None or self._tty.poll() is not None:
                return "fallback_exited"
            if self._window_id() is None:
                self._window_seen_at = self._window_seen_at or self._clock()
                if self._clock() - self._window_seen_at >= 1.0:
                    return "fallback_window_missing"
            else:
                self._window_seen_at = None
        return None

    def run(self) -> int:
        result = 0
        try:
            try:
                self._start_frame_service()
                self._start_browser()
                self._status = "tiles_active"
                self._write_manifest()
            except Exception as exc:
                reason = str(exc) if str(exc) in FAILURE_REASONS else "startup_failed"
                if not self._fallback(reason):
                    result = 1
            while not self._stop.wait(_POLL_INTERVAL_S):
                reason = self._failure_reason()
                if reason is None:
                    continue
                if self._status == "tiles_active":
                    if not self._fallback(reason):
                        result = 1
                        break
                    continue
                self._status = "failed"
                self._reason = reason
                self._write_manifest()
                result = 1
                break
        except Exception:
            self._status = "failed"
            self._reason = "startup_failed"
            self._write_manifest()
            result = 1
        finally:
            browser_stopped = self._stop_process(self._browser)
            tty_stopped = self._stop_process(self._tty)
            self._browser = None
            self._tty = None
            service_stopped = self._stop_frame_service()
            cleanup_ok = browser_stopped and tty_stopped and service_stopped
            try:
                if self._profile_path.is_symlink():
                    cleanup_ok = False
                elif self._profile_path.exists():
                    if self._profile_path.is_dir():
                        shutil.rmtree(self._profile_path)
                    else:
                        cleanup_ok = False
            except OSError:
                cleanup_ok = False
            if cleanup_ok:
                self._status = "stopped"
            else:
                self._status = "cleanup_failed"
                result = 1
            self._write_manifest(cleanup_complete=cleanup_ok)
        return result

    def stop(self) -> None:
        self._stop.set()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--runtime-dir", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--presentation-state", required=True, type=Path)
    parser.add_argument("--runtime-id", required=True)
    parser.add_argument("--generation", required=True, type=int)
    parser.add_argument("--adapter-session", required=True)
    parser.add_argument("--game-window", required=True)
    parser.add_argument("--cols", required=True, type=int)
    parser.add_argument("--rows", required=True, type=int)
    parser.add_argument("--font", required=True)
    parser.add_argument("--font-size", required=True, type=int)
    parser.add_argument("--window-title", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runtime = ActiveRuntime(
        runtime_id=args.runtime_id,
        generation=args.generation,
        adapter_session=args.adapter_session,
        game_window=args.game_window,
    )
    supervisor = NethackTilesSupervisor(
        state_dir=args.state_dir,
        runtime_dir=args.runtime_dir,
        manifest_path=args.manifest,
        presentation_state_path=args.presentation_state,
        runtime=runtime,
        cols=args.cols,
        rows=args.rows,
        font=args.font,
        font_size=args.font_size,
        window_title=args.window_title,
    )
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda _signum, _frame: supervisor.stop())
    return supervisor.run()


if __name__ == "__main__":
    raise SystemExit(main())
