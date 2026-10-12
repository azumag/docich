"""g713 natural B conservation was lost when troops crossed x128, with HP unchanged."""
from pathlib import Path

from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
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
