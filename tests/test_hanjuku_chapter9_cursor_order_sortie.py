"""Chapter 9 sends whoever the list cursor is on; the hero goes last.

Owner 2026-11-11: 9話にかぎり、出撃の時に将軍を選ぶ必要ないので、カーソルがあった
順に出撃させれば短縮になる。ただし主人公だけはゲームオーバーにならないために最後とする.
"""
from docich import hanjuku_chart as chart
from docich import hanjuku_policy as policy

from test_hanjuku_off_chart import _move_list, _move_memory
from docich.hanjuku.state.regions import plan as _plan_region, world as _world

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



def test_an_attack_sends_the_cursor_general_top_down():
    """Owner 2026-11-12: 将軍のステータスによらず、どんどん上から順に将軍を送る."""
    mem = _memory('attack')
    actions = _step(mem, ['ミント', 'ジェラート', 'シナモン', 'マフィン'])
    assert actions == [policy.pad('a')]
    assert _plan_region(mem)['general_override'][mem['_test_step']] == 'ミント'


def test_a_move_prefers_a_healthier_general_when_the_cursor_one_is_wounded():
    """Reinforcements are what the front fights with (v745)."""
    mem = _memory('move')
    _world(mem)['general_hp'] = {'ミント': {'hp': 8, 'max': 32},
                                 'ジェラート': {'hp': 48, 'max': 49}}
    actions = _step(mem, ['ミント', 'ジェラート'])
    assert actions == [policy.pad('down')]
    assert any(r.get('decision') == 'healthy_mover_pick' for r in mem['_records'])


def test_a_move_keeps_the_cursor_when_nobody_is_healthier():
    mem = _memory('move')
    _world(mem)['general_hp'] = {'ミント': {'hp': 30, 'max': 32},
                                 'ジェラート': {'hp': 40, 'max': 49}}
    actions = _step(mem, ['ミント', 'ジェラート'])
    assert policy.pad('a') in actions


def test_a_move_prefers_a_fresh_recruit_over_a_wounded_general():
    """Generals never heal in a run; a fresh recruit arrives whole (v746)."""
    mem = _memory('move')
    _world(mem)['general_hp'] = {'ミント': {'hp': 8, 'max': 32}}
    actions = _step(mem, ['ミント', 'ジェラート'])      # ジェラート has no record
    assert actions == [policy.pad('down')]
    assert any(r.get('decision') == 'healthy_mover_pick' for r in mem['_records'])


def test_a_half_dead_leader_gives_way_to_a_whole_general():
    """A battle is an HP duel and generals never heal (v747)."""
    mem = _memory('attack')
    _world(mem)['general_hp'] = {'ミント': {'hp': 10, 'max': 32},
                                 'ジェラート': {'hp': 48, 'max': 49}}
    actions = _step(mem, ['ミント', 'ジェラート'])
    assert actions == [policy.pad('down')]
    assert any(r.get('decision') == 'full_hp_leader' for r in mem['_records'])


def test_a_mostly_whole_leader_stays_at_the_cursor():
    mem = _memory('attack')
    _world(mem)['general_hp'] = {'ミント': {'hp': 24, 'max': 32},
                                 'ジェラート': {'hp': 49, 'max': 49}}
    assert policy.pad('a') in _step(mem, ['ミント', 'ジェラート'])


def test_the_leader_is_the_whole_general_with_the_most_hp():
    """The オオキク retake lost a gauntlet at 19 HP; lead with the biggest.

    g1163 2026-11-13 13:4x: the cursor was ミモザ, ユイートル was picked as
    the first whole general, and the ally fought 80/42-HP defenders down to
    19 HP. The whole general with the most HP should lead.
    """
    mem = _memory('attack')
    _world(mem)['general_hp'] = {'ミント': {'hp': 10, 'max': 32},
                                 'ジェラート': {'hp': 48, 'max': 49},
                                 'シナモン': {'hp': 60, 'max': 60}}
    _step(mem, ['ミント', 'ジェラート', 'シナモン'])
    records = [r for r in mem['_records'] if r.get('decision') == 'full_hp_leader']
    assert records and records[-1]['observed_metric']['chosen'] == 'シナモン'


def test_a_just_defended_front_keeps_its_strongest_general_home():
    """g1163 2026-11-13 15:3x-16:1x: defences lost at enemy 1-8 HP.

    The healthiest resident had marched out with the attack wave. When the
    source was attacked inside the window, the next-best general leads.
    """
    mem = _memory('attack')
    _world(mem)['general_hp'] = {'ミント': {'hp': 10, 'max': 32},
                                 'ジェラート': {'hp': 48, 'max': 49},
                                 'シナモン': {'hp': 60, 'max': 60}}
    mem['tick'] = 100
    mem['defense_seen'] = {ROAD[1]: 90}
    _step(mem, ['ミント', 'ジェラート', 'シナモン'])
    records = [r for r in mem['_records'] if r.get('decision') == 'full_hp_leader']
    assert records and records[-1]['observed_metric']['chosen'] == 'ジェラート'


def test_a_front_without_a_defence_still_leads_with_the_strongest():
    mem = _memory('attack')
    _world(mem)['general_hp'] = {'ミント': {'hp': 10, 'max': 32},
                                 'ジェラート': {'hp': 48, 'max': 49},
                                 'シナモン': {'hp': 60, 'max': 60}}
    _step(mem, ['ミント', 'ジェラート', 'シナモン'])
    records = [r for r in mem['_records'] if r.get('decision') == 'full_hp_leader']
    assert records and records[-1]['observed_metric']['chosen'] == 'シナモン'
