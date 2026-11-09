"""A decided departure A is a reservation, never a world-state fact.

Regression for docich#3193 A: the confirmed target A used to commit the
order, the garrison and the en-route sortie before the input was even sent.
Now the request holds the fixed actor/source/kit while the observed facts
stay untouched until the sender journal proves the A left and the picker is
seen closed. The sender journal itself is a boundary double here; the flow
and the state transitions under test are the real ones.
"""
from unittest.mock import patch

from docich import hanjuku_chart as chart, hanjuku_policy as p
from docich.hanjuku.os_layer.screen import Screen
from test_hanjuku_execution_binding import confirm, memory
from docich.hanjuku.state.regions import flow as _flow, journal as _journal
from docich.hanjuku.state.regions import world as _world

TRACE = {'game': 'hanjuku-hero', 'runtime_id': 'test-runtime', 'generation': 741,
         'lease_id': 'test-lease', 'decision_id': 'test-runtime:741:20',
         'frame_sha256': 'f' * 64, 'planned_at': 1.0}
TARGET = Screen([], None, '', kind='map_target')
FIELD = Screen([], None, '', kind='map')


def request(mem):
    """Run the real confirm path and return the pending departure request."""
    mem['cursor'] = list(chart.castles(1)['スペンソニア'])
    with patch.object(p, 'nav_step', return_value='arrived'), \
         patch.object(p, '_target_roof_under_marker', return_value=True), \
         patch.object(p, '_want_y_jump', return_value=False):
        assert p.target_step(TARGET, mem, None) == [p.pad('a')]
    req = _flow(mem).get('departure_request')
    assert isinstance(req, dict) and req.get('step') == '1-A3'
    return req


def sent(mem, a_inputs, trace=None):
    """Inject the sender-journal receipt the orchestrator would supply."""
    trace = _flow(mem)['departure_request'].get('request_trace') if trace is None else trace
    _journal(mem)['_departure_input'] = {'request_trace': trace, 'a_inputs': a_inputs}


def assert_reserved(mem):
    assert mem['orders']['1-A3'] == 'pending'
    assert _world(mem)['garrison']['カストーラ'] == ['ゼウス', 'ヴィーナス']
    assert not mem.get('sorties') and not _world(mem).get('launched_orders')


def test_unsent_departure_request_never_commits_world_state():
    mem = memory(); confirm(mem); request(mem)
    assert_reserved(mem)
    # The same picker observed again without a send: hold, never re-decide
    # the world and never fall back to the foreign-marker cancel.
    assert p.target_step(TARGET, mem, None) == []
    assert_reserved(mem)


def test_picker_closed_without_a_send_is_an_unconfirmed_departure():
    mem = memory(); confirm(mem); request(mem)
    p.observe_sortie_transition(FIELD, mem, 'map_target')
    assert mem['orders']['1-A3'] == 'launched_unconfirmed'
    assert mem['sorties']['1-A3']['status'] == 'launched_unconfirmed'
    assert 'departure_request' not in _flow(mem)
    assert _world(mem)['garrison']['カストーラ'] == ['ゼウス', 'ヴィーナス']


def test_journaled_send_and_closed_picker_commit_the_fixed_departure():
    mem = memory(); confirm(mem); req = request(mem)
    req['request_trace'] = dict(TRACE)
    sent(mem, 1)
    p.observe_sortie_transition(FIELD, mem, 'map_target')
    assert mem['orders']['1-A3'] == 'launched'
    assert mem['sorties']['1-A3']['status'] == 'en_route'
    assert mem['sorties']['1-A3']['target'] == 'スペンソニア'
    assert _world(mem)['launched_orders']['1-A3']['general'] == 'ゼウス'
    assert _world(mem)['garrison']['カストーラ'] == ['ヴィーナス']
    assert 'departure_request' not in _flow(mem)


def test_journal_proven_unsent_resends_within_bounds():
    mem = memory(); confirm(mem); req = request(mem)
    req['request_trace'] = dict(TRACE)
    for resends in (1, 2):
        sent(mem, 0)
        assert p.target_step(TARGET, mem, None) == [p.pad('a')]
        assert_reserved(mem)
        assert not _flow(mem)['departure_request'].get('request_trace')
        _flow(mem)['departure_request']['request_trace'] = dict(TRACE)
    sent(mem, 0)
    assert p.target_step(TARGET, mem, None) == []      # resend bound reached
    assert_reserved(mem)
    assert 'departure_request' not in _flow(mem)
    assert _flow(mem)['departure_miss']['1-A3'] == 1


def test_unverifiable_receipt_never_blind_resends():
    mem = memory(); confirm(mem); req = request(mem)
    req['request_trace'] = dict(TRACE)
    for _ in range(p.DEPARTURE_UNKNOWN_HOLD_LIMIT - 1):
        assert p.target_step(TARGET, mem, None) == []  # no receipt: unknown
        assert_reserved(mem)
    # A receipt from a different decision never counts as this request's.
    sent(mem, 1, trace={**TRACE, 'decision_id': 'other'})
    assert p.target_step(TARGET, mem, None) == []
    assert 'departure_request' not in _flow(mem)
    assert_reserved(mem)


def test_sent_picker_staying_open_holds_then_releases():
    mem = memory(); confirm(mem); req = request(mem)
    req['request_trace'] = dict(TRACE)
    for _ in range(p.DEPARTURE_SENT_HOLD_LIMIT):
        sent(mem, 1)
        p.target_step(TARGET, mem, None)
    # A sent A whose picker never closed is released, not committed; the
    # request cycle is bounded and the order may re-confirm.
    assert 'departure_request' not in _flow(mem)
    assert _flow(mem)['departure_miss']['1-A3'] == 1
    assert_reserved(mem)


def test_released_request_cycles_are_bounded_then_fail():
    mem = memory(); confirm(mem)
    for _ in range(p.DEPARTURE_REQUEST_LIMIT):
        req = request(mem)
        req['request_trace'] = dict(TRACE)
        sent(mem, 0)
        for _ in range(p.DEPARTURE_RESEND_LIMIT):
            assert p.target_step(TARGET, mem, None) == [p.pad('a')]
            _flow(mem)['departure_request']['request_trace'] = dict(TRACE)
        sent(mem, 0)
        assert p.target_step(TARGET, mem, None) in ([], [p.pad('b')])
    assert mem['orders']['1-A3'] == 'failed'
    assert _world(mem)['garrison']['カストーラ'] == ['ゼウス', 'ヴィーナス']


WORLD = Screen([], None, '', kind='world_map')
FLAGS = {'カストーラ': 'own', 'スペンソニア': 'enemy', 'アルマムーン': 'own'}


def commit(mem):
    """Run the real commit path and return the armed launch probe."""
    req = request(mem)
    req['request_trace'] = dict(TRACE)
    sent(mem, 1)
    p.observe_sortie_transition(FIELD, mem, 'map_target')
    assert mem['orders']['1-A3'] == 'launched'
    probe = (_flow(mem).get('launch_probe') or {}).get('1-A3')
    assert isinstance(probe, dict) and probe.get('source') == 'カストーラ'
    return probe


def survey(monkeypatch, mem, markers):
    monkeypatch.setattr(p, 'world_flags', lambda *_: dict(FLAGS))
    monkeypatch.setattr(p, 'world_markers', lambda _, list=list: list(markers))
    p.world_map_step(WORLD, mem, None)


def marker_at(castle, dx=0, dy=0):
    x, y = chart.castles(1)[castle]
    ox, oy = chart.WORLD_MAP_OFFSET[1]
    cx, cy = x / 8 + ox + dx, y / 8 + oy + dy
    return (cx - 3, cy - 3, cx + 3, cy + 3)


def test_committed_departure_arms_launch_probe():
    mem = memory(); confirm(mem); commit(mem)
    assert _flow(mem)['launch_probe']['1-A3']['target'] == 'スペンソニア'
    assert mem['world_map_due'] is True


def test_new_marker_near_source_confirms_launch(monkeypatch):
    mem = memory(); confirm(mem); commit(mem)
    survey(monkeypatch, mem, [marker_at('カストーラ', dx=8)])
    assert '1-A3' not in (_flow(mem).get('launch_probe') or {})
    assert mem['orders']['1-A3'] == 'launched'
    assert mem['sorties']['1-A3']['status'] == 'en_route'
    assert _world(mem)['garrison']['カストーラ'] == ['ヴィーナス']
    assert any(r['decision'] == 'sortie_launch_confirmed' for r in mem['_records'])


def test_unseen_unit_waits_for_the_source_roster(monkeypatch):
    """g1133 2026-11-05: the Y view showed only the static house icon."""
    mem = memory(); confirm(mem); commit(mem)
    for _ in range(p.LAUNCH_PROBE_READS):
        survey(monkeypatch, mem, [])
    # No marker is not proof: the probe stays and the launch is not undone.
    probe = (_flow(mem).get('launch_probe') or {}).get('1-A3')
    assert isinstance(probe, dict) and probe.get('blind') is True
    assert mem['orders']['1-A3'] == 'launched'
    assert mem['sorties']['1-A3']['status'] == 'en_route'
    assert _world(mem)['garrison']['カストーラ'] == ['ヴィーナス']
    assert not _flow(mem).get('launch_miss')
    records = [r for r in mem['_records'] if r['decision'] == 'sortie_launch_unverified']
    assert records and records[-1]['resulting_event'] == 'roster_pending'
    # The next completed roster of the source castle is the evidence: gone.
    p.settle_launch_probes_from_roster(mem, 'カストーラ', ['ヴィーナス'])
    assert '1-A3' not in (_flow(mem).get('launch_probe') or {})
    assert mem['orders']['1-A3'] == 'launched'
    assert _world(mem)['garrison']['カストーラ'] == ['ヴィーナス']
    assert not _flow(mem).get('launch_miss')
    assert any(r['decision'] == 'sortie_launch_confirmed' for r in mem['_records'])


def test_roster_still_listing_the_general_rolls_the_launch_back(monkeypatch):
    mem = memory(); confirm(mem); commit(mem)
    for _ in range(p.LAUNCH_PROBE_READS):
        survey(monkeypatch, mem, [])
    p.settle_launch_probes_from_roster(mem, 'カストーラ', ['ゼウス', 'ヴィーナス'])
    assert '1-A3' not in (_flow(mem).get('launch_probe') or {})
    assert mem['orders']['1-A3'] == 'pending'
    assert '1-A3' not in (mem.get('sorties') or {})
    assert '1-A3' not in (_world(mem).get('launched_orders') or {})
    assert _world(mem)['garrison']['カストーラ'] == ['ヴィーナス', 'ゼウス']
    assert _flow(mem)['launch_miss']['1-A3'] == 1
    assert any(r['decision'] == 'sortie_launch_unverified' for r in mem['_records'])


def test_repeatedly_listed_launches_fail_the_order(monkeypatch):
    mem = memory(); confirm(mem)
    for cycle in range(p.LAUNCH_MISS_LIMIT):
        if cycle:
            # A reopened order that confirmed again arms the same probe.
            _flow(mem).setdefault('launch_probe', {})['1-A3'] = {
                'general': 'ゼウス', 'source': 'カストーラ', 'target': 'スペンソニア',
                'reads': 0, 'baseline': [], 'tick': mem['tick'], 'blind': True}
            mem['orders']['1-A3'] = 'launched'
            mem.setdefault('sorties', {})['1-A3'] = {'status': 'en_route'}
        else:
            commit(mem)
        for _ in range(p.LAUNCH_PROBE_READS):
            survey(monkeypatch, mem, [])
        p.settle_launch_probes_from_roster(mem, 'カストーラ', ['ゼウス'])
    assert mem['orders']['1-A3'] == 'failed'
    assert any(r['decision'] == 'sortie_launch_failed' for r in mem['_records'])


def test_a_blind_probe_still_rolls_back_when_the_roster_wait_expires(monkeypatch):
    mem = memory(); confirm(mem); commit(mem)
    for _ in range(p.LAUNCH_PROBE_READS):
        survey(monkeypatch, mem, [])
    mem['tick'] += p.LAUNCH_PROBE_ROSTER_TICKS
    survey(monkeypatch, mem, [])
    assert '1-A3' not in (_flow(mem).get('launch_probe') or {})
    assert mem['orders']['1-A3'] == 'pending'
    assert _world(mem)['garrison']['カストーラ'] == ['ヴィーナス', 'ゼウス']
    assert _flow(mem)['launch_miss']['1-A3'] == 1


def test_a_boss_launch_is_kept_when_the_marker_survey_and_roster_wait_expire(monkeypatch):
    # g1133 2026-11-05: chapter 6 draws marching units as portraits, so the
    # triangle survey can never confirm a boss launch. Twelve boss sorties
    # were rolled back and sent again while the armies were still walking
    # (ライム entered the boss castle at 15:39 after his launch had been
    # rolled back at 15:21). The march is kept; the roster still rolls it
    # back when it lists the general.
    # docich#3515: kept no longer pops the probe. The probe stays behind so
    # the next roster of the source castle (actively requested on blind)
    # settles the phantom; popping it left a phantom en_route forever.
    mem = memory(); confirm(mem); commit(mem)
    _flow(mem)['launch_probe']['1-A3']['target'] = 'ボス'
    for _ in range(p.LAUNCH_PROBE_READS):
        survey(monkeypatch, mem, [])
    mem['tick'] += p.LAUNCH_PROBE_ROSTER_TICKS
    survey(monkeypatch, mem, [])
    probe = (_flow(mem).get('launch_probe') or {}).get('1-A3')
    assert isinstance(probe, dict) and probe.get('kept') is True
    assert mem['sorties']['1-A3'].get('launch_kept') is True
    assert 'カストーラ' in (mem.get('roster_read_due') or {})
    assert mem['orders']['1-A3'] == 'launched'
    assert mem['sorties']['1-A3']['status'] == 'en_route'
    assert not _flow(mem).get('launch_miss')
    kept = [r for r in mem['_records'] if r['decision'] == 'sortie_launch_unconfirmed_kept']
    assert len(kept) == 1
    # A later survey waits for the roster; it does not re-record kept.
    survey(monkeypatch, mem, [])
    assert len([r for r in mem['_records'] if r['decision'] == 'sortie_launch_unconfirmed_kept']) == 1


def test_a_kept_boss_launch_settles_on_still_listed_roster(monkeypatch):
    # docich#3515 (g1133 2026-11-06): リーキ/アルテミス stayed en_route after
    # kept while camped at home. The roster that still lists the general
    # must roll the kept launch back within the same month.
    mem = memory(); confirm(mem); commit(mem)
    _flow(mem)['launch_probe']['1-A3']['target'] = 'ボス'
    for _ in range(p.LAUNCH_PROBE_READS):
        survey(monkeypatch, mem, [])
    mem['tick'] += p.LAUNCH_PROBE_ROSTER_TICKS
    survey(monkeypatch, mem, [])
    assert '1-A3' in (_flow(mem).get('launch_probe') or {})
    p.settle_launch_probes_from_roster(mem, 'カストーラ', ['ゼウス', 'ヴィーナス'])
    assert '1-A3' not in (_flow(mem).get('launch_probe') or {})
    assert mem['orders']['1-A3'] == 'pending'
    assert '1-A3' not in (mem.get('sorties') or {})
    assert _flow(mem)['launch_miss']['1-A3'] == 1


def test_a_kept_boss_launch_confirms_on_gone_roster(monkeypatch):
    # A kept probe whose general left confirms: the march stays en_route
    # and the launch is marked confirmed for the observed-end path.
    mem = memory(); confirm(mem); commit(mem)
    _flow(mem)['launch_probe']['1-A3']['target'] = 'ボス'
    for _ in range(p.LAUNCH_PROBE_READS):
        survey(monkeypatch, mem, [])
    mem['tick'] += p.LAUNCH_PROBE_ROSTER_TICKS
    survey(monkeypatch, mem, [])
    p.settle_launch_probes_from_roster(mem, 'カストーラ', ['ヴィーナス'])
    assert '1-A3' not in (_flow(mem).get('launch_probe') or {})
    assert mem['orders']['1-A3'] == 'launched'
    assert mem['sorties']['1-A3'].get('launch_confirmed') is True
    assert not _flow(mem).get('launch_miss')


def test_sortie_settled_elsewhere_drops_the_probe_without_rollback(monkeypatch):
    mem = memory(); confirm(mem); commit(mem)
    mem['sorties']['1-A3']['status'] = 'arrived'
    survey(monkeypatch, mem, [])
    assert '1-A3' not in (_flow(mem).get('launch_probe') or {})
    assert mem['orders']['1-A3'] == 'launched'
    assert not _flow(mem).get('launch_miss')
    assert not any(r['decision'] == 'sortie_launch_unverified' for r in mem['_records'])


def test_stale_baseline_marker_alone_does_not_confirm(monkeypatch):
    mem = memory(); confirm(mem)
    stale = marker_at('アルマムーン')
    survey(monkeypatch, mem, [stale])         # baseline survey before the order
    commit(mem)
    # The same marker at the same place is not the new unit.
    for _ in range(p.LAUNCH_PROBE_READS):
        survey(monkeypatch, mem, [stale])
    assert not any(r['decision'] == 'sortie_launch_confirmed' for r in mem['_records'])
    # Blind, so the source roster decides: the general is gone, he left.
    p.settle_launch_probes_from_roster(mem, 'カストーラ', ['ヴィーナス'])
    assert '1-A3' not in (_flow(mem).get('launch_probe') or {})
    assert mem['orders']['1-A3'] == 'launched'
    assert not _flow(mem).get('launch_miss')


def test_fresh_marker_anywhere_confirms_once_a_baseline_exists(monkeypatch):
    mem = memory(); confirm(mem)
    survey(monkeypatch, mem, [marker_at('アルマムーン')])
    commit(mem)
    # A unit seen marching mid-field, far from both castles, is proof enough
    # once an older survey established what was already out.
    survey(monkeypatch, mem, [marker_at('アルマムーン'), marker_at('カストーラ', dx=40, dy=40)])
    assert '1-A3' not in (_flow(mem).get('launch_probe') or {})
    assert mem['orders']['1-A3'] == 'launched'

def test_defense_interruption_releases_reservation_without_committing():
    # A defense battle closing the picker is not a departure observation,
    # even when the journal proves the A was sent: the reservation is
    # released into the unconfirmed record and the garrison fact stays.
    mem = memory(); confirm(mem); req = request(mem)
    req['request_trace'] = dict(TRACE)
    sent(mem, 1)
    battle = Screen([], None, '', kind='defense_started')
    p.observe_sortie_transition(battle, mem, 'map_target')
    assert mem['orders']['1-A3'] == 'launched_unconfirmed'
    assert mem['sorties']['1-A3']['status'] == 'launched_unconfirmed'
    assert 'departure_request' not in _flow(mem)
    assert _world(mem)['garrison']['カストーラ'] == ['ゼウス', 'ヴィーナス']


def test_finished_order_releases_stale_reservation():
    # The order the reservation belonged to is gone (it arrived, failed or
    # was cancelled): the reservation owns nothing and must not shadow the
    # foreign-marker cancel of the next target screen.
    mem = memory(); confirm(mem); request(mem)
    mem['orders'].pop('1-A3')
    mem.pop('active', None)
    assert p.target_step(TARGET, mem, None) == [p.pad('b')]
    assert 'departure_request' not in _flow(mem)


def test_reservation_of_another_order_owns_nothing():
    # A reservation whose step no longer matches the active order (e.g. an
    # earlier order finished while the picker result was pending) is dropped
    # before the current order's normal handling continues.
    mem = memory(); confirm(mem); req = request(mem)
    req['step'] = '9-Z9'
    p.target_step(TARGET, mem, None)
    assert 'departure_request' not in _flow(mem)
    assert_reserved(mem)


def test_chapter_reset_drops_pending_reservation_and_misses():
    # The chapter/run reset is the durable boundary for a restart: a pending
    # reservation and its retry budget die with the old run and never leak
    # reservations into the next one.
    from docich.hanjuku.strategy import campaign
    mem = memory(); confirm(mem); request(mem)
    _flow(mem)['departure_miss'] = {'1-A3': 1}
    campaign._enter_chapter(mem, 2, reason='test')
    assert 'departure_request' not in _flow(mem)
    assert 'departure_miss' not in _flow(mem)
    assert 'sortie_attempt' not in _flow(mem)
    assert 'orders' not in mem


def test_recall_transition_neither_commits_nor_steals_the_reservation():
    # While a recall destination picker owns the screen, an observed
    # transition is the recall's, not the sortie's: the pending reservation
    # is neither committed nor misattributed, and is released later by the
    # stale-request guards once the order is gone.
    mem = memory(); confirm(mem); req = request(mem)
    req['request_trace'] = dict(TRACE)
    sent(mem, 1)
    _flow(mem)['recall'] = {'stage': 'dest'}
    p.observe_sortie_transition(FIELD, mem, 'map_target')
    assert_reserved(mem)
    assert _flow(mem).get('departure_request', {}).get('step') == '1-A3'
    _flow(mem).pop('recall', None)
    mem['orders'].pop('1-A3')
    mem.pop('active', None)
    assert p.target_step(TARGET, mem, None) == [p.pad('b')]
    assert 'departure_request' not in _flow(mem)


def tiny_at(castle, dx=0, dy=0):
    """A 3x3 red decoration, not a marching unit."""
    cx, cy = marker_at(castle, dx, dy)[0] + 3, marker_at(castle, dx, dy)[1] + 3
    return (cx - 1, cy - 1, cx + 1, cy + 1)


def test_a_tiny_red_decoration_never_confirms_a_launch(monkeypatch):
    """g1163 2026-11-09 05:28: a 3x3 blob confirmed three launches at once."""
    mem = memory(); confirm(mem); commit(mem)
    probe = _flow(mem)['launch_probe']['1-A3']
    probe['baseline'] = [marker_at('アルマムーン', dx=0)]
    for _ in range(p.LAUNCH_PROBE_READS):
        survey(monkeypatch, mem, [marker_at('アルマムーン'), tiny_at('スペンソニア')])
    assert not any(r['decision'] == 'sortie_launch_confirmed' for r in mem['_records'])
    assert _flow(mem).get('launch_probe', {}).get('1-A3', {}).get('blind') is True


def test_a_full_size_unit_marker_far_from_both_flags_still_confirms(monkeypatch):
    mem = memory(); confirm(mem); commit(mem)
    probe = _flow(mem)['launch_probe']['1-A3']
    probe['baseline'] = [marker_at('アルマムーン')]
    far = marker_at('カストーラ', dx=60, dy=60)
    survey(monkeypatch, mem, [marker_at('アルマムーン'), far])
    assert any(r['decision'] == 'sortie_launch_confirmed' for r in mem['_records'])


def test_a_held_card_scroll_crosses_only_measured_rows():
    """The card list is ID-sorted; held stock between edge and target = rows."""
    from docich.hanjuku_reference import CARD_IDS
    from docich.hanjuku.flows import deploy as d
    names = sorted(CARD_IDS, key=lambda c: CARD_IDS[c])
    deep = names[-1]
    rows = [{'card': c, 'stock': 1, 'y': 40 + 16 * i} for i, c in enumerate(names[:4])]
    inventory = {'rows': rows, 'remaining': 3}
    mem = {'card_stock': {c: 1 for c in names}}
    frames = d._card_scroll_frames(mem, deep, inventory)
    assert 1 < frames <= d.CARD_SCROLL_HOLD_MAX
    # The list does not auto-repeat, so the jump is a burst of taps, never a hold.
    from docich.hanjuku.os_layer.commands import pad as _pad
    assert [_pad('down') for _ in range(frames)] == [{'type': 'pad', 'buttons': ['down'], 'hold_ms': 100}
                                                     for _ in range(frames)]
    # Without measured stock the jump is not taken (a single measured down).
    assert d._card_scroll_frames({}, deep, inventory) == 1
    # A three-row panel or an unreadable row keeps the single down.
    assert d._card_scroll_frames(mem, deep, {'rows': rows[:3], 'remaining': 3}) == 1
    assert d._card_scroll_frames(mem, deep, {'rows': [{**rows[0], 'card': 'x'}, *rows[1:]],
                                             'remaining': 3}) == 1
