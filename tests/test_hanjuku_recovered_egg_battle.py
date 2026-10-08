"""An observed healed egg reaches the existing emergency battle decision."""
import pytest

from docich import hanjuku_house as house, hanjuku_policy as policy
from docich.hanjuku_screen import parse
from test_hanjuku_house import status
from test_hanjuku_egg_timing import memory
from test_hanjuku_survival import menu


@pytest.mark.parametrize('uses', [1, 4, 5])
def test_observed_recovery_removes_only_zero_gate_before_emergency_defense(uses):
    mem = memory(3, 20, uses=0)
    mem.update(month='1-6', tick=20)
    ally = mem['battle']['ally']
    assert policy._own_egg_empty(mem, mem['battle'])
    before = mem['egg_uses'][ally]
    assert before == 0
    # Read the actor's status; do not synthesize an Angelin effect from a selection.
    info = house._observe_status(parse(status(ally, f'エラベルエッグ{uses}', hp=3)), mem)
    assert info is not None and info['uses'] == uses
    assert not policy._own_egg_empty(mem, mem['battle'])
    assert policy.battle_menu_step(menu(), mem) == [policy.pad('a')]
    assert mem['battle']['survival']['egg_attempted'] is True
    assert mem['egg_uses'][ally] == uses  # selecting an egg is not a consumption receipt
    assert not mem['battle']['cards_used']


def test_unread_cursor_still_cannot_confirm_the_recovered_emergency_egg():
    mem = memory(3, 20, uses=0)
    mem.update(month='1-6', tick=20)
    ally = mem['battle']['ally']
    assert house._observe_status(parse(status(ally, 'エラベルエッグ5', hp=3)), mem)
    assert policy.battle_menu_step(menu(selected=None), mem) != [policy.pad('a')]
    assert not mem['battle']['survival']['egg_attempted']
    assert mem['egg_uses'][ally] == 5
