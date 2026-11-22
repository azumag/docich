"""g1163 2026-11-11: a castle sprite over the repair trip's sortie panel.

Sending ジェラート from いちじょうだに to the house, the source castle's sprite
covers は of きりふだは and four cells after きりふだ on every frame. The
exact empty-inventory receipt failed and the repair held 25 observations
(about 38 s) before giving up, every month.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from docich import hanjuku_house as house, hanjuku_policy as p
from docich.hanjuku.os_layer import readers
from docich.hanjuku.os_layer.font import UNKNOWN, TextLine
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
SPRITE = ROOT / 'g1163-repair-confirm-castle-sprite-over-panel.png'
CARD = ROOT / 'g700-balmung-kit-confirm.png'


def _screen(path=SPRITE):
    return parse(read_png(path))


def test_fixture_is_the_sole_defender_repair_confirmation():
    screen = _screen()
    assert screen.kind == 'sortie_confirm'
    info = house.general_status(screen)
    assert (info['general'], info['broken'], info['hp'], info['max_hp']) == ('ジェラート', True, 49, 49)


def test_strict_receipt_is_unchanged_for_the_deploy_flow():
    assert readers._empty_sortie_inventory(_screen()) is False


def test_occluded_cells_still_read_as_an_empty_inventory():
    assert readers._empty_sortie_inventory(_screen(), occluded=house.SORTIE_PANEL_OCCLUDED) is True


def test_a_carried_card_never_passes_the_tolerant_receipt():
    assert readers._empty_sortie_inventory(_screen(CARD), occluded=house.SORTIE_PANEL_OCCLUDED) is False


def _replace_row(screen, y, cells):
    screen.lines = [TextLine(y, tuple(cells)) if line.y == y else line for line in screen.lines]
    return screen


def test_a_readable_wrong_character_is_rejected():
    screen = _screen()
    # Row 79 reads ありません…… at x=176; one readable substitution must fail.
    cells = [(176 + 8 * i, ch) for i, ch in enumerate('ありまセん……')]
    _replace_row(screen, 79, cells)
    assert readers._empty_sortie_inventory(screen, occluded=house.SORTIE_PANEL_OCCLUDED) is False


def test_more_hidden_cells_than_the_bound_are_rejected():
    screen = _screen()
    cells = [(176, UNKNOWN), (184, UNKNOWN), (192, 'ま'), (200, 'せ'), (208, 'ん'), (216, '…'), (224, '…')]
    _replace_row(screen, 79, cells)   # 2 more on top of the measured 1
    assert readers._empty_sortie_inventory(screen, occluded=house.SORTIE_PANEL_OCCLUDED) is False


def _memory():
    return {'chapter': 8, 'month': '15-11', 'gold': 9000, 'tick': 21300,
            'captured': ['いちじょうだに', 'あるまむーん'], 'castle_ownership': {},
            'house': {'phase': 'castle_confirm', 'chapter': 8, 'age': 0, 'total': 0,
                      'general': 'ジェラート', 'source': 'いちじょうだに', 'sole_defender': True,
                      'pending': [], 'seen': [], 'inspected': []}}


def test_repair_confirmation_presses_umu_on_the_occluded_panel():
    mem = _memory()
    with patch.object(house, '_defer_unsafe_house_route', return_value=False), \
            patch.object(house, '_sole_defender_repair_due', return_value=True):
        actions = house._repair_step(_screen(), mem, None)
    assert actions == [p.pad('a')]
    assert mem['house']['phase'] == 'out_target'
