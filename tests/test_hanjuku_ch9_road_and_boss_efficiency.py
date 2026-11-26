"""Regressions for Chapter 9 road efficiency and moving-boss pursuit/combat."""
from collections import Counter
from types import SimpleNamespace

from docich import hanjuku_chart as chart
from docich import hanjuku_policy as policy
from docich import hanjuku_reference as reference
from docich.hanjuku.flows import boss_pursuit as bp
from docich.hanjuku.flows import deploy
from docich.hanjuku.os_layer.font import TextLine
from docich.hanjuku.os_layer.screen import Screen
from docich.hanjuku.os_layer.world import NAME, SORTIE_BUSY_TICKS
from docich.hanjuku.state.regions import plan as _plan_region, world as _world
from docich.hanjuku.strategy import corridor, orders, replan, routes


def _general_list_screen(names, cursor_index=0):
    lines = [
        TextLine(47, tuple((64 + 8 * i, ch) for i, ch in enumerate('しゅつげき'))),
        TextLine(63, tuple((64 + 8 * i, ch) for i, ch in enumerate('ステータス'))),
    ]
    for n, name in enumerate(names):
        lines.append(TextLine(39 + 16 * n, tuple((144 + 8 * i, ch) for i, ch in enumerate(name))))
    hy = 33 + 16 * cursor_index
    return Screen(
        lines=lines,
        hand=(122, hy, 139, hy + 12),
        kind='general_list',
        text='しゅつげきステータス' + ''.join(names),
    )


# ------------------------------------------------------------------ Step 1
def test_routes_moves_into_accepts_probe_binding_and_substitute_moves():
    now = 10000
    mem = {
        'chapter': 9,
        'tick': now,
        'captured': list(chart.corridor(9)[1:]),
        'route_probe_read_tick': {'イデ！': 9000},
        'world': {
            'sorties': {
                'I:c9:イソ>イデ！:1': {
                    'general': 'ギー', 'source': 'イソ', 'target': 'イデ！',
                    'purpose': 'move', 'status': 'en_route', 'tick': 9500,
                },
                'A:3814542a:Mf0523b': {
                    'general': 'エピィ', 'source': 'イソ', 'target': 'イデ！',
                    'purpose': 'move', 'status': 'overdue', 'tick': 9400,
                    'evidence': {'planned_general': 'どうし', 'actual_general': 'エピィ'},
                },
                'A:3814542a:Mbind': {
                    'general': 'カシス', 'source': 'イソ', 'target': 'イデ！',
                    'purpose': 'move', 'status': 'en_route', 'tick': 9600,
                },
                'A:bad:gen': {
                    'general': 'レモン', 'source': 'イソ', 'target': 'イデ！',
                    'purpose': 'move', 'status': 'en_route', 'tick': 9650,
                },
                'A:bad:dst': {
                    'general': 'ブリオッシュ', 'source': 'イソ', 'target': 'カラ',
                    'purpose': 'move', 'status': 'en_route', 'tick': 9700,
                },
            },
            'launched_orders': {
                'I:c9:イソ>イデ！:1': {
                    'general': '—', 'source': 'イソ', 'target': 'イデ！', 'purpose': 'move',
                },
                'A:3814542a:Mf0523b': {
                    'general': 'どうし', 'source': 'イソ', 'target': 'イデ！', 'purpose': 'move',
                },
                'A:3814542a:Mbind': {
                    'general': 'どうし', 'source': 'イソ', 'target': 'イデ！', 'purpose': 'move',
                    'departure_binding': {'general': 'カシス'},
                },
                'A:bad:gen': {
                    'general': 'どうし', 'source': 'イソ', 'target': 'イデ！', 'purpose': 'move',
                },
                'A:bad:dst': {
                    'general': 'ブリオッシュ', 'source': 'イソ', 'target': 'イデ！', 'purpose': 'move',
                },
            },
        },
    }
    found = {step for step, _ in routes._moves_into(mem, 'イデ！')}
    assert found == {'I:c9:イソ>イデ！:1', 'A:3814542a:Mf0523b', 'A:3814542a:Mbind'}
    assert len(routes.unread_dispatches(mem, 'イデ！')) == 3
    assert routes.arrival_probe_due(mem, 'イデ！', cooldown=True) is True
    assert routes.probe_due(mem, 'イデ！') is True


# ------------------------------------------------------------------ Step 2
def test_corridor_attack_equips_strong_kit_and_keeps_moves_cursor_ordered():
    mem = {
        'chapter': 9,
        'tick': 5000,
        'month': '10-1',
        'captured': ['ハヤク'],
        'card_stock': {'クースカン': 10, 'ミックミー': 10, 'イッテツーン': 99},
        'world': {'garrison': {'アルマムーン': [NAME, 'ゼウス'], 'ハヤク': ['ミント', 'カシス']}, 'sorties': {}},
        'plan': {'orders': {}},
    }
    atk = corridor._order(mem, 'ハヤク', 'タオ', 'タオ', 'attack')
    rtk = corridor._order(mem, 'ハヤク', 'タオ', 'タオ', 'retake')
    mv = corridor._order(mem, 'アルマムーン', 'ハヤク', 'タオ', 'move')
    hero_mv = corridor._order(mem, 'アルマムーン', 'ハヤク', 'タオ', 'move', hero=True)

    assert atk.get('corridor_strong_kit') is True
    assert atk['cards'] == ['クースカン', 'ミックミー', 'ミックミー']
    assert rtk.get('corridor_strong_kit') is True
    assert rtk['cards'] == ['クースカン', 'ミックミー', 'ミックミー']
    assert 'corridor_strong_kit' not in mv and mv['cards'] == []
    assert 'corridor_strong_kit' not in hero_mv and hero_mv['cards'] == []

    assert deploy._cursor_order_cards(atk, mem) is False
    assert deploy._cursor_order_cards(rtk, mem) is False
    assert deploy._cursor_order_cards(mv, mem) is True

    corridor._remember_probe(mem, atk)
    assert mem[corridor.PROBE]['mint_order'].get('corridor_strong_kit') is True

    mem_unread = {**mem, 'card_stock': {}}
    atk_unread = corridor._order(mem_unread, 'ハヤク', 'タオ', 'タオ', 'attack')
    assert 'corridor_strong_kit' not in atk_unread and atk_unread['cards'] == []
    assert deploy._cursor_order_cards(atk_unread, mem_unread) is True


# ------------------------------------------------------------------ Step 3
def test_corridor_forward_moves_uses_freshest_partial_and_leads_with_hero():
    road = chart.corridor(9)
    mem = {
        'chapter': 9,
        'tick': 140000,
        'month': '15-12',
        'captured': list(road[1:]),
        '_records': [],
        'world': {
            'garrison': {c: [] for c in road} | {'イソ': [NAME], 'イデ！': ['マスカット']},
            'garrison_tick': {c: 130000 for c in road} | {'イソ': 116988},
            'garrison_partial': {'イソ': [NAME, 'サフラン', 'アルファルファ']},
            'garrison_partial_tick': {'イソ': 139915},
            'sorties': {},
        },
        'plan': {'orders': {}},
    }
    owned = set(road)
    lead, rest, staged = replan._corridor_forward_moves(
        mem, owned, _world(mem)['garrison'], set(), 'イデ！', nearest_first=True,
    )
    iso_moves = [o for o in (*lead, *rest) if o['source'] == 'イソ' and o['target'] == 'イデ！']
    assert [o['general'] for o in iso_moves] == [NAME, 'サフラン', 'アルファルファ']
    assert iso_moves[0].get('corridor_hero') is True
    assert 'サフラン' in staged and 'アルファルファ' in staged

    # Cursor starts on サフラン (index 1); deploy steps up to どうし instead of substituting or refusing.
    hero_order = dict(iso_moves[0], step='I:test:hero')
    _plan_region(mem)['active'] = hero_order['step']
    mem['chart_adjust'] = {'request_id': 't', 'interim_order': hero_order}
    screen = _general_list_screen([NAME, 'サフラン', 'アルファルファ'], cursor_index=1)
    with chart.map_context(mem):
        actions = deploy.deploy_step(screen, mem)
    assert actions == [policy.pad('up')]
    assert not any(r.get('decision') == 'hero_move_refused' for r in mem['_records'])

    # Older partial must NOT beat newer complete garrison.
    mem['world']['garrison_tick']['イソ'] = 140000
    lead2, rest2, _ = replan._corridor_forward_moves(
        mem, owned, _world(mem)['garrison'], set(), 'イデ！', nearest_first=True,
    )
    iso_moves2 = [o for o in (*lead2, *rest2) if o['source'] == 'イソ']
    assert [o['general'] for o in iso_moves2] == [NAME]

    # During road conquest (nearest_first=False), moving from front-1 onto front
    # (len(route) == 3) does NOT set corridor_hero=True.
    mem_road = {
        'chapter': 9,
        'tick': 140000,
        'month': '15-12',
        'captured': list(road[1:-1]),
        'world': {
            'garrison': {c: [] for c in road[:-1]} | {road[-3]: [NAME, 'サフラン']},
            'garrison_tick': {c: 139000 for c in road[:-1]},
            'sorties': {},
        },
        'plan': {'orders': {}},
    }
    owned_road = set(road[:-1])
    lead3, rest3, _ = replan._corridor_forward_moves(
        mem_road, owned_road, _world(mem_road)['garrison'], set(), road[-1], nearest_first=False,
    )
    hero_moves3 = [o for o in (*lead3, *rest3) if o['source'] == road[-3] and o['general'] == NAME]
    assert len(hero_moves3) == 1
    assert 'corridor_hero' not in hero_moves3[0]


# ------------------------------------------------------------------ Step 4
def test_boss_stock_cards_maximizes_damage_and_reserves_scarce_cards():
    stock = {
        'バルムンク': 1,
        'ビッグウェイブ': 9,
        'ゼンマイン': 99,
        'ブラッキー': 99,
        'イッテツーン': 99,
        'バグストーム': 31,
        'クースカン': 99,
        'ミックミー': 99,
        'ころぼぐんだん': 99,
    }
    mem = {'chapter': 9, 'card_stock': stock}
    reserve = Counter()

    wave1 = replan._boss_stock_cards(mem, ['マグネガキン', 'ファバード'], general='マスカット', reserve=reserve)
    assert wave1 == ['バルムンク', 'ビッグウェイブ', 'ブラッキー']
    assert sum(reference.ALL_CARD_IDS[c] for c in wave1) == 47 < 48
    assert sum(reference.boss_damage_card(c) for c in wave1) == 370
    assert 'バグストーム' not in wave1

    reserve.update(wave1)
    wave2 = replan._boss_stock_cards(mem, None, general='コンポート', reserve=reserve)
    assert wave2 == ['ビッグウェイブ', 'ゼンマイン', 'ブラッキー']
    assert sum(reference.ALL_CARD_IDS[c] for c in wave2) == 46 < 48
    assert sum(reference.boss_damage_card(c) for c in wave2) == 170

    # After 9 ビッグウェイブ are spent, ゼンマイン x3 (150 dmg, ID 45) takes over.
    no_wave_stock = dict(stock, バルムンク=0, ビッグウェイブ=0)
    wave3 = replan._boss_stock_cards({'chapter': 9, 'card_stock': no_wave_stock}, None, general='マスカット')
    assert wave3 == ['ゼンマイン', 'ゼンマイン', 'ゼンマイン']

    # Sacrifice general may leverage バグストーム when it maximizes damage; non-sacrifice cannot.
    sac_stock = {'バグストーム': 2, 'イッテツーン': 5}
    sac_cards = replan._boss_stock_cards({'chapter': 9, 'card_stock': sac_stock}, None, general='キャラウェイ')
    assert 'バグストーム' in sac_cards
    non_sac_cards = replan._boss_stock_cards({'chapter': 9, 'card_stock': sac_stock}, None, general='マスカット')
    assert 'バグストーム' not in non_sac_cards

    # Unread stock preserves existing fallback.
    assert replan._boss_stock_cards({'chapter': 9}, ['マグネガキン', 'ファバード']) == ['マグネガキン', 'ファバード']

    # Multi-wave interim_candidates never double-books the single バルムンク.
    road = chart.corridor(9)
    mem_plan = {
        'chapter': 9,
        'tick': 140000,
        'month': '15-12',
        'captured': list(road[1:]),
        'card_stock': stock,
        'boss_cell': {'cell': [800, 500], 'chapter': 9, 'tick': 139990, 'readings': 2},
        'world': {
            'garrison': {c: [] for c in road} | {'イデ！': ['マスカット', 'サフラン', 'アルファルファ']},
            'sorties': {},
        },
        'plan': {'orders': {}},
    }
    chart.set_boss_cell(9, (800, 500))
    try:
        with chart.map_context(mem_plan):
            cands = replan.interim_candidates(mem_plan)
    finally:
        chart.set_boss_cell(9, None)
    boss_attacks = [o for o in cands.values() if o.get('target') == 'ボス' and not o.get('source_probe')]
    assert len(boss_attacks) == 2
    assert boss_attacks[0]['cards'] == ['バルムンク', 'ビッグウェイブ', 'ブラッキー']
    assert boss_attacks[1]['cards'] == ['ビッグウェイブ', 'ゼンマイン', 'ブラッキー']


# ------------------------------------------------------------------ Step 5
def test_ch9_cliff_detour_boundary_and_pursuit_avoids_stuck_loop():
    aim_east = (810, 645)
    # コンポート's live coordinate is east of the south cliff bend (x >= 612 at y >= 476).
    assert chart.ch9_cliff_detour([620, 484], aim_east) is None
    # Existing west-pocket and north-corridor contracts stay intact.
    assert chart.ch9_cliff_detour([564, 368], aim_east) == chart.CH9_BOSS_WAYPOINT
    assert chart.ch9_cliff_detour([588, 516], aim_east) == chart.CH9_BOSS_WAYPOINT
    assert chart.ch9_cliff_detour([568, 212], aim_east) == chart.CH9_EAST_MOUTH

    track = [
        {'cell': [700, 300]},
        {'cell': [800, 500]},
        {'cell': [900, 700]},
        {'cell': [750, 850]},
    ]
    mem = {
        'chapter': 9,
        'tick': 10000,
        '_records': [],
        'boss_cell': {'chapter': 9, 'cell': [800, 500], 'tick': 9990, 'readings': 2},
        'boss_cell_track': track,
        bp.LIST_KEY: {
            'コンポート': {
                'step': 'A', 'launched': 5000,
                'ambush_cell': [700, 300], 'move_from': [620, 484],
            },
        },
    }
    first = bp._ambush(mem, 'コンポート')
    assert first == (700, 300)

    entry = mem[bp.LIST_KEY]['コンポート']
    metric = {'unit_world': [620, 484]}
    bp._note_redirect_move(mem, entry, {'step': 'A', 'general': 'コンポート'}, metric)
    assert metric.get('no_move') is True
    assert entry['no_move_streak'] == 1
    assert entry['ambush_offset'] == 1
    assert entry['failed_aims'] == [[700, 300]]

    second = bp._ambush(mem, 'コンポート')
    assert second != first
    assert not bp._aim_failed(entry, second)

    # Previously failed detour leg is not forced again.
    entry['failed_aims'] = [list(chart.CH9_BOSS_WAYPOINT)]
    detour = bp._next_cliff_detour(entry, [588, 516], (810, 645))
    assert detour == chart.CH9_EAST_MOUTH
    entry['failed_aims'].append(list(chart.CH9_EAST_MOUTH))
    assert bp._next_cliff_detour(entry, [588, 516], (810, 645)) is None

    # Real move resets failure state.
    bp._note_redirect_move(mem, entry, {'step': 'A', 'general': 'コンポート'}, {'unit_world': [710, 484]})
    assert 'failed_aims' not in entry
    assert 'ambush_offset' not in entry
    assert 'no_move_streak' not in entry

    # Stuck general gets extended cooldown and yields priority to fresh wave general.
    mem_cand = {
        'chapter': 9,
        'tick': 10000,
        bp.LIST_KEY: {
            'コンポート': {'step': 'A', 'launched': 5000, 'pursuit_tick': 9600,
                     'no_move_streak': bp.PURSUIT_STUCK_STREAK},
            'サフラン': {'step': 'B', 'launched': 9000, 'pursuit_tick': 9700},
        },
    }
    picked = bp._candidate(mem_cand)
    assert picked is not None and picked[0] == 'サフラン'
    # Stuck general alone is held during extended cooldown (960 ticks).
    mem_cand[bp.LIST_KEY].pop('サフラン')
    assert bp._candidate(mem_cand) is None
    mem_cand['tick'] = 9600 + bp.PURSUIT_STUCK_COOLDOWN
    assert bp._candidate(mem_cand)[0] == 'コンポート'


def test_boss_pursuit_redirect_notes_previous_aim_before_setting_new_aim(monkeypatch):
    import docich.hanjuku.flows.map as m
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (810, 645))
    monkeypatch.setattr(m, '_target_roof_under_marker', lambda *a, **k: True)
    picker = SimpleNamespace(
        kind='map_target', marker=(132, 113), cursor=None, text='', hand=None, selected=None,
    )
    probe = {
        'general': 'コンポート', 'step': 'A', 'phase': 'dest',
        'reads': 5, 'seeks': 0, 'x_retries': 0, 'unit_world': [620, 484],
    }
    mem = {
        'chapter': 9,
        'tick': 10000,
        '_records': [],
        bp.LIST_KEY: {
            'コンポート': {
                'step': 'A', 'launched': 5000,
                'ambush_cell': [700, 300], 'move_from': [620, 484],
            },
        },
        'boss_pursuit': dict(probe),
        'pursuit_aim': [700, 300],
    }
    # Confirming the same failed aim records no_move for [700, 300] and keeps ambush_cell cleared.
    assert bp.step(picker, mem, object()) == [bp.pad('a')]
    entry = mem[bp.LIST_KEY]['コンポート']
    assert entry['failed_aims'] == [[700, 300]]
    assert entry['no_move_streak'] == 1
    assert 'ambush_cell' not in entry

    # Redirecting to a fresh aim while still at [620, 484] must NOT blame [800, 500] as failed.
    mem['tick'] = 10400
    mem['boss_pursuit'] = dict(probe)
    mem['pursuit_aim'] = [800, 500]
    assert bp.step(picker, mem, object()) == [bp.pad('a')]
    assert [800, 500] not in entry['failed_aims']
    assert entry['no_move_streak'] == 1
    assert entry['ambush_cell'] == [800, 500]


# ------------------------------------------------------------------ Step 6
def test_moving_boss_late_entry_matches_up_to_pursuit_stale_ticks():
    launched_tick = 100000
    age = orders.LONG_MARCH_TICKS + 5000  # well past 2400, within 21600
    assert age < orders.MOVING_BOSS_LATE_ENTRY_TICKS
    mem_ch9 = {
        'chapter': 9,
        'tick': launched_tick + age,
        'world': {
            'sorties': {
                'I:381454:672d2c66': {
                    'general': 'シュガー', 'source': 'イデ！', 'target': 'ボス',
                    'status': 'overdue', 'tick': launched_tick,
                },
            },
        },
    }
    assert orders._match_sortie(mem_ch9, 'ボス', 'シュガー') == ('I:381454:672d2c66', 'matched')

    # Expired past MOVING_BOSS_LATE_ENTRY_TICKS does not bind.
    mem_ch9['tick'] = launched_tick + orders.MOVING_BOSS_LATE_ENTRY_TICKS
    assert orders._match_sortie(mem_ch9, 'ボス', 'シュガー') == (None, 'none')

    # Non-ch9 chapter keeps LONG_MARCH_TICKS bound.
    mem_ch6 = dict(mem_ch9, chapter=6, tick=launched_tick + orders.LONG_MARCH_TICKS)
    assert orders._match_sortie(mem_ch6, 'ボス', 'シュガー') == (None, 'none')
