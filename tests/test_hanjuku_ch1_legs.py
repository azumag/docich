"""owner 2026-11-27: 第1話の城は直線でつながらず、実測 leg だけが行軍可能。"""
from docich.hanjuku.strategy import routes


def _mem(chapter=1):
    return {'chapter': chapter, 'world': {'garrison': {}}}


def test_armamoon_to_spensonia_is_not_a_leg():
    # 間に ジョンリギ があるため直接は行けない。
    assert routes.allowed(_mem(), 'アルマムーン', 'スペンソニア') is False


def test_armamoon_to_jonrigi_is_a_leg():
    assert routes.allowed(_mem(), 'アルマムーン', 'ジョンリギ') is True


def test_jonrigi_and_armamoon_cannot_reach_the_boss_directly():
    assert routes.allowed(_mem(), 'ジョンリギ', 'けっかい') is False
    assert routes.allowed(_mem(), 'アルマムーン', 'けっかい') is False


def test_spensonia_reaches_the_boss_directly():
    assert routes.allowed(_mem(), 'スペンソニア', 'けっかい') is True


def test_chapter_three_still_allows_its_boss_leg():
    assert routes.allowed(_mem(3), 'パタゴーヌ', 'ボス') is True
