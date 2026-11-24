"""A kept mechanical plan must gain the hero-hub permission on rebuild.

g1190 2026-11-18 15:19 (#4546): the stored hero order (local_step Ma4237a)
predated the ``hero_hub_move`` propagation, so the rebuild matched its local
steps, kept the plan, and the deploy flow refused the hero again
(ロックフォール departed instead of どうし).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku.strategy import replan


def stored():
    return {'source': 'mechanical', 'orders': [
        {'local_step': 'Ma4237a', 'general': 'どうし', 'purpose': 'move'},
        {'local_step': 'M1', 'general': 'キール', 'purpose': 'move'}]}


def rebuilt():
    return [{'step': 'Ma4237a', 'local_step': 'Ma4237a', 'general': 'どうし',
             'purpose': 'move', 'hero_hub_move': True},
            {'step': 'M1', 'local_step': 'M1', 'general': 'キール', 'purpose': 'move'}]


def test_the_kept_plan_gains_the_flag():
    plan = stored()
    assert replan._carry_hero_permission(plan, rebuilt()) is True
    assert plan['orders'][0]['hero_hub_move'] is True
    assert 'hero_hub_move' not in plan['orders'][1]


def test_an_already_flagged_plan_is_unchanged():
    plan = stored()
    plan['orders'][0]['hero_hub_move'] = True
    assert replan._carry_hero_permission(plan, rebuilt()) is False
