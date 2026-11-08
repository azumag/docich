"""The finished chapter's orders must not run in the next chapter (g1163 2026-11-09).

After the chapter-end save the starter party appears. The bot confirmed it at
04:02, but the chapter-4 `4-B2` site order still ran at 04:04 and the chapter
was only observed at 04:05. Dropping the finished chapter's route state when
the starter departs removes that window without guessing the next chapter
number.
"""
from docich.hanjuku.flows import starter
from docich.hanjuku.strategy import campaign
from docich.hanjuku_screen import Screen


def memory():
    return {'chapter': 4, 'tick': 100,
            'orders': {'4-B2': 'pending'},
            'chart_plan': {'orders': [{'step': '4-B2'}]},
            'sorties': {'4-B2': {'status': 'en_route', 'tick': 90}},
            'season_sites': {'四季王城(南東)': {'state': 'open', 'chapter': 4}},
            'captured': ['アルカ'], 'lost': [],
            'starter_party': {'chapter': 4, 'confirm_sent': True,
                              'selected': ['ゼウス', 'ヴィーナス', 'ココット']}}


def records(mem, decision):
    return [r for r in mem.get('_records') or [] if r.get('decision') == decision]


def test_finish_chapter_drops_the_orders_and_keeps_the_measured_chapter():
    mem = memory()
    campaign.finish_chapter(mem)
    assert mem['chapter'] == 4                     # still measured, never guessed
    assert not mem.get('orders') and not mem.get('sorties')
    assert not mem.get('chart_plan') and not mem.get('season_sites')
    assert not mem.get('captured')                 # the next map is measured afresh
    assert mem['chapter_transition_pending'] is True
    assert records(mem, 'chapter_scope_cleared')


def test_the_starter_departure_clears_the_finished_chapter():
    mem = memory()
    screen = Screen(lines=[], text='', kind='text', hand=None)
    starter.step(screen, mem, None)
    assert records(mem, 'starter_departed_observed')
    assert records(mem, 'chapter_scope_cleared')
    assert not mem.get('orders') and not mem.get('season_sites')
    assert mem['chapter'] == 4
