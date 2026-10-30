"""Issue #2930: chapter-3 direct marches across the cliff are replaced by
route-frontier hops along the chart's directed castle segments."""
from docich import hanjuku_policy as policy
from docich import hanjuku_chart as chart


def mem3(**over):
    base = {'chapter': 3, 'tick': 100, '_records': [],
            'captured': ['アルマムーン'], 'orders': {},
            'garrison': {'アルマムーン': ['ゼウス', 'ヴィーナス']}}
    base.update(over)
    return base


def test_route_frontier_only_exposes_first_unowned_hops():
    frontier = policy._route_frontier(3, {'アルマムーン'})
    # 3話のルート: home→コラダイン/メガパタ が最前線、奥の ドンダム は出ない
    assert 'コラダイン' in frontier and frontier['コラダイン'] == 'アルマムーン'
    assert 'メガパタ' in frontier
    assert 'ドンダム' not in frontier and 'イーピヨル' not in frontier


def test_route_frontier_advances_after_capture():
    frontier = policy._route_frontier(3, {'アルマムーン', 'コラダイン'})
    assert frontier.get('イーピヨル') == 'コラダイン'
    assert frontier.get('パタゴーヌ') == 'アルマムーン'   # 3-A1 is home-sourced
    assert 'ドンダム' not in frontier          # still two hops out
    frontier = policy._route_frontier(3, {'アルマムーン', 'コラダイン', 'イーピヨル'})
    assert frontier.get('ドンダム') == 'イーピヨル'


def test_dispatch_ok_is_unrestricted_outside_chapter3():
    order = {'step': 'x', 'source': 'アルマムーン', 'target': 'ドンダム'}
    assert policy._route_dispatch_ok(order, {'chapter': 1, 'captured': ['アルマムーン']})
    mem = mem3()
    assert policy._route_dispatch_ok({**order, 'purpose': 'move'}, mem)
    assert policy._route_dispatch_ok({**order, 'target': 'アルマムーン'}, mem)


def test_dispatch_ok_requires_frontier_and_route_predecessor():
    mem = mem3()
    direct = {'step': 'x', 'source': 'アルマムーン', 'target': 'ドンダム'}
    assert not policy._route_dispatch_ok(direct, mem)          # not frontier
    hop = {'step': 'y', 'source': 'コラダイン', 'target': 'イーピヨル'}
    assert not policy._route_dispatch_ok(hop, mem)             # source not owned-route pred
    mem2 = mem3(captured=['アルマムーン', 'コラダイン'])
    assert policy._route_dispatch_ok(hop, mem2)                # frontier + predecessor
    wrong_src = {'step': 'z', 'source': 'アルマムーン', 'target': 'イーピヨル'}
    assert not policy._route_dispatch_ok(wrong_src, mem2)      # off-route source


def test_next_order_cancels_a_stale_direct_order():
    order = {'step': 'G:r1:x1', 'general': 'ゼウス', 'source': 'アルマムーン',
             'target': 'ドンダム', 'cards': [], 'after': None, 'purpose': 'attack',
             'note': 'adjusted direct order'}
    mem = mem3(chart_plan={'orders': [order]})
    picked = policy.next_order(mem)
    assert picked is None
    assert mem['orders']['G:r1:x1'] == 'failed'
    [rec] = [r for r in mem['_records'] if r['decision'] == 'order_failed']
    assert rec['deviation_reason'] == 'route_invalid'
    assert rec['observed_metric']['target'] == 'ドンダム'


def test_next_order_keeps_a_valid_frontier_order():
    order = {'step': 'G:r1:x2', 'general': 'ゼウス', 'source': 'アルマムーン',
             'target': 'コラダイン', 'cards': [], 'after': None, 'purpose': 'attack',
             'note': 'adjusted frontier order'}
    mem = mem3(chart_plan={'orders': [order]})
    picked = policy.next_order(mem)
    assert picked is not None and picked['target'] == 'コラダイン'
    assert mem['orders'].get('G:r1:x2') != 'failed'
