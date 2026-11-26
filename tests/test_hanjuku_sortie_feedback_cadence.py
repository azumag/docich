"""Short 500 ms cadence for castle-jump and active sortie transitions."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hanjuku_sortie_cadence_entry', ROOT / 'brains/hanjuku/bot.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def test_castle_jump_uses_short_five_hundred_ms_interval():
    state = {
        'screen_kind': 'main_menu',
        'policy': {'castle_jump': {'step': 'I:c9:タオ>サヌト:1', 'phase': 'menu'}},
    }
    assert entry.observation_interval_ms(state, []) == 500


@pytest.mark.parametrize('kind', ['castle_menu', 'general_list', 'card_select', 'sortie_confirm', 'map_target'])
def test_active_sortie_menu_and_target_keep_five_hundred_ms_interval(kind):
    state = {
        'screen_kind': kind,
        'policy': {'plan': {'active': 'I:c9:タオ>サヌト:1'}},
    }
    assert entry.observation_interval_ms(state, []) == 500


def test_expect_menu_on_active_sortie_uses_five_hundred_ms_interval():
    state = {
        'screen_kind': 'map',
        'policy': {'plan': {'active': 'I:c9:タオ>サヌト:1'}, 'expect_menu': True},
    }
    assert entry.observation_interval_ms(state, []) == 500


def test_inactive_or_held_sortie_states_keep_normal_fifteen_hundred_ms_interval():
    assert entry.observation_interval_ms({'screen_kind': 'castle_menu', 'policy': {}}, []) == 1500
    assert entry.observation_interval_ms({
        'screen_kind': 'map_target',
        'policy': {
            'plan': {'active': 'I:c9:タオ>サヌト:1'},
            'flow': {'departure_request': {'step': 'I:c9:タオ>サヌト:1', 'status': 'requested'}},
        },
    }, []) == 1500
    for hold_key in ('boss_card_hold', 'boss_stock_cancelled'):
        assert entry.observation_interval_ms({
            'screen_kind': 'card_select',
            'policy': {
                'plan': {'active': '9-B1'},
                hold_key: {'9-B1': 1},
            },
        }, []) == 1500
