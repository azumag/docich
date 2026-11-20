"""A boss read in the battle panel authorizes carried boss cards.

Owner 2026-11-21: 卵を使われる前に切り札バルムンク×2を使うのが正解.
The ch9 boss is met by a field ambush (no charted boss order); the panel's
ハデス reading must still unlock the carried kit.
"""
from docich.hanjuku.judgment import battle as J


def test_the_chapter_boss_name_in_the_panel_allows_the_kit():
    cur = {'enemy': 'ハデス', 'step': 'A', 'ally': 'ポセイドン'}
    assert J._boss_tactics_allowed({'chapter': 9}, cur) is True


def test_an_ordinary_enemy_does_not():
    cur = {'enemy': 'コリアンダー', 'step': 'A', 'ally': 'ポセイドン'}
    assert J._boss_tactics_allowed({'chapter': 9}, cur) is False
