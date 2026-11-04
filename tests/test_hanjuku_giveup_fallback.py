"""The source fallback only runs when the home castle can send (v423).

g1133 2026-11-05: `home_garrison` was empty while `source_miss` stayed at 5 for
an hour, so every observation gave the source up to a castle that could never
launch and the order never walked to the chart own source (issue #3417).
The give-up now runs only when the home castle has somebody to send.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.flows import map as M

HOME = chart.home_castle(6)


def memory(garrison):
    return {'chapter': 6, 'tick': 100, '_records': [], 'garrison': garrison}


def test_an_empty_home_cannot_send():
    assert M._home_can_send(memory({})) is False
    assert M._home_can_send(memory({HOME: []})) is False


def test_a_staffed_home_can_send():
    assert M._home_can_send(memory({HOME: ['どうし']})) is True
