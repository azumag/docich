"""presentation.py runs as a *script* (not a package module) in production.

2026-09-23 gen317: `--audio-volume-percent` used a package-relative import,
which raised ImportError right after the viewer started; the presenter tore
the game down and Hanjuku could never become ready.  Exercise the real
execution form with stub X/ffplay/pactl binaries.
"""

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

SCRIPT = Path(__file__).resolve().parents[1] / 'src' / 'docich' / 'presentation.py'

STUBS = {
    'Xvfb': '''#!/usr/bin/env python3
import os, sys, time
fd = int(sys.argv[sys.argv.index('-displayfd') + 1])
os.write(fd, b'99\\n'); os.close(fd)
time.sleep(60)
''',
    'xdotool': '''#!/usr/bin/env python3
import sys
if sys.argv[1] == 'search':
    print('4242')
else:
    print('WINDOW=4242'); print('WIDTH=256'); print('HEIGHT=224')
''',
    'ffplay': '#!/bin/sh\nexec sleep 60\n',
    'pactl': '#!/bin/sh\nexit 0\n',
}


class PresentationScriptVolume(unittest.TestCase):
    def _run(self, *extra):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bin_dir = Path(tmp.name) / 'bin'
        bin_dir.mkdir()
        for name, body in STUBS.items():
            path = bin_dir / name
            path.write_text(body)
            path.chmod(0o755)
        state = Path(tmp.name) / 'presentation.json'
        env = dict(os.environ, PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), '--display', ':0', '--title', 'docich-present-t',
             '--x', '0', '--y', '90', '--width', '960', '--height', '540',
             '--window-pattern', '^RetroArch', '--runtime-state', str(state), *extra,
             '--', sys.executable, '-c', 'import time; time.sleep(60)'],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True)

        def stop():
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=15)
        self.addCleanup(stop)
        limit = time.monotonic() + 10
        status = None
        while time.monotonic() < limit:
            if state.exists():
                try:
                    status = json.loads(state.read_text()).get('status')
                except ValueError:
                    status = None
            if status == 'ready' or proc.poll() is not None:
                break
            time.sleep(0.05)
        return proc, status, state

    def test_volume_option_reaches_ready_when_run_as_script(self):
        proc, status, state = self._run('--audio-sink', 'soren_null',
                                        '--audio-volume-percent', '80')
        stderr = '' if proc.poll() is None else proc.communicate(timeout=5)[1]
        self.assertEqual(status, 'ready', stderr[-800:])
        self.assertIsNone(proc.poll(), 'presenter must keep supervising the game')
        limit = time.monotonic() + 5
        evidence = state.with_name('audio_volume.json')
        while time.monotonic() < limit and not evidence.exists():
            time.sleep(0.05)
        self.assertTrue(evidence.exists(), 'volume evidence must be recorded')

    def test_without_volume_option_reaches_ready(self):
        proc, status, _ = self._run()
        self.assertEqual(status, 'ready')


if __name__ == '__main__':
    unittest.main()
