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
from docich.hanjuku.strategy import orders as ORD, replan, routes
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen


def _memory():
    chapter = 4
    home = chart.home_castle(chapter)
    normal = [c for c in chart.castles(chapter) if not chart.is_boss_castle(c) and c != home]
    return {'chapter': chapter, 'captured': normal, 'tick': 280000,
            '_records': [], 'plan': {'orders': {}}, 'world': {'garrison': {}, 'garrison_tick': {}}}


def _ch12_memory():
    chapter = 12
    home = chart.home_castle(chapter)
    normal = [c for c in chart.castles(chapter) if not chart.is_boss_castle(c) and c != home]
    return {'chapter': chapter, 'captured': normal, 'tick': 1000,
            '_records': [], 'plan': {'orders': {}},
            'world': {'garrison': {}, 'garrison_tick': {}, 'sorties': {}}}


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


def test_ch12_mechanical_boss_plan_gives_boss_an_armable_purpose():
    mem = _ch12_memory()
    assert replan._mechanical_boss_plan(mem, 'rid') is True
    order = next(o for o in mem['plan']['chart_plan']['orders'] if o['step'] == '12-B1')
    assert order['purpose'] == 'attack'
    assert order['purpose'] in ORD.LAUNCH_PRIORITY_PURPOSES


def test_mechanical_boss_plan_preserves_existing_boss_purposes_and_probes(monkeypatch):
    mem = _ch12_memory()
    custom = (
        {'step': 'X:boss-attack', 'general': '騎馬A', 'source': 'ディランダ',
         'target': 'ボス', 'cards': (), 'after': None, 'purpose': 'attack'},
        {'step': 'X:boss-move-probe', 'general': '騎馬A', 'source': 'ディランダ',
         'target': 'ボス', 'cards': (), 'after': None, 'purpose': 'move',
         'source_probe': True, 'roster_probe': True},
        {'step': 'X:boss-read-probe', 'general': '騎馬A', 'source': 'ディランダ',
         'target': 'ボス', 'cards': (), 'after': None,
         'source_probe': True, 'roster_probe': True},
    )
    monkeypatch.setattr(replan.chart, 'orders', lambda chapter: ())
    monkeypatch.setattr(replan.chart, 'all_orders', lambda chapter: custom)

    assert replan._mechanical_boss_plan(mem, 'rid') is True
    rows = {o['step']: o for o in mem['plan']['chart_plan']['orders']}
    assert rows['X:boss-attack']['purpose'] == 'attack'
    assert rows['X:boss-move-probe']['purpose'] == 'move'
    assert rows['X:boss-move-probe']['source_probe'] is True
    assert rows['X:boss-move-probe']['roster_probe'] is True
    assert 'purpose' not in rows['X:boss-read-probe']
    assert rows['X:boss-read-probe']['source_probe'] is True
    assert rows['X:boss-read-probe']['roster_probe'] is True


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


def test_field_map_never_activates_an_attack_on_an_owned_castle():
    # docich#5623: a stale plan step attacking an already-owned castle can
    # never launch; activating it walked into the deploy route check every
    # observation and the cancel handed it back to pending forever.
    mem = _memory()
    mem['world']['garrison'] = {'アルマムーン': ['どうし', 'ピオーネ']}
    stale = {'step': 'A:plan:M1', 'source': 'アルマムーン', 'target': 'ミヒラギアン',
             'general': 'ピオーネ', 'cards': [], 'after': None, 'purpose': 'attack'}
    mem['plan']['orders'] = {stale['step']: 'pending'}
    mem['plan']['chart_plan'] = {'source': 'mechanical', 'orders': [stale]}
    assert O.plan_starved_rearm(mem, 'field', 'map') is False
    assert mem['plan'].get('active') is None
    assert not any(r['decision'] == 'plan_map_activate' for r in mem['_records'])


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

# docich#5153: stuck roster probes must account for marches, respect the
# per-castle stale-read interval, and prefer a due arrival receipt over home.
def _stuck_chapter_memory(*, owned_count=1):
    chapter = 8
    castles = chart.castles(chapter)
    home = chart.home_castle(chapter)
    normal = [c for c in castles if not chart.is_boss_castle(c)]
    owned = [home, *[c for c in normal if c != home][:owned_count - 1]]
    garrison = {castle: [f'general-{index}']
                for index, castle in enumerate(owned)}
    return {
        'chapter': chapter, 'month': '67-10', 'tick': 10000,
        'captured': owned, 'lost': [],
        'country_general_count': {'count': len(owned) + 1},
        'soldiers_seen': 100, 'house_eggs': {},
        'general_location_unknown': [], '_records': [],
        'plan': {'orders': {}}, 'orders': {},
        'world': {
            'garrison': garrison, 'garrison_tick': {},
            'sorties': {}, 'launched_orders': {},
            'route_probe_read_tick': {}, 'arrival_probe_tick': {},
        },
    }


def test_stuck_roster_probe_counts_in_flight_generals_as_located():
    mem = _stuck_chapter_memory()
    home = chart.home_castle(8)
    enemy = next(c for c in chart.castles(8)
                 if not chart.is_boss_castle(c) and c not in mem['captured'])
    mem['world']['sorties']['I:flight:1'] = {
        'source': home, 'target': enemy, 'general': 'general-in-flight',
        'purpose': 'move', 'status': 'en_route', 'tick': mem['tick'] - 10,
    }
    # One roster resident + one marching general exactly accounts for the country total.
    assert 'probe_stuck_1' not in replan.interim_candidates(mem, allow_sole=False)


def test_stuck_roster_probe_respects_the_per_castle_read_cooldown():
    mem = _stuck_chapter_memory()
    home = chart.home_castle(8)
    first = replan.interim_candidates(mem, allow_sole=False)
    assert first['probe_stuck_1']['source'] == home

    mem['world']['garrison_tick'][home] = mem['tick']
    mem['tick'] += 1
    assert 'probe_stuck_1' not in replan.interim_candidates(mem, allow_sole=False)

    mem['tick'] += replan.STALE_ROSTER_PROBE_INTERVAL - 1
    assert replan.interim_candidates(mem, allow_sole=False)['probe_stuck_1']['source'] == home


def test_stuck_roster_probe_prioritizes_a_due_unread_arrival_over_home(monkeypatch):
    mem = _stuck_chapter_memory(owned_count=2)
    home, arrival_castle = mem['captured']
    now = mem['tick']
    mem['country_general_count']['count'] = 3
    mem['world']['garrison_tick'].update({home: now - 1000, arrival_castle: now - 10})
    mem['world']['route_probe_read_tick'][arrival_castle] = now - 1000
    mem['world']['sorties']['I:arrival:1'] = {
        'source': home, 'target': arrival_castle, 'general': 'general-arrived',
        'purpose': 'move', 'status': 'overdue', 'tick': now - 500,
    }
    # Isolate the no-candidate fallback; the fallback itself must choose the
    # castle whose unread dispatch is the only arrival receipt.
    mem['boss_victories'] = {'chapter': 8}
    monkeypatch.setattr(replan, '_interim_source', lambda *args, **kwargs: None)
    monkeypatch.setattr(replan, '_garrison_rotation', lambda *args, **kwargs: [])
    probe = replan.interim_candidates(mem, allow_sole=False)['probe_stuck_1']
    assert probe['source'] == arrival_castle
    assert probe['source_probe'] is True and probe['roster_probe'] is True



def _stuck_road_memory():
    chapter = 3
    home = chart.home_castle(chapter)
    owned = [home, 'グリン']
    return {
        'chapter': chapter, 'month': '7-11', 'tick': 20000,
        'captured': owned, 'lost': [],
        'country_general_count': {'count': 3},
        'soldiers_seen': 100, 'house_eggs': {}, 'general_location_unknown': [],
        'boss_victories': {'chapter': chapter},
        '_records': [], 'plan': {'orders': {}}, 'orders': {},
        'world': {
            'garrison': {home: ['home-guard'], 'グリン': ['grin-guard']},
            'garrison_tick': {home: 19000, 'グリン': 18000},
            'sorties': {}, 'launched_orders': {},
            'route_probe_read_tick': {}, 'arrival_probe_tick': {},
        },
    }


def test_stuck_road_roster_probe_uses_a_route_legal_target(monkeypatch):
    mem = _stuck_road_memory()
    assert not routes.allowed(mem, 'グリン', 'コラダイン')
    assert routes.allowed(mem, 'グリン', 'リグア')
    monkeypatch.setattr(replan, '_interim_source', lambda *args, **kwargs: None)
    monkeypatch.setattr(replan, '_garrison_rotation', lambda *args, **kwargs: [])
    monkeypatch.setattr(replan.chart, 'march_blocked', lambda *args, **kwargs: False)

    probe = replan.interim_candidates(mem, allow_sole=False)['probe_stuck_1']
    assert probe['source'] == 'グリン'
    assert probe['target'] == 'リグア'
    assert routes.allowed(mem, probe['source'], probe['target'])


def test_stuck_roster_probe_tries_next_source_when_oldest_has_rejected_move(monkeypatch):
    mem = _stuck_chapter_memory(owned_count=3)
    home, rejected_source, next_source = mem['captured']
    now = mem['tick']
    mem['country_general_count']['count'] = 4
    mem['world']['garrison_tick'].update({
        home: now - 1000, rejected_source: now - 2000, next_source: now - 1500,
    })
    enemy = next(c for c in chart.castles(8)
                 if not chart.is_boss_castle(c) and c not in mem['captured'])
    monkeypatch.setattr(replan, '_interim_source', lambda *args, **kwargs: None)
    monkeypatch.setattr(replan, '_garrison_rotation', lambda *args, **kwargs: [
        {'general': 'general-1', 'source': rejected_source, 'target': enemy,
         'cards': [], 'after': None, 'purpose': 'move'},
    ])
    monkeypatch.setattr(replan.chart, 'march_blocked', lambda *args, **kwargs: False)
    monkeypatch.setattr(replan, '_post_conquest_wait', lambda *args, **kwargs: False)

    probe = replan.interim_candidates(mem, allow_sole=False)['probe_stuck_1']
    assert probe['source'] == next_source
    assert probe['source'] != rejected_source



def test_stuck_road_roster_probe_falls_back_to_only_source_with_rejected_move(monkeypatch):
    """A planned but route-rejected move must not erase the sole readable roster."""
    mem = _stuck_road_memory()
    home = chart.home_castle(3)
    now = mem['tick']
    enemies = [c for c in chart.castles(3)
               if c not in mem['captured'] and not chart.is_boss_castle(c)]
    legal = [c for c in enemies if routes.allowed(mem, home, c)]
    illegal = [c for c in enemies if not routes.allowed(mem, home, c)]
    assert legal and illegal

    # The home roster is stale; グリン was just read and cannot be probed yet.
    mem['world']['garrison_tick'][home] = now - 1000
    mem['world']['garrison_tick']['グリン'] = now
    mem['country_general_count']['count'] = 3
    monkeypatch.setattr(replan, '_interim_source', lambda *args, **kwargs: None)
    monkeypatch.setattr(replan, '_garrison_rotation', lambda *args, **kwargs: [
        {'general': 'home-guard', 'source': home, 'target': illegal[0],
         'cards': [], 'after': None, 'purpose': 'move'},
    ])
    monkeypatch.setattr(replan.chart, 'march_blocked', lambda *args, **kwargs: False)
    monkeypatch.setattr(replan, '_post_conquest_wait', lambda *args, **kwargs: False)

    probe = replan.interim_candidates(mem, allow_sole=False)['probe_stuck_1']
    assert probe['source'] == home
    assert probe['target'] == legal[0]
    assert probe['source_probe'] is True and probe['roster_probe'] is True
    assert routes.allowed(mem, probe['source'], probe['target'])


def test_field_map_does_not_activate_a_locked_boss_step():
    # Chapter 1/4: the normal castles are not all owned yet, so a mechanical
    # plan whose first step is the boss must not be activated (the launch flow
    # would hold it as boss_target_locked on every observation).
    mem = _memory()
    mem['captured'] = []
    mem['plan']['orders'] = {'4-B1': 'pending'}
    mem['plan']['chart_plan'] = {
        'source': 'mechanical',
        'orders': [next(o for o in chart.orders(4) if o['step'] == '4-B1')],
    }
    mem['world']['garrison'] = {'アルマムーン': ['どうし', 'アルファルファ', 'ゼウス']}
    mem['egg_types'] = {'アルファルファ': 'スーパーエッグ', 'ゼウス': 'ワンダーエッグ'}
    assert O.plan_starved_rearm(mem, 'field', 'map') is False
    assert mem['plan'].get('active') is None


def test_roster_probe_to_a_boss_castle_is_not_boss_locked():
    mem = _memory()
    mem['captured'] = []          # normal castles not owned yet
    order = {'step': 'probe', 'source': chart.home_castle(4),
             'target': chart.boss_sites(4)[0], 'purpose': 'move',
             'roster_probe': True, 'source_probe': True}
    assert ORD._boss_target_unlocked(order, mem) is True
