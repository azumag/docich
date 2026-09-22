"""Graceful shutdown wrapper for the Soren91 coordinator adapter.

The Soren91 gameplay bot temporarily freezes/hides the normal sorengame page
while it owns the shared remote Chrome.  Its ``cleanupRuntime()`` restores that
page, but destroying the tmux agent window skips the bot's SIGINT/SIGTERM
shutdown path and can leave the normal game frozen after the corner ends.

Keep the existing Soren91 adapter unchanged and narrow this hotfix to the
production registry: request a graceful bot stop first, wait a bounded amount
of time for the owned agent window to disappear, and only then fall back to the
existing force-kill behavior.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .base import AdapterError
from .soren91 import Soren91CoordinatorAdapter as _BaseSoren91CoordinatorAdapter

BOT_GRACEFUL_STOP_S = 7.0
BOT_STOP_POLL_INTERVAL_S = 0.1


class Soren91GracefulCoordinatorAdapter(_BaseSoren91CoordinatorAdapter):
    """Soren91 adapter that lets the bot restore the normal game before kill."""

    def _bot_main_pid_alive(self) -> bool:
        """Return whether the gameplay loop still owns its main PID marker.

        ``main.mjs`` writes ``tmp/main.pid`` at startup and removes it from
        ``cleanupRuntime()``.  The Node process can nevertheless remain alive
        for a while after the gameplay loop gives up (for example after a
        sustained remote-capture failure), so tmux pane liveness alone is not
        sufficient evidence that Soren91 is still playing.

        Treat a missing/malformed/stale marker as dead.  This is intentionally
        fail-closed: the corner already requires two consecutive liveness
        misses before it restores the previous game.
        """
        try:
            raw = (Path(self._bot_cwd()) / "tmp" / "main.pid").read_text(
                encoding="utf-8"
            )
            pid = int(raw.strip())
            if pid <= 0:
                return False
            os.kill(pid, 0)
        except (AdapterError, OSError, ValueError):
            return False
        return True

    def agent_alive(self, deadline: float, cancel) -> bool:
        # Preserve the base ownership/window/pane checks first.  Once the bot
        # is enabled, also require the lifecycle marker emitted by main.mjs;
        # otherwise an idle-but-still-live Node pane can mask a stopped game
        # loop and leave the fixed Soren91 slot frozen until its scheduled end.
        if not super().agent_alive(deadline, cancel):
            return False
        if not self.agent_enabled:
            return True
        return self._bot_main_pid_alive()

    def _request_bot_graceful_stop(self, target: str) -> None:
        # main.mjs already treats tmp/stop as an external graceful-stop request.
        # Writing it first also covers a temporarily delayed terminal signal.
        try:
            stop = Path(self._bot_cwd()) / "tmp" / "stop"
            stop.parent.mkdir(parents=True, exist_ok=True)
            stop.write_text("", encoding="utf-8")
        except (AdapterError, OSError):
            # Ctrl-C below is the primary request; inability to persist the
            # auxiliary stop flag must not prevent cleanup from progressing.
            pass

        # main.mjs handles SIGINT by arming a five-second forced cleanup timer,
        # then exits through its finally block where setNormalGameLifecycle()
        # restores lifecycle=active, CPU throttle=1x and canvas visibility.
        self.tmux.send_keys(target, ["C-c"], literal=False)

    def stop_agent(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        target = self._agent_window_target()
        if not self.tmux.window_target_exists(target):
            return

        # Never send input to an unowned/stale tmux window.
        self._verify_window_ownership(target, "agent")
        self._request_bot_graceful_stop(target)

        grace_until = min(deadline, time.monotonic() + BOT_GRACEFUL_STOP_S)
        while self.tmux.window_target_exists(target):
            self._check_active(deadline, cancel)
            now = time.monotonic()
            if now >= grace_until:
                break
            time.sleep(min(BOT_STOP_POLL_INTERVAL_S, max(0.0, grace_until - now)))

        if not self.tmux.window_target_exists(target):
            return

        # Preserve the old bounded fail-safe: a wedged bot must not hold the
        # coordinator forever.  By this point it had enough time to run its
        # own five-second cleanup backstop.
        self._check_active(deadline, cancel)
        self.tmux.kill_window_owned(target, self._ownership("agent"))
