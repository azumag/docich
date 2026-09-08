"""Shared program lock and confirmed Soren cycle boundary wait."""
from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path
import time

from .trading.soren_output import resolve_soren_root


def boundary_ready(state_dir: Path, requested_at: float) -> bool:
    for kind in ('prediction', 'improvement'):
        try:
            stamp = json.loads((state_dir / f'corner_boundary_{kind}.json').read_text())['completed_at']
            if type(stamp) in (int, float) and math.isfinite(stamp) and requested_at <= stamp <= time.time():
                return True
        except (OSError, ValueError, TypeError, KeyError):
            pass
    return False


@contextmanager
def program_lock(g, owner_state=None):
    # Both profiles and both corners share the live Soren root, not docich state_dir.
    root = resolve_soren_root(g) / 'tmp/state'
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'docich_program.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if owner_state is not None:
            from .game_switch import atomic_write_json
            registry = root / 'docich_program_active.json'
            if registry.exists():
                previous = json.loads(registry.read_text())['owner_state']
                if previous != str(owner_state) and Path(previous).exists():
                    state = json.loads(Path(previous).read_text())
                    if state.get('status') in ('starting', 'active'):
                        raise RuntimeError('another corner must recover or finish before starting')
            atomic_write_json(registry, {'owner_state': str(owner_state)})
        yield root


def wait_for_boundary(root, requested_at, *, sleep=time.sleep):
    while not boundary_ready(root, requested_at):
        sleep(5)
