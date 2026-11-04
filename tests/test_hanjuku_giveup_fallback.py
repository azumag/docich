"""An ineffective source give-up must not return no input forever (v418).

g1133 2026-11-05: `_give_up_source` popped `source_miss` and set
`source_override`, yet the persisted policy kept `source_miss` at 5 and never
grew the override (453 identical records, issue #3417). The give-up therefore
fired on every observation and returned no input, so `order_start` stayed at
zero for 25 minutes. When the give-up demonstrably did not take, the map step
now closes the open panel and observes again instead of idling.
"""
from docich.hanjuku.flows import map as M


def test_the_ineffective_giveup_closes_the_panel():
    mem = {'chapter': 6, 'tick': 100, '_records': [],
           'active': 'A:x:M1', 'source_miss': {'A:x:M1': 5}, 'picked': []}
    assert M._source_giveup_stuck(mem, 'A:x:M1') is True


def test_a_clean_state_is_not_stuck():
    mem = {'chapter': 6, 'tick': 100, '_records': [], 'source_miss': {}}
    assert M._source_giveup_stuck(mem, 'A:x:M1') is False
