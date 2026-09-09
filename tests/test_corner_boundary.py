import fcntl
import json
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pytest
from docich.corner_boundary import (
    CornerWaitExpired,
    QUEUE_DIR,
    REGISTRY_FILE,
    ProgramRegistryError,
    _program_slot,
    _queue_read,
    boundary_ready,
    program_lock,
)
from types import SimpleNamespace


def _owner(path, status):
    path.write_text(json.dumps({'status': status}))


def _queue(root, key):
    return _queue_read(root, key)


def _fake_g(root):
    return SimpleNamespace(webui=SimpleNamespace(soren_root=str(root)))


def test_boundary_requires_new_confirmed_completion(tmp_path):
    assert not boundary_ready(tmp_path, 100)
    (tmp_path / 'corner_boundary_prediction.json').write_text(json.dumps({'completed_at': 99}))
    assert not boundary_ready(tmp_path, 100)
    (tmp_path / 'corner_boundary_improvement.json').write_text(json.dumps({'completed_at': 101}))
    assert boundary_ready(tmp_path, 100)


def test_corrupt_future_or_nonfinite_boundary_does_not_unlock(tmp_path):
    for value in ['{', '{"completed_at": NaN}', '{"completed_at": true}', '{"completed_at": "101"}']:
        (tmp_path / 'corner_boundary_prediction.json').write_text(value)
        assert not boundary_ready(tmp_path, 100)


def test_free_slot_runs_and_marks_done(tmp_path):
    owner = tmp_path / 'retro_corner.json'
    _owner(owner, 'idle')
    with _program_slot(tmp_path, owner, time.time() + 60, sleep=lambda s: None, poll_s=0) as root:
        assert root == tmp_path
    assert _queue(tmp_path, 'retro_corner')['status'] == 'done'
    assert json.loads((tmp_path / REGISTRY_FILE).read_text())['owner_state'] == str(owner)


def test_failfast_without_deadline_preserved(tmp_path):
    root = tmp_path / 'tmp' / 'state'
    root.mkdir(parents=True)
    busy = tmp_path / 'a.json'
    _owner(busy, 'active')
    (root / REGISTRY_FILE).write_text(json.dumps({'owner_state': str(busy)}))
    mine = tmp_path / 'b.json'
    _owner(mine, 'idle')
    with pytest.raises(RuntimeError, match='another corner'):
        with program_lock(_fake_g(tmp_path), mine):
            pass


def test_waiter_proceeds_after_owner_finishes(tmp_path):
    busy = tmp_path / 'a.json'
    _owner(busy, 'active')
    (tmp_path / REGISTRY_FILE).write_text(json.dumps({'owner_state': str(busy)}))
    mine = tmp_path / 'b.json'
    _owner(mine, 'idle')
    held = (tmp_path / 'docich_program.lock').open('a')
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        _owner(busy, 'completed')
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()

    with _program_slot(tmp_path, mine, time.time() + 60, sleep=sleep, poll_s=0) as root:
        assert root == tmp_path
    assert waits, 'waiter must block until the owner finishes'
    assert _queue(tmp_path, 'b')['status'] == 'done'


def test_earlier_waiter_wins_over_latecomer(tmp_path):
    first = tmp_path / 'a.json'
    _owner(first, 'idle')
    second = tmp_path / 'b.json'
    _owner(second, 'idle')
    order = []
    with _program_slot(tmp_path, first, time.time() + 60, sleep=lambda s: None, poll_s=0):
        order.append('first')
    with _program_slot(tmp_path, second, time.time() + 60, sleep=lambda s: None, poll_s=0):
        order.append('second')
    assert order == ['first', 'second']
    # 同時待ちのキーが互いを消さない (キー別ファイル化の回帰)。
    assert _queue(tmp_path, 'a')['status'] == 'done'
    assert _queue(tmp_path, 'b')['status'] == 'done'


def test_registry_corruption_is_error_not_cancelled(tmp_path):
    (tmp_path / REGISTRY_FILE).write_text('{broken', encoding='utf-8')
    mine = tmp_path / 'b.json'
    _owner(mine, 'idle')
    with pytest.raises(ProgramRegistryError):
        with _program_slot(tmp_path, mine, time.time() + 60, sleep=lambda s: None, poll_s=0):
            pass
    assert _queue(tmp_path, 'b')['status'] == 'error'


def test_waiter_expires_at_deadline(tmp_path):
    busy = tmp_path / 'a.json'
    _owner(busy, 'active')
    (tmp_path / REGISTRY_FILE).write_text(json.dumps({'owner_state': str(busy)}))
    mine = tmp_path / 'b.json'
    _owner(mine, 'idle')
    held = (tmp_path / 'docich_program.lock').open('a')
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(CornerWaitExpired):
            with _program_slot(tmp_path, mine, time.time() - 1, sleep=lambda s: None, poll_s=0):
                pass
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()
    assert _queue(tmp_path, 'b')['status'] == 'expired'


def test_body_error_marks_queue_error(tmp_path):
    owner = tmp_path / 'retro_corner.json'
    _owner(owner, 'idle')
    with pytest.raises(RuntimeError, match='boom'):
        with _program_slot(tmp_path, owner, time.time() + 60, sleep=lambda s: None, poll_s=0):
            raise RuntimeError('boom')
    assert _queue(tmp_path, 'retro_corner')['status'] == 'error'


def test_latecomer_does_not_overtake_earlier_waiter(tmp_path):
    mine = tmp_path / 'b.json'
    _owner(mine, 'idle')
    (tmp_path / QUEUE_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / QUEUE_DIR / 'a.json').write_text(json.dumps(
        {'status': 'waiting', 'requested_at': time.time() - 10}))
    with pytest.raises(CornerWaitExpired):
        with _program_slot(tmp_path, mine, time.time() - 1, sleep=lambda s: None, poll_s=0):
            pass
    assert _queue(tmp_path, 'a')['status'] == 'waiting'
    assert _queue(tmp_path, 'b')['status'] == 'expired'
