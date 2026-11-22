"""An order naming a general this castle no longer holds cannot start (#4581).

g1190 2026-11-18 65-10: the chapter-11 base chart named キャロット for
アエンタール; he was long dead, the castle list was read, yet next_order kept
returning the order and no fresh order ran for hours.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart_adjust as chart_adjust
from docich.hanjuku.strategy import orders as o
from docich.hanjuku.state.regions import world as _world, plan as _plan_region


def memory(present):
    return {'chapter': 11, '_records': [], 'orders': {},
            'world': {'garrison': {'タタール': present}, 'sorties': {}},
            'plan': {'orders': {}}}


def order(general='キャロット'):
    return {'step': 'A:x:M1', 'general': general, 'source': 'タタール',
            'target': 'アエンタール', 'purpose': 'retake', 'cards': []}


def test_a_named_general_missing_from_the_castle_is_missing():
    mem = memory(['シフォン', 'ミモザ', 'コリアンダー'])
    assert o._source_spare_missing(order(), mem, allow_probe=True) is True


def test_the_named_general_present_is_not_missing():
    mem = memory(['シフォン', 'キャロット', 'ミモザ'])
    assert o._source_spare_missing(order(), mem, allow_probe=True) is False


def test_the_probe_sentinel_is_never_checked_by_name():
    mem = memory(['シフォン', 'ミモザ'])
    assert o._source_spare_missing(order(chart_adjust.PROBE_GENERAL), mem,
                                   allow_probe=True) is False

