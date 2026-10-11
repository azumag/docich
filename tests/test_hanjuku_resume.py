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
from docich.retroarch_boundary import identity, read_record


def state_bytes(mem=b'serialized core'):
    return (b'RASTATE\x01' + b'MEM ' + struct.pack('<I', len(mem)) + mem
            + b'\0' * (-len(mem) % 8) + b'END ' + b'\0' * 4)


def compressed(data):
    return b'#RZIPv\x01#' + struct.pack('<IQ', 131072, len(data)) + struct.pack('<I', len(zlib.compress(data))) + zlib.compress(data)


@pytest.fixture
def setup(tmp_path):
    g = load_global(tmp_path)
    games = tmp_path / 'config/games'; games.mkdir(parents=True)
    rom = tmp_path / 'hanjuku.sfc'; rom.write_bytes(b'ROM')
    core = tmp_path / 'snes9x.so'; core.write_bytes(b'CORE')
    (games / 'hanjuku-hero.toml').write_text(f'[game]\nname="hanjuku-hero"\nadapter="retroarch"\n[retroarch]\nrom="{rom}"\ncore="{core}"\n')
    game = load_game(g, 'hanjuku-hero')
    source = Path(g.state_dir) / 'runtimes/g514-8937051b/states/hanjuku-hero.state'
    source.parent.mkdir(parents=True); source.write_bytes(compressed(state_bytes()))
    older = Path(g.state_dir) / 'runtimes/g478-a7d65415/states/hanjuku-hero.state'
    older.parent.mkdir(parents=True); older.write_bytes(state_bytes(b'older'))
    os.utime(older, ns=(100, 100)); os.utime(source, ns=(200, 200))
    queue = {'corner': 'hanjuku-hero', 'request_id': str(uuid.uuid4()), 'resume_latest': True}
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


def stage(s, *, pending=None, candidate=None):
    resume.prepare(s.g,s.game,s.queue)
    pending=pending if pending is not None else dict(s.queue,source='manual',phase='dispatched')
    atomic_write_json(Path(s.g.state_dir)/'corner_rotation.json', {'pending':pending})
    candidate=candidate if candidate is not None else {k:getattr(s.spec,k) for k in ('game','runtime_id','generation','lease_id')}
    with mock.patch('docich.agent.fence.read_canonical',return_value={'candidate':candidate}):
        resume.claim(s.adapter,time.monotonic()+3,None)


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
    (s.g.repo_root/'hanjuku.sfc').write_bytes(b'changed')
    with pytest.raises(AdapterError):stage(s)
    assert not (s.spec.runtime_dir/resume.RECEIPT_FILE).exists()


def restore_test(s, *, valid=True, ack=True, chapter=1):
    stage(s);commands=[];writes=[]
    path=s.spec.runtime_dir/'states/hanjuku-hero.state'
    atomic_write_json(s.spec.runtime_dir/'presentation.json', {'display':':99','width':256,'height':224,'window':'4'})
    if valid=='unchanged_future':
        os.utime(path,ns=(time.time_ns()+60_000_000_000,time.time_ns()+60_000_000_000))
    def send(command,**kwargs):
        commands.append(command)
        if command=='GET_STATUS':return ('GET_STATUS PLAYING Snes9x,hanjuku' if 'PAUSE_TOGGLE' in commands else 'GET_STATUS PAUSED Snes9x,hanjuku')
        if command=='LOAD_STATE_SLOT 0':return 'LOAD_STATE_SLOT 0' if ack else None
        if command=='SAVE_STATE':
            if valid=='unchanged_future':return None
            # First SAVE precedes asynchronous load completion. It must not be
            # accepted solely because the LOAD receipt/mtime exist.
            payload=b'boot state' if not valid or not writes else b'serialized core'
            path.write_bytes(state_bytes(payload));writes.append(payload)
        return None
    frame=SimpleNamespace(digest=lambda:'loaded-native-rgb',resized=lambda:frame)
    with mock.patch('docich.adapters.retroarch.send_ra_cmd',side_effect=send), \
         mock.patch('docich.xkit.XKit.screenshot'), \
         mock.patch('docich.hanjuku_pixels.read_png',return_value=frame), \
         mock.patch('docich.hanjuku_screen.parse',return_value=SimpleNamespace(header={'chapter':chapter})):
        resume.restore(s.adapter,time.monotonic()+(.3 if not valid or valid=='unchanged_future' or chapter is None else 3),None)
    return commands,writes


def test_native_roundtrip_must_match_before_ai_unpause_and_fresh_policy(setup):
    s=setup;commands,writes=restore_test(s)
    assert writes==[b'boot state',b'serialized core']
    assert commands.index('LOAD_STATE_SLOT 0')<commands.index('SAVE_STATE')<commands.index('FRAMEADVANCE')<commands.index('PAUSE_TOGGLE')
    receipt=read_record(s.spec.runtime_dir/resume.RECEIPT_FILE)
    assert receipt['status']=='verified' and receipt['chapter']==1
    assert read_record(s.spec.runtime_dir/'hanjuku_bot.json')=={'policy':{'chapter':1,'name':{'done':True}}}
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
