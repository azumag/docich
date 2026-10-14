"""Reload only the shared browser service; verify game/audio/encoder continuity."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path('/home/ubuntu/soren')
DOCICH_STATE = Path('/home/ubuntu/docich/run-soren-live')
UNIT = 'soren-shared-overlay.service'

EXIT_HEALTH_UNREACHABLE = 20
EXIT_BROWSER_NOT_READY = 21
EXIT_WINDOW_NOT_READY = 22
EXIT_LAYOUT_NOT_READY = 23
EXIT_OVERLAY_NOT_READY = 24
EXIT_HEALTH_NOT_READY = 25
EXIT_GAME_GAP_DISABLED = 26
EXIT_STATE_UNREACHABLE = 27


class ReadinessTimeout(RuntimeError):
    def __init__(self, exit_code):
        super().__init__('shared overlay not ready')
        self.exit_code = exit_code


def _read(path):
    if path.is_symlink() or path.stat().st_size > 262144:
        raise RuntimeError('invalid runtime record')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RuntimeError('invalid runtime record')
    return value


def _process(pid):
    if type(pid) is not int or pid <= 1:
        raise RuntimeError('required process unavailable')
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] == 'Z':
        raise RuntimeError('required process unavailable')
    return [pid, int(fields[19])]


def snapshot():
    stream = _read(ROOT / 'tmp/state/direct_stream/status.json')
    if stream.get('running') is not True:
        raise RuntimeError('stream intentionally stopped or unavailable')
    audio = int((ROOT / 'tmp/state/audio_worker.pid').read_text().strip())
    switch = _read(DOCICH_STATE / 'game_switch.json')
    active = switch.get('active')
    if switch.get('phase') != 'ready' or not isinstance(active, dict):
        raise RuntimeError('game transition underway')
    groups = []
    runtime_id = active.get('runtime_id')
    if isinstance(runtime_id, str) and '/' not in runtime_id:
        presentation = DOCICH_STATE / 'runtimes' / runtime_id / 'presentation.json'
        if presentation.exists():
            groups = [_process(pid) for pid in _read(presentation).get('groups', [])]
    return {'stream': _process(stream.get('pid')), 'encoder': _process(stream.get('ffmpeg_pid')),
            'audio': _process(audio), 'active': active, 'game_processes': groups}


def _url_json(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError('invalid json object')
    return value


def readiness():
    """Return only a fixed readiness class; never expose service/body details."""
    port = int(os.environ.get('SOREN_SHARED_OVERLAY_PORT', '8092'))
    try:
        health = _url_json(f'http://127.0.0.1:{port}/healthz')
    except (OSError, ValueError, TimeoutError):
        return False, EXIT_HEALTH_UNREACHABLE
    if health.get('browserReady') is not True:
        return False, EXIT_BROWSER_NOT_READY
    if health.get('windowReady') is not True:
        return False, EXIT_WINDOW_NOT_READY
    if health.get('layoutReady') is not True:
        return False, EXIT_LAYOUT_NOT_READY
    if health.get('overlayReady') is not True:
        return False, EXIT_OVERLAY_NOT_READY
    if health.get('ready') is not True:
        return False, EXIT_HEALTH_NOT_READY
    try:
        state = _url_json(f'http://127.0.0.1:{port}/__soren_overlay/broadcast/state')
    except (OSError, ValueError, TimeoutError):
        return False, EXIT_STATE_UNREACHABLE
    if state.get('gameGapEnabled') is not True:
        return False, EXIT_GAME_GAP_DISABLED
    return True, 0


def ready():
    return readiness()[0]


def reload_shared_overlay(*, timeout=45):
    before = snapshot()
    subprocess.run(['sudo', '-n', 'systemctl', 'restart', UNIT], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    deadline = time.monotonic() + timeout
    last_exit_code = EXIT_HEALTH_UNREACHABLE
    while time.monotonic() < deadline:
        if snapshot() != before:
            raise RuntimeError('protected process or active game changed')
        ok, last_exit_code = readiness()
        if ok:
            if snapshot() != before:
                raise RuntimeError('protected process or active game changed')
            return {'status': 'reloaded', 'continuity': before}
        time.sleep(.25)
    raise ReadinessTimeout(last_exit_code)


if __name__ == '__main__':
    try:
        receipt = reload_shared_overlay()
        path = ROOT / 'tmp/state/shared_overlay_reload_receipt.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(receipt, separators=(',', ':')) + '\n')
        os.replace(temporary, path)
        print('{"status":"reloaded","protected_processes":"maintained"}')
    except ReadinessTimeout as exc:
        # Numeric code is the only production diagnostic surfaced through the
        # fixed VM gateway; no service output, URL body, path, PID, or exception
        # text crosses the boundary.
        raise SystemExit(exc.exit_code)
    except Exception:
        raise SystemExit(1)
