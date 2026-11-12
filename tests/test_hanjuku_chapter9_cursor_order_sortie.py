"""Chapter 9 sends whoever the list cursor is on; the hero goes last.

Owner 2026-11-11: 9話にかぎり、出撃の時に将軍を選ぶ必要ないので、カーソルがあった
順に出撃させれば短縮になる。ただし主人公だけはゲームオーバーにならないために最後とする.
"""
from docich import hanjuku_chart as chart
from docich import hanjuku_policy as policy

from test_hanjuku_off_chart import _move_list, _move_memory
from docich.hanjuku.state.regions import plan as _plan_region

HOME = chart.home_castle(9)
ROAD = chart.corridor(9)


def _memory(purpose, chapter=9, general='シナモン'):
    mem = _move_memory()
    order = mem['chart_adjust']['interim_order']
    # Moves walk アルマムーン→ハヤク; attacks leave the front ハヤク for タオ.
    source, target = (HOME, ROAD[1]) if purpose == 'move' else (ROAD[1], ROAD[2])
    order.update(general=general, source=source, target=target, purpose=purpose)
    mem.update(chapter=chapter, captured=[ROAD[1]] if chapter == 9 else ['ジョンリギ'])
    if chapter == 9:
        # v668: chapter 9 runs only its castle-scoped march orders.
        from docich.hanjuku.strategy import corridor
        with chart.map_context(mem):
            march = next(o for o in corridor.orders(mem)
                         if (o['purpose'] == 'move') == (purpose == 'move'))
        order.update(step=march['step'], corridor=True, source_probe=True,
                     general=march['general'])
        mem['active'] = march['step']
    mem['_test_step'] = mem.get('active', 'I:m:1')    # ``active`` migrates into its region
    return mem


def _step(mem, names):
    with chart.map_context(mem):
        return policy.deploy_step(_move_list(names), mem)


def picks(mem):
    return [r for r in mem['_records'] if r.get('decision') == 'cursor_order_pick']


def test_an_attack_takes_the_cursor_general_when_nobody_is_stronger():
    """ミント 32 / シナモン 39: within the margin, so the cursor still goes."""
    mem = _memory('attack')
    actions = _step(mem, ['ミント', 'シナモン'])
    assert actions == [policy.pad('a')]
    assert _plan_region(mem)['general_override'][mem['_test_step']] == 'ミント'
    assert picks(mem)
    assert not mem.get('general_list_scan')


def test_an_attack_steers_to_a_clearly_stronger_general():
    """g1163 2026-11-12: cursor generals lost on the road to オオキク every time."""
    mem = _memory('attack')
    actions = _step(mem, ['ミント', 'ジェラート'])       # 32 against 49
    assert actions == [policy.pad('down')]
    assert not picks(mem)
    assert any(r.get('decision') == 'attack_strongest_pick' for r in mem['_records'])


def test_the_cursor_steps_past_the_hero():
    mem = _memory('move')
    assert _step(mem, [chart.HERO, 'ミント', 'ジェラート']) == [policy.pad('down')]
    assert not picks(mem)


def test_a_march_never_sends_the_hero_even_when_he_is_the_only_one():
    """Owner 2026-11-12: the hero stays one road castle behind the front.

    g1163 2026-11-12 12:4x: I:c9:サヌト>オシロガ:328 marched どうし to the front,
    which emptied his castle and walked him back on the next cycle.
    """
    mem = _memory('move', general=chart.HERO)
    actions = _step(mem, [chart.HERO])
    assert policy.pad('a') not in actions
    assert chart.HERO not in (_plan_region(mem).get('general_override') or {}).values()
    assert any(r.get('decision') == 'hero_move_refused' for r in mem['_records'])


def test_an_attack_never_takes_the_hero_by_cursor():
    mem = _memory('attack')
    _step(mem, [chart.HERO])
    assert not picks(mem)
    assert chart.HERO not in (_plan_region(mem).get('general_override') or {}).values()


def test_other_chapters_keep_choosing_the_planned_general():
    mem = _memory('attack', chapter=8)
    _step(mem, ['ミント', 'ジェラート', 'シナモン'])
    assert not picks(mem)
