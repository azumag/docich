"""A newly readable final cursor can select a live, useful card without retries."""
import pytest

from docich import hanjuku_policy as p
from test_hanjuku_card_damage_gate import memory, cards


def rescue(enemy_hp=32, soldiers=(0, 0)):
    mem = memory('ゼンマイン', enemy='ソーピニヨン', enemy_hp=enemy_hp,
                 ally='ココット', ally_hp=22, soldiers=soldiers, side='defense')
    cur = mem['battle']
    cur['planned_cards'] = []
    p._survival_state(mem, cur)
    cur['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
    return mem


def test_the_final_live_cursor_selects_a_proven_lethal_card_without_claiming_use():
    mem = rescue()
    for _ in range(p.CARD_LIST_CURSOR_LIMIT):
        assert p.card_list_step(cards(['ゼンマイン'], selected=None), mem) == []
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('a')]
    cur = mem['battle']
    assert cur['cards_selected'] == cur['survival']['cards_attempted'] == ['ゼンマイン']
    assert cur['cards_used'] == [] and cur['card_consumption_complete'] is False
    assert cur['card_flow']['stage'] == 'announce'
    assert cur['card_assessment']['reason'] == 'single_card_lethal'


@pytest.mark.parametrize('cursor', [None, 1])
def test_expired_missing_or_wrong_cursor_does_not_get_more_navigation(cursor):
    mem = rescue()
    mem['battle']['card_flow']['list_ticks'] = p.CARD_LIST_CURSOR_LIMIT
    assert p.card_list_step(cards(['ゼンマイン', 'ファバード'], selected=cursor), mem) == [p.pad('b')]
    assert not mem['battle']['cards_selected']
    assert mem['battle']['card_flow'] is None
    assert mem['battle']['survival']['cards_exhausted'] is True
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]


@pytest.mark.parametrize('enemy_hp,soldiers', [(33, (0, 0)), (32, (0, None))])
def test_current_cursor_cannot_override_damage_or_unread_soldier_gates(enemy_hp, soldiers):
    mem = rescue(enemy_hp, soldiers)
    mem['battle']['card_flow']['list_ticks'] = p.CARD_LIST_CURSOR_LIMIT
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert mem['battle']['card_assessment']['allowed'] is False
    assert not mem['battle']['cards_selected']


def test_an_unchanged_selected_copy_is_still_unconfirmed_not_a_new_card():
    mem = rescue()
    cur = mem['battle']
    cur['survival'].update(cards_attempted=['ゼンマイン'],
                           card_counts_at_selection={'ゼンマイン': 1})
    cur['cards_selected'] = ['ゼンマイン']
    cur['card_flow']['list_ticks'] = p.CARD_LIST_CURSOR_LIMIT
    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]
    assert cur['cards_selected'] == ['ゼンマイン'] and cur['cards_used'] == []
