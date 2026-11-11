"""Chapter 9 retakes of the next road castle go out as waves too.

Owner 2026-11-11: 移動し終えたわけじゃないのに画面ずっと放置していたのは何？
g1163 14:12:19-14:14:41 sat on the map: seven retakes of タオ were planned from
ハヤク, but ``_retake_reserved`` held them all while シナモン's retake was still
walking. v660 opened the planner to CORRIDOR_WAVE_INFLIGHT expeditions at the
next road castle; the execution gate now follows it.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.strategy import orders as O
from docich.hanjuku.strategy import replan

ROAD = chart.corridor(9)
FRONT, NEXT = ROAD[1], ROAD[2]


def _mem(chapter=9, marching=1):
    sorties = {f'I:r{i}': {'general': g, 'source': FRONT, 'target': NEXT, 'purpose': 'retake',
                           'status': 'en_route', 'tick': 1000 + i}
               for i, g in enumerate(['シナモン', 'ミント', 'ライム'][:marching])}
    return {'chapter': chapter, 'tick': 1100, 'captured': [FRONT], 'lost': [NEXT], 'orders': {},
            'garrison': {FRONT: ['ローズマリー', 'ベーグル']}, 'sorties': sorties, '_records': []}


def _order():
    return {'step': 'I:x:1', 'general': 'ローズマリー', 'source': FRONT, 'target': NEXT,
            'purpose': 'retake', 'cards': [], 'after': None, 'note': 't'}


def _reserved(mem):
    with chart.map_context(mem):
        return O._retake_reserved(_order(), mem)


def test_one_marching_retake_no_longer_holds_the_next_wave():
    assert _reserved(_mem(marching=1)) is False


def test_the_wave_cap_still_holds_further_retakes():
    assert _reserved(_mem(marching=replan.CORRIDOR_WAVE_INFLIGHT)) is True


def test_other_chapters_keep_one_retake_at_a_time():
    mem = _mem(chapter=8)
    mem['captured'] = ['しらかわ']
    mem['lost'] = ['ながはま']
    mem['sorties']['I:r0'].update(source='しらかわ', target='ながはま')
    order = {**_order(), 'source': 'しらかわ', 'target': 'ながはま'}
    with chart.map_context(mem):
        assert O._retake_reserved(order, mem) is True
