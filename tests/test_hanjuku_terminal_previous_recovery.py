"""Ended Hanjuku recovery releases the original lease and starts only background."""
from copy import deepcopy
import time
import pytest
from docich import game_switch, hanjuku_run
from docich.adapters.base import AdapterError
from docich.retroarch_boundary import BOUNDARY_FILE
from test_retroarch_safe_boundary import adapter
from test_hanjuku_stall_checkpoint import setup, terminal
import test_retro_corner as corner_harness
from test_coordinator import FakeAdapterFactory


@pytest.fixture
def recovery():
    harness = corner_harness.TestRestoreFailedRecovery(); harness.setUp()
    try:
        mgr, original_id = harness._setup_restore_failed(
            receipt_status='failed', error_code='rollback_failed',
            result_patch=lambda r: r.update(from_game=None))
        state = mgr._read_state(); state['game'] = 'hanjuku-hero'; mgr._write_state(state)
        canonical, _ = mgr.store.canonical.load()
        previous = dict(canonical['active'], game='hanjuku-hero', adapter='retroarch')
        canonical.update(phase='failed', active=None, previous=previous)
        mgr.store.canonical.save(canonical)
        factory = FakeAdapterFactory({'hanjuku-hero': {'name':'retroarch'}, 'sorengame': {}})
        old = factory(game_switch.RuntimeSpec.from_runtime(mgr.g.state_dir, previous))
        old.runtime.materialized = True; old.runtime.alive = True
        old.can_release_terminal_previous = lambda deadline,cancel: True
        mgr.coordinator = game_switch.GameSwitchCoordinator(
            mgr.store, factory, quiesce_verify_timeout_s=.05, poll_interval_s=.005)
        original = deepcopy(mgr.store.receipts.load(original_id))
        yield mgr, old, original_id, original
    finally:
        harness.tearDown()


def test_recovery_never_releases_source_lease_or_relaunches_finished_game(recovery):
    mgr, old, original_id, original = recovery
    lease = old.spec.lease_id
    result = mgr.recover_failed()
    assert result.status == 'succeeded', result
    canonical, _ = mgr.store.canonical.load()
    assert canonical['active']['game'] == 'sorengame'
    assert old.spec.lease_id == lease
    assert old.runtime.events == ['agent_stop', 'cleanup']
    assert mgr.store.receipts.load(original_id) == original
    record = mgr._read_state()['terminal_restore']
    receipt = mgr.store.receipts.load(record['request_id'])
    assert receipt['operation'] == 'start' and receipt['target'] == 'sorengame'
    assert receipt['result']['from_game'] is None
    assert mgr._read_state()['status'] == 'interrupted'
    mgr.recover_failed()
    assert mgr._read_state()['terminal_restore']['request_id'] == record['request_id']


@pytest.mark.parametrize('bad', ['terminal_invalid','cleanup_failure','still_alive'])
def test_unproved_terminal_release_keeps_source_and_does_not_start_background(recovery,bad):
    mgr, old, original_id, original = recovery
    if bad == 'terminal_invalid':
        def refuse(*a): raise AdapterError('terminal proof mismatch')
        old.can_release_terminal_previous = refuse
    elif bad == 'cleanup_failure': old.behavior['cleanup_error'] = AdapterError('cleanup failed')
    else: old.behavior['immortal'] = True
    assert mgr.recover_failed().status == 'failed'
    state, _ = mgr.store.canonical.load()
    assert state['phase'] == 'failed' and state['previous']['runtime_id'] == old.spec.runtime_id
    assert state['active'] is None and 'terminal_restore' not in mgr._read_state()
    assert mgr.store.receipts.load(original_id) == original


def test_crash_after_release_resumes_from_durable_release_proof(recovery):
    mgr, old, _, _ = recovery
    assert mgr.coordinator.recover().status == 'succeeded'
    assert mgr.recover_failed().status == 'succeeded'
    assert old.runtime.events == ['agent_stop','cleanup']


def test_rival_active_game_cannot_be_replaced_or_claimed(recovery):
    mgr, old, _, _ = recovery
    assert mgr.coordinator.recover().status == 'succeeded'
    assert mgr.coordinator.start('sorengame').status == 'succeeded'
    before = deepcopy(mgr.store.canonical.load()[0])
    assert mgr._recover_terminal_hanjuku_restore(mgr._read_state()) is None
    assert mgr.store.canonical.load()[0] == before
    assert 'terminal_restore' not in mgr._read_state()


def test_prepared_record_survives_a_crash_before_admission(recovery,monkeypatch):
    mgr, _, _, _ = recovery
    enqueue = game_switch.GameSwitchTransaction.enqueue_request
    def busy(*args,**kwargs): raise game_switch.GameSwitchBusyError('injected admission crash')
    monkeypatch.setattr(game_switch.GameSwitchTransaction,'enqueue_request',busy)
    assert mgr.recover_failed().status == 'failed'
    record = deepcopy(mgr._read_state()['terminal_restore'])
    monkeypatch.setattr(game_switch.GameSwitchTransaction,'enqueue_request',enqueue)
    assert mgr.recover_failed().status == 'succeeded'
    assert mgr._read_state()['terminal_restore'] == record


def test_failed_background_start_is_not_allocated_again(recovery):
    mgr, _, _, _ = recovery
    mgr.coordinator.adapter_factory.behaviors['sorengame']['materialize_error'] = AdapterError('start failed')
    assert mgr.recover_failed().status == 'failed'
    record = deepcopy(mgr._read_state()['terminal_restore'])
    assert mgr.recover_failed().status == 'failed'
    assert mgr._read_state()['terminal_restore'] == record


@pytest.mark.parametrize('bad', ['identity','boundary','save','unknown_reason'])
def test_real_adapter_requires_original_terminal_boundary_and_checkpoint(adapter,monkeypatch,bad):
    setup(adapter,monkeypatch)
    adapter.request_round_boundary('ended',time.monotonic()+2,None)
    monkeypatch.setattr('docich.agent.fence.read_canonical',
        lambda _: {'previous': {'runtime_id':adapter.spec.runtime_id}})
    assert adapter.can_release_terminal_previous(time.monotonic()+1,None)
    run = hanjuku_run.load(adapter.spec.runtime_dir,hanjuku_run.runtime_identity(adapter.spec))
    if bad == 'identity': run['lease_id'] = 'foreign'
    elif bad == 'unknown_reason': run['terminal_reason'] = 'unknown'
    elif bad == 'boundary': (adapter.spec.runtime_dir/BOUNDARY_FILE).unlink()
    else: (adapter.spec.runtime_dir/'states/hanjuku.state').write_bytes(b'changed')
    game_switch.atomic_write_json(adapter.spec.runtime_dir/hanjuku_run.RUN_FILE,run)
    with pytest.raises(AdapterError):
        adapter.can_release_terminal_previous(time.monotonic()+1,None)


def test_stopped_game_without_agent_still_requires_matching_saved_bytes(adapter,monkeypatch):
    setup(adapter,monkeypatch)
    adapter.request_round_boundary('ended',time.monotonic()+2,None)
    monkeypatch.setattr('docich.agent.fence.read_canonical',
        lambda _: {'previous': {'runtime_id':adapter.spec.runtime_id}})
    adapter.cleanup_runtime(time.monotonic()+1,None)
    assert adapter.can_release_terminal_previous(time.monotonic()+1,None)
    (adapter.spec.runtime_dir/'states/hanjuku.state').write_bytes(b'changed')
    with pytest.raises(AdapterError):
        adapter.can_release_terminal_previous(time.monotonic()+1,None)
