"""Recognise real held cards without expanding the strategy planning list."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_policy as policy
from docich.hanjuku_font import TextLine, UNKNOWN
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import Screen, parse


@pytest.fixture(autouse=True)
def refused_card(monkeypatch):
    """These tests pin what follows a refusal, so the hit itself is not the reason to allow.

    The rule for hits that do not finish the enemy is in test_hanjuku_card_wear_down.py.
    """
    monkeypatch.setattr('docich.hanjuku.judgment.battle._card_wears_down_enemy', lambda estimate: False)

FIXTURE = Path(__file__).parent / 'fixtures' / 'hanjuku' / 'g700-balmung-sortie-confirm.png'


def cells(x, text):
    return tuple((x + 8 * i, c) for i, c in enumerate(text))


def confirmation(cards):
    lines = [TextLine(47, cells(136, 'きりふだ') + cells(176, cards[0])),
             TextLine(95, cells(136, 'ーしゅつげき') + cells(192, 'しますか?ー')),
             TextLine(111, cells(160, 'うむッ!')),
             TextLine(127, cells(160, 'いかんッ!'))]
    lines += [TextLine(y, cells(176, card)) for y, card in zip((63, 79), cards[1:])]
    return Screen(lines, (138, 105, 156, 118), '', kind='sortie_confirm')


@pytest.mark.parametrize('card', sorted(policy.ALL_CARD_NAMES))
def test_every_canonical_single_card_is_recognised_in_its_real_slot(card):
    assert policy._sortie_inventory(confirmation([card])) == [card]


@pytest.mark.parametrize('cards', [('イッテツーン', 'バルムンク'),
    ('イッテツーン', 'ダイチスイム', 'バルムンク'),
    ('バルムンク', 'バルムンク', 'バルムンク')])
def test_second_third_and_repeated_card_slots_preserve_actual_cards(cards):
    assert policy._sortie_inventory(confirmation(cards)) == list(cards)


def test_actual_native_balmung_confirmation_has_one_card():
    screen = parse(read_png(FIXTURE))
    assert screen.kind == 'sortie_confirm'
    assert policy._sortie_inventory(screen) == ['バルムンク']
    assert not policy._empty_sortie_inventory(screen)


def test_known_card_recognition_does_not_expand_planning_allowlist():
    assert 'バルムンク' not in policy.CARD_NAMES
    assert policy._sortie_inventory(confirmation(['バルムンク'])) == ['バルムンク']


@pytest.mark.parametrize('card', ['バルムン', 'バルムンク' + UNKNOWN, '未確認札'])
def test_partial_unknown_and_noncanonical_card_names_are_rejected(card):
    assert policy._sortie_inventory(confirmation([card])) is None


def test_shifted_or_gapped_slots_are_not_a_complete_kit():
    screen = confirmation(['バルムンク'])
    screen.lines[0] = TextLine(47, cells(136, 'きりふだ') + cells(184, 'バルムンク'))
    assert policy._sortie_inventory(screen) is None
    screen = confirmation(['バルムンク'])
    screen.lines.append(TextLine(79, cells(176, 'イッテツーン')))
    assert policy._sortie_inventory(screen) is None


def test_extra_known_card_outside_slot_is_not_ignored():
    screen = confirmation(['バルムンク'])
    screen.lines.append(TextLine(87, cells(176, 'バルムンク')))
    assert policy._sortie_inventory(screen) is None


def boss_memory():
    return {'chapter': 2, 'active': '2-B1', 'tick': 3000,
            'captured': list(set(policy.chart.CASTLE_NAMES[2]) - {'アルマムーン', 'ボス'}),
            'garrison': {'スペランザ': [policy.NAME]},
            'sortie_general': {'2-B1': 'ゼウス'}}


def test_native_card_receipt_cannot_approve_a_different_boss_kit():
    from test_hanjuku_sortie_evidence import actor_panel
    # Original card cells plus an explicitly synthetic companion header.
    screen = actor_panel(parse(read_png(FIXTURE)), 'ゼウス')
    mem = boss_memory()
    assert policy.deploy_step(screen, mem) == []
    record = mem['_records'][-1]
    assert record['decision'] == 'situation_held'
    assert record['observed_metric']['carried_cards_read'] == ['バルムンク']
    assert not mem.get('order_context')


def test_native_exact_kit_can_be_confirmed_when_that_is_the_actual_plan(monkeypatch):
    from docich import hanjuku_chart as chart
    order = next(o for o in chart.orders(2) if o['step'] == '2-B1')
    monkeypatch.setattr(policy, '_order', lambda mem: {**order, 'cards': ('バルムンク',)})
    from test_hanjuku_sortie_evidence import actor_panel
    # Original card cells plus an explicitly synthetic companion header.
    screen = actor_panel(parse(read_png(FIXTURE)), 'ゼウス')
    mem = boss_memory()
    assert policy.deploy_step(screen, mem) == [policy.pad('a')]
    context = mem['order_context']['2-B1']
    assert context['observed_metric']['cards'] == ['バルムンク']
    assert context['actual_general'] == 'ゼウス'
    assert mem['_records'][-1]['inventory_evidence'] == 'measured_single_card_sortie'


def test_battle_list_recognises_an_intended_canonical_event_card():
    from test_hanjuku_card_damage_gate import memory, cards
    mem = memory(card='バルムンク', enemy='にせヒーロー', enemy_hp=90,
                 ally='どうし', ally_hp=90, soldiers=(0, 0))
    assert policy.card_list_step(cards(['バルムンク']), mem) == [policy.pad('a')]
    assert mem['battle']['cards_selected'] == ['バルムンク']
    assert mem['battle']['cards_used'] == []
    assert mem['battle']['card_flow']['stage'] == 'announce'


def test_nonlethal_canonical_card_still_obeys_the_existing_damage_gate():
    from test_hanjuku_card_damage_gate import memory, cards, assert_not_spent
    mem = memory(card='バルムンク', enemy='ソーピニヨン', enemy_hp=55,
                 ally='ココット', ally_hp=24, soldiers=(0, 0))
    assert policy.card_list_step(cards(['バルムンク']), mem) == [policy.pad('b')]
    assert_not_spent(mem)
    assert mem['battle']['card_assessment']['allowed'] is False


def test_unknown_battle_card_does_not_select_or_declare_consumption():
    from test_hanjuku_card_damage_gate import memory, cards, assert_not_spent
    mem = memory(card='バルムンク', enemy='にせヒーロー', enemy_hp=90,
                 ally='どうし', ally_hp=90, soldiers=(0, 0))
    assert policy.card_list_step(cards(['バルムン' + UNKNOWN]), mem) == []
    assert_not_spent(mem)
