"""Shared program lock and confirmed Soren cycle boundary wait."""
from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path
import time

from .trading.soren_output import resolve_soren_root

QUEUE_FILE = 'docich_program_queue.json'
REGISTRY_FILE = 'docich_program_active.json'
# program_lock の保持中に他コーナーが starting/active のまま残る状態。
BUSY_OWNER_STATUSES = frozenset({'starting', 'active'})


class CornerWaitExpired(RuntimeError):
    """キュー待ちが窓期限を過ぎた。実行せず expired として終了する。"""


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
def program_lock(g, owner_state=None, *, wait_deadline_ts=None, sleep=time.sleep, poll_s=5.0):
    # Both profiles and both corners share the live Soren root, not docich state_dir.
    root = resolve_soren_root(g) / 'tmp/state'
    root.mkdir(parents=True, exist_ok=True)
    if owner_state is None or wait_deadline_ts is None:
        with (root / 'docich_program.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if owner_state is not None:
                _register_owner(root, owner_state)
            yield root
        return
    with _program_slot(root, owner_state, wait_deadline_ts, sleep=sleep, poll_s=poll_s) as slot_root:
        yield slot_root


def _register_owner(root, owner_state):
    from .game_switch import atomic_write_json
    registry = root / REGISTRY_FILE
    if registry.exists():
        previous = json.loads(registry.read_text())['owner_state']
        if previous != str(owner_state) and Path(previous).exists():
            state = json.loads(Path(previous).read_text())
            if state.get('status') in BUSY_OWNER_STATUSES:
                raise RuntimeError('another corner must recover or finish before starting')
    atomic_write_json(registry, {'owner_state': str(owner_state)})


def _owner_free(root, owner_state):
    """他コーナーが starting/active で占有していなければ True。"""
    registry = root / REGISTRY_FILE
    if not registry.exists():
        return True
    try:
        previous = json.loads(registry.read_text())['owner_state']
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError(f'program registryが不正です: {exc}') from exc
    if previous == str(owner_state):
        return True
    if not Path(previous).exists():
        return True
    try:
        state = json.loads(Path(previous).read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError(f'他コーナー状態が不正です: {exc}') from exc
    return state.get('status') not in BUSY_OWNER_STATUSES


def _queue_update(root, key, **fields):
    from .game_switch import atomic_write_json
    path = root / QUEUE_FILE
    try:
        queue = json.loads(path.read_text())
        if not isinstance(queue, dict):
            queue = {}
    except (OSError, ValueError):
        queue = {}
    entry = queue.get(key)
    if not isinstance(entry, dict):
        entry = {}
    entry.update(fields)
    queue[key] = entry
    atomic_write_json(path, queue)


@contextmanager
def _program_slot(root, owner_state, wait_deadline_ts, *, sleep=time.sleep, poll_s=5.0):
    """キュー待ち付き program 占有。空きが来たら所有権を取って yield する。

    毎分起動の oneshot tick が単一飛行することを前提にする。同一コーナーの
    重複 tick は呼び出し側で noop 化すること (program 待ち行列に積まない)。
    期限切れ時は CornerWaitExpired を送出する (実行しない)。
    """
    key = Path(owner_state).name
    requested_at = time.time()
    root.mkdir(parents=True, exist_ok=True)
    _queue_update(root, key, status='waiting', requested_at=requested_at,
                  wait_deadline_ts=wait_deadline_ts)
    lock_path = root / 'docich_program.lock'
    try:
        while True:
            with lock_path.open('a') as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    acquired = False
                else:
                    if _owner_free(root, owner_state):
                        from .game_switch import atomic_write_json
                        atomic_write_json(root / REGISTRY_FILE, {'owner_state': str(owner_state)})
                        _queue_update(root, key, status='running')
                        try:
                            yield root
                        except BaseException:
                            _queue_update(root, key, status='error')
                            raise
                        else:
                            _queue_update(root, key, status='done')
                        return
            if time.time() >= wait_deadline_ts:
                _queue_update(root, key, status='expired')
                raise CornerWaitExpired(
                    f'program 待ちが窓期限を過ぎました: {key}')
            sleep(poll_s)
    except BaseException:
        try:
            current = json.loads((root / QUEUE_FILE).read_text()).get(key, {})
        except (OSError, ValueError):
            current = {}
        if current.get('status') == 'waiting':
            _queue_update(root, key, status='cancelled')
        raise


def wait_for_boundary(root, requested_at, *, sleep=time.sleep):
    while not boundary_ready(root, requested_at):
        sleep(5)
