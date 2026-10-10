"""Invented records and procfs only; no production or game commands."""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_nethack_legacy_return import legacy
from docich.game_switch import atomic_write_json
from docich.nethack_admin_release import (check, release, Refused, AUDIT_KEY,
    administrative_observation, _resources, _chain)
from docich.nethack_admin_resources import processes
from docich.nethack_admin_result import public_result

SHA = 'a' * 40


@pytest.fixture
def fixture(legacy):
    f = legacy
    f.owner.update(previous_game='sorengame', improve_job={'spawned': False})
    f.write(('nethack_corner.json',), f.owner)
    soren = f.root / 'soren'
    (soren / 'tmp/state').mkdir(parents=True)
    for path in (f.root / 'locks/corner-rotation.lock', f.root / 'corner-manual-queue.lock',
                 f.root / 'locks/nethack-corner-tick.lock',
                 f.root / 'locks/nethack-corner.lock', f.root / 'locks/corner-improve-nethack.lock',
                 f.root / 'locks/nethack-corner-manual-tick.lock', f.root / 'locks/nethack-corner-manual.lock',
                 soren / 'tmp/state/docich_program.lock',
                 f.root / 'locks/game-switch.lock'):
        path.touch()
    for generation, runtime in ((1, 'g1-aaaaaa'), (3, 'g3-cccccc')):
        f.write(('runtimes', runtime, 'presentation.json'), {'status': 'stopped'})
        f.write(('runtimes', runtime, 'nethack_tiles.json'), dict(schema_version=1,
            runtime_id=runtime, generation=generation, status='stopped', cleanup_complete=True,
            adapter_session=f'docich-game-g{generation}', game_window=f'game-g{generation}',
            owner_pid=100+generation, owner_start_ticks=500+generation,
            browser_members=[], tty_members=[]))
    f.soren = soren
    f.probe = Mock(return_value=[])
    f.container_probe = Mock(return_value=[])
    f.tmux = Mock()
    f.tmux.window_target_exists.return_value = False
    f.tmux.session_target_exists.return_value = False
    return f


def inspect(f, **kw):
    return check(f.root, f.soren, player='fixture_player', sha=SHA,
                 now=f.now.timestamp(), probe=f.probe, tmux=f.tmux,
                 container_probe=f.container_probe, **kw)


def apply(f, result=None, **kw):
    result = result or inspect(f)
    options = dict(player='fixture_player', sha=SHA, expected=result['fingerprint'],
                   expires=result['expires_at'], now=f.now.timestamp(), probe=f.probe, tmux=f.tmux,
                   container_probe=f.container_probe)
    options.update(kw)
    return release(f.root, f.soren, **options)


def snapshot(f):
    return {str(p.relative_to(f.root)):p.read_bytes() for p in f.root.rglob('*.json')}


def test_check_read_only_and_release_one_atomic_audited_reservation(fixture):
    f=fixture
    before=snapshot(f)
    from docich.nethack_admin_release import _context
    _context(f.root, f.soren, player='fixture_player', now=f.now, probe=f.probe,
             tmux=f.tmux, container_probe=f.container_probe)
    proposal=inspect(f)
    assert proposal['status']=='admin-eligible'
    assert proposal['history_authority'] is False
    assert snapshot(f)==before
    assert apply(f, proposal)['status']=='admin-released'
    after=snapshot(f)
    assert {k:v for k,v in after.items() if k!='corner_rotation.json'}=={k:v for k,v in before.items() if k!='corner_rotation.json'}
    ledger=json.loads(after['corner_rotation.json'])
    assert ledger['pending'] is None and ledger['status']=='ready'
    assert ledger['history'][:-1]==f.ledger['history']
    assert ledger[AUDIT_KEY][0]['history_authority'] is False
    assert ledger[AUDIT_KEY][0]['owner_sha256']
    assert administrative_observation(f.root,f.owner)['status']=='interrupted'
    assert f.manager._read_state()['status']=='failed'
    assert apply(f, proposal, now=proposal['expires_at']+999)['status']=='already-admin-released'
    assert snapshot(f)==after
    f.manager.coordinator.assert_not_called()


@pytest.mark.parametrize('where,key,value',[
 ('owner','previous_game',None),('owner','status','active'),('owner','rotation_request_id','99999999-9999-4999-8999-999999999999'),
 ('owner','restore_recovery',{}),('owner','restore_cleanup',{}),
 ('ledger','manual_pending',{}),('ledger','pending',{}),('canonical','retiring',[{}]),
 ('canonical','candidate',{}),('canonical','phase','draining')])
def test_missing_or_conflicting_evidence_refuses_unchanged(fixture,where,key,value):
    f=fixture
    target={'owner':f.owner,'ledger':f.ledger,'canonical':f.canonical}[where]
    target[key]=value
    path={'owner':'nethack_corner.json','ledger':'corner_rotation.json','canonical':'game_switch.json'}[where]
    f.write((path,),target)
    before=snapshot(f)
    assert inspect(f)['status']=='refused'
    assert snapshot(f)==before


@pytest.mark.parametrize('which,key,value',[('original','restored_generation',True),
 ('landed','cleanup_pending',True),('landed','source_runtime',{}),('original','source_runtime',{})])
def test_receipt_contradictions_not_reinterpreted(fixture,which,key,value):
    f=fixture
    receipt=deepcopy(getattr(f,which));receipt['result'][key]=value
    f.write(('game-switch','requests',receipt['request_id']+'.json'),receipt)
    assert inspect(f)['status']=='refused'


@pytest.mark.parametrize('field,value',[('cleanup_complete',False),('schema_version',True),('browser_members',None),
 ('tty_members',None),('owner_start_ticks',None),('runtime_id','g3-cccccc')])
def test_historical_tiles_metadata_is_not_current_resource_authority(fixture,field,value):
    f=fixture
    p=f.root/'runtimes/g1-aaaaaa/nethack_tiles.json'
    data=json.loads(p.read_text());data[field]=value;atomic_write_json(p,data)
    assert inspect(f)['status']=='admin-eligible'
    f.probe.return_value=[row()]
    assert inspect(f)['status']=='refused'


@pytest.mark.parametrize('kind',['presentation','tiles','boundary'])
def test_missing_old_optional_manifest_does_not_require_historical_pid_recovery(fixture,kind):
    f=fixture
    name={'presentation':'presentation.json','tiles':'nethack_tiles.json','boundary':'nethack_boundary.json'}[kind]
    (f.root/'runtimes/g1-aaaaaa'/name).unlink()
    assert inspect(f)['status']==('refused' if kind=='boundary' else 'admin-eligible')


def row(pid=888,born=444,**changes):
    return dict(pid=pid,start_ticks=born,ppid=1,state='S',tags={},exe='/usr/bin/python3',cwd='/tmp',argv=[b'python3'],**changes)


@pytest.mark.parametrize('kind',['unknown','old-tag','partial-tag','other-runtime','detached','reused-pid','live-owner','child'])
def test_all_uid_processes_require_positive_current_coverage(fixture,kind):
    f=fixture
    data=row()
    if kind=='old-tag': data['tags']={'DOCICH_TMUX_RUNTIME_ID':'g1-aaaaaa','DOCICH_TMUX_GENERATION':'1','DOCICH_TMUX_ROLE':'game'}
    if kind=='partial-tag': data['tags']={'DOCICH_TMUX_RUNTIME_ID':'g4-dddddd'}
    if kind=='other-runtime': data['tags']={'DOCICH_TMUX_RUNTIME_ID':'g5-eeeeee','DOCICH_TMUX_GENERATION':'5','DOCICH_TMUX_ROLE':'agent'}
    if kind=='detached':data['ppid']=1
    if kind=='reused-pid':data['pid']=101;data['exe']='/usr/bin/tmux'
    if kind=='live-owner':data['pid']=101;data['start_ticks']=501;data['exe']='/usr/bin/tmux'
    if kind=='child':data['exe']='/usr/bin/ffmpeg'
    f.probe.return_value=[data]
    assert inspect(f)['status']=='refused'


def test_current_tags_alone_and_unknown_shared_daemon_are_not_ownership(fixture):
    f=fixture
    current=row();current['tags']={'DOCICH_TMUX_RUNTIME_ID':'g4-dddddd','DOCICH_TMUX_GENERATION':'4','DOCICH_TMUX_ROLE':'game'}
    shared=row(889);shared['exe']='/usr/bin/pulseaudio'
    f.probe.return_value=[current]
    assert inspect(f)['reason']=='process_coverage_unproven'
    f.probe.return_value=[current,shared]
    assert inspect(f)['status']=='refused'


@pytest.mark.parametrize('kind', ['old-tag', 'nethack-argv', 'current-tag', 'current-generic'])
def test_control_ancestor_does_not_hide_a_tagged_or_explicit_game(fixture, kind):
    f = fixture
    child = row(os.getpid()); child['ppid'] = 889
    parent = row(889)
    if kind == 'old-tag':
        parent['tags'] = {'DOCICH_TMUX_RUNTIME_ID':'g1-aaaaaa',
            'DOCICH_TMUX_GENERATION':'1', 'DOCICH_TMUX_ROLE':'game'}
    if kind == 'nethack-argv': parent['argv'] = [b'python3', b'-m', b'docich.nethack_tiles_supervisor']
    if kind in {'current-tag', 'current-generic'}:
        parent['tags'] = {'DOCICH_TMUX_RUNTIME_ID':'g4-dddddd',
            'DOCICH_TMUX_GENERATION':'4', 'DOCICH_TMUX_ROLE':'game'}
        if kind == 'current-tag': parent['argv'] = [b'python3', b'-m', b'docich.nethack_daily_improve']
    f.probe.return_value = [child, parent]
    assert inspect(f)['reason'] == ('process_coverage_unproven' if kind == 'current-generic' else 'resources_present')


def test_only_own_admin_module_token_is_control_code(fixture):
    f = fixture
    control = row(os.getpid()); control['argv'] = [b'python3', b'-m', b'docich.nethack_admin_release']
    f.probe.return_value = [control]
    assert inspect(f)['status'] == 'admin-eligible'
    control['ppid'] = 889
    parent = row(889); parent['argv'] = control['argv']
    f.probe.return_value = [control, parent]
    assert inspect(f)['reason'] == 'resources_present'


def test_foreign_uid_task_is_not_proven_absent_by_the_fixed_socket(fixture):
    f = fixture
    # An orphan on an older alternate daemon needs no private argv/environment
    # observation to be refused. Empty fixed inventory is only necessary.
    f.probe.return_value = [dict(pid=888, ppid=1, start_ticks=444,
        uid=f.root.stat().st_uid + 1)]
    before = snapshot(f)
    assert inspect(f)['reason'] == 'process_coverage_unproven'
    assert snapshot(f) == before


def test_one_normal_tmux_server_can_inherit_old_tags_but_not_its_children(fixture):
    f = fixture
    server = row(888); server.update(exe='/usr/bin/tmux', argv=[b'tmux: server', b''],
        tags={'DOCICH_TMUX_RUNTIME_ID':'g1-aaaaaa','DOCICH_TMUX_GENERATION':'1','DOCICH_TMUX_ROLE':'game'})
    f.tmux._server_pid.return_value = 888
    f.probe.return_value = [server]
    proposal = inspect(f)
    assert proposal['status'] == 'admin-eligible'
    server['start_ticks'] += 1
    with pytest.raises(Refused, match='fingerprint_changed'): apply(f, proposal)
    f.probe.return_value = [server, row(889)]
    assert inspect(f)['reason'] == 'process_coverage_unproven'
    f.probe.return_value = [server]
    f.tmux._server_pid.return_value = 777
    assert inspect(f)['status'] == 'refused'


def test_historical_cleanup_pending_and_missing_job_do_not_invent_history_authority(fixture):
    f = fixture
    f.owner.pop('improve_job'); f.write(('nethack_corner.json',), f.owner)
    f.original['result']['cleanup_pending'] = True
    f.write(('game-switch', 'requests', f.original['request_id'] + '.json'), f.original)
    for runtime in ('g1-aaaaaa', 'g3-cccccc'):
        (f.root / 'runtimes' / runtime / 'presentation.json').unlink()
        (f.root / 'runtimes' / runtime / 'nethack_tiles.json').unlink()
    before = snapshot(f)
    assert apply(f)['status'] == 'admin-released'
    after = snapshot(f)
    assert {k:v for k,v in before.items() if k!='corner_rotation.json'} == {k:v for k,v in after.items() if k!='corner_rotation.json'}
    assert json.loads(after['corner_rotation.json'])[AUDIT_KEY][0]['history_authority'] is False


def test_current_tags_cannot_hide_a_nethack_job(fixture):
    f = fixture
    job = row(); job.update(argv=[b'python3', b'-m', b'docich.nethack_daily_improve'],
        tags={'DOCICH_TMUX_RUNTIME_ID':'g4-dddddd','DOCICH_TMUX_GENERATION':'4','DOCICH_TMUX_ROLE':'game'})
    f.probe.return_value = [job]
    assert inspect(f)['reason'] == 'resources_present'


def test_commit_fence_blocks_a_producer_after_the_last_census(fixture):
    from docich.nethack_resource_fence import resource_fence, FenceUnproven
    f = fixture; proposal = inspect(f)
    def census(uid):
        with pytest.raises(FenceUnproven, match='busy'):
            with resource_fence(f.root): pytest.fail('producer passed commit boundary')
        return []
    f.probe.side_effect = census
    assert apply(f, proposal)['status'] == 'admin-released'


def test_live_producer_refuses_commit_and_preserves_all_records(fixture):
    from docich.nethack_resource_fence import resource_fence
    f = fixture; proposal = inspect(f); before = snapshot(f)
    with resource_fence(f.root):
        with pytest.raises(Refused, match='busy'): apply(f, proposal)
    assert snapshot(f) == before


@pytest.mark.parametrize('inventory', [[["a"*64, 'docich-nh-canary-game-fixture']],
                                      [["b"*64, 'unknown-fixture']], 'unavailable'])
def test_foreign_uid_or_unregistered_container_never_disappears_from_coverage(fixture, inventory):
    f = fixture
    if inventory == 'unavailable': f.container_probe.side_effect = OSError('PRIVATE_SENTINEL')
    else: f.container_probe.return_value = inventory
    before = snapshot(f)
    result = inspect(f)
    assert result['status'] == 'refused' and 'PRIVATE' not in json.dumps(result)
    assert snapshot(f) == before


def test_docker_inspection_is_fixed_local_and_discards_raw_failure(monkeypatch):
    import subprocess
    import docich.nethack_admin_resources as module
    invoked = Mock(return_value=subprocess.CompletedProcess([], 0, b'', b''))
    monkeypatch.setattr(module.subprocess, 'run', invoked)
    assert module.containers() == []
    argv = invoked.call_args.args[0]
    assert argv == ['docker', '--host', 'unix:///var/run/docker.sock', 'ps', '--no-trunc',
                    '--format', '{{.ID}} {{.Names}}']
    assert set(invoked.call_args.kwargs['env']) == {'PATH', 'HOME', 'DOCKER_CONFIG'}
    for output, rc in ((b'PRIVATE_SENTINEL', 0), (b'', 1), (b'a'*65537, 0)):
        invoked.return_value = subprocess.CompletedProcess([], rc, output, b'PRIVATE_SENTINEL')
        with pytest.raises(module.CoverageUnproven): module.containers()


@pytest.mark.parametrize('identity', ['boot_id', 'pid_namespace'])
def test_machine_or_namespace_change_invalidates_exact_process_context(fixture, identity):
    f = fixture
    current = row(); current.update(exe='/usr/bin/tmux', argv=[b'tmux: server', b''],
        boot_id='11111111-1111-4111-8111-111111111111', pid_namespace='pid:[123]')
    f.tmux._server_pid.return_value = current['pid']
    f.probe.return_value = [current]
    proposal = inspect(f)
    current[identity] = 'changed'
    with pytest.raises(Refused, match='fingerprint_changed'): apply(f, proposal)


def test_missing_existing_writer_guard_is_refused_without_creating_it(fixture):
    f = fixture
    lock = f.root/'locks/nethack-corner-manual.lock'; lock.unlink()
    assert inspect(f)['reason'] == 'resources_unproven'
    assert not lock.exists()


def test_replaced_writer_inode_invalidates_approval(fixture):
    f = fixture; proposal = inspect(f)
    lock = f.root/'corner-manual-queue.lock'
    replacement = f.root/'new-guard'; replacement.touch(); lock.unlink(); replacement.rename(lock)
    with pytest.raises(Refused, match='fingerprint_changed'): apply(f, proposal)


@pytest.mark.parametrize('mutation',['owner','canonical','history','resources','code','expiry'])
def test_fingerprint_and_expiry_bind_exact_context(fixture,mutation):
    f=fixture;proposal=inspect(f)
    changes={}
    if mutation=='owner':f.owner['run_score']=9;f.write(('nethack_corner.json',),f.owner)
    if mutation=='canonical':f.canonical['revision']+=1;f.write(('game_switch.json',),f.canonical)
    if mutation=='history':f.ledger['error_kind']='new-classification';f.write(('corner_rotation.json',),f.ledger)
    if mutation=='resources':f.probe.return_value=[row()]
    if mutation=='code':changes['sha']='b'*40
    if mutation=='expiry':changes['now']=proposal['expires_at']
    before=snapshot(f)
    with pytest.raises(Exception):apply(f,proposal,**changes)
    assert snapshot(f)==before


def test_race_on_second_read_refuses_no_write(fixture,monkeypatch):
    f=fixture
    import docich.nethack_admin_release as module
    original=module._context;count=0
    def race(*a,**kw):
        nonlocal count
        count+=1
        result=original(*a,**kw)
        if count==1:
            f.owner['run_score']=1;f.write(('nethack_corner.json',),f.owner)
        return result
    monkeypatch.setattr(module,'_context',race)
    assert inspect(f)['reason']=='context_changed'
    assert json.loads((f.root/'corner_rotation.json').read_text())['pending']==f.ledger['pending']


@pytest.mark.parametrize('after_replace',[False,True])
def test_persistence_failure_same_fingerprint_retry(after_replace,fixture,monkeypatch):
    f=fixture;proposal=inspect(f)
    import docich.nethack_admin_release as module
    original=module.atomic_write_json
    def crash(*a,**kw):
        if after_replace:original(*a,**kw)
        raise OSError('PRIVATE_EXCEPTION_SENTINEL')
    monkeypatch.setattr(module,'atomic_write_json',crash)
    with pytest.raises(Refused,match='persistence_unconfirmed'):apply(f,proposal)
    monkeypatch.setattr(module,'atomic_write_json',original)
    assert apply(f,proposal)['status']==('already-admin-released' if after_replace else 'admin-released')


@pytest.mark.parametrize('mutation',['owner','audit','history','pending'])
def test_corrupt_administrative_record_never_suppresses_failed_owner(fixture,mutation):
    f=fixture;apply(f)
    ledger=json.loads((f.root/'corner_rotation.json').read_text())
    if mutation=='owner':f.owner['run_score']=1
    if mutation=='audit':ledger[AUDIT_KEY][0]['all_resources_released']=None
    if mutation=='history':ledger['history']=[]
    if mutation=='pending':ledger['pending']=f.ledger['pending']
    f.write(('corner_rotation.json',),ledger)
    assert administrative_observation(f.root,f.owner)==f.owner


def test_new_run_cannot_inherit_old_release(fixture):
    f=fixture;apply(f)
    changed={**f.owner,'rotation_request_id':'99999999-9999-4999-8999-999999999999'}
    assert administrative_observation(f.root,changed)==changed


def test_standard_history_compaction_after_later_manual_usage_preserves_audited_closure(fixture):
    f = fixture; apply(f)
    path = f.root / 'corner_rotation.json'; ledger = json.loads(path.read_text())
    later = f.now.timestamp() + 100
    ledger.update(last_seen_at=later, history=[dict(corner='nethack', at=later, source='manual-completion')])
    atomic_write_json(path, ledger)
    assert administrative_observation(f.root, f.owner)['status'] == 'interrupted'
    ledger['history'][0]['at'] = f.now.timestamp() - 100
    atomic_write_json(path, ledger)
    assert administrative_observation(f.root, f.owner) == f.owner


@pytest.mark.parametrize('unsafe',['link','directory','duplicate','nan','oversize'])
def test_unsafe_evidence_refuses_private_only(fixture,unsafe):
    f=fixture;p=f.root/'nethack_corner.json';raw=p.read_bytes();p.unlink()
    if unsafe=='link':target=f.root/'outside.json';target.write_bytes(raw);p.symlink_to(target)
    if unsafe=='directory':p.mkdir()
    if unsafe=='duplicate':p.write_text('{"status":"failed","status":"active"}')
    if unsafe=='nan':p.write_text('{"schema_version":NaN}')
    if unsafe=='oversize':p.write_text(' '*65537)
    result=inspect(f)
    assert result['status']=='refused' and 'fingerprint' not in result
    assert str(f.root) not in json.dumps(result)


def test_public_projection_strips_private_values_and_denies_unverified_envelopes(fixture):
    f=fixture;proposal=inspect(f)
    raw=dict(status='diagnosed',sha=SHA,diagnostics={'nethack_admin_check':{**proposal,'token':'PRIVATE_SENTINEL','player':'PRIVATE_PERSON'}})
    clean,rc=public_result(json.dumps(raw),'check',SHA,0)
    assert rc==0 and 'PRIVATE' not in json.dumps(clean)
    for value,code in ((raw,255),({**raw,'sha':'b'*40},0),({'status':'executed','sha':SHA,'exit_code':0,'output':'visible'},0)):
        result,rc=public_result(json.dumps(value),'check',SHA,code)
        assert rc==1 and 'fingerprint' not in result
    assert public_result('{"status":1,"status":2}','check',SHA,0)[1]==1


def test_procfs_complete_scan_omission_namespace_and_birth_races(tmp_path):
    proc=tmp_path/'proc';(proc/'self/ns').mkdir(parents=True)
    (proc/'self/ns/pid').symlink_to('pid:[123]')
    (proc/'sys/kernel/random').mkdir(parents=True)
    (proc/'sys/kernel/random/boot_id').write_text('11111111-1111-4111-8111-111111111111\n')
    p=proc/'123';(p/'ns').mkdir(parents=True)
    (p/'ns/pid').symlink_to('pid:[123]')
    (p/'exe').symlink_to('/usr/bin/pulseaudio');(p/'cwd').symlink_to('/tmp')
    (p/'status').write_text('Uid:\t1234\t1234\t1234\t1234\n')
    fields=['S','1','123','123']+['0']*15+['500']+['0']*10
    (p/'stat').write_text('123 (audio) '+' '.join(fields))
    (p/'environ').write_bytes(b'TOKEN=PRIVATE_SENTINEL\0')
    (p/'cmdline').write_bytes(b'pulseaudio\0')
    rows=processes(1234,proc=proc)
    assert len(rows)==1 and rows[0]['tags']=={} and b'PRIVATE_SENTINEL' not in repr(rows).encode()
    (p/'environ').unlink()
    with pytest.raises(Exception):processes(1234,proc=proc)


@pytest.mark.parametrize('executable', ['tmux', 'dbus-daemon', 'pulseaudio', 'pipewire', 'ffmpeg'])
def test_unregistered_game_child_never_becomes_shared_by_basename(fixture, executable):
    f = fixture
    child = row()
    child.update(exe=f'/usr/bin/{executable}', argv=[executable.encode(), b'--session', b'--fork'])
    f.probe.return_value = [child]
    before = snapshot(f)
    assert inspect(f)['reason'] == 'process_coverage_unproven'
    assert snapshot(f) == before


@pytest.mark.parametrize('mutation', ['exe', 'cwd', 'argv', 'tags', 'manifest'])
def test_same_pid_exec_and_ownership_or_manifest_changes_invalidate_approval(fixture, mutation):
    f = fixture
    child = row()
    child.update(exe='/usr/bin/tmux', argv=[b'tmux: server', b''])
    f.tmux._server_pid.return_value = child['pid']
    child['tags'] = {'DOCICH_TMUX_RUNTIME_ID': 'g4-dddddd', 'DOCICH_TMUX_GENERATION': '4',
                     'DOCICH_TMUX_ROLE': 'game'}
    f.probe.return_value = [child]
    proposal = inspect(f)
    assert proposal['status'] == 'admin-eligible'
    if mutation == 'exe': child['exe'] = '/usr/bin/other'
    if mutation == 'cwd': child['cwd'] = '/other'
    if mutation == 'argv': child['argv'] = [b'other', b'PRIVATE_ARG']
    if mutation == 'tags': child['tags']['DOCICH_TMUX_ROLE'] = 'agent'
    if mutation == 'manifest':
        path = f.root / 'runtimes/g1-aaaaaa/presentation.json'
        data = json.loads(path.read_text()); data['updated_at'] = 9
        atomic_write_json(path, data)
    before = snapshot(f)
    with pytest.raises(Refused):
        apply(f, proposal)
    assert snapshot(f) == before


@pytest.mark.parametrize('kind', ['inbox', 'ledger-queue', 'unknown-queue', 'soren91-queue',
                                  'paper-manual-queue', 'registry', 'improve'])
def test_competing_manual_requests_jobs_and_unregistered_queues_refuse(fixture, kind):
    f = fixture
    if kind == 'ledger-queue':
        f.ledger['queued_manual'] = {'corner': 'weather'}
        f.write(('corner_rotation.json',), f.ledger)
    elif kind == 'inbox': f.write(('corner_manual_queue.json',), {'corner': 'weather'})
    elif kind == 'improve': f.write(('corner_improve_nethack.json',), {'status': 'running'})
    elif kind == 'registry': atomic_write_json(f.soren / 'tmp/state/docich_program_active.json', {'owner_state': 'unknown'})
    else:
        name = {'unknown-queue': 'unregistered.json', 'soren91-queue': 'soren91_corner.json',
                'paper-manual-queue': 'paper_corner_manual.json'}[kind]
        atomic_write_json(f.soren / 'tmp/state/docich_program_queue' / name, {'status': 'waiting'})
    before = snapshot(f)
    assert inspect(f)['status'] == 'refused'
    assert snapshot(f) == before


def test_manual_inbox_and_program_queue_race_invalidates_release(fixture, monkeypatch):
    f = fixture
    proposal = inspect(f)
    import docich.nethack_admin_release as module
    original = module._context
    calls = 0
    def race(*args, **kw):
        nonlocal calls
        calls += 1
        result = original(*args, **kw)
        if calls == 1:
            f.write(('corner_manual_queue.json',), {'corner': 'weather'})
        return result
    monkeypatch.setattr(module, '_context', race)
    ledger = (f.root / 'corner_rotation.json').read_bytes()
    with pytest.raises(Refused): apply(f, proposal)
    assert (f.root / 'corner_rotation.json').read_bytes() == ledger


@pytest.mark.parametrize('final_clock', ['expired', 'regressed', 'valid'])
def test_commit_uses_fresh_clock_and_refuses_expiry_during_inspection(fixture, monkeypatch, final_clock):
    f = fixture
    proposal = inspect(f)
    start = f.now.timestamp()
    finish = {'expired': proposal['expires_at'], 'regressed': start - 1,
              'valid': (start + proposal['expires_at']) / 2}[final_clock]
    import docich.nethack_admin_release as module
    monkeypatch.setattr(module.time, 'time', Mock(side_effect=[start, finish]))
    before = snapshot(f)
    if final_clock == 'valid':
        assert apply(f, proposal, now=None)['status'] == 'admin-released'
        ledger = json.loads((f.root / 'corner_rotation.json').read_text())
        assert ledger[AUDIT_KEY][0]['released_at'] == finish
    else:
        with pytest.raises(Refused, match='approval_expired'): apply(f, proposal, now=None)
        assert snapshot(f) == before


@pytest.mark.parametrize('lock_name', ['corner-manual-queue.lock', 'locks/corner-rotation.lock',
    'locks/nethack-corner-tick.lock', 'locks/nethack-corner.lock',
    'locks/nethack-corner-manual-tick.lock', 'locks/nethack-corner-manual.lock',
    'soren/tmp/state/docich_program.lock', 'locks/game-switch.lock'])
def test_held_writer_lock_refuses_without_mutation(fixture, lock_name):
    import fcntl
    f = fixture; proposal = inspect(f); before = snapshot(f)
    with (f.root / lock_name).open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(Refused, match='busy'): apply(f, proposal)
    assert snapshot(f) == before


def test_missing_lock_never_created(fixture):
    f = fixture; proposal = inspect(f)
    lock = f.root / 'corner-manual-queue.lock'; lock.unlink()
    with pytest.raises(Refused, match='resources_unproven'): apply(f, proposal)
    assert not lock.exists()


def test_adapter_projects_only_automatic_committed_owner_manual_evidence_preserved(fixture):
    from docich.corner_adapters import NethackCornerAdapter
    f = fixture
    manual = {**f.owner, 'status': 'completed', 'rotation_request_id': '99999999-9999-4999-8999-999999999999'}
    f.write(('nethack_corner_manual.json',), manual)
    adapter = object.__new__(NethackCornerAdapter)
    adapter.g = SimpleNamespace(state_dir=f.root)
    adapter.manager = f.manager
    adapter.corner = SimpleNamespace(game='nethack')
    assert list(adapter.observations()) == [f.owner, manual]
    apply(f)
    observed = list(adapter.observations())
    assert observed[0]['status'] == 'interrupted' and observed[0]['administrative_closure'] is True
    assert observed[1] == manual
    assert 'restore_recovery' not in f.manager._read_state()
    f.manager.coordinator.assert_not_called()
