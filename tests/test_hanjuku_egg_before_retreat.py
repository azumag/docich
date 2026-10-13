"""A real positive egg gets a measured rescue attempt before ordinary retreat."""
from copy import deepcopy
from pathlib import Path

import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_survival import battle, menu


def memory(ally='キッシュ', hp=8, uses=4):
    return {'chapter': 2, 'egg_uses': {ally: uses}, 'battle': {
        'ally': ally, 'enemy': 'ポワソン', 'ally_hp': hp, 'enemy_hp': 80,
        'start_ally_hp': hp, 'start_enemy_hp': 80, 'side': 'attack',
        'step': None, 'planned_cards': [], 'cards_used': [], 'card_evidence_version': 1}}


def test_native_wounded_quiche_opens_rescue_instead_of_retreat():
    s = parse(read_png(Path(__file__).parent / 'fixtures/hanjuku/g749-quiche-poisson-unused-egg.png'))
    assert (s.battle.ally, s.battle.ally_hp, s.battle.enemy_hp) == ('キッシュ', 8, 80)
    mem = memory()
    assert p.battle_step(s, mem) == [p.pad('b')]
    assert mem['battle']['command_open_pending']['owner'] == 'survival'
    assert not mem['battle'].get('hero_retreat')
    assert any(r['decision'] == 'battle_egg_before_retreat' for r in mem['_records'])
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert mem['battle']['survival']['egg_attempted']
    assert mem['egg_uses']['キッシュ'] == 4  # selection is not consumption
    assert 'キッシュ' in mem['egg_recheck']


@pytest.mark.parametrize('ally', ['キッシュ', p.NAME])
def test_positive_egg_precedes_visible_retreat_even_at_critical_hp(ally):
    mem = memory(ally=ally)
    assert p.battle_menu_step(menu(selected=2), mem) == [p.pad('up')]
    assert p.battle_menu_step(menu(selected=1), mem) == [p.pad('up')]
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert not mem['battle'].get('hero_retreat')


@pytest.mark.parametrize('change', [
    {'egg_uses': {'キッシュ': 0}}, {'egg_uses': {'キッシュ': None}},
    {'egg_uses': {'ヴィーナス': 4}}, {'egg_recheck': ['キッシュ']},
    {'egg_zero_owners': ['キッシュ'], 'egg_uses': {}},
])
def test_unknown_zero_other_actor_and_dirty_counts_keep_retreat(change):
    mem = memory(); mem.update(change)
    assert p.battle_menu_step(menu(selected=2), mem) == [p.pad('a')]
    assert mem['battle']['hero_retreat']['selected'] == 1
    assert not (mem['battle'].get('survival') or {}).get('egg_attempted')


@pytest.mark.parametrize('rescue', [
    {'exhausted': True}, {'egg_attempted': True}, {'pending_opens': 3}, {'opens': 12},
])
def test_failed_or_bounded_rescue_does_not_suppress_retreat_forever(rescue):
    mem = memory(); mem['battle']['survival'] = rescue
    assert not p._egg_before_retreat(mem, mem['battle'])
    assert p._hero_retreat_open(mem, mem['battle']) == [p.pad('b')]


def test_final_open_budget_still_allows_a_now_visible_egg():
    mem = memory()
    p._survival_state(mem, mem['battle']).update(pending_opens=3, opens=12)
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert mem['battle']['survival']['egg_attempted']


def test_disabled_egg_row_keeps_retreat_despite_saved_positive_quantity():
    mem = memory()
    assert p.battle_menu_step(menu(('きりふだ', 'たいきゃく'), selected=1), mem) == [p.pad('a')]
    assert mem['battle']['hero_retreat']['selected'] == 1
    assert not (mem['battle'].get('survival') or {}).get('egg_attempted')


def test_existing_owned_summon_does_not_open_another_general_egg():
    mem = memory(); mem['battle']['egg_battle'] = True
    assert not p._egg_before_retreat(mem, mem['battle'])


def test_outmatched_full_hp_general_uses_known_egg_before_hp_collapses():
    mem = memory(hp=26)
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert mem['battle']['command_open_pending']['owner'] == 'survival'
    # Do not scout an unknown card inventory while a usable owned egg is shown.
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert mem['battle']['survival']['egg_attempted']
    assert not mem['battle'].get('card_flow')


def test_real_departure_status_provides_the_named_positive_count():
    from docich.hanjuku.flows.house import general_status
    s = parse(read_png(Path(__file__).parent / 'fixtures/hanjuku/g749-quiche-four-eggs-departure.png'))
    mem = memory(uses=None)
    info = general_status(s)
    p.observe_events(s, mem)
    assert (info['general'], info['hp'], info['max_hp'], info['uses']) == ('キッシュ', 26, 26, 4)
    assert mem['egg_uses']['キッシュ'] == 4
    assert p._egg_before_retreat(mem, mem['battle'])


def test_sword_practice_never_requests_a_nonexistent_resource_menu():
    mem = memory(ally=p.NAME)
    mem['battle'].update(enemy='だいじん', start_ally_hp=90, start_enemy_hp=90, step=None, side=None)
    assert not p._egg_before_retreat(mem, mem['battle'])


def test_selected_egg_has_bounded_fade_before_failed_attempt_retreat():
    mem = memory()
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    for _ in range(3):
        assert p.battle_menu_step(menu(), mem) == []
    assert p.battle_menu_step(menu(selected=2), mem) == [p.pad('a')]
    assert mem['battle']['hero_retreat']['selected'] == 1
    assert mem['egg_uses']['キッシュ'] == 4


def test_selected_egg_does_not_toggle_b_during_ordinary_panel_fade():
    mem = memory()
    p.battle_menu_step(menu(), mem)
    for _ in range(3):
        assert p.battle_step(battle(mem), mem) == [{'type': 'wait', 'ms': 50}]
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert mem['battle']['command_open_pending']['owner'] == 'retreat'


def test_native_first_panel_rechecks_at_existing_short_cadence():
    import importlib.util
    spec = importlib.util.spec_from_file_location('egg_before_retreat_bot',
        Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py')
    bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
    screen = parse(read_png(Path(__file__).parent / 'fixtures/hanjuku/g749-quiche-first-battle.png'))
    assert screen.battle.ally_hp == 17
    mem = memory(); mem.pop('battle')
    assert p.battle_step(screen, mem) == []  # identity still requires two readings
    assert bot.observation_interval_ms({'screen_kind': 'battle', 'policy': mem}, []) == 500
    assert not mem.get('battle')
    assert bot.observation_interval_ms({'screen_kind': 'map', 'policy': mem}, []) == 1500
