"""A generic summon's stall switch still runs end-to-end (#2979 regression intent).

おめでとり's ミカン→あたためる order bypasses the stall count (#2945), so the
end-to-end coverage of the generic stall switch lives on an ordinary summon here.
"""
from types import SimpleNamespace

import pytest

from docich.hanjuku.flows import battle_menus as menus
from docich.hanjuku.flows.battle_menus import MONSTER_STALL_TURNS


def view(*, cursor=160, enemy_hp=45):
    ally = SimpleNamespace(name='なると', hp=300, side='ally')
    other = SimpleNamespace(name='ラズベリー', hp=enemy_hp, side='enemy')
    skills = ['ぎざぎざ', 'うずまき']
    return SimpleNamespace(
        menu_rows=[SimpleNamespace(known=s, y=160 + 16 * i)
                   for i, s in enumerate([*skills, 'たまごにもどれ'])],
        egg_rows=[ally, other], menu_cursor=cursor,
    )


def turn(sc, mem, observations=20):
    mem['tick'] += observations
    mem.pop('monster_menu_choice', None)
    mem['_records'] = []
    menus.monster_menu_step(sc, mem)
    return mem['monster_menu_choice'], [r.get('decision') for r in mem['_records']]


@pytest.fixture(autouse=True)
def no_return_risk(monkeypatch):
    monkeypatch.setattr(menus, '_summoner_return_risk', lambda *args: {})


def test_a_generic_summon_tries_its_other_skill_after_the_bounded_turns_then_records_it():
    sc, mem = view(), {'chapter': 3, 'tick': 1}
    choices = [turn(sc, mem) for _ in range(2 * MONSTER_STALL_TURNS + 1)]
    actions = [c for c, _ in choices]
    first = actions[0]
    other = 'skill2' if first == 'skill1' else 'skill1'
    assert actions == [first] * MONSTER_STALL_TURNS + [other] * MONSTER_STALL_TURNS + [first]
    stalls = [i for i, (_, recs) in enumerate(choices) if 'monster_skill_stall' in recs]
    assert stalls == [MONSTER_STALL_TURNS, 2 * MONSTER_STALL_TURNS]


def test_a_redraw_of_the_same_turn_keeps_its_choice_and_is_not_counted():
    sc, mem = view(), {'chapter': 3, 'tick': 1}
    for _ in range(MONSTER_STALL_TURNS):
        choice = turn(sc, mem)[0]
        assert turn(sc, mem, observations=1)[0] == choice    # redraw: same choice
    assert mem['monster_stall']['turns'] == {choice: MONSTER_STALL_TURNS}
