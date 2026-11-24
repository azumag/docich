"""The general status page belongs to its reader, not to the orphan close.

2026-11-24: the monthly scan failed every month with ``status_unreadable``.
``orphan_battle_menu_step`` closed the general status page with B *before* the
flows that read it ran (the orphan step dispatches before post_month_status in
``_decide_impl``).  These tests go through the real ``decide`` dispatch with a
live 256-wide frame, so the ordering itself is under test -- calling the owner
directly would keep the original blind spot.

The frame's own header is chapter 4 / month 34-6; ``observe_events`` adopts it
before the dispatch, so the owner states must be armed for that scope.

The only unreadable cases use a synthetic Screen (the repo has no 城在籍
frame); every owner-read assertion below uses
``tests/fixtures/hanjuku/g1097-probe-status-marching.png`` (ヴィーナス,
しんぐんちゅうです, HP 82/82, ちんぎん 3 G, Bボタンでもどる).
"""
import json
from pathlib import Path

from docich.hanjuku.flows import boss_pursuit, house, post_month_status
from docich.hanjuku.flows.battle_menus import orphan_battle_menu_step
from docich.hanjuku.flows.status_panel import status_panel_owner_active
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import Screen, parse
from docich.hanjuku_bot import decide

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
PANEL = 'g1097-probe-status-marching.png'
IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g1097-4ddae5c8',
            'generation': 1097, 'lease_id': 'lease'}


def frame():
    return read_png(ROOT / PANEL).resized()


def panel():
    return parse(frame())


def observed_scope():
    """The chapter and month the frame's own header shows (adopted by decide)."""
    header = panel().header
    return header['chapter'], f"{header['year']}-{header['month']}"


def policy(**extra):
    """A minimal policy that already knows this run and this frame's scope."""
    chapter, month = observed_scope()
    mem = {'chapter': chapter, 'month': month, 'tick': 1000, 'name': {'done': True},
           'survey_run_identity': dict(IDENTITY), **extra}
    return {'policy': mem}


def observe(state):
    # Every observation round-trips through JSON, like the bot's policy file.
    return decide(frame(), json.loads(json.dumps(state)), run_identity=IDENTITY)


def decisions(result):
    return [record['decision'] for record in result.get('_records') or []]


def post_month_state(**over):
    chapter, month = observed_scope()
    state = {'stage': 'status', 'scope': [chapter, month], 'identity': dict(IDENTITY),
             'selected': 'ヴィーナス', 'statuses': {}, 'names': [], 'dirty': [],
             'markers': {}, 'tasks': [], 'phase_reads': 0, 'steps': 0,
             'started_tick': 900}
    state.update(over)
    return state


def house_state(**over):
    chapter, _ = observed_scope()
    state = {'phase': 'status', 'chapter': chapter, 'selected': 'ヴィーナス',
             'general': 'ヴィーナス', 'age': 0, 'total': 0, 'pending': [], 'seen': []}
    state.update(over)
    return state


def boss_probe(fort_reads):
    return {'phase': 'unit_move', 'fort_stage': 'status', 'fort_reads': fort_reads,
            'general': 'ヴィーナス', 'step': '9-boss', 'reads': 0,
            'seeks': 0, 'x_retries': 0}


def test_dispatcher_lets_the_monthly_scan_read_the_real_status_panel():
    assert 'Bボタンでもどる' in panel().text
    # A leftover plain-command orphan is the g1190 A-forever shape: the owner
    # branch must return None before the plain-command A path, not just skip B.
    orphan = {'identity': dict(IDENTITY), 'attempts': 3, 'plain_command': True}
    actions, result = observe(policy(post_month_status=post_month_state(),
                                     orphan_battle_menu=orphan))
    # No battle_status_screen_return: the orphan close stood down, the owner
    # read the panel and returns its own B on the way back to its roster.
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' not in decisions(result)
    assert 'battle_menu_plain_advance' not in decisions(result)
    state = result['policy']['post_month_status']
    assert state['stage'] == 'roster_next'
    assert state['statuses']['ヴィーナス']['general'] == 'ヴィーナス'
    assert state['statuses']['ヴィーナス']['location'] == 'march'
    # The stale claim is dropped so it cannot press A on the roster frame that
    # follows the owner's B.
    assert 'orphan_battle_menu' not in result['policy']


def test_dispatcher_lets_the_arrival_probe_read_the_real_status_panel():
    probe = {'phase': 'status', 'general': 'ヴィーナス', 'step': 'probe', 'reads': 0,
             'seeks': 0, 'status_reads': 0, 'x_retries': 0}
    actions, result = observe(policy(arrival_status_probe=probe))
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' not in decisions(result)
    assert 'arrival_status_probe_verified' in decisions(result)
    assert result['policy']['arrival_status_probe']['phase'] == 'close'


def test_dispatcher_still_closes_an_unowned_status_panel():
    # g1190 2026-11-22 09:00 (n=30341): a leftover plain-command orphan had been
    # advancing this page with A forever.  With no owner it must still take the
    # bounded B (and tests/test_hanjuku_plain_battle_fade.py stays valid).
    orphan = {'identity': dict(IDENTITY), 'attempts': 3, 'plain_command': True}
    actions, result = observe(policy(orphan_battle_menu=orphan))
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' in decisions(result)
    assert 'orphan_battle_menu' not in result['policy']


def test_dispatcher_closes_a_foreign_run_leftover_status_panel():
    other = {**IDENTITY, 'lease_id': 'other'}
    actions, result = observe(policy(post_month_status=post_month_state(identity=other)))
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' in decisions(result)


def test_dispatcher_closes_a_previous_month_leftover_status_panel():
    actions, result = observe(policy(post_month_status=post_month_state(scope=[4, '33-12'])))
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' in decisions(result)


def test_dispatcher_closes_a_status_panel_for_another_general():
    actions, result = observe(policy(house=house_state(selected='ゼウス', general='ゼウス')))
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' in decisions(result)


def test_dispatcher_lets_the_house_scan_read_the_real_status_panel():
    actions, result = observe(policy(house=house_state()))
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' not in decisions(result)
    assert 'house_egg_seen' in decisions(result)
    assert result['policy']['house']['phase'] == 'roster_next'
    assert result['policy']['house']['seen'] == ['ヴィーナス']


def test_house_status_reads_are_bounded_and_close_with_b():
    # Synthetic Screen (no 城在籍 frame in the repo): the trigger text is on
    # screen but the status rows cannot be read as the selected general.  A
    # ``text`` screen also has the shorter house_idle_escape (10 observations),
    # so this uses an unclassified frame: that is exactly the unreadable panel
    # whose status phase could otherwise return [] until STEP_LIMIT.
    screen = Screen([], None, 'HP 82/82 ちんぎん 3 G Bボタンでもどる', kind='unknown')
    state = policy(house=house_state())
    actions = []
    for _ in range(house.STATUS_READ_LIMIT - 1):
        actions = house.step(screen, state['policy'], None)
        assert actions == []
        state = {'policy': json.loads(json.dumps(state['policy']))}
    actions = house.step(screen, state['policy'], None)
    assert actions == [pad('b')]
    assert [r['decision'] for r in state['policy']['_records']].count('house_status_unreadable') == 1
    assert state['policy']['house']['phase'] == 'close'


def test_boss_fort_status_uses_its_bounded_a():
    # The frame header is chapter 4, which decide would adopt over chapter 9;
    # drive this owner directly and pass the same pre-flow snapshot the
    # orchestrator computes.
    mem = {'chapter': 9, 'month': '9-1', 'tick': 1000, '_records': [],
           'boss_pursuit': boss_probe(0)}
    screen = panel()
    assert status_panel_owner_active(mem, screen) is True
    assert orphan_battle_menu_step(screen, mem, owner_active=True) is None
    # The 砦 route presses A to open its destination picker; the orphan B must
    # not consume that frame.
    assert boss_pursuit.step(screen, mem, None) == [pad('a')]


def test_boss_fort_status_still_closes_after_its_own_read_limit():
    mem = {'chapter': 9, 'month': '9-1', 'tick': 1000, '_records': [],
           'boss_pursuit': boss_probe(40)}
    screen = panel()
    assert status_panel_owner_active(mem, screen) is True
    assert orphan_battle_menu_step(screen, mem, owner_active=True) is None
    assert boss_pursuit.step(screen, mem, None) == [pad('b')]
    assert 'boss_pursuit_unverified' in [r['decision'] for r in mem['_records']]
    assert mem['boss_pursuit']['outcome'] == 'fort_picker_missing'
    assert 'battle_status_screen_return' not in [r['decision'] for r in mem['_records']]


def test_named_status_owner_survives_an_unreadable_egg_row():
    screen = panel()
    # The owner still needs its bounded retry when the name is legible but the
    # egg row is not. Do not let the name-only guard turn that into an early B.
    screen.lines = [line for line in screen.lines if line.y != 111]
    assert house.general_status_name(screen) == 'ヴィーナス'
    monthly = policy(post_month_status=post_month_state())['policy']
    monthly['_run_identity'] = dict(IDENTITY)
    assert status_panel_owner_active(monthly, screen) is True
    pursuit = {'chapter': 9, 'month': '9-1', 'tick': 1000, '_records': [],
               'boss_pursuit': boss_probe(0)}
    assert status_panel_owner_active(pursuit, screen) is True


def test_post_month_status_leaves_a_different_general_immediately():
    mem = policy(post_month_status=post_month_state(selected='ゼウス'))['policy']
    mem['_run_identity'] = dict(IDENTITY)
    mem['_records'] = []
    screen = panel()
    assert status_panel_owner_active(mem, screen) is False
    assert post_month_status.step(screen, mem, None) == [pad('b')]
    state = mem['post_month_status']
    assert state['stage'] == 'blocked'
    assert state['failure'] == 'status_general_mismatch'
    recorded = [r['decision'] for r in mem['_records']]
    assert 'post_month_status_unverified' in recorded
    # Do not let _observe_status write another general's egg into the ledger.
    assert 'house_egg_seen' not in recorded
    assert 'ヴィーナス' not in mem.get('house_eggs', {})


def test_boss_pursuit_closes_a_different_generals_status_immediately():
    mem = {'chapter': 9, 'month': '9-1', 'tick': 1000, '_records': [],
           'boss_pursuit': boss_probe(0)}
    mem['boss_pursuit']['general'] = 'ゼウス'
    screen = panel()
    assert status_panel_owner_active(mem, screen) is False
    assert boss_pursuit.step(screen, mem, None) == [pad('b')]
    assert mem['boss_pursuit']['outcome'] == 'status_general_mismatch'
    assert mem['boss_pursuit']['fort_reads'] == 0
    assert 'boss_pursuit_unverified' in [r['decision'] for r in mem['_records']]
