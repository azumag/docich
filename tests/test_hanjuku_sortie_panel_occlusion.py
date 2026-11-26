"""docich#5146: a castle sprite over the departure confirm's card panel.

ながはま's sprite covers cells of the right sortie panel on every frame, the
same pattern #3920/v655 already tolerated for the repair dispatch
(SORTIE_PANEL_OCCLUDED). The deploy flow called both receipts with no
allowance, so a readable-but-occluded confirmation cancelled every sortie
from such a castle (g1190-ch1v7 66-5, re-observed 66-7).
"""
from pathlib import Path

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku.flows import house
from docich.hanjuku.os_layer import readers
from docich.hanjuku.os_layer.font import UNKNOWN
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku.state.regions import plan as _plan_region
from test_hanjuku_sortie_evidence import (menu, _c2_memory, empty_order_memory,
                                        foot_order_memory, measured_loaded_sortie,
                                        replace_cell)

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
SPRITE = ROOT / 'g1163-repair-confirm-castle-sprite-over-panel.png'
OCCLUDED = house.SORTIE_PANEL_OCCLUDED


def test_deploy_approves_an_empty_kit_on_the_sprite_occluded_panel():
    screen = parse(read_png(SPRITE))
    assert screen.kind == 'sortie_confirm'
    assert readers._empty_sortie_inventory(screen) is False      # strict stays strict
    mem = empty_order_memory()
    assert policy.deploy_step(screen, mem) == [policy.pad('a')]
    record = mem['_records'][-1]
    assert record['decision'] == 'sortie_confirm'
    assert record['inventory_evidence'] == 'measured_empty_sortie'


def test_fixed_panel_cells_hidden_by_a_sprite_still_read_the_carried_cards():
    screen = measured_loaded_sortie()
    replace_cell(screen, 95, 192, UNKNOWN)                       # し of しますか?ー
    assert policy._sortie_inventory(screen) is None
    assert policy._sortie_inventory(screen, occluded=OCCLUDED) == ['フットバース']


def test_deploy_approves_an_occluded_kit_without_a_miss_round():
    mem = foot_order_memory()
    screen = measured_loaded_sortie()
    replace_cell(screen, 95, 192, UNKNOWN)
    replace_cell(screen, 47, 144, UNKNOWN)                       # り of きりふだ
    assert policy.deploy_step(screen, mem) == [policy.pad('a')]
    record = mem['_records'][-1]
    assert record['decision'] == 'sortie_confirm'
    assert record['cards'] == ['フットバース']
    assert not mem.get('sortie_confirm_miss')


def test_hidden_cells_beyond_the_bound_stay_unreadable():
    screen = measured_loaded_sortie()
    replace_cell(screen, 95, 192, UNKNOWN)
    replace_cell(screen, 95, 200, UNKNOWN)
    replace_cell(screen, 111, 160, UNKNOWN)
    assert policy._sortie_inventory(screen, occluded=OCCLUDED) is None


def test_a_readable_wrong_character_is_never_an_occluded_cell():
    screen = measured_loaded_sortie()
    replace_cell(screen, 95, 192, 'あ')
    assert policy._sortie_inventory(screen, occluded=OCCLUDED) is None


def test_an_occluded_card_name_keeps_the_panel_unreadable():
    # A name split by the sprite is content, not a fixed cell: the receipt
    # must not guess the carried kit.
    screen = measured_loaded_sortie()
    replace_cell(screen, 47, 200, UNKNOWN)                       # inside フットバース
    assert policy._sortie_inventory(screen, occluded=OCCLUDED) is None


def test_unexpected_known_text_still_rejects_the_occluded_panel():
    screen = measured_loaded_sortie()
    replace_cell(screen, 87, 176, 'あ')
    assert policy._sortie_inventory(screen, occluded=OCCLUDED) is None


def _unreadable_round(mem, screen):
    for _ in range(policy.SORTIE_CONFIRM_UNREADABLE_LIMIT - 1):
        assert policy.deploy_step(screen, mem) == []
    return policy.deploy_step(screen, mem)


def test_leaving_the_confirmation_rearms_one_blind_approval():
    mem = _c2_memory()
    screen = menu('sortie_confirm', ['うむッ!'], hand=False)     # unreadable panel
    blind = _unreadable_round(mem, screen)
    assert blind == [policy.pad('a'), {'type': 'wait', 'ms': 150}, policy.pad('a')]
    assert mem['sortie_confirm_blind']['1-C2'] is True
    # A different screen means the panel was left (a battle can swallow the
    # blind A): the next round must not inherit the spent flag.
    assert policy.deploy_step(menu('card_select', ['クースカン1']), mem) == []
    assert '1-C2' not in mem['sortie_confirm_blind']
    assert '1-C2' not in mem['sortie_confirm_miss']
    blind = _unreadable_round(mem, screen)
    assert blind == [policy.pad('a'), {'type': 'wait', 'ms': 150}, policy.pad('a')]
    # Without another departure the second round still cancels.
    assert _unreadable_round(mem, screen) == [policy.pad('b'), policy.pad('b')]
    assert _plan_region(mem)['orders']['1-C2'] == 'failed'


def test_an_unconfirmed_launch_keeps_its_blind_flag_on_other_screens():
    mem = _c2_memory()
    _plan_region(mem)['orders']['1-C2'] = 'launched_unconfirmed'
    mem['sortie_confirm_miss'] = {'1-C2': 2}
    mem['sortie_confirm_blind'] = {'1-C2': True}
    policy.deploy_step(menu('card_select', ['クースカン1']), mem)
    assert mem['sortie_confirm_miss']['1-C2'] == 2
    assert mem['sortie_confirm_blind']['1-C2'] is True
