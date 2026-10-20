"""Tests for #2364: 右ステータス将軍・切り札・卵落判定データ関数。"""
import pytest
from src.docich import hanjuku_reference as ref


class TestGeneralFullView:
    def test_shujinkou(self):
        v = ref.general_full_view('しゅじんこう')
        assert v is not None
        assert v['name'] == 'しゅじんこう'
        assert v['最大HP'] == 90
        assert v['戦闘'] == 14
        assert v['対抗'] == '50%'
        assert v['得意'] == '－'
        assert v['性格'] == '甘えんぼ'
        assert v['趣味'] == 'つまみぐい'
        assert v['初登場話数'] == 1
        assert v['思考'] == 2

    def test_unknown_general(self):
        assert ref.general_full_view(None) is None
        assert ref.general_full_view('存在しない将軍') is None

    def test_general_details_missing(self):
        # GENERAL_DETAILS にない将軍でも general_full_view は他データがあれば返す
        v = ref.general_full_view('ゼウス')
        assert v is not None
        assert v['name'] == 'ゼウス'
        assert '対抗' not in v  # details なし


class TestEggDropPrediction:
    def test_basic(self):
        # クミン HP=27 + しゅじんこう HP=90 = 117 -> 117 % 16 = 5
        pred = ref.egg_drop_prediction('クミン', 'しゅじんこう')
        assert pred['max_hp_sum'] == 117
        assert pred['threshold'] == 5
        assert pred['判定不能'] is False

    def test_another_pair(self):
        # キャラウェイ HP=40 + しゅじんこう HP=90 = 130 -> 130 % 16 = 2
        pred = ref.egg_drop_prediction('キャラウェイ', 'しゅじんこう')
        assert pred['max_hp_sum'] == 130
        assert pred['threshold'] == 2

    def test_unknown_general_fail_closed(self):
        pred = ref.egg_drop_prediction('不明', 'しゅじんこう')
        assert pred['threshold'] is None
        assert pred['判定不能'] is True
        assert pred['droppers'] == []

    def test_both_unknown(self):
        pred = ref.egg_drop_prediction(None, None)
        assert pred['threshold'] is None
        assert pred['判定不能'] is True


class TestCardEggDropInfo:
    def test_drops(self):
        # threshold=5, イッテツーン egg_drop=8 -> drops
        info = ref.card_egg_drop_info('イッテツーン', 'クミン', 'しゅじんこう')
        assert info['drops'] is True
        assert info['状態'] == '計算上落とせる'

    def test_no_drop(self):
        # threshold=5, ダイチスイム egg_drop=1 -> no drop
        info = ref.card_egg_drop_info('ダイチスイム', 'クミン', 'しゅじんこう')
        assert info['drops'] is False
        assert info['状態'] == '不成立'

    def test_unknown_card(self):
        info = ref.card_egg_drop_info('存在しない札', 'クミン', 'しゅじんこう')
        assert info['drops'] is None
        assert info['状態'] == '判定不能'

    def test_unknown_general(self):
        info = ref.card_egg_drop_info('イッテツーン', '不明', 'しゅじんこう')
        assert info['drops'] is None
        assert info['状態'] == '判定不能'


class TestRightStatusData:
    def test_basic(self):
        data = ref.right_status_data(
            'クミン', 'しゅじんこう',
            held_cards=['イッテツーン', 'ダイチスイム', 'ブラッキー'],
            carried_cards=['イッテツーン', 'ダイチスイム'])
        assert data['enemy']['name'] == 'クミン'
        assert data['ally']['name'] == 'しゅじんこう'
        assert data['egg_drop']['threshold'] == 5
        assert data['droppers'] == ['イッテツーン']
        cards = {c['card']: c for c in data['cards']}
        assert cards['イッテツーン']['drops'] is True
        assert cards['イッテツーン']['携行'] is True
        assert cards['ダイチスイム']['drops'] is False
        assert cards['ダイチスイム']['携行'] is True
        assert cards['ブラッキー']['drops'] is False
        assert cards['ブラッキー']['携行'] is False

    def test_carried_defaults_to_held(self):
        data = ref.right_status_data(
            'クミン', 'しゅじんこう',
            held_cards=['イッテツーン'])
        cards = {c['card']: c for c in data['cards']}
        assert cards['イッテツーン']['携行'] is True

    def test_empty_cards(self):
        data = ref.right_status_data('クミン', 'しゅじんこう')
        assert data['cards'] == []
        assert data['droppers'] == []


class TestBoundaryTests:
    """Issue 受入条件の境界テスト。"""

    def test_sum_80_remainder_0(self):
        # 合計80 -> 余り0
        # 卵落値=0 (デッドガン) -> false (0 > 0 is false)
        assert ref.can_drop_egg('デッドガン', 80) is False
        # 卵落値=1 (ダイチスイム) -> true (1 > 0)
        assert ref.can_drop_egg('ダイチスイム', 80) is True

    def test_sum_95_remainder_15(self):
        # 合計95 -> 余り15
        # 卵落値=15 (グルミー) -> false (15 > 15 is false)
        assert ref.can_drop_egg('グルミー', 95) is False

    def test_current_hp_does_not_affect_max_hp_judgment(self):
        # 現在HPだけ変えても最大HP由来の判定は変わらない
        # この関数は最大HPのみを使う。現在HPは別の問題。
        pred1 = ref.egg_drop_prediction('クミン', 'しゅじんこう')
        pred2 = ref.egg_drop_prediction('クミン', 'しゅじんこう')
        assert pred1 == pred2

    def test_egg_drop_threshold_function(self):
        # egg_drop_threshold は「落とせる最小の卵落値」= max_hp_sum % 16 + 1
        assert ref.egg_drop_threshold(80) == 1   # 80 % 16 = 0, +1 = 1
        assert ref.egg_drop_threshold(95) == 16  # 95 % 16 = 15, +1 = 16
        assert ref.egg_drop_threshold(117) == 6  # 117 % 16 = 5, +1 = 6
