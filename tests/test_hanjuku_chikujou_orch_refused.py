"""Orchestrator's chikujou prompt branch records chikujou_refused for month skips.

v1293 recorded ``chikujou_refused`` inside ``month.chikujou_leftover``, but the
real refusal text ("これいじょうのぞうちくはできませんぞ!! どのしろを…") is
intercepted earlier by ``orchestrator._is_chikujou_prompt`` and closed with B.
Recording ``chikujou_refused`` in that branch lets ``_month_chikujou`` actually
skip reopening the refused candidate list for up to two months.
"""
from types import SimpleNamespace

from docich import hanjuku_policy as policy, hanjuku_screen
from docich.hanjuku.flows import month as month_flow
from docich.hanjuku_screen import Screen
from tests.test_hanjuku_chart_bot import decide, month_canvas


def test_orchestrator_chikujou_prompt_records_refused_and_skips_next_months(monkeypatch):
    sc = Screen(
        lines=[],
        hand=None,
        kind='text',
        header=None,
        text='アルマムーン3これいじょうのぞうちくはできませんぞ!!どのしろをぞうちくなさいますか?',
    )
    monkeypatch.setattr(hanjuku_screen, 'parse', lambda *a, **k: sc)
    mem = {'chapter': 9, 'captured': ['イソ', 'イデ！'], '_records': []}
    actions, state = decide(month_canvas(9999), {'policy': mem})
    assert actions == [policy.pad('b')]
    assert state['_records'][0]['decision'] == 'chikujou_leftover'

    pol = state['policy']
    assert pol['chikujou_refused']['skips'] == 0
    assert isinstance(pol['chikujou_refused']['castles'], list)

    month_screen = SimpleNamespace(
        kind='month_menu',
        header={'gold': 9999},
        selected='ちくじょう',
        options=[],
        lines=[],
        hand=None,
    )
    for expected_skips in (1, 2):
        shop = {'chikujou': 'check'}
        assert month_flow._month_chikujou(month_screen, pol, shop) is None
        assert shop['chikujou'] == 'skipped'
        assert pol['chikujou_refused']['skips'] == expected_skips
