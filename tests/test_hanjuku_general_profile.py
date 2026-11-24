"""将軍の表示用プロフィール (#2364)。

gcgx shogun.html の9項目を、既存の静的データ (GENERAL_STATS / GENERAL_DEBUT /
GENERAL_EGGS) と、不足分だけを補う GENERAL_TRAITS から組み立てる。攻略サイトを
実行時に取得しない。未知の将軍は0で埋めず None を返す。
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_reference as reference
from docich.hanjuku_egg_reference import GENERAL_DEBUT, GENERAL_EGGS


def test_every_rostered_general_has_exactly_the_missing_display_fields():
    assert len(reference.GENERAL_STATS) == 128
    assert set(reference.GENERAL_TRAITS) == set(reference.GENERAL_STATS)
    assert set(GENERAL_DEBUT) <= set(reference.GENERAL_STATS)
    for name, (counter, aptitude, personality, hobby) in reference.GENERAL_TRAITS.items():
        assert type(counter) is int and counter in (0, 25, 50, 75, 100), name
        # 得意は季節・地形をそのまま保持する。'－' は「なし」の空文字で、
        # 不明の None とは区別する。
        assert isinstance(aptitude, str), name
        assert aptitude in ('', '森', '山', '平地', '水中', '洞窟', '城攻', '城守',
                            '春１', '春２', '夏１', '夏２', '秋１', '冬１', '冬２'), name
        assert isinstance(personality, str) and personality, name
        assert isinstance(hobby, str) and hobby, name


def test_the_profile_combines_the_nine_requested_fields_with_max_hp():
    profile = reference.general_profile('キッシュ')
    assert profile == {
        'name': 'キッシュ', 'max_hp': 26, 'combat': 5, 'counter': 50,
        'thought': 2, 'egg_type': 'スーパー', 'egg_bonus': '0',
        'aptitude': '洞窟', 'debut': 1, 'personality': '身勝手',
        'hobby': 'カルトクイズ', 'has_egg': True,
    }
    assert profile['max_hp'] == 26 and profile['debut'] == GENERAL_DEBUT['キッシュ']
    assert profile['has_egg'] == GENERAL_EGGS['キッシュ'][0]
    # 季節付きの卵補正と得意を失わない。
    assert reference.general_profile('アルファルファ')['egg_bonus'] == '春+1'
    assert reference.general_profile('アルファルファ')['aptitude'] == '森'
    assert reference.general_profile('タルタル')['egg_bonus'] == '夏+1'
    assert reference.general_profile('ヒーローもどき') is None


def test_unknown_and_missing_inputs_are_not_zero_filled():
    assert reference.general_profile(None) is None
    assert reference.general_profile('') is None
    assert reference.general_profile('未知将軍') is None
    assert reference.general_profile(123) is None


def test_debut_and_egg_type_come_from_the_existing_tables():
    for name in ('しゅじんこう', 'ピオーネ', 'ポワソン'):
        profile = reference.general_profile(name)
        assert profile['debut'] == GENERAL_DEBUT[name], name
        assert profile['egg_type'] == reference.GENERAL_STATS[name][3], name
        assert profile['egg_bonus'] == reference.GENERAL_STATS[name][2], name
        assert profile['thought'] == GENERAL_EGGS[name][1], name
    # だいじんは将軍一覧にいるが初登場話数を持たない (イベントNPC)。
    assert reference.general_profile('だいじん')['debut'] is None
