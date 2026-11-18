"""The purchase confirmation answers instead of holding.

g1190 2026-11-18 63-5: 「1こで 50Gに なりまんな! よろしでっか?」 with
うむッ!/いかんッ! sat static for minutes: the shop quantity step waited on
its own overlay while the confirmation never got an answer.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku import orchestrator as O

CONFIRM = 'せんかいくくピオーネ2 1こで50Gになりまんな!よろしいでっか?うむッ!いかんッ!'


def test_the_purchase_confirmation_answers_a():
    """The hand sits on うむッ! in the measured frame, so A accepts it."""
    assert O._is_quantity_confirm('shop_quantity', CONFIRM) is True


def test_a_camp_dialog_without_the_choice_buttons_is_not_the_confirm():
    assert O._is_quantity_confirm('shop_quantity', 'いどう ステータス キャンプ きかん よろしいでっか') is False


def test_other_screens_are_not_the_purchase_confirmation():
    assert O._is_quantity_confirm('text', CONFIRM) is False
    assert O._is_quantity_confirm('shop_quantity', 'しょうぐんぼしゅう') is False
