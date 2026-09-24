"""Real isolated X11/browser smoke for the optional NetHack tile presenter.

This uses a private Xvfb, a private tmux socket, a fixture TTY pane and a
synthetic canonical runtime. It never launches NetHack or touches production.
The CI job installs the GUI dependencies and fails before pytest if any are
missing; a local skip is therefore not CI evidence.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.adapters.nethack import NethackCoordinatorAdapter  # noqa: E402
from docich.config import DisplayConfig, GameAgentConfig, GameConfig, GameLifecycleConfig  # noqa: E402
from docich.game_switch import GameSwitchStore, RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402
from docich.nethack_tiles_supervisor import browser_binary  # noqa: E402
from docich.tmux import Tmux, TmuxOwnership  # noqa: E402


GUI_BINARIES = ("Xvfb", "xdotool", "tmux", "ffplay", "xterm")
GUI_AVAILABLE = all(shutil.which(name) for name in GUI_BINARIES) and browser_binary() is not None


def _start_outer_xvfb() -> tuple[subprocess.Popen, str]:
    read_fd, write_fd = os.pipe()
    process = subprocess.Popen(
        [shutil.which("Xvfb") or "Xvfb", "-displayfd", str(write_fd), "-screen", "0",
         "960x540x24", "-nolisten", "tcp", "-noreset"],
        pass_fds=(write_fd,),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    os.close(write_fd)
    try:
        deadline = time.monotonic() + 10
        data = bytearray()
        while b"\n" not in data:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or process.poll() is not None:
                raise RuntimeError("outer Xvfb did not start")
            if not select.select([read_fd], [], [], remaining)[0]:
                raise RuntimeError("outer Xvfb did not start")
            data.extend(os.read(read_fd, 32))
        number = data.split(b"\n", 1)[0].decode("ascii")
        if not number.isdigit():
            raise RuntimeError("outer Xvfb returned an invalid display number")
        return process, f":{number}"
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        raise
    finally:
        os.close(read_fd)


def _stop_group(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


@unittest.skipUnless(GUI_AVAILABLE, "isolated GUI dependencies are not installed")
class NethackTilesGuiSmokeTests(unittest.TestCase):
    def test_runtime_starts_before_canonical_commit_and_recovers_to_tty_once(self):
        with tempfile.TemporaryDirectory(prefix="nethack-tiles-gui-smoke-") as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            runtime_id = "g9-abcdef"
            runtime_dir = state_dir / "runtimes" / runtime_id
            save_dir = root / "save"
            save_dir.mkdir()
            runtime_dir.mkdir(parents=True)
            tmux_tmpdir = root / "tmux"
            tmux_tmpdir.mkdir(mode=0o700)
            store = GameSwitchStore(state_dir)
            store.initialize()
            generation_names = runtime_names(9)
            spec = RuntimeSpec(
                game="nethack",
                adapter="cli",
                generation=9,
                runtime_id=runtime_id,
                lease_id=str(uuid.uuid4()),
                runtime_dir=runtime_dir,
                game_window=generation_names.game_window,
                agent_window=generation_names.agent_window,
                adapter_session=generation_names.adapter_session,
            )
            display_server = None
            tmux = None
            adapter = None
            patch_env = mock.patch.dict(
                os.environ, {"TMUX_TMPDIR": str(tmux_tmpdir)}, clear=False
            )
            patch_env.start()
            try:
                display_server, display_name = _start_outer_xvfb()
                display = DisplayConfig(
                    number=int(display_name[1:]),
                    width=960,
                    height=540,
                    viewport_x=0,
                    viewport_y=0,
                    viewport_width=960,
                    viewport_height=540,
                )
                global_config = SimpleNamespace(
                    state_dir=state_dir,
                    config_path=root / "docich.toml",
                    display=display,
                )
                game = GameConfig(
                    name="nethack",
                    title="NetHack fixture",
                    adapter="cli",
                    raw={
                        "nethack": {
                            "player_name": "docich",
                            "save_dir": str(save_dir),
                            "presentation": {"mode": "tiles"},
                        },
                        "cli": {"cols": 80, "rows": 24, "font": "monospace", "font_size": 16},
                    },
                    agent=GameAgentConfig(enabled=False),
                    lifecycle=GameLifecycleConfig(),
                )
                adapter = NethackCoordinatorAdapter(global_config, game, spec)
                tmux = Tmux(spec.adapter_session)
                adapter.tmux = tmux
                adapter_owner = TmuxOwnership(runtime_id, 9, "adapter")
                game_owner = TmuxOwnership(runtime_id, 9, "game")
                tty_lines = [
                    "A fixture dungeon", *("." * 25 for _ in range(7)),
                    "....@....................", *("." * 25 for _ in range(12)),
                    "HP:20/20", "Dlvl:1",
                ]
                fixture = "\n".join(tty_lines) + "\n"
                fixture_script = (
                    "import sys,time; sys.stdout.write(" + repr(fixture)
                    + "); sys.stdout.flush(); time.sleep(120)"
                )
                tmux.create_game_session_owned(
                    spec.adapter_session,
                    [sys.executable, "-c", fixture_script],
                    80,
                    24,
                    adapter_owner,
                )
                tmux.create_window_owned(
                    spec.game_window,
                    adapter._xterm_command(),
                    game_owner,
                    env={"DISPLAY": display_name},
                )

                # The supervisor and browser must be ready while canonical
                # state still says idle; otherwise startup would deadlock on
                # the coordinator's later ready commit.
                adapter.readiness(time.monotonic() + 30, None)
                manifest_path = runtime_dir / "nethack_tiles.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    manifest["status"],
                    "tiles_active",
                    "tiles startup fell back: "
                    + json.dumps(
                        {
                            "mode": manifest.get("mode"),
                            "reason": manifest.get("reason"),
                            "status": manifest.get("status"),
                        },
                        sort_keys=True,
                    ),
                )
                self.assertEqual(manifest["runtime_id"], runtime_id)
                self.assertIsInstance(manifest.get("browser_pid"), int)

                state, _ = store.canonical.load()
                state["active"] = {
                    "game": "nethack",
                    "adapter": "cli",
                    "generation": 9,
                    "runtime_id": runtime_id,
                    "lease_id": spec.lease_id,
                    "adapter_session": spec.adapter_session,
                    "game_window": spec.game_window,
                    "agent_window": spec.agent_window,
                    "started_at": "2026-09-25T00:00:00Z",
                }
                state["phase"] = "ready"
                state["next_generation"] = 10
                store.canonical.save(state)

                port = manifest["frame_port"]
                self.assertIsInstance(port, int)
                deadline = time.monotonic() + 15
                health = {}
                while time.monotonic() < deadline:
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                            health = json.loads(response.read(4096))
                    except Exception:
                        time.sleep(0.1)
                        continue
                    if health.get("state") == "active" and health.get("capture_seq", 0) > 0:
                        break
                    time.sleep(0.1)
                self.assertEqual(health.get("runtime_id"), runtime_id)
                self.assertEqual(health.get("state"), "active")
                self.assertGreater(health.get("capture_seq", 0), 0)

                presentation_path = runtime_dir / "presentation.json"
                before_presentation = json.loads(presentation_path.read_text(encoding="utf-8"))
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(manifest.get("runtime_id"), runtime_id)
                self.assertEqual(manifest.get("generation"), 9)
                self.assertEqual(manifest.get("adapter_session"), spec.adapter_session)
                self.assertEqual(manifest.get("game_window"), spec.game_window)
                self.assertEqual(manifest.get("status"), "tiles_active")
                browser_pid = manifest["browser_pid"]
                self.assertIs(type(browser_pid), int)
                self.assertGreater(browser_pid, 1)
                os.killpg(browser_pid, signal.SIGTERM)
                deadline = time.monotonic() + 20
                fallback = {}
                presentation = {}
                while time.monotonic() < deadline:
                    try:
                        fallback = json.loads(manifest_path.read_text(encoding="utf-8"))
                        presentation = json.loads(presentation_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        time.sleep(0.1)
                        continue
                    if (
                        fallback.get("status") == "fallback_tty"
                        and presentation.get("status") == "ready"
                        and presentation.get("window") != before_presentation.get("window")
                    ):
                        break
                    time.sleep(0.1)
                self.assertEqual(fallback.get("status"), "fallback_tty")
                self.assertEqual(fallback.get("reason"), "browser_exited")
                self.assertIsInstance(fallback.get("tty_pid"), int)
                self.assertNotEqual(presentation.get("window"), before_presentation.get("window"))
                self.assertEqual(
                    store.canonical.load()[0]["active"]["runtime_id"], runtime_id
                )

                adapter.cleanup_runtime(time.monotonic() + 20, None)
                stopped = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(stopped["status"], "stopped")
                self.assertTrue(stopped["cleanup_complete"])
                self.assertEqual(
                    json.loads(presentation_path.read_text(encoding="utf-8"))["status"],
                    "stopped",
                )
            finally:
                if adapter is not None and tmux is not None:
                    try:
                        adapter.cleanup_runtime(time.monotonic() + 12, None)
                    except Exception:
                        try:
                            tmux.kill_session_owned(
                                spec.adapter_session,
                                TmuxOwnership(runtime_id, 9, "adapter"),
                            )
                        except Exception:
                            pass
                if tmux is not None:
                    subprocess.run(
                        ["tmux", "kill-server"],
                        env=dict(os.environ, TMUX_TMPDIR=str(tmux_tmpdir)),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                        check=False,
                    )
                _stop_group(display_server)
                patch_env.stop()


if __name__ == "__main__":
    unittest.main()
