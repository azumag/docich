"""Deterministic boss planning, stale-garrison probing, and map activation.

When every normal castle in Chapter 4 is captured, the 四季王 sites run from
whichever reachable owned castle holds egg-carrying companions (never the hero).
If every known castle looks empty/singleton while ``country_general_count`` is
larger, emit a read-only ``roster_probe`` for the stalest reachable castle so
the real army roster is rediscovered before planning boss sorties.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator as O
from docich.hanjuku.flows import deploy as D, map as M
from docich.hanjuku.strategy import orders as ORD, replan
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen


def _memory():
    chapter = 4
    home = chart.home_castle(chapter)
    normal = [c for c in chart.castles(chapter) if not chart.is_boss_castle(c) and c != home]
    return {'chapter': chapter, 'captured': normal, 'tick': 280000,
            '_records': [], 'plan': {'orders': {}}, 'world': {'garrison': {}, 'garrison_tick': {}}}


def test_ch4_boss_plan_drops_completed_conquest_and_closed_sites():
    mem = _memory()
    mem['season_sites'] = {'四季王城(北西)': {'chapter': 4, 'state': 'closed'}}
    mem['plan']['orders'] = {'4-A1': 'pending', '4-A2': 'failed', '4-C1': 'failed'}
    mem['plan']['source_override'] = {'4-B1': 'アルマムーン'}
    mem['world']['garrison'] = {
        'アルマムーン': [],
        'マラコビア': ['どうし', 'アルファルファ', 'ゼウス'],
    }
    mem['egg_types'] = {'アルファルファ': 'スーパーエッグ', 'ゼウス': 'ワンダーエッグ'}

    assert replan._mechanical_boss_plan(mem, 'rid') is True
    plan = mem['plan']['chart_plan']
    assert plan['source'] == 'mechanical'
    steps = [o['step'] for o in plan['orders']]
    assert steps == ['4-B1', '4-B2', '4-B3']
    assert mem['plan']['orders']['4-A1'] == 'done'
    assert all(mem['plan']['orders'][s] == 'pending' for s in steps)
    assert '4-B1' not in mem['plan']['source_override']


def test_ch4_mechanical_boss_plan_waits_until_every_normal_castle_is_owned():
    mem = _memory()
    mem['captured'] = []
    assert replan._mechanical_boss_plan(mem, 'rid') is False
    assert not mem['plan'].get('chart_plan')


def test_ch4_mechanical_boss_plan_settles_launched_move_only_on_roster_arrival():
    mem = _memory()
    # Non-move conquered orders settle to 'done' once all normal castles are owned.
    mem['plan']['orders'] = {'4-A1': 'launched'}
    mem['world']['garrison'] = {'ミヒラギアン': ['トレビス']}
    assert replan._mechanical_boss_plan(mem, 'rid') is True
    assert mem['plan']['orders']['4-A1'] == 'done'

    # Chapter 5 still has non-site boss moves; those settle only when actor is at target.
    mem5 = {'chapter': 5, 'captured': [c for c in chart.castles(5)
                                        if not chart.is_boss_castle(c)
                                        and c != chart.home_castle(5)],
            'tick': 1000, '_records': [],
            'plan': {'orders': {'5-B1': 'pending'}},
            'world': {'garrison': {chart.home_castle(5): ['どうし']}}}
    if any(chart.is_boss_castle(o.get('target')) for o in chart.orders(5)):
        assert replan._mechanical_boss_plan(mem5, 'rid') is True


def test_field_map_activates_only_ready_mechanical_plan_steps():
    # Unready 4-B1 (known-empty castles, no companions) must NOT be forced active.
    unready_mem = _memory()
    unready_mem['world']['garrison'] = {'アルマムーン': [], 'ミヒラギアン': ['トレビス']}
    unready_mem['plan']['orders'] = {'4-B1': 'pending'}
    unready_mem['plan']['chart_plan'] = {
        'source': 'mechanical',
        'orders': [next(o for o in chart.orders(4) if o['step'] == '4-B1')],
    }
    assert O.plan_starved_rearm(unready_mem, 'field', 'map') is False
    assert unready_mem['plan'].get('active') is None

    # Ready 4-B1 with companions at マラコビア (via source_override or resolved source) activates.
    ready_mem = _memory()
    ready_mem['world']['garrison'] = {'マラコビア': ['どうし', 'アルファルファ', 'ゼウス']}
    ready_mem['egg_types'] = {'アルファルファ': 'スーパーエッグ', 'ゼウス': 'ワンダーエッグ'}
    ready_mem['plan']['orders'] = {'4-B1': 'pending'}
    ready_mem['plan']['source_override'] = {'4-B1': 'マラコビア'}
    ready_mem['plan']['chart_plan'] = {
        'source': 'mechanical',
        'orders': [next(o for o in chart.orders(4) if o['step'] == '4-B1')],
    }
    assert O.plan_starved_rearm(ready_mem, 'field', 'map') is True
    assert ready_mem['plan']['active'] == '4-B1'
    assert ready_mem['_records'][-1]['decision'] == 'plan_map_activate'

    # Event phase never activates a plan.
    ready_mem['plan']['active'] = None
    assert O.plan_starved_rearm(ready_mem, 'event', 'map') is False
    assert ready_mem['plan']['active'] is None


def test_stale_garrison_emits_roster_probe_and_activates_on_field_map():
    mem = _memory()
    mem['country_general_count'] = {'count': 32}
    mem['season_sites'] = {'四季王城(北西)': {'chapter': 4, 'state': 'closed'}}
    mem['world'] = {
        'garrison': {
            'アルカ': ['セルリアク'],
            'アルマムーン': [],
            'ネクスドリア': [],
            'ビクトリア': ['ヘラ'],
            'フヌイヌム': ['ポワソン'],
            'マラコビア': ['キッシュ'],
            'ミヒラギアン': ['トレビス'],
        },
        'garrison_tick': {
            'アルカ': 96844,
            'アルマムーン': 279990,
            'ネクスドリア': 109316,
            'フヌイヌム': 236147,
            'マラコビア': 104621,
            'ミヒラギアン': 268427,
        },
    }
    cands = replan.interim_candidates(mem, allow_sole=False)
    assert list(cands) == ['probe_stuck_1']
    probe = cands['probe_stuck_1']
    # ビクトリア has no garrison_tick so it is stalest, reachable to open sites.
    assert probe['source'] == 'ビクトリア'
    assert probe['source_probe'] is True and probe['roster_probe'] is True

    # source_probe + roster_probe can open even an empty-recorded castle (ネクスドリア).
    empty_probe = dict(probe, step='I:test:probe', source='ネクスドリア')
    assert ORD._source_list_open(empty_probe, mem) is True

    # Once マラコビア reveals egg companions (and castles are staffed),
    # probing stops immediately even though total (32) > located.
    mem['world']['garrison']['アルマムーン'] = ['タルタル']
    mem['world']['garrison']['ネクスドリア'] = ['ライム']
    mem['world']['garrison']['マラコビア'] = ['どうし', 'アルファルファ', 'ゼウス']
    mem['egg_types'] = {'アルファルファ': 'スーパーエッグ', 'ゼウス': 'ワンダーエッグ'}
    mem['world']['garrison_tick']['マラコビア'] = 280000
    assert 'probe_stuck_1' not in replan.interim_candidates(mem, allow_sole=False)


def test_reanchor_uses_active_orders_source():
    mem = {'chapter': 4, 'nav_search': True, 'tick': 1000, '_records': [],
           'plan': {'active': '4-B1', 'orders': {'4-B1': 'pending'},
                    'chart_plan': {'source': 'mechanical', 'orders': [
                        {'step': '4-B1', 'source': 'ミヒラギアン', 'general': 'どうし',
                         'target': '四季王城(南西)', 'purpose': 'attack', 'cards': []}]}}}
    assert M._start_reanchor_jump(mem) == [M.pad('x')]
    assert mem['castle_jump']['source'] == 'ミヒラギアン'
    assert mem['_records'][-1]['decision'] == 'cursor_reanchor_open'


def test_source_castle_mismatch_records_status_panel_garrison():
    mem = {'chapter': 4, 'tick': 280050, '_records': [],
           'plan': {'active': '4-B1', 'orders': {'4-B1': 'pending'}, 'source_override': {'4-B1': 'アルマムーン'},
                    'chart_plan': {'source': 'mechanical', 'orders': [
                        {'step': '4-B1', 'source': 'ミヒラギアン', 'general': 'どうし',
                         'target': '四季王城(南西)', 'purpose': 'attack', 'cards': []}]}}}
    order = {'step': '4-B1', 'source': 'ミヒラギアン', 'target': '四季王城(南西)'}
    lines = [
        TextLine(95, tuple((144 + 8 * i, ch) for i, ch in enumerate('アルファルファ'))),
        TextLine(111, tuple((144 + 8 * i, ch) for i, ch in enumerate('ゼウス'))),
    ]
    screen = Screen(
        lines=lines,
        hand=None,
        text='マラコビアじょうステータスしゅうにゅう しょうぐん2めいアルファルファゼウス',
        kind='text',
    )
    D._check_source_castle(screen, mem, order)
    assert mem['world']['garrison']['マラコビア'] == ['アルファルファ', 'ゼウス']
    assert mem['world']['garrison_tick']['マラコビア'] == 280050
