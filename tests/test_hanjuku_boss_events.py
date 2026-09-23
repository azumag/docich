"""Measured boss entry and bounded retry; no ROM/screenshots are embedded."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_policy as policy
from docich.hanjuku_screen import Screen, Battle, classify_text

MESSAGE = 'ゼウスしょうぐんがボスじょうにせめこんだ!!'

def entry(text=MESSAGE):
    s = Screen(lines=[], hand=None, text=text)
    s.kind = classify_text(s)
    return s

def test_measured_boss_entry_establishes_matching_launched_context():
    mem = {'chapter': 1, 'launched': {'けっかい': {'general': 'ゼウス', 'step': '1-B1'}}}
    assert entry().kind == 'boss_attack_started'
    assert policy.message_step(entry(), mem) == [policy.pad('a')]
    assert mem['attack'] == {'general': 'ゼウス', 'castle': 'けっかい', 'side': 'attack', 'step': '1-B1',
                             'entry_evidence': 'measured_boss_entry'}
    assert mem['_records'][-1]['observed_metric']['message'] == MESSAGE

@pytest.mark.parametrize('chapter,general', [(2, 'ゼウス'), (None, 'ゼウス'), (1, 'どうし')])
def test_boss_message_without_matching_chapter_and_order_holds(chapter, general):
    mem = {'chapter': chapter, 'launched': {'けっかい': {'general': general, 'step': '1-B1'}}}
    assert policy.message_step(entry(), mem) == []
    assert 'attack' not in mem
    assert mem['_records'][-1]['decision'] == 'situation_held'

def test_partial_or_unknown_boss_message_is_not_calibrated():
    assert entry(MESSAGE[:-1]).kind == 'text'
    assert entry(MESSAGE.replace('ゼ', '�')).kind == 'text'

@pytest.mark.parametrize('ally_hp,enemy_hp', [(0, 70), (19, 0)])
def test_no_new_card_command_after_a_panel_reaches_zero(ally_hp, enemy_hp):
    mem = {'chapter': 1, 'attack': {'general': 'ゼウス', 'castle': 'けっかい', 'side': 'attack', 'step': '1-B1'}}
    initial = Screen([], None, '', battle=Battle('クイーン', 70, 'ゼウス', 74), kind='battle')
    policy.battle_step(initial, mem)
    policy.battle_step(initial, mem)
    actions = policy.battle_step(Screen([], None, '', battle=Battle('クイーン', enemy_hp, 'ゼウス', ally_hp), kind='battle'), mem)
    assert actions == []
    assert not mem['battle'].get('card_flow')
    assert not any(r['decision']=='battle_card' for r in mem['_records'])


def boss_loss(retries=0, side='attack', castle='けっかい', measured=True):
    mem = {'chapter': 1, 'orders': {'1-B1': 'launched'}, 'retries': {'1-B1': retries},
           'launched': {'けっかい': {'general': 'ゼウス', 'step': '1-B1'}}}
    if measured:
        assert policy.message_step(entry(), mem) == [policy.pad('a')]
    else:
        mem['attack'] = {'general': 'ゼウス', 'castle': castle, 'side': side, 'step': '1-B1'}
    panel = Screen([], None, '', battle=Battle('クイーン', 70, 'ゼウス', 74), kind='battle')
    policy.battle_step(panel, mem)
    policy.battle_step(panel, mem)
    policy.battle_step(Screen([], None, '', battle=Battle('クイーン', 70, 'ゼウス', 0), kind='battle'), mem)
    mem.update(general_override={'1-B1': 'ゼウス'}, card_override={'1-B1': ['イッテツーン']},
               order_context={'1-B1': {'actual_general': 'ゼウス'}}, sortie_general={'1-B1': 'ゼウス'})
    return mem


def test_confirmed_boss_defeat_retries_the_chart_kit_without_guessing_general_loss():
    mem = boss_loss()
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')
    assert mem['orders']['1-B1'] == 'pending'
    assert mem['retries']['1-B1'] == 1
    for key in ('general_override', 'card_override', 'order_context', 'sortie_general'):
        assert '1-B1' not in mem[key]
    context = mem['retry_context']['1-B1']
    assert context['strategy_variant'] == 'retry_chart_boss_kit'
    assert context['expected_metric']['cards'] == ['クースカン', 'ノリウツール']
    assert mem['stats']['losses'] == 1 and mem['stats']['generals_lost'] is None
    assert any(r['decision']=='order_retry' for r in mem['_records'])


def test_boss_retry_is_bounded_and_unknown_location_does_not_repair_orders():
    mem = boss_loss(3)
    policy.battle_end(mem, 'map'); policy.battle_end(mem, 'map')
    assert mem['orders']['1-B1'] == 'failed'
    assert not any(r['decision']=='order_retry' for r in mem['_records'])
    old = boss_loss(side=None, castle=None, measured=False)
    policy.battle_end(old, 'map'); policy.battle_end(old, 'map')
    assert old['orders']['1-B1'] == 'launched'
    assert old['retries']['1-B1'] == 0


def test_actual_sortie_deviation_is_preserved_in_battle_context():
    context = {'actual_general': 'ゼウス', 'planned_general': 'ココット',
               'strategy_variant': 'substitute_general', 'deviation_reason': '将軍一覧で代役を選択',
               'expected_metric': {'general': 'ココット', 'cards': []}}
    mem = {'chapter': 1, 'order_context': {'1-C1': context},
           'attack': {'general': 'ゼウス', 'castle': 'ジョンリギ', 'side': 'attack', 'step': '1-C1'}}
    panel = Screen([], None, '', battle=Battle('ラズベリー', 90, 'ゼウス', 85), kind='battle')
    policy.battle_step(panel, mem)
    policy.battle_step(panel, mem)
    rec = next(r for r in mem['_records'] if r['decision']=='battle_start')
    assert rec['strategy_variant'] == 'substitute_general'
    assert rec['deviation_reason'] == context['deviation_reason']


def test_boss_retry_commentary_preserves_chart_kit_description():
    from docich.hanjuku_commentary import compose
    _, text = compose({'decision': 'order_retry', 'chart_step': '1-B1',
                       'strategy_variant': 'retry_chart_boss_kit'})
    assert '主人公とチャートの切り札' in text
    assert 'イッテツーン' not in text


@pytest.mark.parametrize('side,castle', [('attack', 'けっかい'), ('attack', None),
                                          ('attack', '誤城'), (None, None)])
def test_legacy_or_unclassified_boss_loss_never_falls_back_to_generic_retry(side, castle):
    mem = boss_loss(side=side, castle=castle, measured=False)
    before = dict(mem['card_override'])
    policy.battle_end(mem, 'map'); policy.battle_end(mem, 'map')
    assert mem['orders']['1-B1'] == 'launched'
    assert mem['retries']['1-B1'] == 0
    assert mem['card_override'] == before
    assert not any(r['decision'] == 'order_retry' for r in mem['_records'])
    assert any(r['decision'] == 'situation_held' and r['strategy_variant'] == 'boss_entry_unclassified'
               for r in mem['_records'])
