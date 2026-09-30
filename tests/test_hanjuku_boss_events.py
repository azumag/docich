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


def test_unmatched_boss_entry_is_closed_after_the_hold_limit():
    """g460 16:43-17:30: an unmatched entry message held forever froze the screen."""
    text = 'どうししょうぐんがボスじょうにせめこんだ!!'
    mem = {'chapter': 1, 'launched': {'けっかい': {'general': 'ゼウス', 'step': '1-B1'}}}
    for _ in range(policy.BOSS_ENTRY_HOLD_LIMIT - 1):
        assert policy.message_step(entry(text), mem) == []
    assert 'attack' not in mem
    assert sum(1 for r in mem['_records'] if r['decision'] == 'situation_held') \
        == policy.BOSS_ENTRY_HOLD_LIMIT - 1
    assert policy.message_step(entry(text), mem) == [policy.pad('a')]
    assert mem['attack'] == {'general': 'どうし', 'castle': 'けっかい', 'side': 'attack',
                             'step': None, 'entry_evidence': 'measured_boss_entry'}
    assert mem['_records'][-1]['decision'] == 'attack_observed'
    assert mem['_records'][-1]['deviation_reason'] == 'boss_entry_hold_released'
    assert 'boss_entry_hold' not in mem
    # A different message starts a fresh count instead of releasing at once.
    mem = {'chapter': 1, 'launched': {'けっかい': {'general': 'ゼウス', 'step': '1-B1'}}}
    other = 'ヴィーナスしょうぐんがボスじょうにせめこんだ!!'
    for _ in range(policy.BOSS_ENTRY_HOLD_LIMIT - 1):
        assert policy.message_step(entry(other), mem) == []
    assert policy.message_step(entry(text), mem) == []


def test_partial_or_unknown_boss_message_is_not_calibrated():
    assert entry(MESSAGE[:-1]).kind == 'text'
    assert entry(MESSAGE.replace('ゼ', '�')).kind == 'text'

@pytest.mark.parametrize('ally_hp,enemy_hp', [(0, 70), (19, 0)])
def test_no_new_card_command_after_a_panel_reaches_zero(ally_hp, enemy_hp):
    mem = {'chapter': 1, 'attack': {'general': 'ゼウス', 'castle': 'けっかい', 'side': 'attack', 'step': '1-B1'}}
    initial = Screen([], None, '', battle=Battle('クイーン', 70, 'ゼウス', 74), kind='battle')
    policy.battle_step(initial, mem)
    policy.battle_step(initial, mem)
    # The chart's first card waits for one measured HP drop.
    assert not mem['battle'].get('card_flow')
    policy.battle_step(Screen([], None, '', battle=Battle('クイーン', 68, 'ゼウス', 74), kind='battle'), mem)
    assert mem['battle'].get('card_flow')['card'] == 'クースカン'
    before = len(mem['_records'])
    zero = Screen([], None, '', battle=Battle('クイーン', enemy_hp, 'ゼウス', ally_hp), kind='battle')
    actions = policy.battle_step(zero, mem)
    assert actions == []
    assert not mem['battle'].get('card_flow')
    assert not any(r['decision'] == 'battle_card' for r in mem['_records'][before:])


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


@pytest.mark.parametrize('chapter,step,enemy', [(1, None, 'クイーン'),
                                             (1, '1-A1', 'クイーン'),
                                             (2, '1-B1', 'クイーン'),
                                             (1, '1-B1', 'ミント')])
def test_measured_boss_name_requires_matching_chapter_and_boss_sortie(chapter, step, enemy):
    mem = {'chapter': chapter}
    assert not policy._boss_tactics_allowed(mem, {'enemy': enemy, 'step': step})


def unconfirmed_boss_memory():
    kit = ['クースカン', 'ノリウツール']
    evidence = {'actual_general': 'どうし', 'planned_general': 'どうし',
                'observed_metric': {'general': 'どうし', 'cards': kit},
                'expected_metric': {'general': 'どうし', 'cards': kit}}
    order = dict(next(o for o in policy.chart.orders(1) if o['step'] == '1-B1'))
    return {'chapter': 1, 'captured': ['ゴーメン', 'スペンソニア'],
            'sorties': {'1-A2': {'general': 'どうし', 'status': 'arrived', 'target': 'ゴーメン'},
                        '1-B1': {'general': 'どうし', 'status': 'launched_unconfirmed',
                                 'target': None, 'planned_target': 'けっかい', 'evidence': evidence}},
            'order_context': {'1-B1': evidence}, 'launched_orders': {'1-B1': order}}


def test_unconfirmed_boss_sortie_keeps_measured_kit_without_claiming_arrival():
    mem = unconfirmed_boss_memory()
    panel = Screen([], None, '', battle=Battle('クイーン', 70, 'どうし', 66), kind='battle')
    policy.battle_step(panel, mem); policy.battle_step(panel, mem)
    cur = mem['battle']
    assert cur['step'] == '1-B1'
    assert cur['planned_cards'] == ['クースカン', 'ノリウツール']
    assert cur['castle'] is None and cur['side'] is None
    assert cur['context'] == 'unclassified_location'
    assert mem['sorties']['1-B1']['status'] == 'launched_unconfirmed'
    assert mem['sorties']['1-B1']['target'] is None
    assert not mem.get('boss_seen')
    # Actual g506: the Queen stays at 70, only the hero loses HP at clash.
    hurt = Screen([], None, '', battle=Battle('クイーン', 70, 'どうし', 55), kind='battle')
    assert policy.battle_step(hurt, mem) == [policy.pad('b')]
    assert cur['card_flow']['card'] == 'クースカン'
    cur['cards_selected'] = ['クースカン']
    cur['card_flow'] = {'card': 'クースカン', 'stage': 'announce', 'enemy_hp_at_open': 70}
    half = Screen([], None, '', battle=Battle('クイーン', 34, 'どうし', 55), kind='battle')
    assert policy.battle_step(half, mem) == [policy.pad('b')]
    assert cur['card_flow']['card'] == 'ノリウツール'
    assert cur['cards_used'] == []  # HP change is not a calibrated use receipt.


def test_unconfirmed_boss_kit_is_not_spent_on_road_enemy():
    mem = unconfirmed_boss_memory()
    panel = Screen([], None, '', battle=Battle('ミント', 32, 'どうし', 66), kind='battle')
    policy.battle_step(panel, mem); policy.battle_step(panel, mem)
    assert mem['battle']['step'] == '1-B1'
    assert mem['battle']['planned_cards'] == []
    policy.battle_step(Screen([], None, '', battle=Battle('ミント', 32, 'どうし', 55), kind='battle'), mem)
    assert not mem['battle'].get('card_flow')


@pytest.mark.parametrize('kind', ['ambiguous', 'different_general', 'finished'])
def test_unconfirmed_sortie_binding_stays_unclassified_when_no_unique_march(kind):
    mem = unconfirmed_boss_memory()
    if kind == 'ambiguous':
        mem['sorties']['other'] = dict(mem['sorties']['1-B1'])
    elif kind == 'different_general':
        mem['sorties']['1-B1']['general'] = 'ゼウス'
    else:
        mem['sorties']['1-B1']['status'] = 'finished'
    cur = policy._battle_context(mem, 'どうし')
    assert cur['step'] is None and cur['castle'] is None and cur['side'] is None


def test_measured_defense_context_wins_over_unconfirmed_march():
    mem = unconfirmed_boss_memory()
    mem['attack'] = {'general': 'どうし', 'side': 'defense', 'castle': 'ゴーメン'}
    cur = policy._battle_context(mem, 'どうし')
    assert cur['side'] == 'defense' and cur['castle'] == 'ゴーメン' and cur['step'] is None
