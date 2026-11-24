"""The Hanjuku projection ffplay must not replay its x11grab probe backlog.

2026-10-29 on the production VM: the projection ffplay showed the ~20 frames
its stream analysis had read (FPS probe) before any live frame.  x11grab is not
a realtime input for ffplay, so its external clock never caught up and the
mirror on :99 ran 1.2-1.3 s behind the RetroArch window for the life of the
projection, while the game audio went straight to Pulse: on the stream the
sound led the picture by about a second.  The same ffplay 6.1.1 in an Ubuntu
24.04 container: 1.23-1.30 s without the flags, 0.03-0.07 s with them.

This runs the real script with stub X/ffplay/ffmpeg binaries and checks the
argv the projection ffplay is actually started with, and that the recovery
operator's expected argv (compared exactly by ``verify_player``) still equals
it.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.presentation import PROJECTION_INPUT_LOW_LATENCY  # noqa: E402
from docich.presentation_recovery import projection_argv  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / 'src' / 'docich' / 'presentation.py'
TITLE = 'docich-present-g7-abcdef12'

STUBS = {
    'Xvfb': '''#!/usr/bin/env python3
import os, sys, time
fd = int(sys.argv[sys.argv.index('-displayfd') + 1])
os.write(fd, b'99\\n'); os.close(fd)
time.sleep(60)
''',
    # The production RetroArch window geometry.
    'xdotool': '''#!/usr/bin/env python3
import sys
if sys.argv[1] == 'search':
    print('4242')
else:
    print('WINDOW=4242'); print('WIDTH=299'); print('HEIGHT=224')
''',
    # measured_contain_size(): 299x224 contained in 960x540 measures 721x540.
    'ffmpeg': '''#!/usr/bin/env python3
import sys
sys.stdin.buffer.read()
sys.stderr.write('[Parsed_showinfo_1] n:0 s:721x540 fmt:bgra\\n')
''',
    'ffplay': '''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
Path(os.environ['RECORD_DIR'], 'ffplay.%d.json' % os.getpid()).write_text(json.dumps(sys.argv[1:]))
time.sleep(60)
''',
}


def _option_before_input(argv, option, value):
    """True when ``option value`` is an input option of the first ``-i``."""
    end = argv.index('-i')
    return any(argv[i] == option and argv[i + 1] == value for i in range(end - 1))


class ProjectionArgv(unittest.TestCase):
    def _projection_argv(self):
        tmp = tempfile.TemporaryDirectory(prefix='presentation-latency-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bin_dir, record_dir = root / 'bin', root / 'record'
        bin_dir.mkdir()
        record_dir.mkdir()
        for name, body in STUBS.items():
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        viewer = root / 'viewer.py'
        viewer.write_text('import time\ntime.sleep(60)\n')
        state = root / 'presentation.json'
        env = dict(os.environ, PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}',
                   RECORD_DIR=str(record_dir))
        # The production Hanjuku presenter options (right-aligned edge projection).
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), '--display', ':0', '--title', TITLE,
             '--x', '0', '--y', '90', '--width', '960', '--height', '540',
             '--window-pattern', '^RetroArch', '--framerate', '15', '--nearest',
             '--align', 'right', '--runtime-state', str(state),
             '--', sys.executable, str(viewer)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True)

        def stop():
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=15)
        self.addCleanup(stop)
        limit = time.monotonic() + 10
        records = []
        while time.monotonic() < limit and proc.poll() is None:
            records = list(record_dir.glob('ffplay.*.json'))
            if records:
                break
            time.sleep(0.05)
        self.assertIsNone(proc.poll(), 'presenter must keep supervising')
        self.assertEqual(len(records), 1, records)
        return json.loads(records[0].read_text())

    def test_projection_drops_the_probe_backlog(self):
        argv = self._projection_argv()
        self.assertEqual(argv[argv.index('-f') + 1], 'x11grab', argv)
        self.assertTrue(_option_before_input(argv, '-fflags', 'nobuffer'), argv)
        self.assertTrue(_option_before_input(argv, '-fpsprobesize', '0'), argv)

    def test_recovery_expects_the_same_projection_argv(self):
        argv = self._projection_argv()
        claim = dict(identity=dict(runtime_id='g7-abcdef12'),
                     native=dict(display=':99', window='4242', width=299, height=224),
                     projection=dict(viewport=[0, 90, 960, 540], content=[239, 0, 721, 540]))
        self.assertEqual(projection_argv(claim)[1:], argv)

    def test_flags_are_pinned(self):
        # Either flag alone removed the backlog on the VM's FFmpeg 6.1.1;
        # keep both so a later FPS-probe default cannot bring it back.
        self.assertEqual(PROJECTION_INPUT_LOW_LATENCY,
                         ('-fflags', 'nobuffer', '-fpsprobesize', '0'))


if __name__ == '__main__':
    unittest.main()
