"""A stale 四季王 retry must refresh roster evidence instead of parking."""

from docich import hanjuku_chart as chart, hanjuku_policy as policy


SITES = set(chart.boss_sites(4))
NORMAL = [c for c in chart.CASTLE_NAMES[4]
          if c not in (chart.home_castle(4), chart.boss_castle(4))]


def test_stale_site_retry_reprobes_home_roster_when_cached_lists_have_no_actor():
    order = next(o for o in chart.orders(4) if o['target'] == '四季王城(南西)')
    home = chart.home_castle(4)
    states = {o['step']: 'launched' for o in chart.orders(4)}
    states[order['step']] = 'pending'
    mem = {
        'chapter': 4,
        'tick': 5000,
        'captured': list(NORMAL),
        'lost': [],
        'orders': states,
        'sorties': {
            order['step']: {
                'general': 'アルテミス',
                'source': order['source'],
                'target': order['target'],
                'status': 'overdue',
                'tick': 100,
            }
        },
        # Both cached lists are legal observations but currently provide no
        # 四季王 actor: the source has only one general, while sending ゼウス
        # would leave only the hero at home.
        'garrison': {
            order['source']: ['アルテミス'],
            home: [chart.HERO, 'ゼウス'],
        },
    }

    assert policy._strategy_orders._boss_companion_source(order, mem) is None
    chosen = policy.next_order(mem)

    assert chosen is not None and chosen['step'] == order['step']
    assert policy._source(chosen, mem) == home
    assert home not in mem['garrison']
    assert mem['boss_roster_reprobe_tick'] == 5000
    records = [r for r in mem['_records'] if r['decision'] == 'boss_roster_reprobe']
    assert len(records) == 1
    assert records[0]['observed_metric']['cached_generals'] == [chart.HERO, 'ゼウス']


def test_roster_reprobe_is_bounded_until_the_interval_expires():
    order = next(o for o in chart.orders(4) if o['target'] == '四季王城(南西)')
    home = chart.home_castle(4)
    states = {o['step']: 'launched' for o in chart.orders(4)}
    states[order['step']] = 'pending'
    mem = {
        'chapter': 4,
        'tick': 5000,
        'captured': list(NORMAL),
        'lost': [],
        'orders': states,
        'sorties': {order['step']: {'status': 'overdue', 'target': order['target'], 'tick': 100}},
        'garrison': {order['source']: ['アルテミス'], home: [chart.HERO, 'ゼウス']},
        'boss_roster_reprobe_tick': 5000,
    }

    assert policy.next_order(mem) is None
    assert mem['garrison'][home] == [chart.HERO, 'ゼウス']

    mem['tick'] += policy.BOSS_ROSTER_REPROBE_TICKS
    assert policy.next_order(mem)['step'] == order['step']
