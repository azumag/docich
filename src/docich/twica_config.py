"""One environment contract shared by the stream adapter and renderer service."""
from dataclasses import dataclass
import os
from pathlib import Path
import re

@dataclass(frozen=True)
class CommonConfig:
    enabled: bool
    state: Path
    frames: Path
    python: str
    fps: int
    proxy_ports: tuple[int, int]
    audio_sink: str


def load_common_config(soren_root: Path, env=None) -> CommonConfig:
    env = os.environ if env is None else env
    raw = str(env.get('DOCICH_TWICA_COMMON_ENABLED', '0')).lower().strip()
    if raw not in {'0', '1', 'false', 'true', 'off', 'on'}:
        raise ValueError('invalid common overlay enable flag')
    enabled = raw in {'1', 'true', 'on'}
    runtime = env.get('XDG_RUNTIME_DIR') or f'/run/user/{os.geteuid()}'
    state = Path(env.get('DOCICH_TWICA_STATE_DIR') or soren_root / 'tmp/state/twica-common')
    frames = Path(env.get('DOCICH_TWICA_FRAME_DIR') or Path(runtime) / 'docich-twica-common')
    if not state.is_absolute() or not frames.is_absolute():
        raise ValueError('common overlay directories must be absolute')
    fps = int(env.get('DOCICH_TWICA_FPS', '15'))
    port = int(env.get('SOREN_DIRECT_TWICA_PROXY_PORT', '18080'))
    if not 1 <= fps <= 30 or not 1 <= port <= 65535:
        raise ValueError('invalid common overlay numeric setting')
    sink = env.get('DOCICH_TWICA_AUDIO_SINK') or env.get('SOREN_DIRECT_STREAM_PULSE_SOURCE', 'soren_null.monitor')
    if sink.endswith('.monitor'):
        sink = sink[:-8]
    if not re.fullmatch(r'[A-Za-z0-9_.:@+-]+', sink):
        raise ValueError('invalid common overlay audio sink')
    python = str(env.get('DOCICH_TWICA_PYTHON') or '/home/ubuntu/docich/.venv-twica/bin/python')
    return CommonConfig(enabled, state, frames, python, fps, (port, 18081), sink)
