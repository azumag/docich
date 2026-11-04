"""A source fallback survives a plan rebuild (v416).

g1133 2026-11-05: the boss order could not open the castle menu at オケアナ, so
`_give_up_source` moved it to the home castle - and the next plan rebuild gave
the same content (`M256898`) a new execution id (`A:<rid>:M256898`), so the
override no longer matched and the bot aimed at オケアナ again. 96 fallbacks in
ten minutes. The override is now also kept under the content name.
"""
from docich.hanjuku.strategy import orders as O


def order(step='A:abcd1234:M1', source='オケアナ'):
    return {'step': step, 'source': source, 'target': 'ボス', 'general': 'トレビス',
            'cards': [], 'after': None, 'local_step': 'M1'}


def test_an_execution_id_override_wins():
    mem = {'source_override': {'A:abcd1234:M1': 'アルマムーン', 'M1': 'トウトウ'}}
    assert O._source(order(), mem) == 'アルマムーン'


def test_a_content_override_applies_to_a_new_execution_id():
    mem = {'source_override': {'M1': 'アルマムーン'}}
    assert O._source(order('A:9999ffff:M1'), mem) == 'アルマムーン'


def test_without_an_override_the_chart_source_stays():
    assert O._source(order(), {'source_override': {}}) == 'オケアナ'
