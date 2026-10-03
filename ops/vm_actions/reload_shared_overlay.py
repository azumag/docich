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


def ready():
    port = int(os.environ.get('SOREN_SHARED_OVERLAY_PORT', '8092'))
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=2) as response:
        health = json.load(response)
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/__soren_overlay/broadcast/state', timeout=2) as response:
        state = json.load(response)
    return health.get('ready') is True and state.get('gameGapEnabled') is True


def reload_shared_overlay(*, timeout=45):
    before = snapshot()
    subprocess.run(['sudo', '-n', 'systemctl', 'restart', UNIT], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if snapshot() != before:
            raise RuntimeError('protected process or active game changed')
        try:
            if ready():
                if snapshot() != before:
                    raise RuntimeError('protected process or active game changed')
                return {'status': 'reloaded', 'continuity': before}
        except (OSError, ValueError):
            pass
        time.sleep(.25)
    raise RuntimeError('shared overlay not ready')


if __name__ == '__main__':
    try:
        receipt = reload_shared_overlay()
        path = ROOT / 'tmp/state/shared_overlay_reload_receipt.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(receipt, separators=(',', ':')) + '\n')
        os.replace(temporary, path)
        print('{"status":"reloaded","protected_processes":"maintained"}')
    except Exception:
        # Fixed category only; do not print service output or exception payloads.
        raise SystemExit('shared overlay reload or continuity verification failed')
