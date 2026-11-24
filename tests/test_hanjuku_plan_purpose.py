"""A plan move keeps its purpose so the reserve rule can exempt it (v405).

g1133 2026-11-05: after the boss waves were sent, the mechanical plan offered
two staging moves (ライム グルーポフ→オケアナ, ゼウス バビラリー→カバルッサ).
The plan carried no ``purpose``, so ``_reserve_source_guard`` treated them as
plan attacks, ``_source_spare_missing`` demanded a second general, and both
moves stayed pending for an hour: the plan never ran and no new boss wave was
formed. The purpose now survives the adjusted-chart normalizer.
"""
import pytest

from docich import hanjuku_chart_adjust as adj
from docich.hanjuku.strategy import orders as O


def doc(purpose):
    order = {'step': 'M1', 'general': 'ライム', 'source': 'グルーポフ',
             'target': 'オケアナ', 'cards': [], 'after': None}
    if purpose is not None:
        order['purpose'] = purpose
    return {'schema': adj.SCHEMA, 'chapter': 6, 'request_id': 'abcd1234abcd1234',
            'orders': [order]}


def test_the_adjusted_plan_keeps_the_move_purpose():
    out = adj.validate(doc('move'))
    assert out['orders'][0]['purpose'] == 'move'


def test_an_order_without_a_purpose_stays_untyped():
    # Untyped orders keep their old shape: no purpose key at all.
    out = adj.validate(doc(None))
    assert 'purpose' not in out['orders'][0]


def test_an_unknown_purpose_is_refused():
    with pytest.raises(ValueError):
        adj.validate(doc('teleport'))


def memory():
    return {'chapter': 6, 'tick': 100, 'captured': ['グルーポフ', 'オケアナ', 'カバルッサ'],
            'garrison': {'グルーポフ': ['ライム'], 'オケアナ': [], 'カバルッサ': []}}


def test_a_move_from_a_single_general_castle_is_blocked_by_the_new_minimum():
    order = {'step': 'A:x:M1', 'general': 'ライム', 'source': 'グルーポフ',
             'target': 'オケアナ', 'purpose': 'move'}
    assert O._source_spare_missing(order, memory(), allow_probe=True) is True


def test_the_same_order_without_a_purpose_is_held():
    order = {'step': 'A:x:M1', 'general': 'ライム', 'source': 'グルーポフ',
             'target': 'カバルッサ', 'purpose': None}
    assert O._source_spare_missing(order, memory(), allow_probe=True) is True
