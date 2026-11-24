"""Regression coverage for #2953 summon-onset survival re-arm."""

from docich import hanjuku_policy as p
from docich.hanjuku_screen import Screen


def _memory():
    return {
        'chapter': 1,
        'battle': {
            'enemy': 'リーキ',
            'ally': 'トレビス',
            'castle': p.chart.home_castle(1),
            'side': 'defense',
            'start_enemy_hp': 30,
            'enemy_hp': 30,
            'start_ally_hp': 66,
            'ally_hp': 66,
            'step': None,
            'cards_used': [],
            'cards_selected': [],
            'cards_unclassified': [],
            'card_evidence_version': 1,
            'planned_cards': [],
            'survival': {
                'opens': 1,
                'menu_ticks': 0,
                'cards_checked': 0,
                'cards_attempted': [],
                'egg_attempted': False,
                'exhausted': True,
                'egg_window_latch': True,
            },
        },
    }


def test_summon_onset_rearms_survival_latch_exactly_once():
    mem = _memory()
    screen = Screen([], None, '', kind='egg_battle_menu')

    p.egg_battle_step(screen, mem)

    survival = mem['battle']['survival']
    assert survival['exhausted'] is False
    assert 'egg_window_latch' not in survival
    rearm = [r for r in mem['_records'] if r['decision'] == 'battle_survival_rearm']
    assert len(rearm) == 1

    # The same battle's next summon-menu tick must not re-arm again.
    survival['exhausted'] = True
    p.egg_battle_step(screen, mem)
    assert survival['exhausted'] is True
    assert len([r for r in mem['_records'] if r['decision'] == 'battle_survival_rearm']) == 1
