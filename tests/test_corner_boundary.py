import fcntl
import json
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pytest
from docich.corner_boundary import (
    CornerWaitExpired,
    QUEUE_FILE,
    REGISTRY_FILE,
    _program_slot,
    boundary_ready,
    program_lock,
)
from types import SimpleNamespace


def _owner(path, status):
    path.write_text(json.dumps({'status': status}))


def _queue(root):
    return json.loads((root / QUEUE_FILE).read_text())


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
    assert _queue(tmp_path)['retro_corner.json']['status'] == 'done'
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
    assert _queue(tmp_path)['b.json']['status'] == 'done'


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
    assert _queue(tmp_path)['b.json']['status'] == 'expired'


def test_body_error_marks_queue_error(tmp_path):
    owner = tmp_path / 'retro_corner.json'
    _owner(owner, 'idle')
    with pytest.raises(RuntimeError, match='boom'):
        with _program_slot(tmp_path, owner, time.time() + 60, sleep=lambda s: None, poll_s=0):
            raise RuntimeError('boom')
    assert _queue(tmp_path)['retro_corner.json']['status'] == 'error'
