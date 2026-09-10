"""Shared program lock and confirmed Soren cycle boundary wait."""
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import time

from .trading.soren_output import resolve_soren_root

QUEUE_DIR = 'docich_program_queue'
REGISTRY_FILE = 'docich_program_active.json'
# program_lock の保持中に他コーナーが starting/active のまま残る状態。
BUSY_OWNER_STATUSES = frozenset({'starting', 'active'})


class CornerWaitExpired(RuntimeError):
    """キュー待ちが窓期限を過ぎた。実行せず expired として終了する。"""


class ProgramRegistryError(RuntimeError):
    """program registry / 他コーナー状態が壊れている (fail-closed で停止する)。"""


PREDICTION_ACTIVE_STATUSES = frozenset({'ACTIVE', 'LOCKED'})
PREDICTION_STATE_FILE = 'current_prediction.json'
PREDICTION_WORKER = 'prediction_worker'


def _pid_is_alive(pid):
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_pid_file(path):
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError:
        return None
    for token in raw.split():
        if token.isdigit():
            return int(token)
    return None


def _worker_running(root, name):
    if (root / f'{name}.paused').is_file():
        return False
    return _pid_is_alive(_read_pid_file(root / f'{name}.pid'))


def _prediction_in_flight(state_dir: Path) -> bool:
    """True only while the prediction worker runs an ACTIVE/LOCKED prediction."""
    if not _worker_running(state_dir, PREDICTION_WORKER):
        return False
    try:
        state = json.loads((state_dir / PREDICTION_STATE_FILE).read_text())
    except (OSError, ValueError):
        return False
    return isinstance(state, dict) and state.get('status') in PREDICTION_ACTIVE_STATUSES


def _fresh_boundary(state_dir: Path, kind: str, requested_at: float, now: float) -> bool:
    try:
        stamp = json.loads((state_dir / f'corner_boundary_{kind}.json').read_text())['completed_at']
    except (OSError, ValueError, TypeError, KeyError):
        return False
    if type(stamp) not in (int, float) or not math.isfinite(stamp):
        return False
    return requested_at <= stamp <= now


def boundary_ready(state_dir: Path, requested_at: float, *, now=None) -> bool:
    """Confirmed prediction boundary while a prediction is actually in flight.

    Only an in-flight prediction holds the corner back (so the prediction is
    not paused mid-count). Improvement cycles and interleaved A/B are not
    gated: the match/round boundary that keeps a game from being cut is owned
    by the game-switch coordinator, and A/B simply pauses and resumes. A
    stopped or paused prediction worker never blocks, and when no prediction
    is in flight the corner may proceed immediately.
    """
    now = time.time() if now is None else now
    if _prediction_in_flight(state_dir) and not _fresh_boundary(state_dir, 'prediction', requested_at, now):
        return False
    return True


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
        try:
            previous = json.loads(registry.read_text())['owner_state']
        except (OSError, ValueError, KeyError) as exc:
            raise ProgramRegistryError(f'program registryが不正です: {exc}') from exc
        if previous != str(owner_state) and Path(previous).exists():
            try:
                state = json.loads(Path(previous).read_text())
            except (OSError, ValueError) as exc:
                raise ProgramRegistryError(f'他コーナー状態が不正です: {exc}') from exc
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
        raise ProgramRegistryError(f'program registryが不正です: {exc}') from exc
    if previous == str(owner_state):
        return True
    if not Path(previous).exists():
        return True
    try:
        state = json.loads(Path(previous).read_text())
    except (OSError, ValueError) as exc:
        raise ProgramRegistryError(f'他コーナー状態が不正です: {exc}') from exc
    return state.get('status') not in BUSY_OWNER_STATUSES


def _queue_path(root, key):
    return root / QUEUE_DIR / f'{key}.json'


def _queue_write(root, key, **fields):
    """キー別ファイルへの原子置換。読取→書込の競合で他キーを失わない。"""
    from .game_switch import atomic_write_json
    path = _queue_path(root, key)
    try:
        entry = json.loads(path.read_text())
        if not isinstance(entry, dict):
            entry = {}
    except (OSError, ValueError):
        entry = {}
    entry.update(fields)
    atomic_write_json(path, entry)


def _queue_read(root, key):
    try:
        entry = json.loads(_queue_path(root, key).read_text())
    except (OSError, ValueError):
        return {}
    return entry if isinstance(entry, dict) else {}


def _earlier_waiter_exists(root, key, requested_at):
    """自分より先に待ち始めた他コーナーがいれば True (FIFO順序の維持)。"""
    queue_dir = root / QUEUE_DIR
    try:
        names = sorted(p.name for p in queue_dir.iterdir() if p.suffix == '.json')
    except OSError:
        return False
    for name in names:
        other = name[:-len('.json')]
        if other == key:
            continue
        entry = _queue_read(root, other)
        if entry.get('status') not in ('waiting', 'waiting_turn'):
            continue
        try:
            other_requested = float(entry.get('requested_at', 0) or 0)
        except (TypeError, ValueError):
            continue
        if other_requested < requested_at:
            return True
    return False


@contextmanager
def _program_slot(root, owner_state, wait_deadline_ts, *, sleep=time.sleep, poll_s=5.0,
                  requested_at=None, queue_status='waiting'):
    """キュー待ち付き program 占有。順番が来たら所有権を取って yield する。

    待ち行列はキー別ファイル (FIFO: 先に待ち始めた他コーナーを追い越さない)。
    毎分起動の oneshot tick が単一飛行することを前提にする。同一コーナーの
    重複 tick は呼び出し側で noop 化すること (program 待ち行列に積まない)。
    期限切れ時は CornerWaitExpired を送出する (実行しない)。
    """
    key = Path(owner_state).stem or Path(owner_state).name
    if requested_at is None:
        requested_at = time.time()
    root.mkdir(parents=True, exist_ok=True)
    _queue_write(root, key, status=queue_status, requested_at=requested_at,
                 wait_deadline_ts=wait_deadline_ts)
    lock_path = root / 'docich_program.lock'
    try:
        while True:
            with lock_path.open('a') as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    if _owner_free(root, owner_state) and not _earlier_waiter_exists(
                        root, key, requested_at
                    ):
                        from .game_switch import atomic_write_json
                        atomic_write_json(root / REGISTRY_FILE, {'owner_state': str(owner_state)})
                        _queue_write(root, key, status='running')
                        try:
                            yield root
                        except BaseException:
                            _queue_write(root, key, status='error')
                            raise
                        else:
                            _queue_write(root, key, status='done')
                        return
            if time.time() >= wait_deadline_ts:
                _queue_write(root, key, status='expired')
                raise CornerWaitExpired(
                    f'program 待ちが窓期限を過ぎました: {key}')
            sleep(poll_s)
    except CornerWaitExpired:
        raise
    except BaseException as exc:
        # 待機のまま落ちた中断は cancelled、registry 破損等は error と区別する。
        if _queue_read(root, key).get('status') in ('waiting', 'waiting_turn', 'waiting_boundary'):
            _queue_write(root, key, status='error' if isinstance(exc, ProgramRegistryError) else 'cancelled')
        raise


@contextmanager
def program_slot(g, owner_state, *, requested_at, wait_deadline_ts, wait_boundary=True,
                 sleep=time.sleep, poll_s=5.0, now=time.time):
    """境界待ちと program 所有を分離したコーナー枠。

    境界待ちの間は program flock を保持しない (他コーナーの発火を塞がない)。
    確定境界が満たされた後だけ枠を取り、発火〜終了の間 flock を保持する。
    境界待ちも枠待ちも ``wait_deadline_ts`` を超えると CornerWaitExpired。
    """
    root = resolve_soren_root(g) / 'tmp/state'
    root.mkdir(parents=True, exist_ok=True)
    key = Path(owner_state).stem or Path(owner_state).name
    _queue_write(root, key, status='waiting_boundary', requested_at=requested_at,
                 wait_deadline_ts=wait_deadline_ts)
    if wait_boundary:
        try:
            while not boundary_ready(root, requested_at, now=now()):
                if now() >= wait_deadline_ts:
                    _queue_write(root, key, status='expired')
                    raise CornerWaitExpired(f'境界待ちが窓期限を過ぎました: {key}')
                sleep(poll_s)
        except CornerWaitExpired:
            raise
        except BaseException as exc:
            _queue_write(root, key, status='error' if isinstance(exc, ProgramRegistryError) else 'cancelled')
            raise
    with _program_slot(root, owner_state, wait_deadline_ts, sleep=sleep, poll_s=poll_s,
                       requested_at=requested_at, queue_status='waiting_turn') as slot_root:
        yield slot_root


def wait_for_boundary(root, requested_at, *, deadline_ts=None, sleep=time.sleep, poll_s=5):
    """Confirmed cycle boundary with an optional hard deadline (fail closed)."""
    while not boundary_ready(root, requested_at):
        if deadline_ts is not None and time.time() >= deadline_ts:
            raise CornerWaitExpired('境界待ちが期限を過ぎました')
        sleep(poll_s)
