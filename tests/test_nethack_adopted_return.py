"""Synthetic adopted-return boundaries; never a VM or natural-run acceptance."""
from copy import deepcopy
import datetime as dt
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docich.nethack_return import ADOPTED_RECORD_KEY, ReturnUnproven, adopted_return_proof
from test_nethack_legacy_return import legacy, SLOT, R0, LEASE


@pytest.fixture
def adopted(legacy):
    f = legacy
    f.owner.update(previous_game='sorengame', finish_reason='stalled', last_error_code='rollback_failed')
    f.write(('nethack_corner.json',), f.owner)
    original = deepcopy(f.original)
    original['status'] = 'failed'
    original['result'] = {'request_id': R0, 'operation': 'switch', 'status': 'failed',
                          'from_game': None, 'to_game': 'sorengame', 'generation': 2,
                          'error_code': 'rollback_failed', 'cleanup_pending': True}
    f.write(('game-switch', 'requests', R0 + '.json'), original)
    f.original = original
    active = deepcopy(f.canonical['active'])
    active.update(generation=2, runtime_id=original['runtime_id'],
                  adapter_session=original['adapter_session'], game_window=original['game_window'],
                  agent_window=original['agent_window'], started_at=(f.now-dt.timedelta(seconds=75)).isoformat())
    result = {'request_id': '', 'operation': 'recover', 'status': 'succeeded',
              'from_game': 'nethack', 'to_game': 'sorengame', 'generation': 2,
              'active_runtime': {k:active[k] for k in ('game','runtime_id','generation','lease_id')}}
    f.canonical.update(active=active,last_result=result)
    f.canonical=f.manager.store.canonical.save(f.canonical)
    f.first.update(outcome='suspended')
    f.write(('runtimes', 'g1-aaaaaa','nethack_boundary.json'), f.first)
    f.manager._adopted_return_runtime_checks=Mock(return_value={'source_absent': True,'target_alive': True})
    return f


def proof(f, **facts):
    return adopted_return_proof(f.root, f.manager._read_state(), f.rotation.load(f.now.timestamp()),
                               player='fixture_player',now=f.now,
                               **({'source_absent':True,'target_alive':True}|facts))


def test_adopted_target_settles_only_same_slot_and_preserves_failed_receipt_and_save_boundary(adopted):
    f=adopted
    failed=(f.root/'game-switch/requests'/f'{R0}.json').read_bytes()
    boundary=(f.root/'runtimes/g1-aaaaaa/nethack_boundary.json').read_bytes()
    canonical=(f.root/'game_switch.json').read_bytes()
    ledger=f.rotation.path.read_bytes()
    assert proof(f)['source_outcome']=='suspended'
    assert f.manager.recover_failed_rotation().status=='succeeded'
    state=f.manager._read_state()
    assert state['status']=='interrupted' and state[ADOPTED_RECORD_KEY]['phase']=='committed'
    assert (f.root/'game-switch/requests'/f'{R0}.json').read_bytes()==failed
    assert (f.root/'runtimes/g1-aaaaaa/nethack_boundary.json').read_bytes()==boundary
    assert (f.root/'game_switch.json').read_bytes()==canonical and f.rotation.path.read_bytes()==ledger
    assert f.manager.coordinator.mock_calls==[]
    assert f.manager.recover_failed_rotation().status=='succeeded'


@pytest.mark.parametrize('bad', ['source','target','bool_gen','other_gen','lease','retiring','other_receipt',
                                 'boundary_request','boundary_unknown','manual','pending','no_dispatch',
                                 'foreign_actor','old_start','restored_source'])
def test_missing_or_foreign_evidence_is_not_a_return(adopted,bad):
    f=adopted
    if bad=='source': facts={'source_absent':False}
    elif bad=='target': facts={'target_alive':False}
    else:
        facts={}
        if bad in ('bool_gen','other_gen','lease','retiring','old_start'):
            s=deepcopy(f.canonical)
            if bad=='bool_gen':s['last_result']['generation']=True
            elif bad=='other_gen':s['last_result']['generation']=3
            elif bad=='lease':s['last_result']['active_runtime']['lease_id']=SLOT
            elif bad=='old_start':s['active']['started_at']=(f.now-dt.timedelta(seconds=1000)).isoformat()
            else:s['retiring']=[deepcopy(s['active'])]
            f.write(('game_switch.json',),s)
        elif bad in ('other_receipt','restored_source'):
            s=deepcopy(f.original)
            if bad=='other_receipt':s['result']['to_game']='nethack'
            else:s['result']['restored_generation']=1
            f.write(('game-switch','requests',R0+'.json'),s)
        elif bad in ('boundary_request','boundary_unknown','foreign_actor'):
            s=deepcopy(f.first)
            if bad=='boundary_request':s['request_id']=SLOT
            elif bad=='boundary_unknown':s['outcome']='unknown'
            else:s['player_name']='other'
            f.write(('runtimes','g1-aaaaaa','nethack_boundary.json'),s)
        else:
            s=deepcopy(f.ledger)
            if bad=='manual':s['manual_pending']={'corner':'nethack','request_id':R0}
            elif bad=='pending':s['pending']['request_id']=R0
            else:s['history']=[]
            f.write(('corner_rotation.json',),s)
    with pytest.raises((ReturnUnproven,ValueError,RuntimeError)):
        proof(f,**facts)


def test_rotation_guard_rechecks_live_evidence_through_ledger_commit(adopted):
    f=adopted
    assert f.manager.recover_failed_rotation().status=='succeeded'
    with f.manager.legacy_return_recovery_guard(f.ledger['pending']) as verified:
        assert verified is True
    f.manager._adopted_return_runtime_checks.return_value={'source_absent':True,'target_alive':False}
    from docich.corner_rotation import RotationError
    with pytest.raises(RotationError):
        with f.manager.legacy_return_recovery_guard(f.ledger['pending']):
            pass


@pytest.mark.parametrize('facts',[{'source_absent':1},{'target_alive':'true'},{'target_alive':None}])
def test_resource_probe_requires_actual_booleans(adopted,facts):
    with pytest.raises(ReturnUnproven): proof(adopted,**facts)


def test_changed_probe_before_preparation_leaves_owner_and_other_states_untouched(adopted):
    f=adopted; before=f.manager.state_path.read_bytes()
    f.manager._adopted_return_runtime_checks.side_effect=[
        {'source_absent':True,'target_alive':True},{'source_absent':False,'target_alive':True}]
    assert f.manager.recover_failed_rotation().status=='failed'
    assert f.manager.state_path.read_bytes()==before
    assert f.manager.coordinator.mock_calls==[]


def test_prepared_record_survives_failed_write_and_retry_reproves_same_evidence(adopted,monkeypatch):
    f=adopted; real=f.manager._write_state; writes=0
    def save(state):
        nonlocal writes
        writes+=1
        if writes==2:raise OSError('synthetic atomic persistence failure')
        real(state)
    monkeypatch.setattr(f.manager,'_write_state',save)
    assert f.manager.recover_failed_rotation().status=='failed'
    pending=f.manager._read_state()
    assert pending['status']=='failed' and pending[ADOPTED_RECORD_KEY]['phase']=='prepared'
    assert f.manager.recover_failed_rotation().status=='succeeded'
    assert f.manager._read_state()[ADOPTED_RECORD_KEY]['phase']=='committed'
    assert f.manager.coordinator.mock_calls==[]


def test_regular_rotation_consumes_same_adopted_slot_once_without_new_execution(adopted,monkeypatch):
    import json
    from test_nethack_legacy_return import prepare_rotation_commit
    f=adopted
    assert f.manager.recover_failed_rotation().status=='succeeded'
    prepare_rotation_commit(f,monkeypatch)
    assert f.rotation.recover()=={'status':'ready','corner':'nethack','result':'interrupted','recovered':True}
    ledger=json.loads(f.rotation.path.read_text())
    assert ledger['pending'] is None and ledger['last_result']['request_id']==SLOT
    assert ledger['history'][:-1]==f.ledger['history']
    assert ledger['history'][-1]['source']=='completion'
    assert f.manager.coordinator.mock_calls==[]


def test_foreign_adopted_lease_after_terminalization_cannot_commit_rotation(adopted,monkeypatch):
    from test_nethack_legacy_return import prepare_rotation_commit
    from docich.corner_rotation import RotationError
    f=adopted
    assert f.manager.recover_failed_rotation().status=='succeeded'
    s=deepcopy(f.canonical)
    s['active']['lease_id']=SLOT
    s['last_result']['active_runtime']['lease_id']=SLOT
    f.write(('game_switch.json',),s)
    before=f.rotation.path.read_bytes()
    prepare_rotation_commit(f,monkeypatch)
    with pytest.raises(RotationError):f.rotation.recover()
    assert f.rotation.path.read_bytes()==before and f.manager.coordinator.mock_calls==[]
