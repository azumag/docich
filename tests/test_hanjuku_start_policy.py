"""Natural/manual starts share the same reached-boundary policy, offline."""
from contextlib import contextmanager, nullcontext
from dataclasses import replace
import hashlib
import os
import shutil
from pathlib import Path
import time
from types import SimpleNamespace
from unittest import mock
import uuid

import pytest

from docich import hanjuku_resume as resume, hanjuku_run
from docich.adapters.base import AdapterError
from docich.corner_adapters import CornerExecutionCoordinator
from docich.game_switch import atomic_write_json
from docich.retroarch_boundary import BOUNDARY_FILE, MANUAL_SAVE_FILE, read_record
from test_hanjuku_resume import setup, ended, stage, state_bytes, restore_test, recovery_setup, recover_startup


def request(s, source, explicit=False):
    queued = dict(s.queue, source=source, phase='dispatched')
    if not explicit:
        queued.pop('resume_latest')
    return queued


def execute(s, queued, monkeypatch, *, manual_slot=False):
    """Real common execution entry and real claim, fake slot/game process."""
    order = []
    @contextmanager
    def slot(*args, **kwargs):
        order.append('previous-ended')
        yield
    monkeypatch.setattr('docich.corner_boundary.program_slot', slot)
    canonical = {k: getattr(s.spec, k) for k in ('game', 'runtime_id', 'generation', 'lease_id')}
    monkeypatch.setattr('docich.agent.fence.read_canonical', lambda _: {'candidate': canonical})
    key = 'manual_pending' if manual_slot else 'pending'
    atomic_write_json(Path(s.g.state_dir) / 'corner_rotation.json', {key: queued})
    s.game.raw['hanjuku'] = {'script_bot': True}
    # prepare_start loads the same configured game; claim uses the fixture's
    # game with strict scripted-start guards enabled.
    store = SimpleNamespace(lock=lambda **_: nullcontext(),
                            canonical=SimpleNamespace(load=lambda: ({'phase': 'ready'}, False)))
    def run(_):
        order.append('run')
        assert read_record(Path(s.g.state_dir) / 'hanjuku-resume' / resume.REQUEST_FILE)['status'] == 'queued'
        resume.claim(s.adapter, time.monotonic() + 3, None)
        return 'completed'
    adapter = SimpleNamespace(manager=SimpleNamespace(store=store),
                              state_path=Path(s.g.state_dir) / 'retro_corner.json', run=run)
    coordinator = CornerExecutionCoordinator(s.g, clock=lambda: 1000000.0, sleep=lambda _: None)
    assert coordinator.execute(adapter, queued) == 'completed'
    assert order == ['previous-ended', 'run']
    return read_record(s.spec.runtime_dir / resume.RECEIPT_FILE)


@pytest.mark.parametrize('source,explicit,manual_slot', [
    ('automatic', False, False), ('manual', False, False),
    ('manual', True, False), ('manual', False, True)])
@pytest.mark.parametrize('reason', ['screen_stalled', 'input_stalled', 'game_over'])
def test_common_execution_starts_from_latest_end(setup, monkeypatch, source, explicit, manual_slot, reason):
    s = setup
    boundary = ended(s.source, reason)
    # A much newer mtime on a lower generation must not win, including a
    # stale save touched after the latest game over.
    os.utime(s.older, ns=(time.time_ns(), time.time_ns()))
    if reason == 'game_over':
        s.source.unlink()  # no checkpoint is needed for a proven game over
    receipt = execute(s, request(s, source, explicit), monkeypatch, manual_slot=manual_slot)
    assert receipt['source_boundary'] == boundary
    assert receipt['start_mode'] == ('fresh' if reason == 'game_over' else 'resume')
    if reason == 'game_over':
        assert receipt['status'] == 'verified'
        assert not list((s.spec.runtime_dir / 'states').iterdir())
        with mock.patch('docich.adapters.retroarch.send_ra_cmd') as send:
            resume.restore(s.adapter, time.monotonic() + 3, None)
        send.assert_not_called()
        assert not (s.spec.runtime_dir / 'hanjuku_bot.json').exists()
    else:
        assert receipt['status'] == 'claimed'
        assert (s.spec.runtime_dir / 'states/hanjuku-hero.state').read_bytes() == s.source.read_bytes()


@pytest.mark.parametrize('source', ['automatic', 'manual'])
@pytest.mark.parametrize('reason', ['screen_stalled', 'input_stalled'])
def test_default_start_uses_existing_core_roundtrip_before_ai(setup, source, reason):
    s = setup
    ended(s.source, reason)
    s.queue = request(s, source)
    resume.prepare_start(s.g, s.queue)
    commands, _ = restore_test(s)
    assert commands.count('LOAD_STATE_SLOT 0') == 1
    assert read_record(s.spec.runtime_dir / resume.RECEIPT_FILE)['status'] == 'verified'
    resume.require_verified(s.spec)


@pytest.mark.parametrize('explicit', [False, True])
@pytest.mark.parametrize('bad', [
    'missing-save', 'empty-save', 'changed-save', 'bad-format', 'save-symlink', 'states-symlink',
    'missing-boundary', 'broken-boundary', 'waiting', 'schema', 'runtime', 'generation',
    'boolean-generation', 'lease', 'game', 'reason', 'frame', 'basename', 'sha', 'bytes',
    'missing-run', 'broken-run', 'run-generation', 'run-lease', 'run-frame', 'nonterminal',
    'unknown-reason', 'forced-unsaved', 'duplicate-generation', 'newer-unknown',
])
def test_uncertain_latest_progress_never_uses_old_save_or_fresh(setup, explicit, bad):
    s = setup
    runtime = s.source.parent.parent
    path = runtime / BOUNDARY_FILE
    boundary = read_record(path)
    run = read_record(runtime / hanjuku_run.RUN_FILE)
    if bad == 'missing-save':
        s.source.unlink()
    elif bad == 'empty-save':
        s.source.write_bytes(b'')
    elif bad == 'changed-save':
        s.source.write_bytes(state_bytes(b'changed'))
    elif bad == 'bad-format':
        s.source.write_bytes(b'corrupt state format')
        boundary.update(sha256=hashlib.sha256(s.source.read_bytes()).hexdigest(), checkpoint_bytes=s.source.stat().st_size)
    elif bad == 'save-symlink':
        s.source.unlink(); s.source.symlink_to(s.older)
    elif bad == 'states-symlink':
        s.source.unlink(); s.source.parent.rmdir(); s.source.parent.symlink_to(s.older.parent)
    elif bad == 'missing-boundary':
        path.unlink()
    elif bad == 'broken-boundary':
        path.write_text('{')
    elif bad == 'waiting':
        boundary['status'] = 'waiting'
    elif bad in {'schema', 'runtime', 'generation', 'boolean-generation', 'lease', 'game', 'reason',
                 'frame', 'basename', 'sha', 'bytes', 'unknown-reason', 'forced-unsaved'}:
        key, value = {
            'schema': ('schema', True), 'runtime': ('runtime_id', s.older.parent.parent.name),
            'generation': ('generation', 478), 'boolean-generation': ('generation', True),
            'lease': ('lease_id', str(uuid.uuid4())), 'game': ('game', 'other'),
            'reason': ('outcome', 'input_stalled'), 'frame': ('frame_sha256', 'b' * 64),
            'basename': ('checkpoint', '../hanjuku-hero.state'), 'sha': ('sha256', 'b' * 64),
            'bytes': ('checkpoint_bytes', True), 'unknown-reason': ('outcome', 'unknown'),
            'forced-unsaved': ('outcome', 'manual_forced_stop'),
        }[bad]
        boundary[key] = value
    elif bad == 'missing-run':
        (runtime / hanjuku_run.RUN_FILE).unlink()
    elif bad == 'broken-run':
        (runtime / hanjuku_run.RUN_FILE).write_text('{')
    elif bad.startswith('run-') or bad == 'nonterminal':
        key, value = {'run-generation': ('generation', 478), 'run-lease': ('lease_id', str(uuid.uuid4())),
                      'run-frame': ('frame_sha256', 'b' * 64), 'nonterminal': ('terminal_reason', None)}[bad]
        run[key] = value
        atomic_write_json(runtime / hanjuku_run.RUN_FILE, run)
    elif bad == 'duplicate-generation':
        other = runtime.with_name('g514-cafe1234'); other.mkdir()
        atomic_write_json(other / hanjuku_run.RUN_FILE, run)
    elif bad == 'newer-unknown':
        other = runtime.with_name('g600-cafe1234'); other.mkdir()
        atomic_write_json(other / hanjuku_run.RUN_FILE, dict(run, generation=600, runtime_id=other.name, terminal_reason=None))
    if bad not in {'missing-boundary', 'broken-boundary'}:
        atomic_write_json(path, boundary)
    with pytest.raises(AdapterError):
        resume.prepare_start(s.g, request(s, 'manual' if explicit else 'automatic', explicit))
    assert not (Path(s.g.state_dir) / 'hanjuku-resume' / resume.REQUEST_FILE).exists()
    assert not (s.spec.runtime_dir / resume.RECEIPT_FILE).exists()
    assert s.older.read_bytes() == state_bytes(b'older')


def test_legacy_unbound_state_requires_recovery_even_for_explicit_resume(setup):
    s = setup
    for runtime in (s.source.parent.parent, s.older.parent.parent):
        (runtime / BOUNDARY_FILE).unlink()
        (runtime / hanjuku_run.RUN_FILE).unlink()
    with pytest.raises(AdapterError):
        resume.prepare_start(s.g, s.queue)


def manual_boundary(s, outcome):
    path = s.source.parent.parent / BOUNDARY_FILE
    boundary = read_record(path)
    boundary['outcome'] = outcome
    if outcome == 'manual_forced_stop':
        boundary = {k: v for k, v in boundary.items() if k not in {
            'checkpoint', 'sha256', 'checkpoint_bytes', 'checkpoint_mtime_ns', 'frame_sha256'}}
        boundary['save_error'] = 'save_timeout'
    atomic_write_json(path, boundary)
    atomic_write_json(path.parent / MANUAL_SAVE_FILE, dict({k: boundary[k] for k in
        ('schema', 'game', 'runtime_id', 'generation', 'lease_id', 'request_id')}, allow_unsaved_stop=True))
    run = read_record(path.parent / hanjuku_run.RUN_FILE)
    atomic_write_json(path.parent / hanjuku_run.RUN_FILE, dict(run, terminal_reason=None))
    return boundary


@pytest.mark.parametrize('source,explicit', [('automatic', False), ('manual', False), ('manual', True)])
def test_valid_manual_suspension_preserves_ordinary_fresh_and_explicit_resume(setup, monkeypatch, source, explicit):
    s = setup
    boundary = manual_boundary(s, 'suspended')
    receipt = execute(s, request(s, source, explicit), monkeypatch)
    assert receipt['source_boundary'] == boundary
    assert receipt['start_mode'] == ('resume' if explicit else 'fresh')
    assert receipt['status'] == ('claimed' if explicit else 'verified')
    assert (s.spec.runtime_dir / 'states/hanjuku-hero.state').exists() == explicit


@pytest.mark.parametrize('source', ['automatic', 'manual'])
def test_authorized_unsaved_manual_stop_preserves_ordinary_fresh_without_old_stall(setup, monkeypatch, source):
    s = setup
    boundary = manual_boundary(s, 'manual_forced_stop')
    s.source.unlink()  # only g478's old stalled save remains
    receipt = execute(s, request(s, source), monkeypatch)
    assert receipt['source_boundary'] == boundary and receipt['start_mode'] == 'fresh'
    assert not list((s.spec.runtime_dir / 'states').iterdir())


@pytest.mark.parametrize('bad', ['missing', 'changed', 'invalid-format'])
def test_corrupt_manual_suspension_still_requires_safety_decision_instead_of_fresh(setup, bad):
    s = setup
    manual_boundary(s, 'suspended')
    if bad == 'missing':
        s.source.unlink()
    else:
        s.source.write_bytes(b'invalid checkpoint')
        if bad == 'invalid-format':
            path = s.source.parent.parent / BOUNDARY_FILE
            record = read_record(path)
            atomic_write_json(path, dict(record, sha256=hashlib.sha256(s.source.read_bytes()).hexdigest(),
                                         checkpoint_bytes=s.source.stat().st_size))
    with pytest.raises(AdapterError):
        resume.prepare_start(s.g, request(s, 'automatic'))


@pytest.mark.parametrize('reason', ['screen_stalled', 'game_over'])
def test_retry_keeps_same_decision_even_after_newer_run_appears(setup, reason):
    s = setup
    ended(s.source, reason)
    queued = request(s, 'automatic')
    resume.prepare_start(s.g, queued)
    path = Path(s.g.state_dir) / 'hanjuku-resume' / resume.REQUEST_FILE
    before = path.read_bytes()
    newer = s.source.parent.parent.with_name('g600-cafe1234') / 'states/hanjuku-hero.state'
    newer.parent.mkdir(parents=True); newer.write_bytes(state_bytes(b'newer'))
    ended(newer, 'game_over' if reason == 'screen_stalled' else 'screen_stalled')
    resume.prepare_start(s.g, queued)
    assert path.read_bytes() == before


def test_scripted_candidate_without_prepared_or_matching_reservation_refuses_start(setup):
    s = setup
    s.game.raw['hanjuku'] = {'script_bot': True}
    with pytest.raises(AdapterError):
        resume.claim(s.adapter, time.monotonic() + 3, None)
    resume.prepare_start(s.g, request(s, 'automatic'))
    with pytest.raises(AdapterError):
        resume.claim(s.adapter, time.monotonic() + 3, None)


@pytest.mark.parametrize('explicit', [False, True])
def test_first_start_only_defaults_to_fresh_when_no_progress_exists(setup, explicit):
    s = setup
    shutil.rmtree(s.source.parent.parent)
    shutil.rmtree(s.older.parent.parent)
    queued = request(s, 'manual', explicit)
    if explicit:
        with pytest.raises(AdapterError, match='no remaining checkpoint'):
            resume.prepare_start(s.g, queued)
    else:
        resume.prepare_start(s.g, queued)
        decision = read_record(Path(s.g.state_dir) / 'hanjuku-resume' / resume.REQUEST_FILE)
        assert decision['start_mode'] == 'fresh' and decision['source_boundary'] is None


@pytest.mark.parametrize('status', ['prepared', 'queued'])
def test_unclaimed_legacy_mtime_reservation_cannot_bypass_newer_game_over(setup, status):
    s = setup
    resume.prepare(s.g, s.game, s.queue)
    path = Path(s.g.state_dir) / 'hanjuku-resume' / resume.REQUEST_FILE
    old = read_record(path)
    old.pop('start_policy')
    atomic_write_json(path, dict(old, status=status))
    ended(s.source, 'game_over')
    before = path.read_bytes()
    with pytest.raises(AdapterError, match='legacy unclaimed'):
        resume.prepare_start(s.g, s.queue)
    assert path.read_bytes() == before


def test_changed_bytes_between_selection_and_sealing_are_rejected(setup, monkeypatch):
    s = setup
    select = resume._latest_boundary
    def change(g, game, **kwargs):
        result = select(g, game, **kwargs)
        s.source.write_bytes(state_bytes(b'changed after validation'))
        return result
    monkeypatch.setattr(resume, '_latest_boundary', change)
    with pytest.raises(AdapterError, match='no longer matches'):
        resume.prepare_start(s.g, request(s, 'automatic'))


@pytest.mark.parametrize('mode', ['fresh', 'unknown'])
def test_resume_request_cannot_be_changed_into_an_implicit_fresh_start(setup, mode):
    s = setup
    resume.prepare(s.g, s.game, s.queue)
    path = Path(s.g.state_dir) / 'hanjuku-resume' / resume.REQUEST_FILE
    prior = read_record(path)
    atomic_write_json(path, dict(prior, start_mode=mode))
    with pytest.raises(AdapterError):
        stage(s)
    assert not (s.spec.runtime_dir / resume.RECEIPT_FILE).exists()


@pytest.mark.parametrize('has_active_proof', [False, True])
def test_missing_newest_committed_runtime_does_not_resurrect_old_stall(setup, has_active_proof):
    from docich.game_switch import GameSwitchStore
    s = setup
    store = GameSwitchStore(Path(s.g.state_dir))
    with store.transaction() as tx:
        canonical, _ = store.canonical.load()
        canonical['next_generation'] = 600
        store.canonical.save(canonical)
        acceptance = tx.accept_request(str(uuid.uuid4()), 'start', 'hanjuku-hero')
        result = {
            'request_id': acceptance.request_id, 'operation': 'start', 'status': 'succeeded',
            'from_game': None, 'to_game': 'hanjuku-hero', 'generation': acceptance.generation,
            'source_runtime': None, 'active_runtime': {
                'game': 'hanjuku-hero', 'runtime_id': acceptance.receipt['runtime_id'],
                'generation': acceptance.generation, 'lease_id': str(uuid.uuid4())}}
        if not has_active_proof:
            result.pop('active_runtime')
            result.pop('source_runtime')
        tx.finish_request(acceptance.request_id, 'succeeded', result)
    # No run, boundary, state or even runtime directory remains for generation
    # 600, but the durable start receipt proves newer progress existed.
    with pytest.raises(AdapterError, match='unconfirmed'):
        resume.prepare_start(s.g, request(s, 'automatic'))


@pytest.mark.parametrize('operation', ['start', 'switch'])
@pytest.mark.parametrize('reason', ['screen_stalled', 'input_stalled', 'game_over'])
def test_real_queued_noop_success_does_not_hide_actual_runtime_end(setup, operation, reason):
    from docich.game_switch import GameSwitchCoordinator, GameSwitchStore, runtime_state_dict
    from docich.naming import runtime_names
    s = setup
    boundary = ended(s.source, reason)
    runtime = s.source.parent.parent
    final_run = read_record(runtime / hanjuku_run.RUN_FILE)
    atomic_write_json(runtime / hanjuku_run.RUN_FILE, dict(final_run, terminal_reason=None))
    (runtime / BOUNDARY_FILE).unlink()
    names = runtime_names(boundary['generation'])
    active_spec = replace(s.spec, generation=boundary['generation'], runtime_id=runtime.name,
                          runtime_dir=runtime, lease_id=boundary['lease_id'],
                          game_window=names.game_window, agent_window=names.agent_window,
                          adapter_session=names.adapter_session)
    store = GameSwitchStore(Path(s.g.state_dir))
    with store.transaction():
        canonical = store.canonical.initialize()
        canonical.update(phase='ready', active=runtime_state_dict(active_spec, '2026-10-17T00:00:00Z'),
                         next_generation=600)
        store.canonical.save(canonical)
    pending_id = str(uuid.uuid4())
    reserved = store.enqueue_request(pending_id, operation, 'hanjuku-hero').receipt
    factory = mock.Mock(side_effect=AssertionError('no-op must never create an adapter'))
    coordinator = GameSwitchCoordinator(store, factory)
    result = getattr(coordinator, operation)('hanjuku-hero', request_id=pending_id)
    assert result.status == 'succeeded' and result.generation == 600
    noop = store.receipts.load(pending_id)
    assert noop['result']['detail'] == '既に起動中です'
    assert 'active_runtime' not in noop['result']
    assert not Path(reserved['runtime_dir']).exists()
    factory.assert_not_called()
    # The real active runtime reaches its normal end after the no-op request.
    atomic_write_json(runtime / hanjuku_run.RUN_FILE, final_run)
    atomic_write_json(runtime / BOUNDARY_FILE, boundary)
    resume.prepare_start(s.g, request(s, 'automatic'))
    decision = read_record(Path(s.g.state_dir) / 'hanjuku-resume' / resume.REQUEST_FILE)
    assert decision['source_boundary'] == boundary
    assert decision['start_mode'] == ('fresh' if reason == 'game_over' else 'resume')


def test_legacy_claimed_startup_recovery_keeps_logical_snapshot_through_common_entry(recovery_setup, monkeypatch):
    s = recovery_setup
    base = Path(s.g.state_dir)
    # Simulate a pre-policy claim; the existing recovery must still recognize
    # it, preserve its sealed bytes and bind only the derived attempt.
    legacy = {k: v for k, v in s.claim.items() if k not in {
        'start_policy', 'start_mode', 'source_boundary', 'dispatch_source', 'explicit_resume'}}
    atomic_write_json(base / 'hanjuku-resume' / resume.REQUEST_FILE, legacy)
    atomic_write_json(s.spec.runtime_dir / resume.RECEIPT_FILE, legacy)
    s.fingerprint = s.op.fingerprint(legacy)
    assert recover_startup(s, apply=True, expected=s.fingerprint)['status'] == 'recovery-queued'
    queued = dict(s.manager.load(1000000.0)['queued_manual'], source='manual', phase='dispatched')
    runtime = base / 'runtimes/g701-cafe1234'; (runtime / 'states').mkdir(parents=True)
    s.spec = replace(s.spec, generation=701, runtime_id=runtime.name, runtime_dir=runtime, lease_id=str(uuid.uuid4()))
    s.adapter.spec = s.spec
    receipt = execute(s, queued, monkeypatch)
    assert receipt['request_id'] == legacy['request_id']
    assert receipt['sha256'] == legacy['sha256'] and receipt['status'] == 'claimed'
