"""Checkpoint identity and acceptance tests; no emulator/save/ROM export."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import struct
import time
from types import SimpleNamespace
from unittest import mock
import uuid
import zlib

import pytest
from docich import hanjuku_resume as resume
from docich.adapters.base import AdapterError
from docich.game_switch import DeadlineExceededError, RuntimeSpec, atomic_write_json
from docich.config import load_global, load_game
from docich.retroarch_boundary import BOUNDARY_FILE, MANUAL_SAVE_FILE, identity, read_record
from docich import hanjuku_run


def state_bytes(mem=b'serialized core'):
    return (b'RASTATE\x01' + b'MEM ' + struct.pack('<I', len(mem)) + mem
            + b'\0' * (-len(mem) % 8) + b'END ' + b'\0' * 4)


def compressed(data):
    return b'#RZIPv\x01#' + struct.pack('<IQ', 131072, len(data)) + struct.pack('<I', len(zlib.compress(data))) + zlib.compress(data)


def ended(source, reason='screen_stalled'):
    """Synthetic generation-bound reached boundary; never real game data."""
    runtime = source.parent.parent
    generation = int(runtime.name.split('-')[0][1:])
    bound = dict(game='hanjuku-hero', runtime_id=runtime.name, generation=generation,
                 lease_id=str(uuid.uuid4()))
    run = dict(bound, schema=1, terminal_reason=reason, frame_sha256='a' * 64,
        unchanged_seconds=300, observed_monotonic=1000, last_input_monotonic=1,
        observations=1000, last_input_observations=0, name_entered=True,
        gameplay_seen=True, phase='title', title_count=3, title_since=0)
    atomic_write_json(runtime / hanjuku_run.RUN_FILE, run)
    record = dict(bound, schema=1, request_id=str(uuid.uuid4()), status='reached',
                  outcome=reason, frame_sha256=run['frame_sha256'])
    if reason != 'game_over':
        record.update(checkpoint=source.name, sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                      checkpoint_bytes=source.stat().st_size, checkpoint_mtime_ns=source.stat().st_mtime_ns)
    atomic_write_json(runtime / BOUNDARY_FILE, record)
    return record


@pytest.fixture
def setup(tmp_path):
    g = load_global(tmp_path)
    games = tmp_path / 'config/games'; games.mkdir(parents=True)
    rom = tmp_path / 'hanjuku-hero.sfc'; rom.write_bytes(b'ROM')
    core = tmp_path / 'snes9x.so'; core.write_bytes(b'CORE')
    (games / 'hanjuku-hero.toml').write_text(f'[game]\nname="hanjuku-hero"\nadapter="retroarch"\n[retroarch]\nrom="{rom}"\ncore="{core}"\n')
    game = load_game(g, 'hanjuku-hero')
    source = Path(g.state_dir) / 'runtimes/g514-8937051b/states/hanjuku-hero.state'
    source.parent.mkdir(parents=True); source.write_bytes(compressed(state_bytes()))
    older = Path(g.state_dir) / 'runtimes/g478-a7d65415/states/hanjuku-hero.state'
    older.parent.mkdir(parents=True); older.write_bytes(state_bytes(b'older'))
    os.utime(older, ns=(100, 100)); os.utime(source, ns=(200, 200))
    ended(older); ended(source)
    queue = {'corner': 'hanjuku-hero', 'request_id': str(uuid.uuid4()), 'resume_latest': True, 'selected_at': 1000000.0}
    rd = Path(g.state_dir) / 'runtimes/g700-abcdef12'; (rd / 'states').mkdir(parents=True)
    spec = RuntimeSpec('hanjuku-hero', 'retroarch', 700, rd.name, str(uuid.uuid4()), rd, 'game-700', 'agent-700', 'docich')
    def check(deadline, cancel):
        if time.monotonic() >= deadline or (cancel and cancel.is_set()):
            raise DeadlineExceededError('cancelled')
    adapter = SimpleNamespace(g=g, game=game, spec=spec, _check_active=check,
        _network_port=lambda:56000, _verify_manual_content=mock.Mock(), _require_paused=mock.Mock())
    return SimpleNamespace(g=g, game=game, source=source, older=older, queue=queue, spec=spec, adapter=adapter)


@pytest.mark.parametrize('packed', [False, True])
def test_core_digest_compares_core_not_container(tmp_path, packed):
    p=tmp_path/'save';p.write_bytes(compressed(state_bytes()) if packed else state_bytes())
    assert resume.core_digest(p)==hashlib.sha256(b'serialized core').hexdigest()


@pytest.mark.parametrize('bad', [b'', b'RASTATE\x01', b'other game', state_bytes()[:-1],
    b'#RZIPv\x01#'+struct.pack('<IQ',131072,resume.MAX_BYTES+1),
    compressed(state_bytes())[:-1], compressed(state_bytes())+b'junk',
    b'RASTATE\x01'+b'MEM '+struct.pack('<I',resume.MAX_BYTES)])
def test_malformed_or_expanding_checkpoint_rejected(tmp_path, bad):
    p=tmp_path/'save';p.write_bytes(bad)
    with pytest.raises(AdapterError):resume.core_digest(p)


def test_prepare_preserves_latest_and_never_copies_final_bot(setup):
    s=setup;resume.prepare(s.g,s.game,s.queue)
    receipt=read_record(Path(s.g.state_dir)/'hanjuku-resume'/resume.REQUEST_FILE)
    sealed=Path(s.g.state_dir)/'hanjuku-resume'/(s.queue['request_id']+'.state')
    assert receipt['selected_source'].startswith('runtimes/g514-')
    assert receipt['sha256']==hashlib.sha256(s.source.read_bytes()).hexdigest()
    assert sealed.read_bytes()==s.source.read_bytes()
    assert sealed.stat().st_mode & 0o777 == 0o600
    assert not (s.spec.runtime_dir/'hanjuku_bot.json').exists()
    resume.prepare(s.g,s.game,s.queue)  # exact retry keeps original selection
    os.utime(s.older,ns=(300,300))
    resume.prepare(s.g,s.game,s.queue)
    assert receipt==read_record(sealed.parent/resume.REQUEST_FILE)


def test_prepare_filters_thumbnails_backups_empty_and_symlinks(setup):
    s=setup
    for name,data in [('hanjuku-hero.state.png',b'picture'),('hanjuku-hero.state.bak',b'backup'),('hanjuku-hero.state9',b'')]:
        (s.source.parent/name).write_bytes(data)
    (s.source.parent/'hanjuku-hero.state1').symlink_to(s.older)
    resume.prepare(s.g,s.game,s.queue)
    assert read_record(Path(s.g.state_dir)/'hanjuku-resume'/resume.REQUEST_FILE)['selected_mtime_ns']==200


def test_corrupt_latest_fails_without_falling_back_to_older(setup):
    s=setup;s.source.write_bytes(b'corrupt')
    with pytest.raises(AdapterError):resume.prepare(s.g,s.game,s.queue)
    assert not (Path(s.g.state_dir)/'hanjuku-resume'/resume.REQUEST_FILE).exists()


def test_oversized_latest_fails_without_falling_back_to_older(setup):
    s=setup
    with s.source.open('wb') as stream:stream.truncate(resume.MAX_BYTES+1)
    os.utime(s.source,ns=(400,400))
    with pytest.raises(AdapterError):resume.prepare(s.g,s.game,s.queue)
    assert not (Path(s.g.state_dir)/'hanjuku-resume'/resume.REQUEST_FILE).exists()
    assert s.older.read_bytes()==state_bytes(b'older')


@pytest.mark.parametrize('failure', ['inbox','publication'])
def test_failed_publication_retries_same_request_and_sealed_selection(setup,failure):
    from docich.corner_catalog import Corner
    from docich.corner_rotation import CornerRotationManager
    s=setup
    (s.g.repo_root/'config.toml').write_text('[corner_rotation]\nenabled=true\n')
    g=load_global(s.g.repo_root,s.g.repo_root/'config.toml')
    adapter=SimpleNamespace(eligible=lambda:True,observations=lambda:[])
    def manager():
        return CornerRotationManager(g,clock=lambda:1000000.0,seed='retry',
            catalog=[Corner('hanjuku-hero','game','hanjuku-hero')],
            adapter_factory=lambda *_:adapter)
    first=manager()
    from docich.game_switch import atomic_write_json as write
    def inbox_write(path,data):
        if Path(path)==first._manual_queue_path:raise OSError('synthetic inbox replace failure')
        return write(path,data)
    patch=('docich.corner_rotation.atomic_write_json' if failure=='inbox' else 'docich.hanjuku_resume.publish')
    with mock.patch(patch,side_effect=inbox_write if failure=='inbox' else OSError('publication interrupted')):
        with pytest.raises(OSError):first.queue_manual('hanjuku-hero',resume_latest=True)
    receipt_path=Path(g.state_dir)/'hanjuku-resume'/resume.REQUEST_FILE
    prepared=read_record(receipt_path)
    assert first._manual_queue_path.exists()==(failure=='publication')
    # A new process sees a newer checkpoint, but must retain the original sealed
    # selection and identity instead of choosing it again or restarting fresh.
    os.utime(s.older,ns=(500,500))
    second=manager()
    queued=second.queue_manual('hanjuku-hero',resume_latest=True)
    assert prepared['status']=='prepared'
    assert queued['request_id']==prepared['request_id']
    published=read_record(receipt_path)
    assert published==dict(prepared,status='queued')
    assert second.queue_manual('hanjuku-hero',resume_latest=True)==queued
    assert json.loads(second._manual_queue_path.read_text())['request_id']==prepared['request_id']
    assert len(list(receipt_path.parent.glob('*.state')))==1


def test_published_request_is_not_revived_after_inbox_removal(setup):
    s=setup;resume.prepare(s.g,s.game,s.queue);resume.publish(s.g,s.queue)
    assert resume.recover_prepared(s.g,'hanjuku-hero') is None
    with pytest.raises(AdapterError):resume.prepare(s.g,s.game,dict(s.queue,request_id=str(uuid.uuid4())))


def test_unpublished_snapshot_change_cannot_be_requeued(setup):
    s=setup;resume.prepare(s.g,s.game,s.queue)
    sealed=Path(s.g.state_dir)/'hanjuku-resume'/(s.queue['request_id']+'.state')
    sealed.write_bytes(state_bytes(b'changed'))
    with pytest.raises(AdapterError,match='changed'):resume.recover_prepared(s.g,'hanjuku-hero')


def test_publication_rejects_another_identity(setup):
    s=setup;resume.prepare(s.g,s.game,s.queue)
    with pytest.raises(AdapterError,match='identity'):resume.publish(s.g,dict(s.queue,request_id=str(uuid.uuid4())))
    assert read_record(Path(s.g.state_dir)/'hanjuku-resume'/resume.REQUEST_FILE)['status']=='prepared'


def test_prepared_without_dispatch_cannot_be_claimed(setup):
    s=setup;resume.prepare(s.g,s.game,s.queue)
    resume.claim(s.adapter,time.monotonic()+30,None)
    assert read_record(Path(s.g.state_dir)/'hanjuku-resume'/resume.REQUEST_FILE)['status']=='prepared'
    assert not (s.spec.runtime_dir/resume.RECEIPT_FILE).exists()


def test_dispatched_prepared_request_survives_publication_interruption(setup):
    s=setup;resume.prepare(s.g,s.game,s.queue)
    atomic_write_json(Path(s.g.state_dir)/'corner_rotation.json',{'pending':dict(s.queue,source='manual')})
    candidate={k:getattr(s.spec,k) for k in ('game','runtime_id','generation','lease_id')}
    with mock.patch('docich.agent.fence.read_canonical',return_value={'candidate':candidate}):
        resume.claim(s.adapter,time.monotonic()+30,None)
    assert read_record(s.spec.runtime_dir/resume.RECEIPT_FILE)['status']=='claimed'


def stage(s, *, pending=None, candidate=None):
    resume.prepare(s.g,s.game,s.queue)
    resume.publish(s.g,s.queue)
    pending=pending if pending is not None else dict(s.queue,source=s.queue.get('source','manual'),phase='dispatched')
    atomic_write_json(Path(s.g.state_dir)/'corner_rotation.json', {'pending':pending})
    candidate=candidate if candidate is not None else {k:getattr(s.spec,k) for k in ('game','runtime_id','generation','lease_id')}
    with mock.patch('docich.agent.fence.read_canonical',return_value={'candidate':candidate}):
        resume.claim(s.adapter,time.monotonic()+30,None)


def test_claim_requires_manual_dispatch_and_exact_candidate(setup):
    s=setup;stage(s,pending={'request_id':'another','source':'automatic'})
    assert not (s.spec.runtime_dir/resume.RECEIPT_FILE).exists()
    with pytest.raises(AdapterError):stage(s,candidate={'runtime_id':'wrong'})
    assert read_record(Path(s.g.state_dir)/'hanjuku-resume'/resume.REQUEST_FILE)['status']=='queued'
    stage(s)
    receipt=read_record(s.spec.runtime_dir/resume.RECEIPT_FILE)
    assert receipt['status']=='claimed' and receipt['lease_id']==s.spec.lease_id
    assert (s.spec.runtime_dir/'states/hanjuku-hero.state').read_bytes()==s.source.read_bytes()
    with pytest.raises(AdapterError):resume.require_verified(s.spec)
    with pytest.raises(AdapterError):stage(s)  # candidate never starts fresh on retry


def test_claim_rejects_changed_rom_without_default_game(setup):
    s=setup;resume.prepare(s.g,s.game,s.queue)
    (s.g.repo_root/'hanjuku-hero.sfc').write_bytes(b'changed')
    with pytest.raises(AdapterError):stage(s)
    assert not (s.spec.runtime_dir/resume.RECEIPT_FILE).exists()


def restore_test(s, *, valid=True, ack=True, chapter=1, native_frame=None,
                 stale_writes=(), seconds=None):
    """Exercise restore. ``stale_writes`` lists 1-based SAVE writes whose file
    mtime must predate the request, so the production loop legitimately retries
    the roundtrip instead of accepting an unprovable save."""
    stage(s);commands=[];writes=[];slots_written=[]
    s.slots_written=slots_written
    path=s.spec.runtime_dir/'states/hanjuku-hero.state1'
    slots=[0]
    load_source=s.spec.runtime_dir/'states/hanjuku-hero.state'
    original_source=load_source.read_bytes()
    atomic_write_json(s.spec.runtime_dir/'presentation.json', {'display':':99','width':256,'height':224,'window':'4'})
    if valid=='unchanged_future':
        path.write_bytes(state_bytes())
        os.utime(path,ns=(time.time_ns()+60_000_000_000,time.time_ns()+60_000_000_000))
    def send(command,**kwargs):
        commands.append(command)
        if command=='GET_STATUS':return ('GET_STATUS PLAYING Snes9x,hanjuku-hero' if 'PAUSE_TOGGLE' in commands else 'GET_STATUS PAUSED Snes9x,hanjuku-hero')
        if command=='LOAD_STATE_SLOT 0':return 'LOAD_STATE_SLOT 0' if ack else None
        if command=='STATE_SLOT_PLUS':slots[0]+=1
        if command=='STATE_SLOT_MINUS':slots[0]-=1
        if command=='SAVE_STATE':
            if valid=='unchanged_future':return None
            if slots[0]==1:assert load_source.read_bytes()==original_source
            else:assert writes[-1]==b'serialized core'  # complete proof before slot0 is touched
            # First SAVE precedes asynchronous load completion. It must not be
            # accepted solely because the LOAD receipt/mtime exist.
            payload=b'boot state' if not valid or not writes else b'serialized core'
            output=s.spec.runtime_dir/('states/hanjuku-hero.state'+(str(slots[0]) if slots[0] else ''))
            output.write_bytes(state_bytes(payload));writes.append(payload);slots_written.append(slots[0])
            if len(writes) in stale_writes:
                # A file update older than the request proves nothing: the
                # production verification loop must retry, not trust it.
                stale=time.time_ns()-5_000_000_000
                os.utime(output,ns=(stale,stale))
        return None
    frame=SimpleNamespace(digest=lambda:'loaded-native-rgb',resized=lambda:frame)
    if native_frame is not None:
        from docich.hanjuku_screen import parse
        loaded_screen = parse(native_frame.resized())
        frame = native_frame
    else:
        loaded_screen = SimpleNamespace(header={'chapter':chapter},kind='map',lines=[])
    with mock.patch('docich.adapters.retroarch.send_ra_cmd',side_effect=send), \
         mock.patch('docich.xkit.XKit.screenshot'), \
         mock.patch('docich.hanjuku_pixels.read_png',return_value=frame), \
         mock.patch('docich.hanjuku_screen.parse',return_value=loaded_screen):
        budget=seconds if seconds is not None else (.3 if not valid or valid=='unchanged_future' or chapter is None and native_frame is None else 30)
        resume.restore(s.adapter,time.monotonic()+budget,None)
    return commands,writes


def test_native_roundtrip_must_match_before_ai_unpause_and_fresh_policy(setup):
    s=setup;commands,writes=restore_test(s)
    # The pre-load SAVE is never accepted as proof; every later SAVE re-proves
    # the core in its own slot before the next slot is touched.
    assert writes[0]==b'boot state'
    assert set(writes[1:])=={b'serialized core'}
    assert len(writes)==commands.count('SAVE_STATE')
    assert set(s.slots_written)=={0,1}  # both slots are proven independently
    assert commands.count('LOAD_STATE_SLOT 0')==1
    assert commands.index('STATE_SLOT_PLUS')<commands.index('SAVE_STATE')<commands.index('STATE_SLOT_MINUS')
    assert commands.index('LOAD_STATE_SLOT 0')<commands.index('SAVE_STATE')<commands.index('FRAMEADVANCE')<commands.index('PAUSE_TOGGLE')
    receipt=read_record(s.spec.runtime_dir/resume.RECEIPT_FILE)
    assert receipt['status']=='verified' and receipt['chapter']==1
    assert read_record(s.spec.runtime_dir/'hanjuku_bot.json')=={'policy':{'chapter':1,'name':{'done':True}}}
    assert s.source.read_bytes()==compressed(state_bytes())
    resume.require_verified(s.spec)


def test_stale_core_write_is_retried_without_a_second_load(setup):
    """A SAVE whose file mtime predates the request cannot prove the roundtrip.

    Production repeats the SAVE until a stable core is observed, so the test
    must accept that retry while still requiring exactly one LOAD, an unchanged
    sealed source and a verified receipt (#2099).
    """
    s=setup;commands,writes=restore_test(s,stale_writes=(2,),seconds=8)
    assert writes[0]==b'boot state'
    assert set(writes[1:])=={b'serialized core'}
    assert s.slots_written.count(1)>=3  # the unprovable slot-1 save was repeated
    assert s.slots_written.count(0)>=1
    assert commands.count('LOAD_STATE_SLOT 0')==1
    receipt=read_record(s.spec.runtime_dir/resume.RECEIPT_FILE)
    assert receipt['status']=='verified' and receipt['chapter']==1
    assert s.source.read_bytes()==compressed(state_bytes())
    resume.require_verified(s.spec)


@pytest.mark.parametrize('failure', ['mismatch','no_ack','unread_header'])
def test_failed_load_never_opens_ai_or_rewinds_again(setup,failure):
    s=setup
    with pytest.raises((AdapterError,DeadlineExceededError)):
        restore_test(s,valid=failure not in {'mismatch','unchanged_future'},ack=failure!='no_ack',chapter=None if failure=='unread_header' else 1)
    assert read_record(s.spec.runtime_dir/resume.RECEIPT_FILE)['status']=='claimed'
    with pytest.raises(AdapterError):resume.require_verified(s.spec)
    assert not (s.spec.runtime_dir/'hanjuku_bot.json').exists()
    assert s.source.read_bytes()==compressed(state_bytes())


def test_preserved_native_checkpoint_header():
    from docich.hanjuku_pixels import read_png
    from docich.hanjuku_screen import parse
    path=Path(__file__).parent/'fixtures/hanjuku/g514-checkpoint-native.png'
    assert parse(read_png(path).resized()).header=={'chapter':1,'year':2,'month':11,'gold':74}


def test_unchanged_future_mtime_is_not_fresh_roundtrip_proof(setup):
    s=setup
    with pytest.raises(DeadlineExceededError):restore_test(s,valid='unchanged_future')
    with pytest.raises(AdapterError):resume.require_verified(s.spec)


def test_verified_readiness_does_not_reload_or_reset_policy(setup):
    s=setup;restore_test(s)
    original=read_record(s.spec.runtime_dir/'hanjuku_bot.json')
    with mock.patch('docich.adapters.retroarch.send_ra_cmd') as send:
        resume.restore(s.adapter,time.monotonic()+30,None)
    send.assert_not_called()
    assert read_record(s.spec.runtime_dir/'hanjuku_bot.json')==original


def test_verified_runtime_cannot_fall_back_to_fresh_when_pane_is_missing(setup):
    s=setup;restore_test(s)
    s.adapter._game_window_target=lambda:'game-700'
    s.adapter.tmux=SimpleNamespace(window_target_exists=lambda t:False)
    with pytest.raises(AdapterError):resume.claim(s.adapter,time.monotonic()+30,None)
    s.adapter.tmux.window_target_exists=lambda t:True
    s.adapter._verify_window_ownership=mock.Mock()
    resume.claim(s.adapter,time.monotonic()+30,None)
    s.adapter._verify_window_ownership.assert_called_once_with('game-700','game')


def test_native_fake_hero_checkpoint_restores_with_only_observed_chapter(setup):
    from docich.hanjuku_pixels import read_png
    frame=read_png(Path(__file__).parent/'fixtures/hanjuku/g700-fake-hero-announcement.png')
    commands,writes=restore_test(setup,chapter=None,native_frame=frame)
    # Same proof-ordering contract as the other roundtrip tests: only the
    # pre-load write may differ, both slots are proven independently, and a
    # legitimate SAVE retry (stale mtime under load) is not a failure (#2138).
    assert writes[0]==b'boot state'
    assert set(writes[1:])=={b'serialized core'}
    assert len(writes)==commands.count('SAVE_STATE')
    assert set(setup.slots_written)=={0,1}
    assert commands.count('LOAD_STATE_SLOT 0')==1
    assert commands.index('FRAMEADVANCE') < commands.index('PAUSE_TOGGLE')
    receipt=read_record(setup.spec.runtime_dir/resume.RECEIPT_FILE)
    assert receipt['status']=='verified' and receipt['chapter']==2
    assert receipt['frame_sha256']==frame.digest()
    assert read_record(setup.spec.runtime_dir/'hanjuku_bot.json')=={
        'policy': {'name': {'done': True}, 'chapter': 2}}


def test_fake_hero_stale_core_write_is_retried_without_a_second_load(setup):
    """A SAVE whose file mtime predates the request cannot prove the roundtrip.

    CI mtime granularity can make a legitimate slot-1 SAVE look older than the
    request, so production repeats the SAVE instead of trusting it. That retry
    must still end with exactly one LOAD, a verified receipt naming the observed
    chapter and an unchanged sealed source (#2099, #2138).
    """
    from docich.hanjuku_pixels import read_png
    frame=read_png(Path(__file__).parent/'fixtures/hanjuku/g700-fake-hero-announcement.png')
    commands,writes=restore_test(setup,chapter=None,native_frame=frame,
                                 stale_writes=(2,),seconds=8)
    assert writes[0]==b'boot state'
    assert set(writes[1:])=={b'serialized core'}
    assert len(writes)==commands.count('SAVE_STATE')
    assert setup.slots_written.count(1)>=3  # the unprovable slot-1 save was repeated
    assert setup.slots_written.count(0)>=1
    assert commands.count('LOAD_STATE_SLOT 0')==1
    assert commands.index('FRAMEADVANCE') < commands.index('PAUSE_TOGGLE')
    receipt=read_record(setup.spec.runtime_dir/resume.RECEIPT_FILE)
    assert receipt['status']=='verified' and receipt['chapter']==2
    assert receipt['frame_sha256']==frame.digest()
    assert read_record(setup.spec.runtime_dir/'hanjuku_bot.json')=={
        'policy': {'name': {'done': True}, 'chapter': 2}}
    assert setup.source.read_bytes()==compressed(state_bytes())
    resume.require_verified(setup.spec)


def test_native_starter_checkpoint_restores_without_inventing_chapter(setup):
    from docich.hanjuku_pixels import read_png
    from docich.hanjuku_screen import parse
    frame=read_png(Path(__file__).parent/'fixtures/hanjuku/g698-resume-starter.png')
    screen=parse(frame.resized())
    assert screen.kind=='starter_list' and screen.header['chapter'] is None
    restore_test(setup,chapter=None,native_frame=frame)
    receipt=read_record(setup.spec.runtime_dir/resume.RECEIPT_FILE)
    assert receipt['status']=='verified' and receipt['chapter'] is None
    policy=read_record(setup.spec.runtime_dir/'hanjuku_bot.json')['policy']
    assert policy=={'name':{'done':True},'resume_chapter_pending':True}
    assert 'garrison' not in policy and 'chapter' not in policy


def test_native_mid_selection_checkpoint_restores_with_only_fresh_cancellation_owner(setup):
    from docich.hanjuku_pixels import read_png
    from docich.hanjuku_screen import parse
    frame=read_png(Path(__file__).parent/'fixtures/hanjuku/g706-resume-card-select.png')
    assert parse(frame).kind=='card_select' and not parse(frame).header
    restore_test(setup,chapter=None,native_frame=frame)
    receipt=read_record(setup.spec.runtime_dir/resume.RECEIPT_FILE)
    assert receipt['status']=='verified' and receipt['chapter'] is None
    policy=read_record(setup.spec.runtime_dir/'hanjuku_bot.json')['policy']
    assert policy=={'name':{'done':True},'resume_chapter_pending':True,
                   'resume_sortie_cancel':{'readings':0}}
    assert setup.source.read_bytes()==compressed(state_bytes())


def test_starter_resume_seed_uses_live_selected_row_and_never_issues_sortie(setup):
    from docich.hanjuku_pixels import read_png
    from docich.hanjuku_bot import decide
    frame=read_png(Path(__file__).parent/'fixtures/hanjuku/g698-resume-starter.png')
    state={'policy':{'name':{'done':True},'resume_chapter_pending':True}}
    actions,updated=decide(frame.resized(),state)
    assert actions and actions[0]['buttons']==['down']
    assert updated['policy']['starter_party']['selected']==['ゼウス']
    assert updated['policy'].get('chapter') is None
    assert not any(r['decision']=='order_launched' for r in updated['_records'])


@pytest.mark.parametrize('castle', ['アルマムーン','あるまむーん','ボス','フー�リック','しゅじんこう'])
def test_unknown_resume_chapter_never_resolves_common_or_unread_names(castle):
    from docich.hanjuku_policy import observe_chapter_castle
    mem={'resume_chapter_pending':True,'_records':[]}
    observe_chapter_castle(mem,castle)
    assert mem.get('chapter') is None and mem['resume_chapter_pending'] is True


def test_unknown_resume_chapter_resolves_only_fresh_unique_castle():
    from docich.hanjuku_policy import observe_chapter_castle
    mem={'resume_chapter_pending':True,'_records':[]}
    observe_chapter_castle(mem,'フーリック')
    assert mem['chapter']==2 and mem['chapter_evidence']=='フーリック'
    assert 'resume_chapter_pending' not in mem and 'garrison' not in mem
    untouched={'_records':[]}
    observe_chapter_castle(untouched,'フーリック')
    assert untouched.get('chapter') is None


@pytest.fixture
def recovery_setup(setup, monkeypatch):
    from docich import hanjuku_resume_recovery as op
    from docich.corner_rotation import CornerRotationManager
    from docich.corner_catalog import Corner
    from docich.game_switch import GameSwitchStore, _initial_state
    s = setup
    config = s.g.repo_root / 'config.toml'
    config.write_text('[corner_rotation]\nenabled=true\n')
    s.g = load_global(s.g.repo_root, config); s.adapter.g = s.g
    manager = CornerRotationManager(s.g, clock=lambda:1000000.0,
        catalog=[Corner('hanjuku-hero','game','hanjuku-hero')],
        adapter_factory=lambda *_:SimpleNamespace(eligible=lambda:True, observations=lambda:[]))
    ledger = manager.load(1000000.0)
    stage(s)
    base = Path(s.g.state_dir)
    claim = read_record(s.spec.runtime_dir / resume.RECEIPT_FILE)
    ledger.update(status='recovery_required', pending=dict(s.queue,source='manual',phase='dispatched'))
    manager.save(ledger)
    owner = {'status':'failed','rotation_request_id':s.queue['request_id'],'game':'hanjuku-hero','detail':'retained'}
    atomic_write_json(base/'retro_corner.json',owner)
    atomic_write_json(s.spec.runtime_dir/'presentation.json',{'status':'stopped'})
    soren = s.g.repo_root/'soren'; (soren/'tmp/state').mkdir(parents=True)
    (soren/'tmp/state/docich_program.lock').touch()
    (base/'corner-manual-queue.lock').touch()
    for name in ('corner-rotation','retro-corner','retro-corner-manual'):
        (base/'locks').mkdir(exist_ok=True); (base/f'locks/{name}.lock').touch()
    store=GameSwitchStore(base);store.initialize()
    idle = _initial_state();idle['next_generation']=700;store.canonical.save(idle)
    terminal=dict(store.accept_request(s.queue['request_id'],'switch','hanjuku-hero').receipt)
    terminal.update(runtime_id=s.spec.runtime_id,runtime_dir=str(s.spec.runtime_dir),status='rolled_back',
        result={'request_id':s.queue['request_id'],'generation':700,'status':'rolled_back'})
    store.receipts.save(terminal)
    idle['next_generation']=701;store.canonical.save(idle)
    monkeypatch.setattr(op,'CornerRotationManager',lambda g:manager)
    monkeypatch.setattr(op,'resolve_soren_root',lambda g:soren)
    class Absent:
        def window_target_exists(self,*args,**kwargs):return False
        def session_target_exists(self,*args,**kwargs):return False
    s.tmux=Absent();s.op=op;s.store=store;s.manager=manager;s.claim=claim
    s.fingerprint=op.fingerprint(claim)
    return s


def snapshot_recovery(s):
    return {p:p.read_bytes() for p in Path(s.g.state_dir).rglob('*') if p.is_file()}


def recover_startup(s, **kwargs):
    return s.op.recover(s.g,tmux=s.tmux,now=lambda:1000000.0,**kwargs)


def test_recovery_check_read_only_and_exact_logical_checkpoint_preserved(recovery_setup):
    s=recovery_setup;before=snapshot_recovery(s)
    assert recover_startup(s)=={'status':'recovery-eligible','fingerprint':s.fingerprint}
    assert snapshot_recovery(s)==before
    # A newer save must never replace the immutable selection.
    s.older.write_bytes(state_bytes(b'newer'));os.utime(s.older,ns=(500,500))
    result=recover_startup(s,apply=True,expected=s.fingerprint)
    assert result['status']=='recovery-queued'
    current=read_record(Path(s.g.state_dir)/'hanjuku-resume'/resume.REQUEST_FILE)
    assert current['request_id']==s.queue['request_id']
    assert current['sha256']==s.claim['sha256']
    assert current['startup_recovery']['original']==s.claim
    queued=s.manager.load(1000000.0)['queued_manual']
    assert queued['request_id']!=s.queue['request_id']
    assert queued['request_id']==current['startup_recovery']['attempt_id']
    assert queued['resume_request_id']==s.queue['request_id']
    unchanged=snapshot_recovery(s)
    assert recover_startup(s,apply=True,expected=s.fingerprint)==result
    assert snapshot_recovery(s)==unchanged
    # Old local claim and terminal execution remain byte-for-byte immutable.
    assert (s.spec.runtime_dir/resume.RECEIPT_FILE).read_bytes()==before[s.spec.runtime_dir/resume.RECEIPT_FILE]
    path=s.store.receipts._path(s.queue['request_id'])
    assert path.read_bytes()==before[path]
    assert len(list((Path(s.g.state_dir)/'hanjuku-resume').glob('*.state')))==1


@pytest.mark.parametrize('change', ['bot','run','events','wrong-fingerprint','local-mismatch',
    'not-rolled-back','wrong-runtime','active','live-presentation','tmux','paused','checkpoint',
    'queued-conflict','owner-active','automatic','wrong-generation','missing-lock'])
def test_recovery_refuses_gameplay_uncertainty_drift_or_other_owner(recovery_setup,change):
    from docich.hanjuku_manual_cancel import CancelRefused
    from docich.game_switch import runtime_state_dict
    s=recovery_setup;base=Path(s.g.state_dir);expected=s.fingerprint
    if change in {'bot','run','events'}:
        names={'bot':'hanjuku_bot.json','run':'hanjuku_run.json','events':'hanjuku_events.jsonl'}
        (s.spec.runtime_dir/names[change]).touch()
    elif change=='wrong-fingerprint':expected='a'*64
    elif change=='local-mismatch':atomic_write_json(s.spec.runtime_dir/resume.RECEIPT_FILE,dict(s.claim,status='verified'))
    elif change in {'not-rolled-back','wrong-runtime'}:
        d=s.store.receipts.load(s.queue['request_id'])
        if change=='not-rolled-back':d['status']='failed'
        else:d.update(runtime_id='g700-ffffabcd',runtime_dir=str(base/'runtimes/g700-ffffabcd'))
        s.store.receipts.save(d)
    elif change=='active':
        d,_=s.store.canonical.load();d.update(phase='ready',active=runtime_state_dict(RuntimeSpec.from_runtime(base, {**vars(s.spec)}),'2026-10-12T00:00:00Z'));s.store.canonical.save(d)
    elif change=='live-presentation':atomic_write_json(s.spec.runtime_dir/'presentation.json',{'status':'ready'})
    elif change=='tmux':s.tmux.window_target_exists=lambda *a,**k:True
    elif change=='paused':
        p=base/'corners/hanjuku-hero.paused';p.parent.mkdir();p.touch()
    elif change=='checkpoint':(base/'hanjuku-resume'/(s.queue['request_id']+'.state')).write_bytes(state_bytes(b'changed'))
    elif change=='queued-conflict':
        d=s.manager.load(1000000.0);d['queued_manual']={'corner':'weather','request_id':str(uuid.uuid4()),'selected_at':1000000.0};s.manager.save(d)
    elif change=='owner-active':atomic_write_json(base/'retro_corner.json',{'status':'active','rotation_request_id':s.queue['request_id']})
    elif change=='automatic':
        d=s.manager.load(1000000.0);d['pending']['source']='automatic';s.manager.save(d)
    elif change=='wrong-generation':
        d=dict(s.claim,generation=True);atomic_write_json(base/'hanjuku-resume'/resume.REQUEST_FILE,d)
    elif change=='missing-lock':(base/'locks/retro-corner.lock').unlink()
    before=snapshot_recovery(s)
    with pytest.raises(CancelRefused):recover_startup(s,apply=True,expected=expected)
    assert snapshot_recovery(s)==before


@pytest.mark.parametrize('failed_write', ['owner','ledger'])
def test_recovery_partial_publication_repairs_same_attempt(recovery_setup,failed_write):
    s=recovery_setup;base=Path(s.g.state_dir)
    from docich.game_switch import atomic_write_json as write
    def fail(path,value):
        if Path(path)==(base/'retro_corner.json' if failed_write=='owner' else s.manager.path):
            raise OSError('interrupted')
        return write(path,value)
    target='docich.hanjuku_resume_recovery.atomic_write_json' if failed_write=='owner' else 'docich.corner_rotation.atomic_write_json'
    with mock.patch(target,side_effect=fail):
        with pytest.raises((OSError,s.op.CancelRefused)):recover_startup(s,apply=True,expected=s.fingerprint)
    prepared=read_record(base/'hanjuku-resume'/resume.REQUEST_FILE)
    assert prepared['request_id']==s.queue['request_id']
    assert recover_startup(s,apply=True,expected=s.fingerprint)['status']=='recovery-queued'
    queued=s.manager.load(1000000.0)['queued_manual']
    assert queued['request_id']==prepared['startup_recovery']['attempt_id']


def test_recovery_claim_requires_derived_attempt_and_retains_logical_uuid(recovery_setup):
    s=recovery_setup;base=Path(s.g.state_dir)
    recover_startup(s,apply=True,expected=s.fingerprint)
    queued=s.manager.load(1000000.0)['queued_manual']
    # Normal / natural starts cannot steal the queued recovery.
    rd=base/'runtimes/g701-cafe1234';(rd/'states').mkdir(parents=True)
    spec=replace(s.spec,generation=701,runtime_id=rd.name,runtime_dir=rd,lease_id=str(uuid.uuid4()))
    adapter=SimpleNamespace(**{**vars(s.adapter),'spec':spec})
    candidate={k:getattr(spec,k) for k in ('game','runtime_id','generation','lease_id')}
    for pending in (dict(s.queue,source='manual'),dict(queued,source='automatic'),
                    {k:v for k,v in dict(queued,source='manual').items() if k!='resume_request_id'}):
        atomic_write_json(base/'corner_rotation.json',{'pending':pending})
        with mock.patch('docich.agent.fence.read_canonical',return_value={'candidate':candidate}):
            resume.claim(adapter,time.monotonic()+30,None)
        assert not (rd/resume.RECEIPT_FILE).exists()
    atomic_write_json(base/'corner_rotation.json',{'pending':dict(queued,source='manual')})
    with mock.patch('docich.agent.fence.read_canonical',return_value={'candidate':candidate}):
        resume.claim(adapter,time.monotonic()+30,None)
    receipt=read_record(rd/resume.RECEIPT_FILE)
    assert receipt['request_id']==s.queue['request_id'] and receipt['generation']==701
    assert (rd/'states/hanjuku-hero.state').read_bytes()==s.source.read_bytes()
    before=snapshot_recovery(s)
    assert recover_startup(s,apply=True,expected=s.fingerprint)['status']=='already-dispatched'
    assert snapshot_recovery(s)==before


def automatic_recovery(s, *, released=False, reused_owner=False):
    base = Path(s.g.state_dir)
    s.claim = dict(s.claim, dispatch_source='automatic', explicit_resume=False)
    s.fingerprint = s.op.fingerprint(s.claim)
    atomic_write_json(base/'hanjuku-resume'/resume.REQUEST_FILE, s.claim)
    atomic_write_json(s.spec.runtime_dir/resume.RECEIPT_FILE, s.claim)
    ledger = s.manager.load(1000000.0)
    ledger['pending'].update(source='automatic')
    ledger['pending'].pop('resume_latest')
    if released:
        ledger.update(status='waiting', pending=None, failed_corners=[{
            'corner':'hanjuku-hero', 'request_id':s.queue['request_id'],
            'resolution':'released-unverified'}])
    s.manager.save(ledger)
    if reused_owner:
        atomic_write_json(base/'retro_corner.json', {
            'game':'bastet', 'status':'completed', 'rotation_request_id':str(uuid.uuid4())})


@pytest.mark.parametrize('released,reused_owner', [(False,False),(True,False),(True,True)])
def test_automatic_failed_start_recovers_same_checkpoint(recovery_setup,released,reused_owner):
    s=recovery_setup;automatic_recovery(s,released=released,reused_owner=reused_owner)
    base=Path(s.g.state_dir);before=snapshot_recovery(s)
    with pytest.raises(AdapterError,match='another checkpoint request is pending'):
        resume.prepare_start(s.g,dict(s.queue,request_id=str(uuid.uuid4())))
    assert recover_startup(s)['status']=='recovery-eligible'
    assert snapshot_recovery(s)==before
    assert recover_startup(s,apply=True,expected=s.fingerprint)['status']=='recovery-queued'
    current=read_record(base/'hanjuku-resume'/resume.REQUEST_FILE)
    assert current['startup_recovery']['original']==s.claim
    assert current['dispatch_source']=='manual' and current['explicit_resume'] is True
    assert current['sha256']==s.claim['sha256']
    assert current['source_boundary']==s.claim['source_boundary']
    assert current['request_id']==s.claim['request_id']
    assert read_record(s.spec.runtime_dir/resume.RECEIPT_FILE)==s.claim
    if reused_owner:
        assert (base/'retro_corner.json').read_bytes()==before[base/'retro_corner.json']
    queued=s.manager.load(1000000.0)['queued_manual']
    unchanged=snapshot_recovery(s)
    assert recover_startup(s,apply=True,expected=s.fingerprint)['status']=='recovery-queued'
    assert snapshot_recovery(s)==unchanged
    pending=dict(queued,source='manual')
    resume.prepare_start(s.g,pending)
    rd=base/'runtimes/g701-cafe1234';(rd/'states').mkdir(parents=True)
    spec=replace(s.spec,generation=701,runtime_id=rd.name,runtime_dir=rd,lease_id=str(uuid.uuid4()))
    adapter=SimpleNamespace(**{**vars(s.adapter),'spec':spec})
    s.game.raw['hanjuku']={'script_bot':True}
    atomic_write_json(base/'corner_rotation.json',{'pending':pending})
    candidate={k:getattr(spec,k) for k in ('game','runtime_id','generation','lease_id')}
    with mock.patch('docich.agent.fence.read_canonical',return_value={'candidate':candidate}):
        resume.claim(adapter,time.monotonic()+30,None)
    receipt=read_record(rd/resume.RECEIPT_FILE)
    assert receipt['request_id']==s.claim['request_id'] and receipt['status']=='claimed'
    assert (rd/'states/hanjuku-hero.state').read_bytes()==s.source.read_bytes()


def test_automatic_recovery_accepts_pinned_release_after_audit_rollover(recovery_setup):
    s=recovery_setup;automatic_recovery(s,released=True,reused_owner=True)
    ledger=s.manager.load(1000000.0)
    ledger.update(failed_corners=[],released_requests={
        'hanjuku-hero':{'request_id':s.claim['request_id'],'at':1000000.0}})
    s.manager.save(ledger)
    assert recover_startup(s,apply=True,expected=s.fingerprint)['status']=='recovery-queued'


@pytest.mark.parametrize('change', ['other-pending','owner-active',
    'gameplay','checkpoint','not-rolled-back','explicit-mismatch','fresh','paused',
    'newer-run','newer-save','newer-receipt','newer-result','runtime-symlink'])
def test_detached_automatic_recovery_refuses_uncertainty(recovery_setup,change):
    s=recovery_setup;automatic_recovery(s,released=True,reused_owner=True)
    base=Path(s.g.state_dir);ledger=s.manager.load(1000000.0)
    if change=='other-pending':ledger['pending']=dict(s.queue,corner='weather',source='automatic',phase='dispatched')
    elif change=='owner-active':atomic_write_json(base/'retro_corner.json',{'status':'active','game':'bastet'})
    elif change=='gameplay':(s.spec.runtime_dir/'hanjuku_run.json').touch()
    elif change=='checkpoint':(base/'hanjuku-resume'/(s.claim['request_id']+'.state')).write_bytes(state_bytes(b'changed'))
    elif change=='not-rolled-back':
        row=s.store.receipts.load(s.claim['request_id']);row['status']='failed';s.store.receipts.save(row)
    elif change in {'explicit-mismatch','fresh'}:
        s.claim.update({'explicit_resume':True} if change=='explicit-mismatch' else {'start_mode':'fresh'})
        s.fingerprint=s.op.fingerprint(s.claim)
        atomic_write_json(base/'hanjuku-resume'/resume.REQUEST_FILE,s.claim)
        atomic_write_json(s.spec.runtime_dir/resume.RECEIPT_FILE,s.claim)
    elif change=='paused':
        p=base/'corners/hanjuku-hero.paused';p.parent.mkdir();p.touch()
    elif change in {'newer-run','newer-save'}:
        runtime=base/'runtimes/g701-cafe1234';runtime.mkdir()
        if change=='newer-run':(runtime/'hanjuku_run.json').touch()
        else:
            (runtime/'states').mkdir();(runtime/'states/hanjuku-hero.state').touch()
    elif change in {'newer-receipt','newer-result'}:
        target='sorengame' if change=='newer-result' else 'hanjuku-hero'
        row=dict(s.store.accept_request(str(uuid.uuid4()),'switch',target).receipt)
        row.update(status='succeeded',result={'status':'succeeded','to_game':'hanjuku-hero'})
        s.store.receipts.save(row)
    elif change=='runtime-symlink':
        (base/'runtimes/g701-cafe1234').symlink_to(s.spec.runtime_dir)
    s.manager.save(ledger)
    before=snapshot_recovery(s)
    with pytest.raises(s.op.CancelRefused):recover_startup(s,apply=True,expected=s.fingerprint)
    assert snapshot_recovery(s)==before


def interrupted_manual_release(s, *, last=None, owner=None):
    """g1063 2026-10-28: the manual resume's probe hit its deadline; rotation
    closed the slot as interrupted (no pending, no failed_corners audit)."""
    base=Path(s.g.state_dir);ledger=s.manager.load(1000000.0)
    ledger.update(status='waiting',pending=None,failed_corners=[],released_requests={},
        last_result=last or {'at':1000000.0,'corner':'hanjuku-hero',
                             'request_id':s.queue['request_id'],'status':'interrupted'})
    s.manager.save(ledger)
    atomic_write_json(base/'retro_corner.json', owner or {'status':'interrupted','game':'hanjuku-hero',
        'rotation_request_id':s.queue['request_id'],'startup_resume_recovery':None})


def test_manual_resume_closed_as_interrupted_recovers_same_checkpoint(recovery_setup):
    s=recovery_setup;interrupted_manual_release(s)
    base=Path(s.g.state_dir);before=snapshot_recovery(s)
    with pytest.raises(AdapterError,match='another checkpoint request is pending'):
        resume.prepare_start(s.g,dict(s.queue,request_id=str(uuid.uuid4())))
    assert recover_startup(s)=={'status':'recovery-eligible','fingerprint':s.fingerprint}
    assert snapshot_recovery(s)==before
    assert recover_startup(s,apply=True,expected=s.fingerprint)['status']=='recovery-queued'
    current=read_record(base/'hanjuku-resume'/resume.REQUEST_FILE)
    assert current['startup_recovery']['original']==s.claim
    assert current['sha256']==s.claim['sha256']
    queued=s.manager.load(1000000.0)['queued_manual']
    assert queued['resume_request_id']==s.queue['request_id']


@pytest.mark.parametrize('change', ['other-request','completed','owner-other','owner-active','newer-run'])
def test_interrupted_manual_recovery_refuses_other_evidence(recovery_setup,change):
    s=recovery_setup;base=Path(s.g.state_dir)
    last={'at':1000000.0,'corner':'hanjuku-hero','request_id':s.queue['request_id'],'status':'interrupted'}
    owner={'status':'interrupted','game':'hanjuku-hero','rotation_request_id':s.queue['request_id']}
    if change=='other-request':last['request_id']=str(uuid.uuid4())
    elif change=='completed':last['status']='completed'
    elif change=='owner-other':owner['rotation_request_id']=str(uuid.uuid4())
    elif change=='owner-active':owner['status']='active'
    interrupted_manual_release(s,last=last,owner=owner)
    if change=='newer-run':
        runtime=base/'runtimes/g701-cafe1234';runtime.mkdir();(runtime/'hanjuku_run.json').touch()
    before=snapshot_recovery(s)
    with pytest.raises(s.op.CancelRefused):recover_startup(s,apply=True,expected=s.fingerprint)
    assert snapshot_recovery(s)==before


def test_automatic_recovery_works_after_original_audit_is_evicted(recovery_setup):
    s=recovery_setup;automatic_recovery(s,released=True,reused_owner=True)
    ledger=s.manager.load(1000000.0)
    ledger['failed_corners']=[]
    ledger['released_requests']={'hanjuku-hero':{'request_id':str(uuid.uuid4()),'at':1000000.0}}
    s.manager.save(ledger)
    outcome=s.op.queue_failed_start(s.g,tmux=s.tmux,now=lambda:1000000.0)
    assert outcome['status']=='queued' and outcome['corner']=='hanjuku-hero'
    assert outcome['request_id']==s.manager.load(1000000.0)['queued_manual']['request_id']
    assert s.op.queue_failed_start(s.g,tmux=s.tmux,now=lambda:1000000.0)==outcome


@pytest.fixture
def failed_derived_attempt(recovery_setup):
    s=recovery_setup;automatic_recovery(s,released=True,reused_owner=True)
    base=Path(s.g.state_dir)
    first=s.op.queue_failed_start(s.g,tmux=s.tmux,now=lambda:1000000.0)
    ledger=s.manager.load(1000000.0)
    queued=ledger['queued_manual']
    ledger.update(status='running',pending=dict(queued,source='manual',phase='dispatched'),queued_manual=None)
    s.manager.save(ledger)
    rd=base/'runtimes/g701-cafe1234';(rd/'states').mkdir(parents=True)
    spec=replace(s.spec,generation=701,runtime_id=rd.name,runtime_dir=rd,lease_id=str(uuid.uuid4()))
    adapter=SimpleNamespace(**{**vars(s.adapter),'spec':spec})
    s.game.raw['hanjuku']={'script_bot':True}
    candidate={k:getattr(spec,k) for k in ('game','runtime_id','generation','lease_id')}
    with mock.patch('docich.agent.fence.read_canonical',return_value={'candidate':candidate}):
        resume.claim(adapter,time.monotonic()+30,None)
    claim=read_record(rd/resume.RECEIPT_FILE)
    atomic_write_json(rd/'presentation.json',{'status':'stopped'})
    row=dict(s.store.accept_request(first['request_id'],'switch','hanjuku-hero').receipt)
    row.update(runtime_id=spec.runtime_id,runtime_dir=str(rd),status='rolled_back',
               result={'request_id':first['request_id'],'generation':701,'status':'rolled_back'})
    s.store.receipts.save(row)
    from docich.game_switch import _initial_state
    idle=_initial_state();idle['next_generation']=702;s.store.canonical.save(idle)
    ledger.update(status='waiting',pending=None,queued_manual=None)
    s.manager.save(ledger)
    atomic_write_json(base/'retro_corner.json',{
        'status':'interrupted','game':'hanjuku-hero','rotation_request_id':first['request_id']})
    s.retry_claim=claim;s.retry_spec=spec;s.retry_first=first;s.retry_adapter=adapter
    return s


def test_webui_can_retry_derived_rollback_without_reselecting_save(failed_derived_attempt):
    s=failed_derived_attempt;base=Path(s.g.state_dir)
    result=s.op.queue_failed_start(s.g,tmux=s.tmux,now=lambda:1000000.0)
    assert result['status']=='queued' and result['request_id']!=s.retry_first['request_id']
    current=read_record(base/'hanjuku-resume'/resume.REQUEST_FILE)
    assert current['startup_recovery']['failed_attempt']['claim']==s.retry_claim
    assert current['startup_recovery']['original']==s.claim
    assert current['sha256']==s.claim['sha256'] and current['source_boundary']==s.claim['source_boundary']
    before=snapshot_recovery(s)
    assert s.op.queue_failed_start(s.g,tmux=s.tmux,now=lambda:1000000.0)==result
    assert snapshot_recovery(s)==before
    ledger=s.manager.load(1000000.0);queued=ledger['queued_manual']
    ledger.update(status='running',pending=dict(queued,source='manual',phase='dispatched'),queued_manual=None)
    s.manager.save(ledger)
    rd=base/'runtimes/g702-babe1234';(rd/'states').mkdir(parents=True)
    spec=replace(s.spec,generation=702,runtime_id=rd.name,runtime_dir=rd,lease_id=str(uuid.uuid4()))
    adapter=SimpleNamespace(**{**vars(s.adapter),'spec':spec})
    candidate={k:getattr(spec,k) for k in ('game','runtime_id','generation','lease_id')}
    resume.prepare_start(s.g,ledger['pending'])
    with mock.patch('docich.agent.fence.read_canonical',return_value={'candidate':candidate}):
        resume.claim(adapter,time.monotonic()+30,None)
    receipt=read_record(rd/resume.RECEIPT_FILE)
    assert receipt['status']=='claimed' and receipt['request_id']==s.claim['request_id']
    assert (rd/'states/hanjuku-hero.state').read_bytes()==s.source.read_bytes()


def test_derived_retry_repairs_interrupted_ledger_write(failed_derived_attempt,monkeypatch):
    s=failed_derived_attempt
    real_save=s.manager.save
    monkeypatch.setattr(s.manager,'save',mock.Mock(side_effect=OSError('interrupted')))
    with pytest.raises((OSError,s.op.CancelRefused)):s.op.queue_failed_start(s.g,tmux=s.tmux,now=lambda:1000000.0)
    monkeypatch.setattr(s.manager,'save',real_save)
    result=s.op.queue_failed_start(s.g,tmux=s.tmux,now=lambda:1000000.0)
    assert result['request_id']==s.manager.load(1000000.0)['queued_manual']['request_id']


@pytest.mark.parametrize('bad',['gameplay','no-rollback','active','checkpoint','newer','queue','owner','provenance','tmux','schema'])
def test_derived_retry_rejects_unsafe_or_conflicting_state(failed_derived_attempt,bad):
    s=failed_derived_attempt;base=Path(s.g.state_dir)
    if bad=='gameplay':(s.retry_spec.runtime_dir/'hanjuku_run.json').touch()
    elif bad=='no-rollback':
        row=s.store.receipts.load(s.retry_first['request_id']);row['status']='failed';s.store.receipts.save(row)
    elif bad=='active':
        from docich.game_switch import runtime_state_dict
        state,_=s.store.canonical.load()
        valid=RuntimeSpec.from_runtime(base,vars(s.retry_spec))
        state.update(phase='ready',active=runtime_state_dict(valid,'2026-10-12T00:00:00Z'))
        s.store.canonical.save(state)
    elif bad=='checkpoint':(base/'hanjuku-resume'/(s.claim['request_id']+'.state')).write_bytes(state_bytes(b'changed'))
    elif bad=='newer':
        r=base/'runtimes/g702-babe1234';r.mkdir();(r/'hanjuku_run.json').touch()
    elif bad=='queue':
        state=s.manager.load(1000000.0);state['queued_manual']=dict(corner='weather',request_id=str(uuid.uuid4()),selected_at=1000000.0);s.manager.save(state)
    elif bad=='owner':atomic_write_json(base/'retro_corner.json',{'status':'active','game':'bastet'})
    elif bad in {'provenance','schema'}:
        claim=dict(s.retry_claim,**({'rom_sha256':'a'*64} if bad=='provenance' else {'schema':True}))
        atomic_write_json(base/'hanjuku-resume'/resume.REQUEST_FILE,claim)
        atomic_write_json(s.retry_spec.runtime_dir/resume.RECEIPT_FILE,claim)
    elif bad=='tmux':s.tmux.window_target_exists=lambda *args,**kwargs:True
    before=snapshot_recovery(s)
    with pytest.raises(s.op.CancelRefused):s.op.queue_failed_start(s.g,tmux=s.tmux,now=lambda:1000000.0)
    assert snapshot_recovery(s)==before


@pytest.mark.parametrize('failed_write', ['owner','ledger'])
@pytest.mark.parametrize('released', [False,True])
def test_automatic_recovery_repairs_partial_publication(recovery_setup,failed_write,released):
    s=recovery_setup;automatic_recovery(s,released=released)
    base=Path(s.g.state_dir)
    def fail(path,value):
        if Path(path)==(base/'retro_corner.json' if failed_write=='owner' else s.manager.path):
            raise OSError('interrupted')
        return atomic_write_json(path,value)
    target='docich.hanjuku_resume_recovery.atomic_write_json' if failed_write=='owner' else 'docich.corner_rotation.atomic_write_json'
    with mock.patch(target,side_effect=fail):
        with pytest.raises((OSError,s.op.CancelRefused)):recover_startup(s,apply=True,expected=s.fingerprint)
    assert recover_startup(s,apply=True,expected=s.fingerprint)['status']=='recovery-queued'
    assert read_record(base/'hanjuku-resume'/resume.REQUEST_FILE)['startup_recovery']['original']==s.claim


def test_native_priest_stall_restores_without_inventing_chapter_or_old_payment(setup):
    from docich.hanjuku_pixels import read_png
    from docich.hanjuku_bot import decide
    from docich.hanjuku_screen import parse, resume_egg_ritual_panel
    frame = read_png(Path(__file__).parent / 'fixtures/hanjuku/g725-priest-ritual-preparation.png')
    assert resume_egg_ritual_panel(parse(frame))
    restore_test(setup, chapter=None, native_frame=frame)
    receipt = read_record(setup.spec.runtime_dir / resume.RECEIPT_FILE)
    assert receipt['status'] == 'verified' and receipt['chapter'] is None
    policy = read_record(setup.spec.runtime_dir / 'hanjuku_bot.json')['policy']
    assert policy == {'name': {'done': True}, 'resume_chapter_pending': True,
                      'resume_egg_ritual': {'readings': 0, 'month': '1-5'}}
    actions, state = decide(frame, {'policy': policy})
    assert actions == [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}]
    assert state['policy'].get('chapter') is None
    assert not state['policy'].get('month_sub') and not state['policy'].get('egg_uses')
    assert not state['policy'].get('orders')


@pytest.mark.parametrize('gold', [907, 1000])
def test_gift_menu_checkpoint_restores_existing_policy_without_guessing_chapter(setup,gold):
    from docich.hanjuku_bot import decide
    from docich.hanjuku_screen import parse,resume_bijin_gift_panel
    from test_hanjuku_bijin_house_policy import gifts
    frame=gifts(gold,selected=0)
    assert resume_bijin_gift_panel(parse(frame))
    restore_test(setup,chapter=None,native_frame=frame)
    receipt=read_record(setup.spec.runtime_dir/resume.RECEIPT_FILE)
    assert receipt['status']=='verified' and receipt['chapter'] is None
    policy=read_record(setup.spec.runtime_dir/'hanjuku_bot.json')['policy']
    assert policy=={'name':{'done':True},'resume_chapter_pending':True}
    actions,state=decide(frame,{'policy':policy})
    assert state['policy']['bijin_gift_hold'] is True
    assert state['policy'].get('chapter') is None
    assert actions==([] if gold<1000 else [{'type':'pad','buttons':['down'],'hold_ms':100}])
    if gold<1000:
        from docich.hanjuku.flows.prompts import BIJIN_GIFT_HOLD_MAX
        for _ in range(BIJIN_GIFT_HOLD_MAX-1):
            actions,state=decide(frame,state)
            assert actions==[]
        actions,state=decide(frame,state)
        assert actions==[{'type':'pad','buttons':['a'],'hold_ms':100}]


@pytest.mark.parametrize('gold,house,prompt',[(None,'いえ500G',True),(907,'コート500G',False)])
def test_gift_checkpoint_requires_full_native_menu_and_readable_header(gold,house,prompt):
    from docich.hanjuku_screen import parse,resume_bijin_gift_panel
    from test_hanjuku_bijin_house_policy import gifts
    assert not resume_bijin_gift_panel(parse(gifts(gold,house=house,prompt=prompt)))


def test_castle_event_dialogue_checkpoint_restores_without_guessing_chapter(setup):
    # g1059 07:42 stalled on 「メリ〜 クリスマ〜ス!」; g1063/g1065 then rolled
    # back because no chapter or known panel ever appeared on the restore.
    from docich.hanjuku.os_layer.pixels import read_png
    from docich.hanjuku.os_layer.screen import parse
    from docich.hanjuku.orchestrator import decide
    frame = read_png(Path(__file__).parent / 'fixtures/hanjuku/g1059-christmas-event-dialogue.png')
    screen = parse(frame)
    assert screen.kind == 'text' and screen.header['chapter'] is None
    restore_test(setup, chapter=None, native_frame=frame)
    receipt = read_record(setup.spec.runtime_dir / resume.RECEIPT_FILE)
    assert receipt['status'] == 'verified' and receipt['chapter'] is None
    policy = read_record(setup.spec.runtime_dir / 'hanjuku_bot.json')['policy']
    assert policy == {'name': {'done': True}, 'resume_chapter_pending': True}
    actions, state = decide(frame, {'policy': policy})
    assert actions == [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}]
    assert state['policy'].get('chapter') is None


def test_headerless_dialogue_is_not_a_restorable_foreground():
    from docich.hanjuku_screen import parse
    from docich import hanjuku_resume
    from test_hanjuku_chart_bot import Canvas
    c = Canvas(); c.text(16, 175, 'メリー クリスマース!')
    screen = parse(c.frame())
    assert screen.kind == 'text'
    assert not hanjuku_resume._castle_dialogue(screen)


def test_chapterless_restore_reaching_month_menu_still_sends_input():
    # g1067 08:40: after a chapterless restore the house scan read
    # mem['chapter'] at the monthly menu; every observation raised KeyError
    # ("invalid observation") and the bot sent nothing.
    from docich.hanjuku.os_layer.pixels import read_png
    from docich.hanjuku.orchestrator import decide
    frame = read_png(Path(__file__).parent / 'fixtures/hanjuku/g1067-chapterless-month-menu.png').resized()
    policy = {'name': {'done': True}, 'resume_chapter_pending': True}
    actions, state = decide(frame, {'policy': policy})
    assert actions
    assert state['policy'].get('chapter') is None
    assert not state['policy'].get('house')


def test_native_namaste_stall_can_restore_only_bounded_foreground(setup):
    from docich.hanjuku.os_layer.pixels import read_png
    from docich.hanjuku.os_layer.screen import parse, resume_egg_ritual_panel
    from docich.hanjuku.orchestrator import decide
    frame = read_png(Path(__file__).parent / 'fixtures/hanjuku/g731-priest-namaste.png')
    assert frame.digest() == '7b0af6558effa6e61a270c3de4bd1bff7815d00e41fc23a44452c2159a37817a'
    assert resume_egg_ritual_panel(parse(frame))
    restore_test(setup, chapter=None, native_frame=frame)
    receipt = read_record(setup.spec.runtime_dir / resume.RECEIPT_FILE)
    assert receipt['status'] == 'verified' and receipt['chapter'] is None
    policy = read_record(setup.spec.runtime_dir / 'hanjuku_bot.json')['policy']
    assert policy == {'name': {'done': True}, 'resume_chapter_pending': True,
                      'resume_egg_ritual': {'readings': 0, 'month': '1-5'}}
    actions, state = decide(frame, {'policy': policy})
    assert actions == [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}]
    assert not state['policy'].get('egg_uses') and not state['policy'].get('month_sub')


def test_verified_native_restore_context_can_detect_subsequent_title_return(setup):
    from docich import hanjuku_run
    from docich.hanjuku.os_layer.pixels import read_png
    from test_hanjuku_script import title_frame
    frame = read_png(Path(__file__).parent / 'fixtures/hanjuku/g731-priest-namaste.png')
    restore_test(setup, chapter=None, native_frame=frame)
    receipt = read_record(setup.spec.runtime_dir / resume.RECEIPT_FILE)
    assert receipt['gameplay_context'] == 'verified_checkpoint_foreground'
    identity = hanjuku_run.runtime_identity(setup.spec)
    wall = receipt['verified_at'] + 1
    run = hanjuku_run.observe(setup.spec.runtime_dir, identity, frame, now=0, wall=wall)
    assert run['checkpoint_started'] and not run['name_entered'] and not run['gameplay_seen']
    battle_frame = read_png(Path(__file__).parent / 'fixtures/hanjuku/g713-zeus-cumin-healthy.png')
    run = hanjuku_run.observe(setup.spec.runtime_dir, identity, battle_frame, now=1, wall=wall + 1)
    assert run['gameplay_seen'] and not run['name_entered']
    for now in (2, 3, 4):
        run = hanjuku_run.observe(setup.spec.runtime_dir, identity, title_frame(), now=now, wall=wall + now)
    assert run['terminal_reason'] == 'game_over'
    assert hanjuku_run.terminal(setup.spec.runtime_dir, identity) == run


def test_native_priest_result_boundary_restores_bounded_dialogue_without_old_transaction(setup):
    from docich.hanjuku_pixels import read_png
    from docich.hanjuku_bot import decide
    frame = read_png(Path(__file__).parent / 'fixtures/hanjuku/g739-priest-recovery-dialogue.png')
    restore_test(setup, chapter=None, native_frame=frame)
    receipt = read_record(setup.spec.runtime_dir / resume.RECEIPT_FILE)
    assert receipt['status']=='verified' and receipt['chapter'] is None
    assert receipt['gameplay_context']=='verified_checkpoint_foreground'
    policy=read_record(setup.spec.runtime_dir / 'hanjuku_bot.json')['policy']
    assert 'egg_uses' not in policy and 'month_sub' not in policy
    assert decide(frame,{'policy':policy})[0] == [{'type':'pad','buttons':['a'],'hold_ms':100}]


@pytest.mark.parametrize('uid', [8819, 8820])
def test_disabled_battle_parent_checkpoint_seeds_only_finite_reobservation(setup, uid):
    from docich.hanjuku_pixels import read_png
    frame=read_png(Path(__file__).parent/f'fixtures/hanjuku/g741-orphan-menu-{uid}.png')
    commands,writes=restore_test(setup,chapter=None,native_frame=frame)
    receipt=read_record(setup.spec.runtime_dir/resume.RECEIPT_FILE)
    assert receipt['status']=='verified' and receipt['chapter'] is None
    assert receipt['frame_sha256']==frame.resized().digest()
    assert commands.count('LOAD_STATE_SLOT 0')==1
    assert writes[0]==b'boot state' and all(b==b'serialized core' for b in writes[1:])
    assert read_record(setup.spec.runtime_dir/'hanjuku_bot.json')=={'policy':{
        'name':{'done':True},'resume_chapter_pending':True,
        'resume_battle_menu':{'readings':0}}}
    assert setup.source.read_bytes()==compressed(state_bytes())
