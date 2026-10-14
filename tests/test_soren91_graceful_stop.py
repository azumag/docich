"""Regression tests for graceful Soren91 corner shutdown."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.adapters import _COORDINATOR_REGISTRY  # noqa: E402
from docich.adapters.soren91 import Soren91CoordinatorAdapter  # noqa: E402
from docich.adapters.soren91_graceful import (  # noqa: E402
    Soren91GracefulCoordinatorAdapter,
)
from docich.tmux import TmuxOwnership  # noqa: E402

TARGET = "docich-game-g3:agent-g3"
OWNER = TmuxOwnership(runtime_id="g3-abcdef12", generation=3, role="agent")


class FakeTmux:
    def __init__(self, *, close_on_interrupt: bool):
        self.windows = {TARGET}
        self.close_on_interrupt = close_on_interrupt
        self.sent_keys = []
        self.killed = []

    def window_target_exists(self, target, strict=False):
        return target in self.windows

    def send_keys(self, target, keys, literal=False):
        self.sent_keys.append((target, list(keys), literal))
        if self.close_on_interrupt and keys == ["C-c"]:
            self.windows.discard(target)

    def kill_window_owned(self, target, expected):
        self.killed.append((target, expected))
        self.windows.discard(target)
        return True


class TestSoren91GracefulStop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _adapter(self, tmux):
        adapter = object.__new__(Soren91GracefulCoordinatorAdapter)
        adapter.tmux = tmux
        adapter._check_active = lambda deadline, cancel: None
        adapter._agent_window_target = lambda: TARGET
        adapter._verify_window_ownership = lambda target, role: self.assertEqual(
            (target, role), (TARGET, "agent")
        )
        adapter._ownership = lambda role: OWNER
        adapter._bot_cwd = lambda: str(self.root)
        return adapter

    def test_registry_uses_graceful_adapter(self):
        self.assertEqual(
            _COORDINATOR_REGISTRY["soren91"],
            "docich.adapters.soren91_graceful:Soren91GracefulCoordinatorAdapter",
        )

    def test_agent_alive_requires_main_pid_marker_when_bot_is_enabled(self):
        tmux = FakeTmux(close_on_interrupt=False)
        adapter = self._adapter(tmux)
        adapter.agent_enabled = True
        marker = self.root / "tmp" / "main.pid"

        with mock.patch.object(Soren91CoordinatorAdapter, "agent_alive", return_value=True):
            # Reproduces the 2026-09-21 incident: tmux/Node still looks alive,
            # but main.mjs has already left the gameplay loop and removed its
            # lifecycle marker.  The corner must now observe this as dead.
            self.assertFalse(adapter.agent_alive(time.monotonic() + 5, None))

            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(f"{os.getpid()}\n", encoding="utf-8")
            self.assertTrue(adapter.agent_alive(time.monotonic() + 5, None))

            marker.unlink()
            self.assertFalse(adapter.agent_alive(time.monotonic() + 5, None))

    def test_agent_alive_rejects_stale_pid_marker(self):
        tmux = FakeTmux(close_on_interrupt=False)
        adapter = self._adapter(tmux)
        adapter.agent_enabled = True
        marker = self.root / "tmp" / "main.pid"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("424242\n", encoding="utf-8")

        with mock.patch.object(Soren91CoordinatorAdapter, "agent_alive", return_value=True):
            with mock.patch(
                "docich.adapters.soren91_graceful.os.kill",
                side_effect=ProcessLookupError,
            ):
                self.assertFalse(adapter.agent_alive(time.monotonic() + 5, None))

    def test_agent_alive_without_bot_preserves_base_liveness(self):
        tmux = FakeTmux(close_on_interrupt=False)
        adapter = self._adapter(tmux)
        adapter.agent_enabled = False

        with mock.patch.object(Soren91CoordinatorAdapter, "agent_alive", return_value=True):
            self.assertTrue(adapter.agent_alive(time.monotonic() + 5, None))

    def test_stop_agent_requests_graceful_exit_before_force_kill(self):
        tmux = FakeTmux(close_on_interrupt=True)
        adapter = self._adapter(tmux)

        adapter.stop_agent(time.monotonic() + 5, None)

        self.assertEqual(tmux.sent_keys, [(TARGET, ["C-c"], False)])
        self.assertEqual(tmux.killed, [])
        self.assertFalse(tmux.window_target_exists(TARGET))
        self.assertTrue((self.root / "tmp" / "stop").exists())

    def test_stop_agent_force_kills_only_after_grace_window(self):
        tmux = FakeTmux(close_on_interrupt=False)
        adapter = self._adapter(tmux)

        with mock.patch("docich.adapters.soren91_graceful.BOT_GRACEFUL_STOP_S", 0.0):
            adapter.stop_agent(time.monotonic() + 5, None)

        self.assertEqual(tmux.sent_keys, [(TARGET, ["C-c"], False)])
        self.assertEqual(tmux.killed, [(TARGET, OWNER)])
        self.assertFalse(tmux.window_target_exists(TARGET))

    def test_stop_agent_is_noop_when_window_is_already_gone(self):
        tmux = FakeTmux(close_on_interrupt=True)
        tmux.windows.clear()
        adapter = self._adapter(tmux)

        adapter.stop_agent(time.monotonic() + 5, None)

        self.assertEqual(tmux.sent_keys, [])
        self.assertEqual(tmux.killed, [])
        self.assertFalse((self.root / "tmp" / "stop").exists())


if __name__ == "__main__":
    unittest.main()
