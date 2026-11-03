"""ランプキンは自分のHPが一桁なら攻撃を止めて「たまごにもどる」。"""
from types import SimpleNamespace

from docich.hanjuku.flows import battle_menus as menus

SKILLS = ['へんなおどり', 'かぼちゃ']


def view(*, ally_hp=9, enemy='ハデス', enemy_hp=500, cursor=160):
    ally = SimpleNamespace(name='ランプキン', hp=ally_hp, side='ally')
    other = SimpleNamespace(name=enemy, hp=enemy_hp, side='enemy')
    return SimpleNamespace(
        menu_rows=[SimpleNamespace(known=s, y=160 + 16 * i)
                   for i, s in enumerate([*SKILLS, 'たまごに もどれ'])],
        egg_rows=[ally, other], menu_cursor=cursor,
    )


def state():
    return {'battle': {'ally': 'ヴィーナス', 'enemy': 'クイーン'}}


def quiet(monkeypatch):
    monkeypatch.setattr(menus, '_record', lambda *a, **k: None)
    monkeypatch.setattr(menus, '_summoner_return_risk', lambda *a, **k: {})


def test_lampkin_returns_to_the_egg_at_single_digit_hp(monkeypatch):
    quiet(monkeypatch)
    state_ = state()
    # Cursor starts on the first skill; the return row is the third row.
    assert menus.monster_menu_step(view(ally_hp=9, cursor=160), state_) == [menus.pad('down')]
    assert state_['monster_menu_choice'] == 'retreat'
    assert menus.monster_menu_step(view(ally_hp=9, cursor=176), state_) == [menus.pad('down')]
    assert state_['monster_menu_choice'] == 'retreat'
    assert menus.monster_menu_step(view(ally_hp=9, cursor=192), state_) == [menus.pad('a')]
    assert state_['monster_menu_choice'] == 'retreat'


def test_lampkin_keeps_attacking_at_ten_hp(monkeypatch):
    quiet(monkeypatch)
    for hp in (10, 11, 99):
        state_ = state()
        menus.monster_menu_step(view(ally_hp=hp, cursor=160), state_)
        assert state_['monster_menu_choice'] in ('skill1', 'skill2')


def test_lampkin_return_also_applies_at_one_hp_and_to_bosses(monkeypatch):
    quiet(monkeypatch)
    for enemy in ('ハデス', 'ボス'):
        state_ = state()
        menus.monster_menu_step(view(ally_hp=1, enemy=enemy, cursor=160), state_)
        assert state_['monster_menu_choice'] == 'retreat'
