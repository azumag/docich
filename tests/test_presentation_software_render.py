"""--sdl-software-render: both ffplay processes get SDL's CPU renderer.

2026-10-11 FlyHome on the 4 vCPU prod VM: the two ffplay processes (udp viewer
on the private Xvfb, x11grab projection onto :99) spent almost all of their CPU
in Mesa llvmpipe threads (software OpenGL), ~1 core in total.  SDL_RENDER_DRIVER
alone does not stop it; SDL_FRAMEBUFFER_ACCELERATION=0 is needed as well.  This
checks the env reaches the real processes in the real execution form (script +
stub X/ffplay binaries), and that the option stays opt-in.
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
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.adapters.external_video_program import ExternalVideoProgramAdapter  # noqa: E402
from docich.presentation import SDL_SOFTWARE_RENDER_ENV, _parser  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / 'src' / 'docich' / 'presentation.py'

# Every stub records the SDL_* environment it was started with.
RECORD = '''
import json, os, sys
from pathlib import Path
out = Path(os.environ['RECORD_DIR']) / (sys.argv[0].rsplit('/', 1)[-1] + '.' + str(os.getpid()) + '.json')
out.write_text(json.dumps({k: v for k, v in os.environ.items() if k.startswith('SDL_')}))
'''

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
    # The projection ffplay (has -f x11grab) and the viewer share this stub.
    'ffplay': '#!/usr/bin/env python3' + RECORD + 'import time\ntime.sleep(60)\n',
}


class SdlSoftwareRenderEnv(unittest.TestCase):
    def _run(self, *extra):
        tmp = tempfile.TemporaryDirectory(prefix='presentation-sdl-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bin_dir, record_dir = root / 'bin', root / 'record'
        bin_dir.mkdir()
        record_dir.mkdir()
        for name, body in STUBS.items():
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        viewer = root / 'viewer.py'
        viewer.write_text(RECORD + 'import time\ntime.sleep(60)\n')
        state = root / 'presentation.json'
        env = dict(os.environ, PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}',
                   RECORD_DIR=str(record_dir))
        # Never inherit a developer's own SDL settings into the assertion.
        for key in SDL_SOFTWARE_RENDER_ENV:
            env.pop(key, None)
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), '--display', ':0', '--title', 'docich-present-t',
             '--x', '0', '--y', '90', '--width', '960', '--height', '540',
             '--runtime-state', str(state), *extra, '--', sys.executable, str(viewer)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True)

        def stop():
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=15)
        self.addCleanup(stop)
        limit = time.monotonic() + 10
        while time.monotonic() < limit:
            status = None
            if state.exists():
                try:
                    status = json.loads(state.read_text()).get('status')
                except ValueError:
                    pass
            records = list(record_dir.glob('*.json'))
            if (status == 'ready' and len(records) >= 2) or proc.poll() is not None:
                break
            time.sleep(0.05)
        self.assertIsNone(proc.poll(), 'presenter must keep supervising')
        return {p.name.split('.')[0]: json.loads(p.read_text()) for p in records}

    def test_flag_sets_cpu_renderer_for_viewer_and_projection(self):
        records = self._run('--sdl-software-render')
        self.assertEqual(set(records), {'ffplay', 'viewer'}, records)
        for name, sdl in records.items():
            with self.subTest(process=name):
                for key, value in SDL_SOFTWARE_RENDER_ENV.items():
                    self.assertEqual(sdl.get(key), value)

    def test_default_leaves_sdl_untouched(self):
        for name, sdl in self._run().items():
            with self.subTest(process=name):
                for key in SDL_SOFTWARE_RENDER_ENV:
                    self.assertNotIn(key, sdl)


class SettingsAreBothNeeded(unittest.TestCase):
    def test_both_variables_are_pinned(self):
        # SDL_RENDER_DRIVER alone left llvmpipe running (measured on the VM's
        # ffplay 6.1.1 / SDL2 build); do not drop either one.
        self.assertEqual(SDL_SOFTWARE_RENDER_ENV, {
            'SDL_RENDER_DRIVER': 'software', 'SDL_FRAMEBUFFER_ACCELERATION': '0'})

    def test_option_is_opt_in(self):
        args = _parser().parse_args([
            '--display', ':97', '--title', 't', '--x', '0', '--y', '90',
            '--width', '960', '--height', '540', '--', 'true'])
        self.assertFalse(args.sdl_software_render)


class ExternalVideoUsesIt(unittest.TestCase):
    def test_flyhome_presenter_enables_cpu_renderer(self):
        display = SimpleNamespace(name=':99', viewport_x=0, viewport_y=90,
                                  viewport_width=960, viewport_height=540)
        fake = SimpleNamespace(g=SimpleNamespace(display=display),
                               spec=SimpleNamespace(runtime_id='r1', runtime_dir=Path('/x')))
        command = ExternalVideoProgramAdapter._xterm_command(fake)
        self.assertIn('--sdl-software-render', command)
        # The flag belongs to presentation.py, not to the wrapped viewer ffplay.
        self.assertLess(command.index('--sdl-software-render'), command.index('--'))
        # And the whole command still parses with the real option parser.
        args = _parser().parse_args(command[command.index('--display'):])
        self.assertTrue(args.sdl_software_render)
        self.assertEqual(args.framerate, 30)


if __name__ == '__main__':
    unittest.main()
