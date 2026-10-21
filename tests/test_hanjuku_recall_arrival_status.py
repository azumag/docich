"""Actual return dispatch, followed by counterfactual current-status reads."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from docich import hanjuku_policy as p
from docich.hanjuku.flows import camp_recheck as c
from docich.hanjuku_screen import parse
from docich.hanjuku_pixels import read_png
from test_hanjuku_retreat_recheck import MAP, FRAME, main_menu
from test_hanjuku_house import roster, status

ROOT=Path(__file__).parent/'fixtures/hanjuku'
EVIDENCE=json.loads((ROOT/'g749-cinnamon-recall-evidence.json').read_text())
ACTOR='シナモン'


def memory():
    proof=deepcopy(EVIDENCE['dispatch_verification'])
    return {'chapter':2,'tick':29700,'_run_identity':deepcopy(proof['identity']),
            '_records':[],'recall_verification':proof,
            'retreat_rechecks':{ACTOR:{'identity':deepcopy(proof['identity']), 'chapter':2,
                'tick':29529,'attempts':3,'retry_after':29600,'status':'needs_observation'}},
            'sorties':{'old':{'general':ACTOR,'status':'arrived','target':'ドミノーラ'}}}


def start(mem):
    assert p.camp_recall_step(MAP,mem,FRAME)==[p.pad('x')]
    assert mem['recall']['arrival_check_only'] is True
    assert p.camp_recall_step(main_menu(),mem,FRAME)==[p.pad('a')]
    assert p.camp_recall_step(parse(roster(names=('どうし',ACTOR),selected=1)),mem,FRAME)==[p.pad('a')]
    assert mem['recall']['stage']=='recheck_roster_read'


def test_original_picker_and_sender_receipts_do_not_prove_arrival():
    f=read_png(ROOT/EVIDENCE['fixture'])
    assert f.digest()==EVIDENCE['rgb_sha256']==EVIDENCE['decision']['frame_sha256']
    assert parse(f).kind=='world_map'
    assert len(EVIDENCE['input_receipts'])==2
    for r in EVIDENCE['input_receipts']:
        assert r['buttons']==['a']
        assert r['decision_id']==EVIDENCE['decision']['decision_id']
        assert r['decision_frame_sha256']==f.digest()
    assert EVIDENCE['dispatch_verification']['status']=='arrival_unconfirmed'


def test_sent_return_can_read_current_castle_status_after_old_camp_budget_exhausted():
    mem=memory();before=deepcopy(mem['sorties']);start(mem)
    assert p.camp_recall_step(parse(status(ACTOR,main=True,castle=True,hp=16)),mem,FRAME)==[p.pad('b')]
    assert ACTOR not in (mem.get('retreat_rechecks') or {})
    assert not mem.get('recall')
    assert mem['sorties']==before and not mem.get('garrison')
    assert mem['recall_verification']['status']=='in_castle_observed'
    row=mem['_records'][-1]
    assert row['resulting_event']=='in_castle_destination_unconfirmed'
    assert row['observed_metric']['destination_confirmed'] is False


@pytest.mark.parametrize('location',['moving','unknown'])
def test_field_or_unknown_status_does_not_reissue_return_or_claim_success(location):
    mem=memory();start(mem)
    s=parse(status(ACTOR,main=True,castle=False,hp=16))
    if location=='unknown':s.text=s.text.replace('いどうしています','')
    assert p.camp_recall_step(s,mem,FRAME)==[p.pad('b')]
    assert mem['retreat_rechecks'][ACTOR]['status']=='arrival_unconfirmed'
    assert mem['retreat_rechecks'][ACTOR]['attempts']==3
    assert mem['retreat_rechecks'][ACTOR]['arrival_reads']==1
    assert not mem.get('recall') and not mem.get('garrison')
    assert mem['recall_verification']['status']=='arrival_unconfirmed'
    assert not any(r['decision']=='camp_recall_requested' for r in mem['_records'])


@pytest.mark.parametrize('change',['foreign_run','foreign_chapter','actor','not_sent','trace','hash','future'])
def test_foreign_or_unverified_dispatch_does_not_get_extra_read_budget(change,monkeypatch):
    mem=memory();v=mem['recall_verification']
    if change=='foreign_run':v['identity']['lease_id']='foreign'
    elif change=='foreign_chapter':v['chapter']=1
    elif change=='actor':v['general']='ゼウス'
    elif change=='not_sent':v['input_sent']=False
    elif change=='trace':v['request_trace']['runtime_id']='other'
    elif change=='hash':v['request_trace']['frame_sha256']='bad'
    else:v['requested_tick']=mem['tick']+1
    monkeypatch.setattr(p,'own_camps',lambda _:[])
    assert p.camp_recall_step(MAP,mem,FRAME) is None
    assert not mem.get('recall')


def test_current_other_general_or_field_layout_cannot_clear_named_return():
    for name,main in [('ゼウス',True),(ACTOR,False)]:
        mem=memory();start(mem);s=parse(status(name,main=main,castle=True,hp=16))
        for _ in range(3):p.camp_recall_step(s,mem,FRAME)
        assert ACTOR in mem['retreat_rechecks']
        assert mem['recall_verification']['status']=='arrival_unconfirmed'


def test_each_actor_keeps_its_dispatch_when_the_global_latest_return_changes():
    mem=memory();start(mem)
    p.camp_recall_step(parse(status(ACTOR,main=True,castle=False,hp=16)),mem,FRAME)
    mem['recall_verification']['general']='ゼウス'
    mem['tick']+=c.ARRIVAL_RETRY_TICKS
    start(mem)
    p.camp_recall_step(parse(status(ACTOR,main=True,castle=True,hp=16)),mem,FRAME)
    assert ACTOR not in (mem.get('retreat_rechecks') or {})
    assert mem['recall_verification']['general']=='ゼウス'
    assert mem['recall_verification']['status']=='arrival_unconfirmed'


def test_hp_zero_invalidates_old_return_without_classifying_death_or_arrival():
    mem=memory();start(mem)
    p.camp_recall_step(parse(status(ACTOR,main=True,castle=True,hp=0)),mem,FRAME)
    assert ACTOR not in (mem.get('retreat_rechecks') or {})
    assert not any(r['decision']=='recall_in_castle_observed' for r in mem['_records'])
    assert mem['recall_verification']['status']=='arrival_unconfirmed'


def test_new_castle_status_decision_keeps_a_matching_snapshot(tmp_path):
    from test_hanjuku_camp_receipts import command_bot
    frame=status(ACTOR,main=True,castle=True,hp=16)
    state={'step':7,'policy':{},'screen_kind':'text'}
    records=[{'decision':'recall_in_castle_observed','general':ACTOR,
              'resulting_event':'in_castle_destination_unconfirmed'}]
    command_bot().persist(tmp_path,state,records,{'hanjuku':EVIDENCE['dispatch_verification']['identity']},
                          actions=[p.pad('b')],frame_sha256=frame.digest(),frame=frame)
    saved=read_png(tmp_path/'hanjuku_frames/decision-007.png')
    assert saved.digest()==frame.digest()


def test_arrival_observation_has_its_own_finite_budget_across_saved_state(monkeypatch):
    mem=memory();monkeypatch.setattr(p,'own_camps',lambda _:[])
    for i in range(3):
        start(mem)
        p.camp_recall_step(parse(status(ACTOR,main=True,castle=False,hp=16)),mem,FRAME)
        assert mem['retreat_rechecks'][ACTOR]['arrival_reads']==i+1
        mem=json.loads(json.dumps(mem));mem['tick']+=c.ARRIVAL_RETRY_TICKS
    assert p.camp_recall_step(MAP,mem,FRAME) is None
    assert not mem.get('recall')


@pytest.mark.parametrize('attempts', [0, 1, 2])
@pytest.mark.parametrize('reads', [3, 4, -1, True])
def test_sent_return_cannot_fall_back_to_camp_search_after_arrival_budget(attempts, reads, monkeypatch):
    mem = memory(); row = mem['retreat_rechecks'][ACTOR]
    row.update(attempts=attempts, arrival_reads=reads)
    before = deepcopy(row)
    monkeypatch.setattr(p, 'own_camps', lambda _: [])
    for _ in range(5):
        assert p.camp_recall_step(MAP, mem, FRAME) is None
        assert not mem.get('recall')
        assert mem['retreat_rechecks'][ACTOR] == before
        mem = json.loads(json.dumps(mem)); mem['tick'] += c.ARRIVAL_RETRY_TICKS
    assert not any(r['decision'] == 'camp_recall_requested' for r in mem['_records'])


def test_sent_actor_does_not_block_another_actors_unsent_recheck(monkeypatch):
    mem = memory(); row = mem['retreat_rechecks'][ACTOR]
    row.update(attempts=1, arrival_reads=3)
    mem['retreat_rechecks']['ゼウス'] = {
        'identity': deepcopy(row['identity']), 'chapter': 2, 'tick': row['tick'],
        'retry_after': row['retry_after'], 'attempts': 0, 'status': 'needs_observation'}
    monkeypatch.setattr(p, 'own_camps', lambda _: [])
    assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('x')]
    assert mem['recall']['general'] == 'ゼウス'
    assert mem['retreat_rechecks'][ACTOR]['attempts'] == 1


def test_anonymous_view_cannot_restore_a_sent_actors_return_budget():
    from test_hanjuku_chart_bot import camp_menu
    mem = memory(); row = mem['retreat_rechecks'][ACTOR]
    row.update(attempts=1, arrival_reads=3)
    assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('a')]
    assert not mem['recall'].get('retreat_recheck')
    assert p.camp_recall_step(camp_menu(1), mem, FRAME) == [p.pad('a')]
    assert p.camp_recall_step(parse(status(ACTOR, main=False, hp=16)), mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall')
    assert row['attempts'] == 1 and row['arrival_reads'] == 3


def test_same_observed_camp_stays_suppressed_after_sent_arrival_budget_is_spent():
    from test_hanjuku_camp_proof_binding import opened
    mem = memory(); row = mem['retreat_rechecks'][ACTOR]
    row.update(attempts=1, arrival_reads=3)
    proof = deepcopy(opened()['recall']['camp_observation'])
    proof.update(identity=deepcopy(row['identity']), chapter=2)
    row['camp_observation'] = proof
    before = deepcopy(row)
    for _ in range(5):
        assert p.camp_recall_step(MAP, mem, FRAME) is None
        assert not mem.get('recall') and mem['retreat_rechecks'][ACTOR] == before
        mem = json.loads(json.dumps(mem)); mem['tick'] += c.ARRIVAL_RETRY_TICKS
