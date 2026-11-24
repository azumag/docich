"""The general status page belongs to its reader, not to the orphan close.

2026-11-24: the monthly scan failed every month with ``status_unreadable``.
``orphan_battle_menu_step`` closed the general status page with B *before* the
flows that read it ran (the orphan step dispatches before post_month_status in
``_decide_impl``).  These tests go through the real ``decide`` dispatch with a
live 256-wide frame, so the ordering itself is under test -- calling the owner
directly would keep the original blind spot.

The marching frame's own header is chapter 4 / month 34-6 and the castle
frame's is chapter 4 / month 47-3; ``observe_events`` adopts each header before
the dispatch, so the owner states must be armed for that frame's scope.

Most owner-read assertions use
``tests/fixtures/hanjuku/g1097-probe-status-marching.png`` (ヴィーナス,
しんぐんちゅうです, HP 82/82, ちんぎん 3 G, Bボタンでもどる).  The 城在籍
panel (「しろのなかにいます」) was captured with the isolated probe on
2026-11-24 from a copy of the preserved g1190-ch1v7
``ch1v7-20261122-after-field-a.state`` and is
``g1190-ch4-castle-status-panel.png`` (ヴィーナス, chapter 4 / 47-3).
Unreadable cases use a synthetic Screen.
"""
import json
from pathlib import Path

import pytest

from docich.hanjuku.flows import boss_pursuit, house, post_month_status
from docich.hanjuku.flows.battle_menus import orphan_battle_menu_step
from docich.hanjuku.flows.status_panel import status_panel_owner_active
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.font import TextLine
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import Screen, parse
from docich.hanjuku_bot import decide

# The synthetic frames use the same measured glyph canvas as the house tests.
from test_hanjuku_house import Canvas, roster as synthetic_roster, status as synthetic_status

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
PANEL = 'g1097-probe-status-marching.png'
CASTLE_PANEL = 'g1190-ch4-castle-status-panel.png'
CAMP_PANEL = 'g1097-status-holds-camp.png'
FORT_PANEL = 'g1097-status-inside-fort.png'
IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g1097-4ddae5c8',
            'generation': 1097, 'lease_id': 'lease'}


def frame(name=PANEL):
    return read_png(ROOT / name).resized()


def panel(name=PANEL):
    return parse(frame(name))


def observed_scope(name=PANEL):
    """The chapter and month the frame's own header shows (adopted by decide)."""
    header = panel(name).header
    return header['chapter'], f"{header['year']}-{header['month']}"


def policy(name=PANEL, **extra):
    """A minimal policy that already knows this run and this frame's scope."""
    chapter, month = observed_scope(name)
    mem = {'chapter': chapter, 'month': month, 'tick': 1000, 'name': {'done': True},
           'survey_run_identity': dict(IDENTITY), **extra}
    return {'policy': mem}


def observe(state, name=PANEL):
    # Every observation round-trips through JSON, like the bot's policy file.
    return decide(frame(name), json.loads(json.dumps(state)), run_identity=IDENTITY)


def decisions(result):
    return [record['decision'] for record in result.get('_records') or []]


def post_month_state(name=PANEL, **over):
    chapter, month = observed_scope(name)
    state = {'stage': 'status', 'scope': [chapter, month], 'identity': dict(IDENTITY),
             'selected': 'ヴィーナス', 'statuses': {}, 'names': [], 'dirty': [],
             'markers': {}, 'tasks': [], 'phase_reads': 0, 'steps': 0,
             'started_tick': 900}
    state.update(over)
    return state


def house_state(name=PANEL, **over):
    chapter, _ = observed_scope(name)
    state = {'phase': 'status', 'chapter': chapter, 'selected': 'ヴィーナス',
             'general': 'ヴィーナス', 'age': 0, 'total': 0, 'pending': [], 'seen': []}
    state.update(over)
    return state


def boss_probe(fort_reads, general='ヴィーナス'):
    return {'phase': 'unit_move', 'fort_stage': 'status', 'fort_reads': fort_reads,
            'general': general, 'step': '9-boss', 'reads': 0,
            'seeks': 0, 'x_retries': 0}


# A later-chapter general whose real name carries a digit.  The name-only
# reader must not treat that digit as OCR noise when it is the expected name.
DIGIT_TARGET = 'てつじん8ごう'


def observe_frame(fr, state):
    """One decide() observation with the policy JSON round-tripped."""
    return decide(fr, json.loads(json.dumps(state)), run_identity=IDENTITY)


def policy_at(chapter, month, **extra):
    mem = {'chapter': chapter, 'month': month, 'tick': 1000, 'name': {'done': True},
           'survey_run_identity': dict(IDENTITY), **extra}
    return {'policy': mem}


def post_month_state_at(chapter, month, **over):
    state = {'stage': 'status', 'scope': [chapter, month], 'identity': dict(IDENTITY),
             'selected': 'ヴィーナス', 'statuses': {}, 'names': [], 'dirty': [],
             'markers': {}, 'tasks': [], 'phase_reads': 0, 'steps': 0,
             'started_tick': 900}
    state.update(over)
    return state


def synthetic_status_frame(name=None):
    """A measured-layout status page drawn without a header or name row.

    ``name=None`` leaves the name row blank/absent, the synthetic unreadable
    frame used by the owner tests.  Every other status field is present, so
    ``battle_menus._is_general_status_panel_text`` recognises the page and the
    orphan-B path is a real alternative to the owner handler.
    """
    canvas = Canvas()
    if name:
        canvas.text(24, 39, name)
        canvas.text(88, 39, 'しょうぐん')
    canvas.text(24, 55, 'HP 85/85 P')
    canvas.text(24, 71, 'ちんぎん 3 G')
    canvas.text(24, 87, 'せんとう 8')
    canvas.text(24, 103, 'ないせい 7')
    canvas.text(24, 119, 'たまご こわれている')
    canvas.text(24, 135, 'しゅみ 3')
    canvas.text(24, 151, 'しろのなかにいます')
    canvas.text(24, 183, 'Bボタンでもどる')
    return canvas.frame()


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


def test_real_castle_status_fixture_is_the_castle_page():
    # Isolated probe 2026-11-24: a copy of the preserved g1190-ch1v7 state
    # (ch1v7-20261122-after-field-a.state) was loaded into the snes9x core and
    # X -> しょうぐん -> ヴィーナス -> A opened this page.  No live runtime was
    # used.  The header is adopted by decide, so the fixture scope is explicit.
    screen = panel(CASTLE_PANEL)
    assert screen.header['chapter'] == 4
    assert 'しろのなかにいます' in screen.text
    info = house.general_status(screen)
    assert info['general'] == 'ヴィーナス'
    assert info['location'] == 'castle'


def test_dispatcher_lets_the_monthly_scan_read_the_real_castle_status_panel():
    actions, result = observe(
        policy(CASTLE_PANEL, post_month_status=post_month_state(CASTLE_PANEL)),
        CASTLE_PANEL)
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' not in decisions(result)
    state = result['policy']['post_month_status']
    assert state['stage'] == 'roster_next'
    assert state['statuses']['ヴィーナス']['location'] == 'castle'


def test_dispatcher_lets_the_house_scan_read_the_real_castle_status_panel():
    actions, result = observe(
        policy(CASTLE_PANEL, house=house_state(CASTLE_PANEL)), CASTLE_PANEL)
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' not in decisions(result)
    assert 'house_egg_seen' in decisions(result)
    assert result['policy']['house']['phase'] == 'roster_next'
    assert result['policy']['house']['seen'] == ['ヴィーナス']


def test_dispatcher_lets_the_arrival_probe_read_the_real_castle_status_panel():
    probe = {'phase': 'status', 'general': 'ヴィーナス', 'step': 'probe', 'reads': 0,
             'seeks': 0, 'status_reads': 0, 'x_retries': 0}
    actions, result = observe(
        policy(CASTLE_PANEL, arrival_status_probe=probe), CASTLE_PANEL)
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' not in decisions(result)
    assert 'arrival_status_probe_verified' in decisions(result)
    assert result['policy']['arrival_status_probe']['phase'] == 'close'
    assert result['policy']['arrival_general_observations']['ヴィーナス']['location'] == 'castle'


def test_arrival_owner_stands_down_when_its_step_would_defer():
    # #5407: arrival_status.step returns None before it increments `reads`
    # while any post_month/house/recall state is present.  A phase-only owner
    # would suppress the orphan B and leave the panel to nobody.  Here the
    # house state is present but its phase is not a status owner, so the real
    # dispatch must close the page instead of holding it forever.
    chapter, _ = observed_scope(CASTLE_PANEL)
    probe = {'phase': 'status', 'general': 'ヴィーナス', 'step': 'probe', 'reads': 0,
             'seeks': 0, 'status_reads': 0, 'x_retries': 0}
    house_idle = {'phase': 'idle', 'chapter': chapter, 'selected': 'ヴィーナス',
                  'general': 'ヴィーナス', 'pending': [], 'seen': []}
    mem = {'chapter': chapter, 'month': observed_scope(CASTLE_PANEL)[1],
           'house': house_idle, 'arrival_status_probe': probe}
    assert status_panel_owner_active(mem, panel(CASTLE_PANEL)) is False
    actions, result = observe(
        policy(CASTLE_PANEL, arrival_status_probe=probe, house=house_idle),
        CASTLE_PANEL)
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' in decisions(result)
    assert result['policy']['arrival_status_probe']['reads'] == 0
    assert result['policy']['arrival_status_probe']['phase'] == 'status'


def test_dispatcher_does_not_let_the_monthly_scan_claim_another_generals_page():
    # #5410: stage 'status' names the general in `selected`.  The real panel is
    # ヴィーナス, so a scan that opened ゼウス does not own it and the orphan B
    # must close the page instead of spending its 12-read retry budget.
    actions, result = observe(
        policy(CASTLE_PANEL,
               post_month_status=post_month_state(CASTLE_PANEL, selected='ゼウス')),
        CASTLE_PANEL)
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' in decisions(result)
    state = result['policy']['post_month_status']
    # The orphan B is the pre-flow snapshot's action here: owner_active was
    # false, so post_month_status.step never ran and the state stays at
    # 'status'.  The flow-level early leave is covered directly below.
    assert state['stage'] == 'status'
    assert state['phase_reads'] == 0
    assert not state['statuses']
    recorded = decisions(result)
    assert 'post_month_status_unverified' not in recorded
    assert 'house_egg_seen' not in recorded


def test_named_status_owner_survives_an_unreadable_egg_row():
    screen = panel()
    # The owner still needs its bounded retry when the name is legible but the
    # egg row is not. Do not let the name-only guard turn that into an early B.
    screen.lines = [line for line in screen.lines if line.y != 119]
    assert house.general_status_name(screen) == 'ヴィーナス'
    assert house.general_status(screen) is None
    monthly = policy(post_month_status=post_month_state())['policy']
    monthly['_run_identity'] = dict(IDENTITY)
    assert status_panel_owner_active(monthly, screen) is True
    pursuit = {'chapter': 9, 'month': '9-1', 'tick': 1000, '_records': [],
               'boss_pursuit': boss_probe(0)}
    assert status_panel_owner_active(pursuit, screen) is True


def test_general_status_name_rejects_digits_like_general_status():
    # The name-only reader must keep general_status()'s digit/space rejection;
    # otherwise an OCR-misread name could claim another page.
    cells = tuple((24 + i * 8, ch) for i, ch in enumerate('ヴィーナス1'))
    cells += tuple((88 + i * 8, ch) for i, ch in enumerate('しょうぐん'))
    assert house.general_status_name(Screen([TextLine(39, cells)], None, '',
                                              kind='text')) is None
    cells = tuple((24 + i * 8, ch) for i, ch in enumerate('ヴィーナス'))
    cells += tuple((88 + i * 8, ch) for i, ch in enumerate('しょうぐん'))
    assert house.general_status_name(Screen([TextLine(39, cells)], None, '',
                                              kind='text')) == 'ヴィーナス'


def test_post_month_status_skips_a_different_generals_egg_and_continues():
    mem = policy(post_month_status=post_month_state(selected='ゼウス'))['policy']
    mem['_run_identity'] = dict(IDENTITY)
    mem['_records'] = []
    screen = panel()
    assert status_panel_owner_active(mem, screen) is False
    assert post_month_status.step(screen, mem, None) == [pad('b')]
    state = mem['post_month_status']
    # A readable foreign name leaves only this general behind; it must not
    # finish the whole monthly scan as status_unreadable (#5414 follow-up).
    assert state['stage'] == 'roster_next'
    assert 'failure' not in state
    assert state['skipped'] == ['ゼウス']
    assert state['walked'] == ['ゼウス']
    recorded = decisions({'_records': mem['_records']})
    assert 'post_month_status_unverified' in recorded
    assert 'house_egg_seen' not in recorded
    assert 'ヴィーナス' not in mem.get('house_eggs', {})


def test_boss_pursuit_closes_a_different_generals_status_immediately():
    mem = {'chapter': 9, 'month': '9-1', 'tick': 1000, '_records': [],
           'boss_pursuit': {**boss_probe(0), 'general': 'ゼウス'}}
    screen = panel()
    assert status_panel_owner_active(mem, screen) is False
    assert boss_pursuit.step(screen, mem, None) == [pad('b')]
    assert mem['boss_pursuit']['outcome'] == 'status_general_mismatch'
    assert mem['boss_pursuit']['fort_reads'] == 0
    assert 'boss_pursuit_unverified' in decisions({'_records': mem['_records']})


def test_monthly_scan_without_a_named_target_does_not_claim_the_page():
    # 読めない/未設定の対象名は false で従来の B（#5410）。  stage 'status' の
    # `selected` が対象将軍名の実コード上の置き場所。
    state = post_month_state(CASTLE_PANEL)
    state.pop('selected')
    actions, result = observe(
        policy(CASTLE_PANEL, post_month_status=state), CASTLE_PANEL)
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' in decisions(result)


def test_boss_fort_status_does_not_claim_another_generals_real_panel():
    mem = {'chapter': 9, 'month': '9-1', 'tick': 1000, '_records': [],
           'boss_pursuit': {**boss_probe(0), 'general': 'ゼウス'}}
    screen = panel(CASTLE_PANEL)
    owner = status_panel_owner_active(mem, screen)
    assert owner is False
    assert orphan_battle_menu_step(screen, mem, owner_active=owner) == [pad('b')]
    assert 'battle_status_screen_return' in [r['decision'] for r in mem['_records']]


@pytest.mark.parametrize('name', [CASTLE_PANEL, PANEL, CAMP_PANEL, FORT_PANEL])
def test_real_general_status_pages_still_trigger_the_status_close(name):
    # The three older frames are the camp (キャンプしています), fort
    # (キャンプのなかにいます) and marching (しんぐんちゅうです) pages; the new
    # castle frame fills the しろのなかにいます location.  All are the same
    # measured status layout and stay closable when no owner is active.
    screen = panel(name)
    assert 'Bボタンでもどる' in screen.text
    mem = {}
    assert orphan_battle_menu_step(screen, mem) == [pad('b')]
    assert mem['_records'][-1]['decision'] == 'battle_status_screen_return'


def test_hp_and_wage_without_the_status_fields_do_not_close_a_panel():
    # A camp/army panel can carry HP + ちんぎん and the return sentence without
    # being the general status page.  The g1190 close must not take it.
    screen = Screen([], None, 'HP 40/40 ちんぎん 3 G Bボタンでもどるですじゃ!',
                    kind='text')
    mem = {}
    assert orphan_battle_menu_step(screen, mem) is None
    assert not mem


def test_real_fort_card_with_hp_and_wage_is_not_a_status_panel():
    # Measured 砦 card: it has HP and ちんぎん but not the return sentence.
    screen = panel('g1097-probe-fort-status.png')
    assert 'HP' in screen.text and 'ちんぎん' in screen.text
    assert 'Bボタンでもどる' not in screen.text
    assert orphan_battle_menu_step(screen, {}) is None


def test_house_status_reads_are_bounded_and_close_with_b():
    # Synthetic unreadable Screen: the two trigger rows are on screen but the
    # status layout cannot be read as the selected general.  A ``text`` screen
    # also has the shorter house_idle_escape (10 observations),
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


def test_unreadable_name_keeps_monthly_ownership_and_does_not_take_the_b():
    # #5414 follow-up: the name-only reader can return None on a real frame
    # (temporary OCR hole).  That must keep the #5403 owner, not hand the
    # monthly scan's page to the orphan B (which regressed to status_unreadable).
    screen = panel()
    screen.lines = [line for line in screen.lines if line.y not in (31, 39)]
    assert house.general_status_name(screen) is None
    mem = policy(post_month_status=post_month_state())['policy']
    mem['_run_identity'] = dict(IDENTITY)
    assert status_panel_owner_active(mem, screen) is True

    actions, result = observe_frame(
        synthetic_status_frame(), policy(post_month_status=post_month_state()))
    assert actions == []
    assert 'battle_status_screen_return' not in decisions(result)
    owner = result['policy']['post_month_status']
    assert owner['stage'] == 'status'
    assert owner['phase_reads'] == 1
    assert 'failure' not in owner


def test_house_owner_keeps_a_named_page_without_the_egg_row():
    # #5403 read the whole card through general_status; the name row remains
    # sufficient while the egg row is unreadable, so the house walk is never
    # stricter than #5403.
    screen = panel()
    screen.lines = [line for line in screen.lines if line.y != 119]
    assert house.general_status(screen) is None
    assert house.general_status_name(screen) == 'ヴィーナス'
    mem = policy(house=house_state())['policy']
    mem['_run_identity'] = dict(IDENTITY)
    assert status_panel_owner_active(mem, screen) is True


def synthetic_roster_frame(names=('ヴィーナス', 'ゼウス'), selected=0,
                           header='4わ34ねん6のつき250G'):
    """A 256-wide roster whose header matches the synthetic status scope."""
    canvas = Canvas()
    canvas.text(56, 7, header)
    for index, name in enumerate(names):
        canvas.text(168, 39 + index * 16, name)
    canvas.text(24, 183, 'Aボタンでステータスひょうじ')
    canvas.text(24, 199, 'コマンドにもどりますじゃ!')
    canvas.hand(146, 33 + selected * 16)
    return canvas.frame()


def test_monthly_scan_recovers_after_the_orphan_closes_a_foreign_panel():
    # The dispatcher's orphan guard takes the B for a readable foreign page
    # (owner_active is false), so post_month_status.step never sees that
    # panel.  The next roster frames used to retry for STATUS_RETRIES and then
    # fail the whole month; after a short bounded grace the scan now skips
    # only that general and steps to the next row.
    state = policy_at(4, '34-6', post_month_status=post_month_state_at(
        4, '34-6', selected='ヴィーナス'))
    actions, result = observe_frame(synthetic_status_frame('ゼウス'), state)
    assert actions == [pad('b')]
    assert 'battle_status_screen_return' in decisions(result)
    assert result['policy']['post_month_status']['stage'] == 'status'

    for _ in range(post_month_status.STATUS_OPEN_GRACE - 1):
        actions, result = observe_frame(synthetic_roster_frame(), result)
        assert actions == []
        assert result['policy']['post_month_status']['stage'] == 'status'

    actions, result = observe_frame(synthetic_roster_frame(), result)
    assert actions == [pad('down')]
    owner = result['policy']['post_month_status']
    assert owner['stage'] == 'roster'
    assert owner['skipped'] == ['ヴィーナス']
    assert 'failure' not in owner


def test_readable_foreign_name_skips_only_that_general_and_keeps_walking():
    # One readable foreign name leaves that general behind; it must not finish
    # the whole monthly scan.  The next roster frame advances the walk.
    state = policy_at(1, '1-6', post_month_status=post_month_state_at(
        1, '1-6', selected='どうし', dirty=['どうし']))
    actions, result = observe_frame(synthetic_status('ゼウス'), state)
    assert actions == [pad('b')]
    owner = result['policy']['post_month_status']
    assert owner['stage'] == 'roster_next'
    assert 'failure' not in owner
    assert owner['skipped'] == ['どうし']
    assert owner['walked'] == ['どうし']
    assert not owner['statuses']
    assert 'post_month_status_unverified' in decisions(result)

    actions, result = observe_frame(
        synthetic_roster(names=('どうし', 'ゼウス'), selected=0), result)
    assert actions == [pad('down')]
    assert result['policy']['post_month_status']['stage'] == 'roster'


def test_digit_target_with_unreadable_name_is_skipped_after_status_retries():
    # The digit is part of the target's identity, so the reader may return
    # None when it refuses a digit.  Bounded by STATUS_RETRIES, the scan skips
    # only this general instead of failing the month as status_unreadable.
    state = policy_at(4, '34-6', post_month_status=post_month_state_at(
        4, '34-6', selected=DIGIT_TARGET, dirty=[DIGIT_TARGET]))
    for _ in range(post_month_status.STATUS_RETRIES - 1):
        actions, result = observe_frame(synthetic_status_frame(), state)
        assert actions == []
        owner = result['policy']['post_month_status']
        assert owner['stage'] == 'status'
        assert 'failure' not in owner
        state = {'policy': json.loads(json.dumps(result['policy']))}

    actions, result = observe_frame(synthetic_status_frame(), state)
    assert actions == [pad('b')]
    owner = result['policy']['post_month_status']
    assert owner['stage'] == 'roster_next'
    assert 'failure' not in owner
    assert owner['skipped'] == [DIGIT_TARGET]
    assert 'post_month_status_unverified' in decisions(result)


def test_expected_digit_name_is_read_and_recorded():
    # When the target itself carries a digit, the readers accept exactly that
    # digit-bearing name; no OCR-noise digit is allowed for digit-free targets.
    state = policy_at(1, '1-6', post_month_status=post_month_state_at(
        1, '1-6', selected=DIGIT_TARGET, dirty=[DIGIT_TARGET]))
    actions, result = observe_frame(synthetic_status_frame(DIGIT_TARGET), state)
    assert actions == [pad('b')]
    owner = result['policy']['post_month_status']
    assert owner['stage'] == 'roster_next'
    assert 'failure' not in owner
    assert owner['statuses'][DIGIT_TARGET]['general'] == DIGIT_TARGET


def test_boss_fort_digit_target_keeps_the_panel_when_name_is_unreadable():
    mem = {'chapter': 9, 'month': '9-1', 'tick': 1000, 'name': {'done': True},
           'survey_run_identity': dict(IDENTITY),
           'boss_pursuit': boss_probe(0, DIGIT_TARGET)}
    actions, result = observe_frame(synthetic_status_frame(), {'policy': mem})
    assert actions == []
    assert 'battle_status_screen_return' not in decisions(result)
    probe = result['policy']['boss_pursuit']
    assert 'outcome' not in probe
    assert probe['fort_reads'] == 1


def test_boss_fort_reads_the_expected_digit_name_and_opens_the_picker():
    mem = {'chapter': 9, 'month': '9-1', 'tick': 1000, 'name': {'done': True},
           'survey_run_identity': dict(IDENTITY),
           'boss_pursuit': boss_probe(0, DIGIT_TARGET)}
    actions, result = observe_frame(
        synthetic_status_frame(DIGIT_TARGET), {'policy': mem})
    assert actions == [pad('a')]
    assert 'outcome' not in result['policy']['boss_pursuit']


def test_boss_fort_closes_a_readable_foreign_name_via_dispatcher():
    fr = read_png(ROOT / 'g1190-ch9-riesling-fort-card.png').resized()
    mem = {'chapter': 9, 'month': '9-1', 'tick': 1000, 'name': {'done': True},
           'survey_run_identity': dict(IDENTITY),
           'boss_pursuit': boss_probe(0, 'ゼウス')}
    actions, result = observe_frame(fr, {'policy': mem})
    assert actions == [pad('b')]
    assert result['policy']['boss_pursuit']['outcome'] == 'status_general_mismatch'
    assert 'post_month_status_unverified' not in decisions(result)


def test_skipped_general_is_not_a_complete_membership_proof():
    mem = policy()['policy']
    mem['_run_identity'] = dict(IDENTITY)
    mem['_records'] = []
    state = post_month_state_at(
        4, '34-6', stage='roster', names=['ヴィーナス'],
        walked=['ヴィーナス', 'ゼウス'], skipped=['ゼウス'],
        top_confirmed=True, list_moves=2, dirty=[],
        statuses={'ヴィーナス': {'general': 'ヴィーナス', 'location': 'castle',
                                'broken': False}})
    assert post_month_status._complete_scan(mem, state) is True
    assert state['complete'] is False
    assert state['skipped_count'] == 1
    assert mem['general_statuses']['complete'] is False
    recorded = [record['decision'] for record in mem['_records']]
    assert 'post_month_status_skipped' in recorded
    assert 'post_month_general_departed' not in recorded
