"""g713 natural B conservation was lost when troops crossed x128, with HP unchanged."""
from pathlib import Path
import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_pixels import Frame, read_png
from docich.hanjuku_screen import parse
from test_hanjuku_combat_strength import duel

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
DIGESTS = {
    24397: 'b3da922b4301833db4a297c0169bf8a322e4b831552177c6f06526c5cbc345c1',
    24401: 'a9fada64a784892ab472d129f7c2834828f5960eaebc75bdf3034e27df286845',
    24403: '0776e224c69fdbcf6f50e0de6c7624a0761e2fb2e5209c04869716053b427cf7',
    24407: 'e973f661a04fa2e4992e8019b07d14bdcf5371735ead93de4073e0669a53d254',
}


def native(uid):
    frame = read_png(ROOT / f'g713-coco-gaspacho-{uid}.png')
    assert frame.digest() == DIGESTS[uid]
    return parse(frame)


def memory():
    cur = duel(ally='ココット', enemy='ガスパチョ', hp=24, enemy_hp=37,
               soldiers=(6, 6))
    cur.update(side='defense', castle='ジョンリギ')
    return {'chapter': 1, 'tick': 1, 'battle': cur}


def test_native_advancing_troops_keep_their_armies_and_hp():
    opening, advancing = native(24397), native(24403)
    assert opening.battle == advancing.battle
    assert opening.field_soldiers == (6, 6)
    assert advancing.field_soldiers == (6, 5)


def test_native_conservation_survives_the_advancing_panel_and_next_real_menu():
    mem = memory()
    assert p.battle_step(native(24397), mem) == [p.pad('b')]
    assert p.battle_menu_step(native(24401), mem) == [p.pad('b')]
    # Neither the observed HP nor the general's combat changed. Advancing
    # enemies must not become eight allies and disable the army evidence.
    assert p.battle_step(native(24403), mem) == []
    assert p.battle_menu_step(native(24407), mem) == [p.pad('b')]
    assert not mem['battle']['survival']['egg_attempted']
    assert not mem['battle'].get('cards_selected')
    assert not any(r['decision'] == 'battle_survival_select' for r in mem['_records'])


def test_current_hp_danger_still_overrides_prior_native_conservation():
    mem = memory()
    p.battle_step(native(24397), mem)
    p.battle_menu_step(native(24401), mem)
    mem['battle']['ally_hp'] = 10
    assert p.battle_menu_step(native(24407), mem) == [p.pad('a')]


def test_current_owner_zero_is_not_unlocked_by_native_army_advantage():
    mem = memory(); mem['egg_uses'] = {'ココット': 0}
    p.battle_step(native(24397), mem)
    assert p.battle_menu_step(native(24407), mem) == [p.pad('b')]
    assert p._own_egg_empty(mem, mem['battle'])


def translated_enemy_boundary(x, y):
    """Derived RGB-preserving boundary, not a naturally observed screenshot."""
    frame = read_png(ROOT / 'g713-coco-gaspacho-24397.png')
    assert frame.digest() == DIGESTS[24397]
    rgb = bytearray(frame.rgb)
    # Remove the initial enemy formation, keeping all six native ally bodies
    # and the original HP panel. Reinsert the actual (41,69)..(53,83) body.
    for yy in range(64, 150):
        for xx in range(0, 100):
            i = (yy * frame.width + xx) * 3
            rgb[i:i+3] = bytes((106, 40, 49))
    for dy in range(15):
        for dx in range(13):
            xx, yy = x + dx, y + dy
            if 0 <= xx < frame.width and 0 <= yy < frame.height:
                i = (yy * frame.width + xx) * 3
                rgb[i:i+3] = bytes(frame.pixel(41+dx, 69+dy))
    return Frame(frame.width, frame.height, bytes(rgb))


@pytest.mark.parametrize('x,y', [(245, 69), (250, 69), (254, 69),
                                (-7, 69), (-11, 69), (41, 27), (41, 30), (41, 36)])
def test_rgb_preserved_thin_enemy_edges_stay_unread(x, y):
    screen = parse(translated_enemy_boundary(x, y))
    assert screen.battle == native(24397).battle
    assert screen.field_soldiers == (6, None)
    mem = memory()
    p._card_battle_reading(screen, mem['battle'], mem)
    # docich#4048: the command menu keeps its field visible, so the menu
    # frame re-measures both armies ((6,4) on g713-24407). A complete fresh
    # read legitimately licenses conservation; the thin-edge panel itself
    # still cannot, which the (6, None) assertion above keeps covered.
    assert p.battle_menu_step(native(24407), mem) == [p.pad('b')]
    assert mem['battle']['ally_soldiers'] == 6
    assert mem['battle']['enemy_soldiers'] == 4


def test_battle_menu_frames_still_measure_the_field():
    """docich#4048: the command menu hides the HP panel, not the armies."""
    menu = native(24401)
    assert menu.kind == 'battle_menu' and menu.battle is None
    assert menu.field_soldiers == (6, 5)
    assert native(24407).field_soldiers == (6, 4)


def test_battle_menu_frame_refreshes_only_the_measured_sides():
    mem = memory()
    cur = mem['battle']
    p._card_battle_reading(native(24397), cur, mem)
    assert (cur['ally_soldiers'], cur['enemy_soldiers']) == (6, 6)
    # The menu reads (6,4): a real drop since the opening, both sides measured.
    p._card_battle_reading(native(24407), cur, mem)
    assert (cur['ally_soldiers'], cur['enemy_soldiers']) == (6, 4)
    assert cur['card_soldiers_current'] is True
    # A side the menu cannot read keeps its last value rather than
    # invalidating the earlier complete panel read.
    cur['enemy_soldiers'] = 4
    menu = native(24401)
    menu.field_soldiers = (6, None)
    p._card_battle_reading(menu, cur, mem)
    assert (cur['ally_soldiers'], cur['enemy_soldiers']) == (6, 4)


def test_egg_battle_menu_frames_carry_no_soldier_count():
    """Summon menus show monsters, not armies: nothing may be counted."""
    frame = read_png(ROOT / 'g747-enemy-command-1310.png')
    screen = parse(frame)
    assert screen.kind == 'egg_battle_menu'
    assert screen.field_soldiers is None


def test_battle_start_record_carries_the_measured_armies():
    """docich#4048: battle_start must log real counts, not a silent None."""
    mem = {'chapter': 1, 'tick': 1}
    p.battle_step(native(24397), mem)
    p.battle_step(native(24397), mem)
    start = next(r for r in mem['_records'] if r['decision'] == 'battle_start')
    assert start['ally_soldiers'] == 6 and start['enemy_soldiers'] == 6
    assert start['observed_metric']['ally_soldiers'] == 6
    assert start['observed_metric']['enemy_soldiers'] == 6
    assert mem['battle']['ally_soldiers'] == 6
    assert mem['battle']['enemy_soldiers'] == 6
