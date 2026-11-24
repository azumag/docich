"""A spent-but-intact egg on a losing defender baits the shell break.

When a castle defender is losing and its egg has 0 uses left, the empty
shell still shows たまごをつかう greyed in the rescue menu, so nothing
is usable and the general just bleeds out (チコリ at 0 uses). The owner
tactic: pull the egg out (select the greyed row), deliberately take the
next hit to crack the shell, then おくのて becomes the only usable row;
after repelling, the eggless general grabs a 一発エッグ or goes for
repair. Castle defense only -- an attacker has no defensive need to
sacrifice the shell.
"""
from pathlib import Path

import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_survival import battle, menu


def memory(ally='チコリ', hp=8, uses=0, side='defense', castle='アルマムーン'):
    return {'chapter': 5, 'egg_uses': {ally: uses}, 'battle': {
        'ally': ally, 'enemy': 'チキータ', 'ally_hp': hp, 'enemy_hp': 80,
        'start_ally_hp': hp, 'start_enemy_hp': 80, 'side': side,
        'castle': castle, 'step': None, 'planned_cards': [],
        'cards_used': [], 'card_evidence_version': 1}}


def test_spent_egg_defender_baits_shell_break_at_critical_hp():
    """Losing defense, egg at 0 uses: select the greyed egg row once."""
    mem = memory()
    assert p.battle_menu_step(menu(labels=('たまごをつかう',)), mem) == [p.pad('a')]
    rescue = mem['battle']['survival']
    assert rescue['egg_break_bait'] is True
    assert rescue['egg_attempted'] is True
    assert any(r['decision'] == 'battle_egg_break_bait' for r in mem['_records'])


def test_bait_is_bounded_to_one_selection():
    """After the bait the flag blocks a repeat; exhaustion returns to melee."""
    mem = memory()
    assert p.battle_menu_step(menu(labels=('たまごをつかう',)), mem) == [p.pad('a')]
    # The bait's egg_pending waits out the selection fade, then the spent
    # shell offers nothing more to select -> exhausted melee.
    out = []
    for _ in range(6):
        out = p.battle_menu_step(menu(labels=('たまごをつかう',)), mem)
        if out == [p.pad('b')]:
            break
    assert out == [p.pad('b')]
    assert mem['battle']['survival']['exhausted'] is True


def test_bait_requires_castle_defense():
    """Attackers never bait the shell: the same losing frame stays on melee."""
    mem = memory(side='attack', castle=None)
    out = p.battle_menu_step(menu(labels=('たまごをつかう',)), mem)
    assert out == [p.pad('b')]
    assert not mem['battle']['survival'].get('egg_break_bait')


def test_bait_requires_critical_hp():
    """A healthy defender does not risk the shell on a whim."""
    mem = memory(hp=40)
    out = p.battle_menu_step(menu(labels=('たまごをつかう',)), mem)
    assert out == [p.pad('b')]
    assert not mem['battle']['survival'].get('egg_break_bait')


def test_usable_egg_takes_the_normal_path_not_the_bait():
    """With uses left the egg summons normally; no bait is recorded."""
    mem = memory(uses=4)
    assert p.battle_menu_step(menu(labels=('たまごをつかう',)), mem) == [p.pad('a')]
    rescue = mem['battle']['survival']
    assert rescue['egg_attempted'] is True
    assert not rescue.get('egg_break_bait')
