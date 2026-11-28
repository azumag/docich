"""owner 2026-11-28: 卵の残数が未読でも、実メニューの白行があれば卵を優先する。"""
from docich.hanjuku.judgment import battle as rules


def _battle(**over):
    base = {'ally_hp': 7, 'enemy_hp': 82, 'enemy': '四季王', 'castle': '四季王城(北東)'}
    base.update(over)
    return base


def test_white_egg_row_licenses_the_egg_when_the_bot_is_dying():
    # 卵の残数は未読だが、実メニューで「たまごをつかう」の白行を観測している。
    assert rules._dying_with_egg({'chapter': 4}, _battle(egg_white_seen=True)) is True


def test_unread_egg_without_the_white_row_stays_fail_closed():
    assert rules._dying_with_egg({'chapter': 4}, _battle()) is False


def test_a_healthy_battle_does_not_switch_to_the_egg():
    battle = _battle(ally_hp=80, enemy_hp=60, egg_white_seen=True)
    assert rules._dying_with_egg({'chapter': 4}, battle) is False
