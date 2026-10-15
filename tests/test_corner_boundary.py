import fcntl
import json
import os
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pytest
import docich.corner_boundary as boundary
from docich.corner_boundary import (
    CornerWaitExpired,
    QUEUE_DIR,
    REGISTRY_FILE,
    ProgramRegistryError,
    _program_slot,
    _queue_read,
    boundary_ready,
    program_lock,
    program_slot,
)
from types import SimpleNamespace


def _owner(path, status):
    path.write_text(json.dumps({'status': status}))


@pytest.fixture(autouse=True)
def _no_ambient_audio_queue(monkeypatch):
    # The audio gate reads COMMENT_QUEUE_DIR; a developer shell that exports it
    # must not change which queue these tests inspect.
    monkeypatch.delenv('COMMENT_QUEUE_DIR', raising=False)


def _queue(root, key):
    return _queue_read(root, key)


def _fake_g(root):
    return SimpleNamespace(webui=SimpleNamespace(soren_root=str(root)))


def _state_dir(tmp_path):
    root = tmp_path / 'tmp' / 'state'
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_boundary_free_when_nothing_active(tmp_path):
    state = _state_dir(tmp_path)
    assert boundary_ready(state, 100)


def test_boundary_does_not_gate_on_improvement(tmp_path):
    # An active improvement/A-B state must not hold the corner back; only an
    # in-flight prediction does.
    state = _state_dir(tmp_path)
    (state.parent / 'improve.lock').write_text('')
    (state / 'ab_state.json').write_text('{}')
    (state / 'ab_candidate').mkdir()
    (state / 'ab_candidate' / 'meta.json').write_text('{}')
    assert boundary_ready(state, 100)


def test_boundary_requires_prediction_only_when_in_flight(tmp_path):
    state = _state_dir(tmp_path)
    # Stopped worker (no pid) never blocks, even with a stale ACTIVE file.
    (state / 'current_prediction.json').write_text(json.dumps({'status': 'ACTIVE'}))
    assert boundary_ready(state, 100)
    # Running worker + in-flight prediction requires a fresh prediction boundary.
    (state / 'prediction_worker.pid').write_text(str(os.getpid()))
    assert not boundary_ready(state, 100)
    (state / 'corner_boundary_prediction.json').write_text(json.dumps({'completed_at': time.time() - 1}))
    assert boundary_ready(state, 100)
    # A resolved prediction no longer blocks.
    (state / 'current_prediction.json').write_text(json.dumps({'status': 'RESOLVED'}))
    assert boundary_ready(state, 100)


def test_paused_prediction_worker_does_not_block(tmp_path):
    state = _state_dir(tmp_path)
    (state / 'prediction_worker.pid').write_text(str(os.getpid()))
    (state / 'prediction_worker.paused').write_text('')
    (state / 'current_prediction.json').write_text(json.dumps({'status': 'ACTIVE'}))
    assert boundary_ready(state, 100)


def test_corrupt_future_or_nonfinite_boundary_does_not_unlock(tmp_path):
    state = _state_dir(tmp_path)
    (state / 'prediction_worker.pid').write_text(str(os.getpid()))
    (state / 'current_prediction.json').write_text(json.dumps({'status': 'ACTIVE'}))
    for value in ['{', '{"completed_at": NaN}', '{"completed_at": true}', '{"completed_at": "101"}']:
        (state / 'corner_boundary_prediction.json').write_text(value)
        assert not boundary_ready(state, 100)


def _audio_queue(root):
    return root / 'tmp' / '.comment_queue'


def test_boundary_waits_for_a_completely_quiet_audio_queue(tmp_path):
    state = _state_dir(tmp_path)
    queue = _audio_queue(tmp_path)
    queue.mkdir(parents=True)
    assert boundary_ready(state, 100)
    # A queued item blocks even though nothing is playing yet.
    (queue / 'comment_announce_1_2_3_chat.txt').write_text('viewer comment')
    assert not boundary_ready(state, 100)
    # The worker renames it while playing; that still blocks (an intro must not
    # cut into a comment that is already being read out).
    (queue / 'comment_announce_1_2_3_chat.txt').rename(
        queue / 'comment_announce_1_2_3_chat.playing')
    assert not boundary_ready(state, 100)
    (queue / 'comment_announce_1_2_3_chat.playing').unlink()
    assert boundary_ready(state, 100)


def test_boundary_audio_gate_counts_only_queue_items(tmp_path):
    state = _state_dir(tmp_path)
    queue = _audio_queue(tmp_path)
    queue.mkdir(parents=True)
    (queue / 'comment_announce_1_2_3_chat.txt.speaker').write_text('sidecar')
    (queue / 'processed_batch_hashes.log').write_text('log')
    (queue / 'radio_1_2_news_3.txt').write_text('radio has its own queue')
    (queue / 'spoken_history').mkdir()
    (queue / 'comment_announce_4_5_6_chat.txt').mkdir()
    assert boundary_ready(state, 100)


def test_boundary_audio_gate_honors_the_queue_override(tmp_path, monkeypatch):
    state = _state_dir(tmp_path)
    custom = tmp_path / 'custom_queue'
    custom.mkdir()
    monkeypatch.setenv('COMMENT_QUEUE_DIR', str(custom))
    (custom / 'comment_announce_1_2_3_chat.txt').write_text('x')
    assert not boundary_ready(state, 100)
    # The default queue is not consulted when it is overridden.
    default = _audio_queue(tmp_path)
    default.mkdir(parents=True)
    (custom / 'comment_announce_1_2_3_chat.txt').unlink()
    assert boundary_ready(state, 100)


def test_boundary_audio_gate_reads_the_soren_env_like_the_sink(tmp_path):
    state = _state_dir(tmp_path)
    (tmp_path / '.env').write_text('COMMENT_QUEUE_DIR=tmp/env_queue\n')
    env_queue = tmp_path / 'tmp' / 'env_queue'
    env_queue.mkdir(parents=True)
    (env_queue / 'comment_announce_1_2_3_chat.txt').write_text('x')
    assert not boundary_ready(state, 100)


def test_missing_and_unreadable_audio_queue_never_block(tmp_path):
    state = _state_dir(tmp_path)
    assert boundary_ready(state, 100)
    # A path that cannot be listed is "quiet" too: a broken queue must not hold
    # every corner back.
    queue = _audio_queue(tmp_path)
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text('not a directory')
    assert boundary_ready(state, 100)


def test_boundary_requires_both_prediction_and_audio(tmp_path):
    state = _state_dir(tmp_path)
    queue = _audio_queue(tmp_path)
    queue.mkdir(parents=True)
    (state / 'prediction_worker.pid').write_text(str(os.getpid()))
    (state / 'current_prediction.json').write_text(json.dumps({'status': 'ACTIVE'}))
    (queue / 'comment_announce_1_2_3_chat.txt').write_text('x')
    assert not boundary_ready(state, 100)
    # A fresh prediction boundary alone is not enough while audio is busy.
    (state / 'corner_boundary_prediction.json').write_text(
        json.dumps({'completed_at': time.time() - 1}))
    assert not boundary_ready(state, 100)
    (queue / 'comment_announce_1_2_3_chat.txt').unlink()
    assert boundary_ready(state, 100)


def test_program_slot_waits_for_audio_then_owns_slot(tmp_path):
    state = tmp_path / 'tmp' / 'state'
    state.mkdir(parents=True)
    queue = _audio_queue(tmp_path)
    queue.mkdir()
    item = queue / 'comment_announce_1_2_3_chat.txt'
    item.write_text('x')
    owner = tmp_path / 'retro_corner.json'
    _owner(owner, 'idle')
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        item.unlink()

    with program_slot(_fake_g(tmp_path), owner, requested_at=time.time() - 10,
                      wait_deadline_ts=time.time() + 60, sleep=sleep, poll_s=0):
        pass
    assert slept, 'must wait for the audio queue to become quiet'
    assert _queue(state, 'retro_corner')['status'] == 'done'


def test_program_slot_expires_while_audio_is_still_busy(tmp_path):
    state = tmp_path / 'tmp' / 'state'
    state.mkdir(parents=True)
    queue = _audio_queue(tmp_path)
    queue.mkdir()
    (queue / 'comment_announce_1_2_3_chat.playing').write_text('x')
    owner = tmp_path / 'retro_corner.json'
    _owner(owner, 'idle')
    with pytest.raises(CornerWaitExpired):
        with program_slot(_fake_g(tmp_path), owner, requested_at=time.time() - 10,
                          wait_deadline_ts=time.time() - 1, sleep=lambda s: None, poll_s=0):
            pass
    assert _queue(state, 'retro_corner')['status'] == 'expired'


def test_boundary_waiter_does_not_block_waiting_turn(tmp_path):
    (tmp_path / QUEUE_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / QUEUE_DIR / 'a.json').write_text(json.dumps(
        {'status': 'waiting_boundary', 'requested_at': time.time() - 10}))
    mine = tmp_path / 'b.json'
    _owner(mine, 'idle')
    with _program_slot(tmp_path, mine, time.time() + 60, sleep=lambda s: None, poll_s=0):
        pass
    assert _queue(tmp_path, 'b')['status'] == 'done'
    assert _queue(tmp_path, 'a')['status'] == 'waiting_boundary'


def test_program_slot_waits_boundary_then_owns_slot(tmp_path):
    state = tmp_path / 'tmp' / 'state'
    state.mkdir(parents=True)
    (state / 'prediction_worker.pid').write_text(str(os.getpid()))
    (state / 'current_prediction.json').write_text(json.dumps({'status': 'ACTIVE'}))
    owner = tmp_path / 'retro_corner.json'
    _owner(owner, 'idle')
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        (state / 'corner_boundary_prediction.json').write_text(json.dumps({'completed_at': time.time()}))

    with program_slot(_fake_g(tmp_path), owner, requested_at=time.time() - 10,
                      wait_deadline_ts=time.time() + 60, sleep=sleep, poll_s=0):
        pass
    assert slept, 'must wait for the boundary'
    assert _queue(state, 'retro_corner')['status'] == 'done'


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


@pytest.mark.parametrize("filename", ["paper_corner.json", "paper_corner_manual.json", "retro_corner.json"])
@pytest.mark.parametrize("case", ["restored", "mismatch", "draining", "recovery-required", "no-completion", "missing", "corrupt", "other-directory"])
@pytest.mark.parametrize("queued", [False, True])
def test_program_owner_paper_terminal_contract(tmp_path, monkeypatch, filename, case, queued):
    from docich import corner_terminal
    root = _state_dir(tmp_path)
    state_dir = tmp_path / "run"
    state_dir.mkdir()
    previous = state_dir / filename
    original = {"status": "failed", "previous_game": "sorengame", "completed_at": 100,
                "recovery_required": case == "recovery-required"}
    if case == "no-completion":
        original.pop("completed_at")
    previous.write_text(json.dumps(original))
    original_bytes = previous.read_bytes()
    (root / REGISTRY_FILE).write_text(json.dumps({"owner_state": str(previous)}))
    mine = (tmp_path if case == "other-directory" else state_dir) / "next_corner.json"
    _owner(mine, "idle")

    def load():
        if case == "corrupt":
            raise ValueError("invalid canonical")
        return {"phase": "draining" if case == "draining" else "ready",
                "active": {"game": "nsnake" if case == "mismatch" else "sorengame"}}, case == "missing"

    monkeypatch.setattr(corner_terminal, "GameSwitchStore", lambda _: SimpleNamespace(canonical=SimpleNamespace(load=load)))
    scope = (_program_slot(root, mine, time.time() - 1, sleep=lambda _: None)
             if queued else program_lock(_fake_g(tmp_path), mine))
    if filename.startswith("paper_") and case == "restored":
        with scope:
            assert json.loads((root / REGISTRY_FILE).read_text())["owner_state"] == str(mine)
    else:
        with pytest.raises(CornerWaitExpired if queued else RuntimeError):
            with scope:
                pytest.fail("unsafe owner must block")
    assert previous.read_bytes() == original_bytes


@pytest.mark.parametrize("case", ["restored", "mismatch", "draining", "recovery-required", "no-completion", "missing", "corrupt", "other-directory", "wrong-game"])
@pytest.mark.parametrize("queued", [False, True])
def test_program_owner_doushi_terminal_contract(tmp_path, monkeypatch, case, queued):
    # #2278: an automatic DOUSHI start recorded failed after its prepare_failed
    # rollback. Once canonical proves the previous game is back, the record is
    # observed as terminal and the shared program slot is free again; every
    # unproven or mismatched shape keeps blocking.
    from docich import corner_terminal
    root = _state_dir(tmp_path)
    state_dir = tmp_path / "run"
    state_dir.mkdir()
    previous = state_dir / "doushi_corner.json"
    original = {"status": "failed", "game": "doushi-view", "previous_game": "sorengame",
                "requested_at": 99, "completed_at": 100, "last_error_code": "prepare_failed",
                "recovery_required": case == "recovery-required"}
    if case == "no-completion":
        original.pop("completed_at")
    if case == "wrong-game":
        original["game"] = "nethack-view"
    previous.write_text(json.dumps(original))
    original_bytes = previous.read_bytes()
    (root / REGISTRY_FILE).write_text(json.dumps({"owner_state": str(previous)}))
    mine = (tmp_path if case == "other-directory" else state_dir) / "next_corner.json"
    _owner(mine, "idle")

    def load():
        if case == "corrupt":
            raise ValueError("invalid canonical")
        return {"phase": "draining" if case == "draining" else "ready",
                "active": {"game": "nsnake" if case == "mismatch" else "sorengame"}}, case == "missing"

    monkeypatch.setattr(corner_terminal, "GameSwitchStore", lambda _: SimpleNamespace(canonical=SimpleNamespace(load=load)))
    scope = (_program_slot(root, mine, time.time() - 1, sleep=lambda _: None)
             if queued else program_lock(_fake_g(tmp_path), mine))
    if case == "restored":
        with scope:
            assert json.loads((root / REGISTRY_FILE).read_text())["owner_state"] == str(mine)
    else:
        with pytest.raises(CornerWaitExpired if queued else RuntimeError):
            with scope:
                pytest.fail("unsafe owner must block")
    assert previous.read_bytes() == original_bytes


@pytest.mark.parametrize("completed", ["", "invalid", "2026-09-22T00:00:00", True, -1, float("nan"), float("inf"), []])
def test_invalid_completion_never_releases_paper_owner(tmp_path, monkeypatch, completed):
    from docich import corner_terminal
    monkeypatch.setattr(corner_terminal, "GameSwitchStore", lambda _: SimpleNamespace(
        canonical=SimpleNamespace(load=lambda: ({"phase": "ready", "active": {"game": "sorengame"}}, False))))
    state = {"status": "failed", "previous_game": "sorengame", "completed_at": completed}
    assert corner_terminal.normalize_terminal_paper_failure(tmp_path, state) is state


@pytest.mark.parametrize("recovery", [True, None, "false", 0, 1, []])
def test_invalid_or_required_recovery_never_releases_paper_owner(tmp_path, monkeypatch, recovery):
    from docich import corner_terminal
    monkeypatch.setattr(corner_terminal, "GameSwitchStore", lambda _: SimpleNamespace(
        canonical=SimpleNamespace(load=lambda: ({"phase": "ready", "active": {"game": "sorengame"}}, False))))
    state = {"status": "failed", "previous_game": "sorengame", "completed_at": 100,
             "recovery_required": recovery}
    assert corner_terminal.normalize_terminal_paper_failure(tmp_path, state) is state


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


def _memory_program_waiter(monkeypatch):
    """Exercise the real slot flow with only in-memory queue/lock side effects."""
    from pathlib import PurePosixPath
    from docich import corner_boundary as boundary
    from docich import game_switch

    state = SimpleNamespace(clock=100.0, held=False, lock_busy=False,
                            owner_busy=False, earlier=False, audio_busy=False,
                            prediction_busy=False, queue={}, registry=[],
                            queue_writes=[], boundary_checks=[], closed=0)

    class MemoryLock:
        def __enter__(self):
            return self

        def fileno(self):
            return self

        def __exit__(self, *args):
            state.held = False
            state.closed += 1

    class MemoryPath(PurePosixPath):
        def mkdir(self, **kwargs):
            pass

        def open(self, mode):
            assert self.name == 'docich_program.lock' and mode == 'a'
            return MemoryLock()

    def flock(lock, flags):
        assert flags == fcntl.LOCK_EX | fcntl.LOCK_NB
        if state.lock_busy:
            raise BlockingIOError
        assert not state.held
        state.held = True

    def queue_write(root, key, **fields):
        state.queue.setdefault(key, {}).update(fields)
        state.queue_writes.append((key, dict(fields)))

    def audio_quiet(root):
        state.boundary_checks.append((state.clock, state.held, state.audio_busy))
        return not state.audio_busy

    def register(path, value):
        assert state.held, 'owner registration must remain inside the program lock'
        state.registry.append(dict(value))

    monkeypatch.setattr(boundary, 'Path', MemoryPath)
    monkeypatch.setattr(boundary, 'resolve_soren_root', lambda g: MemoryPath('/synthetic/soren'))
    monkeypatch.setattr(boundary, 'fcntl', SimpleNamespace(flock=flock,
                         LOCK_EX=fcntl.LOCK_EX, LOCK_NB=fcntl.LOCK_NB))
    monkeypatch.setattr(boundary, 'time', SimpleNamespace(time=lambda: state.clock))
    monkeypatch.setattr(boundary, '_queue_write', queue_write)
    monkeypatch.setattr(boundary, '_queue_read', lambda root, key: state.queue.get(key, {}))
    monkeypatch.setattr(boundary, '_owner_free', lambda root, owner: not state.owner_busy)
    monkeypatch.setattr(boundary, '_earlier_waiter_exists', lambda root, key, requested: state.earlier)
    monkeypatch.setattr(boundary, '_prediction_in_flight', lambda root: state.prediction_busy)
    monkeypatch.setattr(boundary, '_fresh_boundary', lambda *args: False)
    monkeypatch.setattr(boundary, '_audio_queue_quiet', audio_quiet)
    monkeypatch.setattr(game_switch, 'atomic_write_json', register)
    return boundary, state, MemoryPath('/synthetic/soren/next_corner.json')


@pytest.mark.parametrize('wait_reason', ['lock', 'owner', 'fifo'])
def test_program_slot_rechecks_audio_after_wait_without_holding_lock(monkeypatch, wait_reason):
    boundary, state, owner = _memory_program_waiter(monkeypatch)
    setattr(state, {'lock': 'lock_busy', 'owner': 'owner_busy', 'fifo': 'earlier'}[wait_reason], True)
    sleeps = []

    def sleep(seconds):
        assert not state.held, 'busy audio must be waited for after closing the lock'
        assert not state.registry, 'the waiting corner must not register as owner'
        assert state.queue['next_corner'] == {
            'status': 'waiting_turn', 'requested_at': 90.0, 'wait_deadline_ts': 120.0}
        sleeps.append(seconds)
        assert len(sleeps) <= 2
        state.clock += seconds
        if len(sleeps) == 1:
            state.lock_busy = state.owner_busy = state.earlier = False
            state.audio_busy = True
        else:
            state.audio_busy = False

    with boundary.program_slot(None, owner, requested_at=90.0, wait_deadline_ts=120.0,
                               sleep=sleep, poll_s=1.0, now=lambda: state.clock) as root:
        assert str(root) == '/synthetic/soren/tmp/state'
        assert sleeps == [1.0, 1.0], 'must recheck audio that became busy during slot wait'
        assert not state.audio_busy and state.held
        assert state.registry == [{'owner_state': str(owner)}]
        assert state.queue['next_corner']['status'] == 'running'
    assert not state.held
    assert state.queue['next_corner']['status'] == 'done'
    assert state.boundary_checks[-1] == (102.0, True, False)


def test_program_slot_recheck_busy_expires_at_original_deadline(monkeypatch):
    boundary, state, owner = _memory_program_waiter(monkeypatch)
    state.lock_busy = True
    sleeps = []

    def sleep(seconds):
        assert not state.held and not state.registry
        sleeps.append(seconds)
        assert len(sleeps) <= 2
        state.clock += seconds
        state.lock_busy = False
        state.audio_busy = True

    with pytest.raises(CornerWaitExpired):
        with boundary.program_slot(None, owner, requested_at=90.0, wait_deadline_ts=102.0,
                                   sleep=sleep, poll_s=1.0, now=lambda: state.clock):
            pytest.fail('audio remained busy: an expired waiter must never yield')
    assert sleeps == [1.0, 1.0]
    assert state.queue['next_corner'] == {
        'status': 'expired', 'requested_at': 90.0, 'wait_deadline_ts': 102.0}
    assert not state.registry and not state.held


def test_program_slot_recheck_preserves_prediction_gate(monkeypatch):
    boundary, state, owner = _memory_program_waiter(monkeypatch)
    state.lock_busy = True
    sleeps = []

    def sleep(seconds):
        assert not state.held and not state.registry
        sleeps.append(seconds)
        assert len(sleeps) <= 2
        state.clock += seconds
        state.lock_busy = False
        state.prediction_busy = len(sleeps) == 1

    with boundary.program_slot(None, owner, requested_at=90.0, wait_deadline_ts=120.0,
                               sleep=sleep, poll_s=1.0, now=lambda: state.clock):
        assert sleeps == [1.0, 1.0]
        assert state.held and not state.prediction_busy
    assert state.queue['next_corner']['status'] == 'done'


@pytest.mark.parametrize('entrypoint', ['program_slot', '_program_slot', 'program_lock'])
def test_program_slot_boundary_opt_out_and_direct_callers_remain_ungated(monkeypatch, entrypoint):
    boundary, state, owner = _memory_program_waiter(monkeypatch)
    state.audio_busy = state.prediction_busy = True

    def no_sleep(seconds):
        pytest.fail('ungated callers must keep their existing immediate-start behavior')

    if entrypoint == 'program_slot':
        slot = boundary.program_slot(None, owner, requested_at=90.0, wait_deadline_ts=120.0,
                                     wait_boundary=False, sleep=no_sleep, now=lambda: state.clock)
    elif entrypoint == '_program_slot':
        slot = boundary._program_slot(boundary.resolve_soren_root(None) / 'tmp/state', owner,
                                      120.0, requested_at=90.0, sleep=no_sleep)
    else:
        slot = boundary.program_lock(None, owner, wait_deadline_ts=120.0, sleep=no_sleep)
    with slot:
        assert state.held and state.audio_busy and state.prediction_busy
        assert not state.boundary_checks
    assert state.queue['next_corner']['status'] == 'done'
    assert not state.held


def test_program_slot_recheck_registry_error_stays_fail_closed(monkeypatch):
    boundary, state, owner = _memory_program_waiter(monkeypatch)

    def corrupt_registry(root, owner_state):
        raise ProgramRegistryError('synthetic corrupt registry')

    monkeypatch.setattr(boundary, '_owner_free', corrupt_registry)
    with pytest.raises(ProgramRegistryError):
        with boundary.program_slot(None, owner, requested_at=90.0, wait_deadline_ts=120.0,
                                   sleep=lambda _: pytest.fail('must fail closed'),
                                   now=lambda: state.clock):
            pytest.fail('corrupt registry must never yield')
    assert not state.registry and not state.held
    assert state.queue['next_corner']['status'] == 'error'


@pytest.mark.parametrize('interruption', [RuntimeError, KeyboardInterrupt])
def test_program_slot_recheck_wait_interruption_stays_cancelled(monkeypatch, interruption):
    boundary, state, owner = _memory_program_waiter(monkeypatch)
    state.lock_busy = True
    sleeps = []

    def sleep(seconds):
        assert not state.held and not state.registry
        sleeps.append(seconds)
        if len(sleeps) == 1:
            state.lock_busy = False
            state.audio_busy = True
            state.clock += seconds
        else:
            raise interruption('synthetic stop')

    with pytest.raises(interruption):
        with boundary.program_slot(None, owner, requested_at=90.0, wait_deadline_ts=120.0,
                                   sleep=sleep, poll_s=1.0, now=lambda: state.clock):
            pytest.fail('interrupted boundary recheck must never yield')
    assert sleeps == [1.0, 1.0]
    assert state.queue['next_corner']['status'] == 'cancelled'
    assert not state.registry and not state.held


@pytest.mark.parametrize('status', ['failed', 'interrupted'])
def test_release_program_owner_drops_only_the_matching_row(tmp_path, status):
    # #2282: a released corner's own state stays ``failed`` forever, and the
    # registry row is the only thing that still names it. ``_owner_free``
    # reads just that row, so leaving it in place observed ``failed`` as BUSY
    # and livelocked every later corner in the shared program slot.
    from docich.corner_boundary import release_program_owner

    root = _state_dir(tmp_path)
    g = _fake_g(tmp_path)
    mine = root / 'doushi_corner.json'
    other = root / 'weather_corner.json'
    _owner(mine, status)
    _owner(other, status)
    registry = root / REGISTRY_FILE

    registry.write_text(json.dumps({'owner_state': str(mine)}))
    assert release_program_owner(g, mine) is True
    assert not registry.exists()

    registry.write_text(json.dumps({'owner_state': str(other)}))
    assert release_program_owner(g, mine) is False
    assert json.loads(registry.read_text())['owner_state'] == str(other)


def test_release_program_owner_tolerates_a_missing_or_corrupt_registry(tmp_path):
    from docich.corner_boundary import release_program_owner

    root = _state_dir(tmp_path)
    g = _fake_g(tmp_path)
    mine = root / 'doushi_corner.json'
    _owner(mine, 'failed')
    registry = root / REGISTRY_FILE

    assert release_program_owner(g, mine) is False
    assert not registry.exists()

    registry.write_text('{ not json')
    assert release_program_owner(g, mine) is False
    assert registry.exists()


def test_release_program_owner_keeps_the_row_while_the_program_lock_is_held(tmp_path):
    # A running corner holds the program flock while the registry names it, so
    # a concurrent release must not race it out of the slot (#2282).
    import docich.corner_boundary as boundary

    root = _state_dir(tmp_path)
    g = _fake_g(tmp_path)
    mine = root / 'doushi_corner.json'
    _owner(mine, 'failed')
    registry = root / REGISTRY_FILE
    registry.write_text(json.dumps({'owner_state': str(mine)}))

    with (root / 'docich_program.lock').open('a') as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert boundary.release_program_owner(g, mine) is False
    assert json.loads(registry.read_text())['owner_state'] == str(mine)


def test_release_program_owner_frees_the_slot_for_the_next_corner(tmp_path):
    # The regression the issue describes, end to end: the released corner's row
    # is dropped, so the next corner in the same program slot is no longer
    # refused by ``_register_owner``/``_owner_free`` and can take the slot.
    from docich.corner_boundary import release_program_owner

    root = _state_dir(tmp_path)
    g = _fake_g(tmp_path)
    released = root / 'doushi_corner.json'
    next_corner = root / 'weather_corner.json'
    _owner(released, 'failed')
    registry = root / REGISTRY_FILE
    registry.write_text(json.dumps({'owner_state': str(released)}))

    assert boundary._owner_free(root, str(next_corner)) is False
    with pytest.raises(RuntimeError):
        boundary._register_owner(root, next_corner)

    assert release_program_owner(g, released) is True

    assert boundary._owner_free(root, str(next_corner)) is True
    with boundary.program_lock(g, next_corner):
        assert json.loads((root / REGISTRY_FILE).read_text())['owner_state'] == str(next_corner)
