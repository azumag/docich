"""Owner 2026-11-05: forget marches that can no longer arrive.

g1133 kept 46 overdue records, so every general stayed "out" forever and the
plan had no spare to send at the boss. A record past the bindable window is
dropped and its general goes back onto the source roster; the next castle
menu read replaces that guess with the measured list.
"""
from docich.hanjuku.strategy import orders as O


def memory(age, status='overdue', garrison=None):
    return {'chapter': 6, 'tick': 50000, '_records': [], 'orders': {},
            'garrison': {'アルマムーン': [], 'アブダレ': []} if garrison is None else garrison,
            'sorties': {'A:x:M1': {'general': 'ヘルメス', 'source': 'アルマムーン',
                                   'target': 'ボス', 'status': status,
                                   'tick': 50000 - age}},
            'launched_orders': {}}


def test_a_spent_record_is_dropped_and_its_general_returns():
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in mem['sorties']
    assert 'ヘルメス' in mem['garrison']['アルマムーン']
    assert [r for r in mem['_records'] if r['decision'] == 'sortie_record_expired']


def test_a_recent_overdue_record_is_kept():
    mem = memory(age=O.SPENT_SORTIE_TICKS - 1)
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in mem['sorties']
    assert mem['garrison']['アルマムーン'] == []


def test_a_general_already_placed_elsewhere_is_not_duplicated():
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1)
    mem['garrison']['アブダレ'] = ['ヘルメス']
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in mem['sorties']
    assert mem['garrison']['アルマムーン'] == []
    assert mem['garrison']['アブダレ'] == ['ヘルメス']


def test_an_unread_source_gets_the_general_back():
    # A castle whose list was never read is stored as None; the returned
    # general is a guess the next castle menu read replaces.
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1, garrison={'アルマムーン': None, 'アブダレ': []})
    O._expire_spent_sorties(mem)
    assert mem['garrison']['アルマムーン'] == ['ヘルメス']


def test_a_zombie_march_is_expired_with_the_overdue_ones():
    # g1133 2026-11-05: グレナデン stayed launched_unconfirmed for 5115
    # observations; a march cannot still be running past the window.
    mem = memory(age=O.SPENT_SORTIE_TICKS + 1, status='en_route')
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' not in mem['sorties']
    assert 'ヘルメス' in mem['garrison']['アルマムーン']


def test_a_recent_march_is_kept():
    mem = memory(age=O.SPENT_SORTIE_TICKS - 1, status='en_route')
    O._expire_spent_sorties(mem)
    assert 'A:x:M1' in mem['sorties']
