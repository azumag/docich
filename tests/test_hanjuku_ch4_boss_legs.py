"""owner 2026-11-28: 第4話のボス（四季王城 x4）はアルマムーンから直接行けない。"""
from docich.hanjuku.strategy import routes


def _mem(chapter=4):
    return {'chapter': chapter, 'world': {'garrison': {}}, 'captured': ['アルマムーン', 'アルカ']}


def test_armamoon_cannot_reach_any_chapter_four_boss_directly():
    for boss in ('四季王城(北西)', '四季王城(北東)', '四季王城(南西)', '四季王城(南東)'):
        assert routes.allowed(_mem(), 'アルマムーン', boss) is False, boss


def test_another_castle_can_reach_the_chapter_four_boss():
    assert routes.allowed(_mem(), 'アルカ', '四季王城(北西)') is True
    assert routes.allowed(_mem(), 'ネクスドリア', '四季王城(南東)') is True


def test_non_boss_legs_stay_allowed():
    assert routes.allowed(_mem(), 'アルマムーン', 'ビクトリア') is True


def test_chapter_one_rule_is_unchanged():
    mem = _mem(1)
    assert routes.allowed(mem, 'アルマムーン', 'スペンソニア') is False
    assert routes.allowed(mem, 'アルマムーン', 'けっかい') is False
