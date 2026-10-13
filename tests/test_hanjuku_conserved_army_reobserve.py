"""Original g745 conservation -> unread army -> egg A; no inferred egg use.

Post-wait panels below are counterfactual replay, not native runtime acceptance.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
EVIDENCE = json.loads((ROOT / 'g745-coco-semillon-evidence.json').read_text())


def native(uid):
    e = next(e for e in EVIDENCE['evidence']
             if e['decision']['decision_id'].endswith(':' + str(uid)))
    frame = read_png(ROOT / e['fixture'])
    assert frame.digest() == e['rgb_sha256'] == e['decision']['frame_sha256']
    return parse(frame)


def conserved():
    mem = {'chapter': 2, 'tick': 10, '_run_identity': deepcopy(EVIDENCE['identity']),
           'battle': {'ally': 'ココット', 'enemy': 'セミヨン', 'side': 'defense',
                      'castle': 'アルマムーン', 'ally_hp': 24, 'enemy_hp': 36,
                      'start_ally_hp': 24, 'start_enemy_hp': 36,
                      'cards_used': [], 'cards_selected': [], 'planned_cards': [],
                      'card_evidence_version': 1}}
    # 1435's walking enemy fragments are unread (formerly misread as two).
    # Use the later complete1445 panel for this isolated budget replay. This
    # is counterfactual chronology, not the original path into menu1436.
    p._card_battle_reading(native(1445), mem['battle'], mem)
    assert p.battle_menu_step(native(1436), mem) == [p.pad('b')]
    return mem


def test_original_opening_fragments_do_not_recreate_the_old_conservation():
    mem = conserved()
    p._card_battle_reading(native(1435), mem['battle'], mem)
    assert mem['battle']['enemy_soldiers'] is None
    assert not p._hold_melee_egg(mem, mem['battle'], {'たまごをつかう'})


def test_real_conservation_and_later_egg_a_have_exact_frame_receipts():
    for uid, button in ((1436, 'b'), (1448, 'a')):
        e = next(e for e in EVIDENCE['evidence']
                 if e['decision']['decision_id'].endswith(':' + str(uid)))
        native(uid)
        assert any(r['decision_id'] == e['decision']['decision_id']
                   and r['buttons'] == [button]
                   and r['decision_frame_sha256'] == r['frame_sha256'] == e['rgb_sha256']
                   for r in e['input_receipts'])
    assert native(1446).battle.ally_hp == 24
    assert native(1446).field_soldiers == (4, None)


def test_unread_current_army_is_reobserved_without_restoring_old_counts():
    mem = conserved()
    assert p.battle_step(native(1446), mem) == [{'type': 'wait', 'ms': 50}]
    cur = mem['battle']
    assert cur['ally_soldiers'] == 4 and cur['enemy_soldiers'] is None
    assert not cur['survival']['egg_attempted']
    assert mem['_records'][-1]['observed_metric']['army_advantage'] == 'unconfirmed'
    # Counterfactual next readable panel: reuse a real earlier frame, explicitly
    # not a naturally captured post-change sequence. Only new reading permits B.
    p.battle_step(native(1445), mem)
    assert p.battle_menu_step(native(1448), mem) == [p.pad('b')]
    assert not cur['survival']['egg_attempted']


def test_repeated_unread_army_still_reaches_rescue_after_two_observations():
    mem = conserved()
    for _ in range(2):
        assert p.battle_step(native(1446), mem) == [{'type': 'wait', 'ms': 50}]
        mem = json.loads(json.dumps(mem))
    assert p.battle_step(native(1446), mem) == [p.pad('b')]
    assert p.battle_menu_step(native(1448), mem) == [p.pad('a')]
    assert mem['battle']['survival']['egg_attempted']


def test_readable_unread_oscillation_does_not_refill_the_battle_budget():
    mem = conserved()
    for _ in range(2):
        assert p.battle_step(native(1446), mem) == [{'type': 'wait', 'ms': 50}]
        p.battle_step(native(1445), mem)
    assert p.battle_step(native(1446), mem) == [p.pad('b')]


@pytest.mark.parametrize('change', [
    {'ally_hp': 12}, {'ally_hp': 0}, {'ally_hp': None}, {'ally_hp': True},
    {'enemy_hp': 0}, {'enemy_hp': 85}, {'ally': '未確認'}, {'enemy': '未確認'},
    {'egg_battle': True}, {'card_summon_observed': True}, {'card_flow': {'stage': 'list'}},
    {'card_context_unclassified': True}, {'card_hp_unread': True},
    {'command_open_pending': {'owner': 'survival'}}, {'card_damage_rescue_needed': True},
    {'card_soldiers_current': False}, {'ally_soldiers': 0, 'enemy_soldiers': 6},
    {'ally_soldiers': True}, {'enemy_soldiers': -1}, {'ally_soldiers': 7},
])
def test_danger_unknown_context_or_tactic_never_gets_reobservation(change):
    mem = conserved(); cur = mem['battle']
    p._card_battle_reading(native(1446), cur, mem)
    cur.update(change)
    assert not p._conserved_army_recheck(mem, cur)


@pytest.mark.parametrize('claim', [True, {}, {'observations': True},
                                  {'observations': -1}, {'observations': 2}])
def test_malformed_or_exhausted_saved_claim_never_refills(claim):
    mem = conserved(); cur = mem['battle']
    p._card_battle_reading(native(1446), cur, mem)
    cur['conserved_army_recheck'] = claim
    assert not p._conserved_army_recheck(mem, cur)


def test_foreign_identity_saved_claim_and_owner_zero_are_not_released():
    mem = conserved(); cur = mem['battle']
    p._card_battle_reading(native(1446), cur, mem)
    assert p._conserved_army_recheck(mem, cur)
    mem['_run_identity']['lease_id'] = 'other'
    assert not p._conserved_army_recheck(mem, cur)
    mem['_run_identity'] = deepcopy(EVIDENCE['identity'])
    assert not p._conserved_army_recheck(mem, cur)  # no budget revival after foreign scope
    mem = conserved(); mem['egg_uses'] = {'ココット': 0}
    p._card_battle_reading(native(1446), mem['battle'], mem)
    assert not p._conserved_army_recheck(mem, mem['battle'])
    assert p._own_egg_empty(mem, mem['battle'])
