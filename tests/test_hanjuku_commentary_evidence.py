"""Speech must not turn intended inputs into completed game outcomes."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_commentary as commentary, hanjuku_narration as narration
from docich.game_switch import atomic_write_json


@pytest.mark.parametrize('record', [
    {'decision':'buy','card':'クースカン','qty':2},
    {'decision':'prompt','strategy_variant':'accept_duel'},
    {'decision':'battle_card','card':'クースカン','enemy':'クイーン','enemy_hp':60},
])
def test_intentions_do_not_claim_completion(record):
    text = commentary.compose(record)[1]
    assert commentary.evidence_kind(record) == 'plan'
    assert '予定' in text
    assert '買いました' not in text and '受けた。' not in text


def test_battle_win_does_not_claim_castle_ownership():
    record = {'decision':'battle_result','outcome':'win','ally':'どうし',
              'enemy':'クイーン','castle':'ボス','side':'attack'}
    assert commentary.compose(record)[1] == 'どうし将軍がクイーンに勝ちました。'
    owned = {'decision':'castle_owned_observed','castle':'ジョンリギ'}
    assert '確認しました' in commentary.compose(owned)[1]
    assert owned['decision'] in commentary.SPOKEN
    assert commentary.evidence_kind(owned) == 'observation'


@pytest.mark.parametrize('plan,age,same,expected', [
    (True,0,True,'enqueued'), (True,6,True,'skipped:stale'),
    (True,0,False,'skipped:plan_superseded'),
    (False,6,False,'enqueued'),
])
def test_plan_age_and_current_decision_checked_before_enqueue(tmp_path, monkeypatch, plan, age, same, expected):
    identity = {'game':'hanjuku-hero','runtime_id':'g1-test','generation':1,'lease_id':'lease-1'}
    monkeypatch.setattr(narration.time, 'time', lambda:1000)
    monkeypatch.setattr('docich.agent.fence.read_canonical', lambda _: {'active':identity})
    monkeypatch.setattr('docich.agent.fence.shared_section', lambda _, fn, **kw: fn())
    atomic_write_json(tmp_path / 'hanjuku_run.json', identity)
    atomic_write_json(tmp_path / 'hanjuku_bot.json', {'decision_trace':{
        **identity, 'decision_id':'current' if same else 'newer'}})
    item = {**identity,'seq':1,'key':'test','text':'切り札を使う予定です。','at':1000-age,
            'decision_id':'current','evidence_kind':'plan' if plan else 'observation'}
    calls=[]
    narration._deliver(SimpleNamespace(state_dir=tmp_path),tmp_path,item,'',
                       lambda *args, **kw:calls.append(kw),20)
    result=json.loads((tmp_path/'hanjuku_narration.jsonl').read_text())
    assert result['status'] == expected
    assert bool(calls) == (expected == 'enqueued')
    if calls:
        assert calls[0]['runtime_fence']['expires_at'] == item['at']+(5 if plan else 20)


def test_writer_tags_plan_and_links_actual_decision_identity(tmp_path):
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('commentary_writer_evidence', path)
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)
    identity = {'game':'hanjuku-hero','runtime_id':'g1-test','generation':1,'lease_id':'lease-1'}
    state = {'step':3,'screen_kind':'battle'}
    writer.persist(tmp_path,state,[{'decision':'battle_card','card':'クースカン',
                                  'enemy':'クイーン','enemy_hp':60}],
                   {'hanjuku':identity},actions=[],frame_sha256='a'*64)
    item = json.loads((tmp_path/'hanjuku_commentary.jsonl').read_text())
    assert item['evidence_kind'] == 'plan'
    assert item['decision_id'] == state['decision_trace']['decision_id']
    assert '予定' in item['text']
