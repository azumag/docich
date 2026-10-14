"""Opt-in bounded re-projection of the presenter's own player (#2097).

A shared output-display restart (`start_all.sh --supervisor`) kills the owned
projection ffplay while the private Xvfb/viewer and the game keep running. The
presenter must re-attach *its own* player to the same source window, bounded,
and must leave the failed state for the operator path when the budget runs out.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.presentation import ReprojectPolicy  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / 'src' / 'docich' / 'presentation.py'

XVFB = '''#!/usr/bin/env python3
import os, sys, time
fd = int(sys.argv[sys.argv.index('-displayfd') + 1])
os.write(fd, b'99\\n'); os.close(fd)
time.sleep(120)
'''
XDOTOOL = '''#!/usr/bin/env python3
import sys
if sys.argv[1] == 'search':
    print('4242')
else:
    print('WINDOW=4242'); print('WIDTH=256'); print('HEIGHT=224')
'''


class ReprojectPolicyTests(unittest.TestCase):
    def test_first_loss_attempts_immediately_then_respects_the_interval(self):
        policy = ReprojectPolicy(window_s=60.0, interval_s=2.0, limit=4)
        policy.lost(100.0)
        self.assertTrue(policy.due(100.0))
        policy.attempted(100.0)
        self.assertFalse(policy.due(101.9))
        self.assertTrue(policy.due(102.0))

    def test_budget_stops_at_the_attempt_limit_and_the_window(self):
        limited = ReprojectPolicy(window_s=60.0, interval_s=0.0, limit=2)
        limited.lost(0.0)
        limited.attempted(0.0)
        limited.attempted(0.0)
        self.assertFalse(limited.due(0.0))
        expired = ReprojectPolicy(window_s=5.0, interval_s=0.0, limit=10)
        expired.lost(0.0)
        self.assertFalse(expired.due(5.1))

    def test_no_attempt_before_a_loss_and_reset_restores_the_budget(self):
        policy = ReprojectPolicy(window_s=60.0, interval_s=0.0, limit=2)
        self.assertFalse(policy.due(0.0))
        policy.lost(0.0)
        policy.attempted(0.0)
        policy.attempted(0.0)
        self.assertFalse(policy.due(0.0))
        policy.reset()
        self.assertFalse(policy.due(0.0), 'a reset budget waits for the next loss')
        policy.lost(10.0)
        self.assertTrue(policy.due(10.0))


class PresentationReprojectScript(unittest.TestCase):
    """Run the real script with stub X/ffplay binaries."""

    def _run(self, *, fail_first: int, flag: bool, window: float | None = None,
             interval: float | None = None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        spawns = root / 'ffplay-count'
        for name, body in (('Xvfb', XVFB), ('xdotool', XDOTOOL)):
            path = bin_dir / name
            path.write_text(body)
            path.chmod(0o755)
        ffplay = bin_dir / 'ffplay'
        ffplay.write_text(textwrap.dedent(f'''\
            #!/usr/bin/env python3
            import time
            from pathlib import Path
            counter = Path({str(spawns)!r})
            count = int(counter.read_text()) + 1 if counter.exists() else 1
            counter.write_text(str(count))
            if count <= {fail_first}:
                raise SystemExit(1)
            time.sleep(120)
        '''))
        ffplay.chmod(0o755)
        env = dict(os.environ, PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
        state = root / 'presentation.json'
        argv = [sys.executable, str(SCRIPT), '--display', ':0', '--title', 'docich-present-t',
                '--x', '0', '--y', '90', '--width', '960', '--height', '540',
                '--window-pattern', '^RetroArch', '--runtime-state', str(state)]
        if flag:
            argv.append('--reproject-on-player-loss')
        if window is not None:
            argv += ['--reproject-window-sec', str(window)]
        if interval is not None:
            argv += ['--reproject-interval-sec', str(interval)]
        argv += ['--', sys.executable, '-c', 'import time; time.sleep(120)']
        proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)

        def stop():
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=15)
        self.addCleanup(stop)
        return proc, state, spawns

    def _wait_status(self, state: Path, wanted: set[str], timeout: float):
        limit = time.monotonic() + timeout
        seen = None
        while time.monotonic() < limit:
            if state.exists():
                try:
                    seen = json.loads(state.read_text()).get('status')
                except ValueError:
                    seen = None
                if seen in wanted:
                    return seen
            time.sleep(0.05)
        return seen

    def _wait_settled_spawns(self, spawns: Path, timeout: float,
                             stable_for: float = 1.5) -> int:
        """Return the spawn count once it has stopped growing."""
        limit = time.monotonic() + timeout
        last = int(spawns.read_text()) if spawns.exists() else 0
        unchanged_since = time.monotonic()
        while time.monotonic() < limit:
            time.sleep(0.1)
            current = int(spawns.read_text()) if spawns.exists() else 0
            if current != last:
                last = current
                unchanged_since = time.monotonic()
            elif time.monotonic() - unchanged_since >= stable_for:
                return last
        return last

    def _wait_spawns(self, spawns: Path, count: int, timeout: float) -> int:
        """Wait for the ffplay stub to have been started `count` times."""
        limit = time.monotonic() + timeout
        seen = 0
        while time.monotonic() < limit:
            if spawns.exists():
                try:
                    seen = int(spawns.read_text())
                except ValueError:
                    seen = 0
                if seen >= count:
                    return seen
            time.sleep(0.05)
        return seen

    def test_reproject_reattaches_the_owned_player_after_the_display_dies(self):
        proc, state, spawns = self._run(fail_first=1, flag=True)
        # The first player dies immediately (display restart); the presenter must
        # re-attach its own player and publish ready again.
        self.assertEqual(self._wait_spawns(spawns, 2, timeout=20), 2,
                         'exactly one re-projection')
        status = self._wait_status(state, {'ready'}, timeout=10)
        stderr = '' if proc.poll() is None else proc.communicate(timeout=5)[1]
        self.assertEqual(status, 'ready', stderr[-800:])
        time.sleep(1.0)
        self.assertEqual(spawns.read_text(), '2', 'no further re-projection while healthy')
        self.assertIsNone(proc.poll(), 'the presenter must keep supervising')

    def test_without_the_flag_the_failed_projection_is_left_alone(self):
        proc, state, spawns = self._run(fail_first=1, flag=False)
        status = self._wait_status(state, {'presentation_failed'}, timeout=10)
        self.assertEqual(status, 'presentation_failed')
        self.assertEqual(self._wait_spawns(spawns, 1, timeout=5), 1)
        time.sleep(3.0)
        self.assertEqual(spawns.read_text(), '1', 'no re-projection without the flag')
        self.assertEqual(json.loads(state.read_text()).get('status'), 'presentation_failed')
        self.assertIsNone(proc.poll())

    def test_reproject_gives_up_inside_the_window(self):
        # Every attempt dies: the presenter must retry inside the window, then
        # stop spawning, record the failure and keep running for the operator
        # path (a short window keeps this test fast; the default is 60s).
        proc, state, spawns = self._run(fail_first=99, flag=True, window=3.0, interval=0.3)
        settled = self._wait_settled_spawns(spawns, timeout=25)
        self.assertGreaterEqual(settled, 3, 'retries must happen inside the window')
        self.assertEqual(self._wait_status(state, {'presentation_failed'}, timeout=10),
                         'presentation_failed')
        self.assertEqual(int(spawns.read_text()), settled, 'attempts stop at the window')
        self.assertEqual(json.loads(state.read_text()).get('status'), 'presentation_failed')
        self.assertIsNone(proc.poll())


if __name__ == '__main__':
    unittest.main()
