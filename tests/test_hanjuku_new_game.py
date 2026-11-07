"""Explicit fresh starts preserve prior saves and use the existing coordinator."""
import json
import shutil
import subprocess
import time
from unittest import mock

import pytest

from docich import hanjuku_resume as resume, webui
from docich.adapters.base import AdapterError
from docich.corner_catalog import Corner
from docich.corner_rotation import CornerRotationManager, RotationError
from test_corner_rotation import Adapter, Executor
from test_hanjuku_resume import setup, stage


def new_queue(s):
    return {k: v for k, v in dict(s.queue, new_game=True, source='manual').items()
            if k != 'resume_latest'}


@pytest.mark.parametrize('damaged_save', [False, True])
def test_new_game_claim_and_restore_never_load_or_modify_previous_saves(setup, damaged_save):
    s = setup
    if damaged_save:
        s.source.write_bytes(b'synthetic unreadable save')
    saves = s.source.parent.parent / 'saves'
    saves.mkdir()
    (saves / 'hanjuku-hero.srm').write_bytes(b'synthetic SRAM')
    (s.source.parent.parent / 'hanjuku_bot.json').write_text('{"chapter": 6}')
    paths = [p for p in s.source.parents[2].rglob('*') if p.is_file()]
    before = {p: p.read_bytes() for p in paths}
    s.queue = new_queue(s)
    with mock.patch.object(resume, '_latest_boundary', side_effect=AssertionError('old save inspected')):
        stage(s)
        resume.restore(s.adapter, time.monotonic() + 30, None)
    receipt = json.loads((s.spec.runtime_dir / resume.RECEIPT_FILE).read_text())
    assert receipt['status'] == 'verified'
    assert receipt['start_mode'] == 'fresh' and receipt['new_game'] is True
    assert receipt['source_boundary'] is None
    assert not any(s.spec.runtime_dir.joinpath('states').iterdir())
    assert not (s.spec.runtime_dir / 'hanjuku_bot.json').exists()
    s.adapter._verify_manual_content.assert_not_called()
    assert {p: p.read_bytes() for p in paths} == before


def test_new_game_cannot_replace_pending_resume_or_relabel_same_request(setup):
    s = setup
    resume.prepare(s.g, s.game, s.queue)
    before = (s.g.state_dir / 'hanjuku-resume' / resume.REQUEST_FILE).read_bytes()
    with pytest.raises(AdapterError, match='intent changed'):
        resume.prepare(s.g, s.game, new_queue(s))
    queued = dict(new_queue(s), request_id='00000000-0000-4000-8000-000000000001')
    with pytest.raises(AdapterError, match='pending'):
        resume.prepare(s.g, s.game, queued)
    assert (s.g.state_dir / 'hanjuku-resume' / resume.REQUEST_FILE).read_bytes() == before


@pytest.mark.parametrize('changes', [{'resume_latest': True}, {'source': 'automatic'}, {'new_game': 'true'}])
def test_new_game_requires_exclusive_manual_intent(setup, changes):
    s = setup
    with pytest.raises(AdapterError):
        resume.prepare(s.g, s.game, dict(new_queue(s), **changes))


def test_new_game_intent_must_survive_publication_and_claim(setup):
    s = setup
    s.queue = new_queue(s)
    resume.prepare(s.g, s.game, s.queue)
    with pytest.raises(AdapterError, match='identity'):
        resume.publish(s.g, dict(s.queue, new_game=False))
    stage(s, pending=dict(s.queue, new_game=False))
    assert not (s.spec.runtime_dir / resume.RECEIPT_FILE).exists()
    stage(s)
    assert json.loads((s.spec.runtime_dir / resume.RECEIPT_FILE).read_text())['new_game'] is True


def test_new_game_queue_survives_restart_and_refuses_other_start_modes(setup):
    s = setup
    config = s.g.repo_root / 'rotation.toml'
    config.write_text('[corner_rotation]\nenabled=true\n')
    from docich.config import load_global
    g = load_global(s.g.repo_root, config)
    executor = Executor()
    def manager():
        return CornerRotationManager(g, clock=lambda: 1000000.0, seed='new-game',
            catalog=[Corner('hanjuku-hero', 'game', 'hanjuku-hero')],
            adapter_factory=Adapter, executor=executor)
    m = manager()
    queued = m.queue_manual('hanjuku-hero', new_game=True)
    inbox = m._manual_queue_path.read_bytes()
    assert manager().queue_manual('hanjuku-hero', new_game=True) == queued
    for kwargs in ({}, {'resume_latest': True}):
        with pytest.raises(RotationError) as caught:
            manager().queue_manual('hanjuku-hero', **kwargs)
        assert caught.value.reason_code == 'manual_queue_conflict'
    assert m._manual_queue_path.read_bytes() == inbox
    assert executor.calls == []
    manager().tick()
    request = executor.calls[0]
    assert request['request_id'] == queued['request_id']
    assert request['new_game'] is True and request['source'] == 'manual'
    resume.prepare_start(g, request)
    receipt = json.loads((g.state_dir / 'hanjuku-resume' / resume.REQUEST_FILE).read_text())
    assert receipt['start_mode'] == 'fresh' and receipt['status'] == 'queued'


@pytest.mark.parametrize('game,kwargs', [
    ('nsnake', {'new_game': True}),
    ('hanjuku-hero', {'new_game': True, 'resume_latest': True}),
    ('hanjuku-hero', {'new_game': True, 'trigger_at': 2000000}),
    ('hanjuku-hero', {'new_game': 1}),
])
def test_invalid_new_game_queue_is_rejected_without_writes(setup, game, kwargs):
    m = CornerRotationManager(setup.g, catalog=[], adapter_factory=Adapter, executor=Executor())
    with pytest.raises(RotationError) as caught:
        m.queue_manual(game, **kwargs)
    assert caught.value.reason_code == 'invalid_value'
    assert not m._manual_queue_path.exists()


@pytest.mark.skipif(shutil.which('node') is None, reason='Node required for WebUI JavaScript')
def test_new_game_button_confirmation_cancel_and_success():
    html = webui.INDEX_HTML
    handler = html.split('  const cNewGame=', 1)[1].split('  const cResume=', 1)[0]
    action = 'async function cornerAction' + html.split('async function cornerAction', 1)[1].split('async function loadStatus', 1)[0]
    script = '''
const button={}, msg={textContent:''}; let accepted=false, requests=[], prompts=[];
const document={getElementById:id=>id==='corners-new-game'?button:id==='corners-select'?{value:'hanjuku-hero'}:msg};
const CORNERS_DATA={catalog:[{id:'hanjuku-hero',game:'hanjuku-hero'}]};
const toast=()=>{}, loadCorners=async()=>{}, confirm=t=>{prompts.push(t);return accepted};
const api=async(path,opts)=>{const body=JSON.parse(opts.body);requests.push(body);return {ok:true,...body,corner:{game:'hanjuku-hero',target:'rotation'}}};
''' + action + '\nconst cNewGame=' + handler + '''
(async()=>{
  button.onclick(); await new Promise(r=>setImmediate(r));
  if(requests.length) throw Error('cancel dispatched');
  accepted=true; button.onclick(); await new Promise(r=>setImmediate(r));
  process.stdout.write(JSON.stringify({requests,prompts,message:msg.textContent}));
})();
'''
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10, check=True)
    out = json.loads(result.stdout)
    assert out['requests'] == [{'confirm': True, 'action': 'new-game', 'corner': 'hanjuku-hero'}]
    assert '削除・上書きせず' in out['prompts'][0]
    assert '第1話からの新規開始を予約' in out['message']
    assert '停止を要求' not in out['message']
