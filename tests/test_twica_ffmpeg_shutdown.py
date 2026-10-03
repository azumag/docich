"""Exercise the runner's actual stop fallback with disposable native processes."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('stop_mode', ['fallback', 'kill', 'sigint'])
def test_native_stop_reaps_child_and_closes_progress(tmp_path, stop_mode):
    # An isolated Linux subreaper lets the fixture clean up even the pre-fix
    # orphan. It does not change pytest's child adoption or signal handlers.
    scenario = tmp_path / 'scenario.py'
    scenario.write_text(r'''
import ctypes, importlib.util, json, os, select, signal, subprocess, sys, time
from pathlib import Path
from docich.twica_operator import prepare

root, mode = Path(sys.argv[1]).resolve(), sys.argv[2]
if sys.platform == 'linux':
    assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
directory = root / 'control'
prepare(directory)
events = root / 'events'
identity = root / 'native.json'
fake = root / 'fake-ffmpeg'
fake.write_text('#!' + sys.executable + '\n' + r"""
import json, os, signal, sys
from pathlib import Path
root = Path(__file__).parent
def record(event):
    with (root / 'events').open('a') as stream:
        stream.write(event + '\n')
def interrupt(*_):
    record('SIGINT')
    if os.environ['FIXTURE_NATIVE_MODE'] == 'sigint':
        sys.exit(255)
signal.signal(signal.SIGINT, interrupt)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
(root / 'native.json').write_text(json.dumps({'pid': os.getpid(), 'parent': os.getppid()}))
print('progress=continue', flush=True)
while True:
    line = sys.stdin.readline()
    if line:
        record(line.strip())
    else:
        signal.pause()
""")
fake.chmod(0o700)
env = dict(os.environ, FIXTURE_NATIVE_MODE=mode, DOCICH_TWICA_STATE_DIR=str(directory),
           DOCICH_TWICA_FRAME_DIR=str(root / 'frames'), DOCICH_TWICA_REAL_FFMPEG=str(fake))
args = ['-f', 'x11grab', '-video_size', '320x180', '-framerate', '1', '-i', ':fixture',
        '-f', 'pulse', '-i', 'fixture', '-map', '0:v:0', '-map', '1:a:0',
        '-progress', 'pipe:1', '-f', 'null', '-']
wrapper = subprocess.Popen([sys.executable, '-m', 'docich.twica_ffmpeg', *args],
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, env=env)
native = None
def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('fixture deadline expired')
def absent(pid):
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True
try:
    wait_for(lambda: identity.exists() and identity.stat().st_size > 0)
    native = json.loads(identity.read_text())['pid']
    # The pipeline heartbeat follows signal-handler installation, preventing
    # a startup race where SIGINT could kill the wrapper before forwarding.
    wait_for(lambda: (directory / 'pipeline.json').exists())
    assert json.loads((directory / 'pipeline.json').read_text())['encoder_pid'] == native
    assert select.select([wrapper.stdout], [], [], 5)[0], 'fixture did not publish progress'
    assert wrapper.stdout.readline() == 'progress=continue\n'
    if mode in ('fallback', 'sigint'):
        path = Path(os.environ['SOREN_STOP_MODULE'])
        spec = importlib.util.spec_from_file_location('fixture_direct_stream', path)
        runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runner
        spec.loader.exec_module(runner)
        runner._graceful_stop_ffmpeg(wrapper, q_timeout_sec=.3, sigint_timeout_sec=.3)
    else:
        wrapper.kill()
    assert wrapper.wait(timeout=5) == (255 if mode == 'sigint' else -signal.SIGKILL)
    assert select.select([wrapper.stdout], [], [], 3)[0], 'native retained progress stdout after wrapper SIGKILL'
    assert wrapper.stdout.read(1) == ''
    wait_for(lambda: absent(native))  # Guardian must have waited/reaped native.
    if mode in ('fallback', 'sigint'):
        assert events.read_text().splitlines() == ['q', 'SIGINT']
    print('native reaped; progress EOF; stop mode=' + mode)
finally:
    if wrapper.poll() is None:
        wrapper.kill()
    wrapper.wait(timeout=5)
    if native and not absent(native):
        os.kill(native, signal.SIGKILL)  # Only the PID created by this fixture.
    # Reap adopted fixture descendants on Linux, including failed baselines.
    if sys.platform == 'linux':
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if not pid:
                time.sleep(.02)
    for stream in (wrapper.stdin, wrapper.stdout, wrapper.stderr):
        stream.close()
''')
    module = Path(__file__).resolve().parents[1] / 'games/soviet_now/lib/direct_stream.py'
    result = subprocess.run([sys.executable, str(scenario), str(tmp_path), stop_mode],
                            capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, SOREN_STOP_MODULE=str(module)))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'native reaped; progress EOF' in result.stdout
