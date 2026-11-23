"""Tests for boss-wave source picking from partial rosters and move source overrides."""

from docich import hanjuku_chart as chart
from docich.hanjuku.state.regions import plan as _plan_region
from docich.hanjuku.strategy import orders, replan


def _ch1_boss_ready_mem(*, garrison=None, garrison_partial=None):
    owned = [c for c in chart.castles(1) if not chart.is_boss_castle(c)]
    mem = {
        'chapter': 1,
        'month': '16-4',
        'tick': 66000,
        'captured': list(owned),
        'lost': [],
        'orders': {},
        'world': {
            'sorties': {},
            'garrison': garrison if garrison is not None else {
                'カストーラ': ['デュオニソス'],
                'キカンドン': [],
                'ゴーメン': ['ヴィーナス'],
                'ジョンリギ': ['チコリ'],
                'スペンソニア': ['ソーピニヨン'],
                'ナキューメラ': ['ゼウス'],
            },
            'garrison_partial': garrison_partial or {},
        },
        'house_eggs': {},
        'general_location_unknown': [],
        '_records': [],
    }
    return mem


def test_boss_wave_pick_uses_garrison_partial_roster():
    mem = _ch1_boss_ready_mem(
        garrison={
            'アルマムーン': ['どうし'],
            'カストーラ': ['デュオニソス'],
            'キカンドン': [],
            'ゴーメン': ['ヴィーナス'],
            'ジョンリギ': ['チコリ'],
            'ナキューメラ': ['ゼウス'],
        },
        garrison_partial={'スペンソニア': ['ソーピニヨン', 'シナモン']},
    )
    waves = [o for o in replan.interim_candidates(mem).values()
             if o.get('target') == chart.boss_castle(1)]
    assert waves, 'garrison_partial に 2 名以上いる城からボス波が生成されるべき'
    assert waves[0]['source'] == 'スペンソニア'
    assert waves[0]['general'] in {'ソーピニヨン', 'シナモン'}


def test_redirect_blocked_sources_does_not_redirect_boss_order_to_understaffed_blocker():
    owned = [c for c in chart.castles(1) if not chart.is_boss_castle(c)]
    step = 'A:live:Mboss1'
    mem = {
        'chapter': 1,
        'month': '21-2',
        'tick': 85709,
        'captured': list(owned),
        'lost': [],
        'orders': {step: 'pending'},
        'chart_plan': {
            'orders': [
                {
                    'step': step,
                    'purpose': 'attack',
                    'general': 'シナモン',
                    'source': 'アルマムーン',
                    'target': chart.boss_castle(1),
                    'cards': [],
                    'after': None,
                },
            ],
        },
        'source_override': {step: 'スペンソニア'},
        'world': {
            'sorties': {},
            'garrison': {
                'カストーラ': ['デュオニソス'],
                'キカンドン': [],
                'ゴーメン': ['ヴィーナス'],
                'ジョンリギ': ['チコリ'],
                'スペンソニア': ['ソーピニヨン'],
                'ナキューメラ': ['ゼウス'],
            },
            'garrison_partial': {'アルマムーン': ['どうし', 'セルリアク', 'シナモン']},
        },
        'house_eggs': {},
        'general_location_unknown': [],
        '_records': [],
    }
    picked = orders.next_order(mem)
    assert step not in (_plan_region(mem).get('source_override') or {})
    assert picked is not None
    assert picked['step'] == step
    assert orders._source(picked, mem) == 'アルマムーン'


def test_boss_wave_pick_still_falls_back_to_unread_probe_when_partial_has_single_general():
    mem = _ch1_boss_ready_mem(
        garrison={
            'カストーラ': ['デュオニソス'],
            'キカンドン': [],
            'ゴーメン': None,
            'ジョンリギ': ['チコリ'],
            'スペンソニア': ['ソーピニヨン'],
            'ナキューメラ': ['ゼウス'],
        },
        garrison_partial={'アルマムーン': ['どうし']},
    )
    waves = [o for o in replan.interim_candidates(mem).values()
             if o.get('target') == chart.boss_castle(1)]
    probes = [o for o in waves if o.get('source_probe')]
    assert len(probes) == 1
    assert probes[0]['source'] == 'ゴーメン'
    assert probes[0]['general'] == replan.MOVE_ANY_GENERAL


def test_redirect_blocked_sources_skips_move_and_clears_stale_blocker_override():
    owned = [c for c in chart.castles(1) if not chart.is_boss_castle(c)]
    step_move = 'A:testmove:M1'
    step_move_other = 'A:testmove:M2'
    blocker_sp = chart.march_blocker(1, 'アルマムーン', 'スペンソニア', set(owned))
    blocker_go = chart.march_blocker(1, 'アルマムーン', 'ゴーメン', set(owned))
    assert blocker_sp is not None
    assert blocker_go is not None
    mem = {
        'chapter': 1,
        'month': '16-4',
        'tick': 66000,
        'captured': list(owned),
        'lost': [],
        'orders': {step_move: 'pending', step_move_other: 'pending'},
        'chart_plan': {
            'orders': [
                {
                    'step': step_move,
                    'purpose': 'move',
                    'general': 'シナモン',
                    'source': 'アルマムーン',
                    'target': 'スペンソニア',
                    'cards': [],
                    'after': None,
                },
                {
                    'step': step_move_other,
                    'purpose': 'move',
                    'general': 'セルリアク',
                    'source': 'アルマムーン',
                    'target': 'ゴーメン',
                    'cards': [],
                    'after': None,
                },
            ],
        },
        'source_override': {step_move: blocker_sp},
        'world': {
            'sorties': {},
            'garrison': {
                'カストーラ': ['デュオニソス'],
                'キカンドン': [],
                'ゴーメン': ['ヴィーナス'],
                'ジョンリギ': ['チコリ'],
                'スペンソニア': ['ソーピニヨン'],
                'ナキューメラ': ['ゼウス'],
            },
            'garrison_partial': {'アルマムーン': ['どうし', 'セルリアク', 'シナモン']},
        },
        'house_eggs': {},
        'general_location_unknown': [],
        '_records': [],
    }
    picked = orders.next_order(mem)
    overrides = _plan_region(mem).get('source_override') or {}
    assert step_move not in overrides
    assert step_move_other not in overrides
    assert picked is not None
    assert picked['step'] == step_move


def test_redirect_blocked_sources_still_redirects_non_move_attack():
    mem = {
        'chapter': 5,
        'month': '39-9',
        'tick': 1000,
        'captured': ['アルマムーン', 'オトラント', 'テラビル'],
        'lost': [],
        'orders': {'A:x:Atk1': 'pending'},
        'launched_orders': {
            'A:x:Atk1': {
                'step': 'A:x:Atk1',
                'purpose': 'attack',
                'source': 'アルマムーン',
                'target': 'クロチェット',
                'general': 'エピィ',
                'cards': [],
                'after': None,
            },
        },
        'garrison': {
            'アルマムーン': ['どうし', 'ミュスカデ'],
            'オトラント': ['グレナデン'],
            'テラビル': ['ラビオリ'],
        },
        'sorties': {},
        '_records': [],
        'house_eggs': {},
        'general_location_unknown': [],
    }
    orders.next_order(mem)
    assert _plan_region(mem)['source_override']['A:x:Atk1'] == 'オトラント'


def test_next_order_clears_stale_companion_override_when_boss_wave_actor_at_partial_source():
    owned = [c for c in chart.castles(1) if not chart.is_boss_castle(c)]
    step = 'A:live:Mda3882'
    mem = {
        'chapter': 1,
        'month': '19-5',
        'tick': 77664,
        'captured': list(owned),
        'lost': [],
        'orders': {step: 'pending'},
        'chart_plan': {
            'orders': [
                {
                    'step': step,
                    'purpose': 'attack',
                    'general': 'シナモン',
                    'source': 'アルマムーン',
                    'target': chart.boss_castle(1),
                    'cards': [],
                    'after': None,
                },
            ],
        },
        'source_override': {step: 'カストーラ'},
        'world': {
            'sorties': {},
            'garrison': {
                'カストーラ': ['デュオニソス'],
                'キカンドン': [],
                'ゴーメン': ['ヴィーナス'],
                'ジョンリギ': ['チコリ'],
                'スペンソニア': ['ソーピニヨン'],
                'ナキューメラ': ['ゼウス'],
            },
            'garrison_partial': {'アルマムーン': ['どうし', 'セルリアク', 'シナモン']},
        },
        'house_eggs': {},
        'general_location_unknown': [],
        '_records': [],
    }
    picked = orders.next_order(mem)
    assert step not in (_plan_region(mem).get('source_override') or {})
    assert not any(r.get('decision') == 'boss_companion_source' for r in mem['_records'])
    assert picked is not None
    assert picked['step'] == step
    assert orders._source(picked, mem) == 'アルマムーン'


def test_boss_companion_source_skips_one_general_castle_and_uses_partial_source():
    boss = next(o for o in chart.all_orders(1) if o['target'] == chart.boss_castle(1))
    owned = [c for c in chart.castles(1) if not chart.is_boss_castle(c)]
    mem = {
        'chapter': 1,
        'month': '19-5',
        'tick': 77664,
        'captured': list(owned),
        'lost': [],
        'orders': {boss['step']: 'pending'},
        'source_override': {boss['step']: 'アルマムーン'},
        'world': {
            'sorties': {},
            'garrison': {
                'カストーラ': ['デュオニソス'],
                'キカンドン': [],
                'ゴーメン': ['ヴィーナス'],
                'ジョンリギ': ['チコリ'],
                'スペンソニア': ['ソーピニヨン'],
                'ナキューメラ': ['ゼウス'],
            },
            'garrison_partial': {'アルマムーン': ['どうし', 'セルリアク', 'シナモン']},
        },
        'house_eggs': {},
        'general_location_unknown': [],
        '_records': [],
    }
    assert orders._boss_companion_source(boss, mem) == 'アルマムーン'
    mem['world']['garrison_partial'] = {}
    mem['world']['garrison']['アルマムーン'] = []
    assert orders._boss_companion_source(boss, mem) is None
