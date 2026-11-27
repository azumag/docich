"""owner 2026-11-27: 守備を残せず失敗した城は、その月の出撃元から外す。"""
from docich.hanjuku.strategy import replan


def test_a_depleted_source_is_dropped_this_month():
    mem = {'chapter': 1, 'month': '2-11',
           'sortie_source_depleted': {'キカンドン': '2-11'}}
    assert replan._drop_depleted_sources(mem, {'キカンドン', 'アルマムーン'}) == {'アルマムーン'}


def test_every_source_depleted_keeps_the_original_set():
    mem = {'chapter': 1, 'month': '2-11',
           'sortie_source_depleted': {'キカンドン': '2-11'}}
    assert replan._drop_depleted_sources(mem, {'キカンドン'}) == {'キカンドン'}


def test_a_depletion_from_another_month_is_ignored():
    mem = {'chapter': 1, 'month': '3-1',
           'sortie_source_depleted': {'キカンドン': '2-11'}}
    assert replan._drop_depleted_sources(mem, {'キカンドン'}) == {'キカンドン'}
