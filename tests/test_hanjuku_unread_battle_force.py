"""Walking body fragments cannot certify a small or empty army."""
import hashlib
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.os_layer.pixels import Frame, read_png
from docich.hanjuku.os_layer.screen import _field_soldiers, parse
from test_hanjuku_egg_hold import BAND_TONES, field_frame


@pytest.mark.parametrize('team', [0, 1])
@pytest.mark.parametrize('body', [(20, 90, 8, 6), (20, 90, 9, 6),
                                  (20, 90, 6, 8), (20, 90, 6, 9)])
def test_small_thin_fragments_still_do_not_invent_full_bodies(team, body):
    other = ((160, 90, 10, 10, 1-team),)
    counts = _field_soldiers(field_frame(((*body, team),) + other))
    assert counts[team] == 0 and counts[1-team] == 1


@pytest.mark.parametrize('team', [0, 1])
def test_body_sized_fragment_below_minimum_area_is_unread(team):
    frame = field_frame(((20, 90, 8, 8, team), (160, 90, 10, 10, 1-team)))
    rgb = bytearray(frame.rgb)
    # 64-10=54 pixels, preserving the connected 8x8 outer shape.
    for x, y in [(x, y) for y in range(91, 97) for x in range(21, 27)][:10]:
        i = (y*frame.width+x)*3
        rgb[i:i+3] = bytes(BAND_TONES[0])
    counts = _field_soldiers(Frame(frame.width, frame.height, bytes(rgb)))
    assert counts[team] is None and counts[1-team] == 1


def test_native_walking_enemy_fragments_cannot_supply_two_soldiers():
    root = Path(__file__).parent / 'fixtures/hanjuku'
    meta = json.loads((root/'g747-walking-enemy-evidence.json').read_text())
    image = root/'g747-walking-enemy-2751.png'
    frame = read_png(image)
    assert hashlib.sha256(image.read_bytes()).hexdigest() == meta['png_sha256']
    assert frame.digest() == meta['rgb_sha256'] == meta['decision']['frame_sha256']
    # This original observation planned a wait; it is not a new live input.
    assert meta['input_receipts'] == []
    screen = parse(frame)
    assert (screen.battle.ally_hp, screen.battle.enemy_hp) == (24, 66)
    assert screen.field_soldiers == (6, None)


def test_native_unread_count_removes_the_false_healthy_melee_permission():
    screen = parse(read_png(Path(__file__).parent/'fixtures/hanjuku/g747-walking-enemy-2751.png'))
    cur = {'ally': 'ココット', 'enemy': 'トレビス', 'ally_hp': 24, 'enemy_hp': 66,
           'start_ally_hp': 24, 'start_enemy_hp': 66, 'side': 'defense',
           'ally_soldiers': 6, 'enemy_soldiers': 2, 'card_soldiers_current': True,
           'planned_cards': [], 'cards_used': [], 'card_evidence_version': 1}
    mem = {'chapter': 1, 'battle': cur}
    assert p._hold_melee_egg(mem, cur, {'たまごをつかう'}) is True
    p._card_battle_reading(screen, cur)
    assert cur['enemy_soldiers'] is None
    assert p._hold_melee_egg(mem, cur, {'たまごをつかう'}) is False
    assert not cur.get('cards_used') and not cur.get('egg_consumed')
