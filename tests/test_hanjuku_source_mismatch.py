"""Only the same wrong castle counts as the same source failure (v417).

g1133 2026-11-05: aiming at オケアナ the bot opened the home castle instead and
corrected its position three times, so the source was given up and the order
was re-aimed. Opening a *different* castle each time is the position being
corrected, not the same failure; only a repeated identical mismatch should
accumulate to the give-up limit.
"""
from docich.hanjuku.flows import deploy as D


def test_the_same_wrong_castle_accumulates():
    mem = {}
    assert D._count_source_mismatch(mem, 'A:x:M1', 'アルマムーン') == 1
    assert D._count_source_mismatch(mem, 'A:x:M1', 'アルマムーン') == 2
    assert D._count_source_mismatch(mem, 'A:x:M1', 'アルマムーン') == 3


def test_a_different_castle_restarts_the_count():
    mem = {}
    assert D._count_source_mismatch(mem, 'A:x:M1', 'アルマムーン') == 1
    assert D._count_source_mismatch(mem, 'A:x:M1', 'トウトウ') == 1
    assert mem['source_miss']['A:x:M1'] == 1


def test_the_count_is_per_order():
    mem = {}
    assert D._count_source_mismatch(mem, 'A:x:M1', 'アルマムーン') == 1
    assert D._count_source_mismatch(mem, 'A:x:M2', 'アルマムーン') == 1
