"""The give-up resets the miss counter by assignment (v422).

g1133 2026-11-05: `source_miss` for the boss order stayed at 5 for an hour, so
`_give_up_source` fired on every observation and the order never started
walking. The reset used `dict.pop`, which did not reach the persisted state
while plain assignments did (issue #3417), so the counter is now reset by
assignment.
"""
from docich.hanjuku.strategy import orders as O


def memory():
    return {'chapter': 6, 'tick': 100, '_records': [], 'source_miss': {'A:x:M1': 5},
            'garrison': {'オケアナ': ['トレビス']}, 'captured': ['オケアナ'],
            'world': {'sorties': {}}}


def order():
    return {'step': 'A:x:M1', 'source': 'オケアナ', 'target': 'ボス', 'general': 'トレビス',
            'cards': [], 'after': None, 'local_step': 'M1'}


def test_the_counter_is_left_at_zero_not_removed():
    mem = memory()
    O._give_up_source(mem, order())
    assert mem['source_miss']['A:x:M1'] == 0


def test_the_same_give_up_twice_still_resets():
    mem = memory()
    O._give_up_source(mem, order())
    mem['source_miss']['A:x:M1'] = 3
    O._give_up_source(mem, order())
    assert mem['source_miss']['A:x:M1'] == 0
