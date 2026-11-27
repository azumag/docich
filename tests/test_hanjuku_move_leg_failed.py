"""owner 2026-11-27: 月内に失敗した移動 leg は再計画しない。"""
from docich.hanjuku.strategy import replan


def test_a_failed_move_leg_is_not_replanned_in_the_same_month():
    mem = {'chapter': 1, 'month': '2-11',
           'move_leg_failed': {'アルマムーン>スペンソニア': '2-11'}}
    moves = [{'general': 'ア', 'source': 'アルマムーン', 'target': 'スペンソニア'},
             {'general': 'イ', 'source': 'キカンドン', 'target': 'ゴーメン'}]
    kept = replan._filter_failed_move_legs(mem, moves)
    assert [f"{m['source']}>{m['target']}" for m in kept] == ['キカンドン>ゴーメン']


def test_a_leg_failed_in_another_month_is_planned_again():
    mem = {'chapter': 1, 'month': '3-1',
           'move_leg_failed': {'アルマムーン>スペンソニア': '2-11'}}
    moves = [{'general': 'ア', 'source': 'アルマムーン', 'target': 'スペンソニア'}]
    assert replan._filter_failed_move_legs(mem, moves) == moves


def test_without_a_record_moves_are_unchanged():
    moves = [{'general': 'ア', 'source': 'アルマムーン', 'target': 'スペンソニア'}]
    assert replan._filter_failed_move_legs({'chapter': 1, 'month': '2-11'}, moves) == moves
