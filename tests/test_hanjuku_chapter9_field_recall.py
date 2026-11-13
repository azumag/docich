"""docich#3958: chapter 9's field camps feed the existing camp recall again.

Before its boss phase, chapter 9 runs ``corridor.mechanical``: the monthly
pass is disabled (v665/v668), so no recall task is queued and no status panel
is opened — a camp left on the field was never found again. The orchestrator
now hands idle map frames to the existing ``camp_recall_step`` while the
mechanical march runs; the flow's own gates (``camp_stays``/``boss_phase``,
skip cooldowns, the named recheck queue of #3008) are unchanged.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator
from docich.hanjuku.flows import recall as recall_flow
from docich.hanjuku.state.regions import flow as _flow
from docich.hanjuku.os_layer.pixels import Frame
from docich.hanjuku_screen import Screen

ROAD = chart.corridor(9)
MAP = Screen([], None, '', kind='map', cursor=(10, 10))
CAMP = {'target': (40, 40), 'clipped': False, 'kind': 'tent'}
FRAME = Frame(256, 224, b'\x00' * 256 * 224 * 3)  # blank field: only the patched detector sees a camp


def _mem(**extra):
    return {'chapter': 9, 'tick': 100, '_records': [], 'captured': list(ROAD[:-1]), **extra}


def test_the_mech_march_hands_map_frames_to_the_camp_recall():
    assert orchestrator._chapter9_field_recall(_mem()) is True


def test_the_boss_phase_keeps_its_own_flows():
    # Every road castle ours ends ``corridor.mechanical``; the boss phase's
    # redirect flows were never disabled and need no new entry point.
    assert orchestrator._chapter9_field_recall(_mem(captured=list(ROAD))) is False


def test_other_chapters_are_unchanged():
    mem = _mem()
    mem['chapter'] = 4
    assert orchestrator._chapter9_field_recall(mem) is False


def test_a_visible_camp_still_starts_the_ordinary_recall(monkeypatch):
    # The mechanism the gate revives is the unmodified camp recall: before the
    # boss phase ``camp_stays`` is False, so a seen camp is recalled home, not
    # re-sent at the moving boss.
    monkeypatch.setattr(recall_flow, 'own_camps', lambda frame: [dict(CAMP)])
    mem = _mem()
    actions = recall_flow.camp_recall_step(MAP, mem, FRAME)
    state = _flow(mem).get('recall') or {}
    assert state.get('stage') == 'to_camp' and state.get('target') == list(CAMP['target'])
    assert 'boss' not in state            # no boss redirect before the boss phase
    assert actions                        # the cursor walks onto the tent


def test_an_empty_field_changes_nothing(monkeypatch):
    monkeypatch.setattr(recall_flow, 'own_camps', lambda frame: [])
    mem = _mem()
    assert recall_flow.camp_recall_step(MAP, mem, FRAME) is None
    assert 'recall' not in _flow(mem)


def test_the_camp_world_cell_survives_a_camera_shift():
    assert recall_flow._camp_world_cell((10, 10), (100, 50), (40, 40)) == [130, 80]
    assert recall_flow._camp_world_cell(None, (100, 50), (40, 40)) is None
    assert recall_flow._camp_world_cell((10, 10), None, (40, 40)) is None


def test_a_lost_tent_records_where_it_was_looked_for(monkeypatch):
    """The unconfirmed record keeps the cursor, the miss count and the world cell."""
    monkeypatch.setattr(recall_flow, 'own_camps', lambda frame: [])
    mem = _mem()
    state = _flow(mem)['recall'] = {'stage': 'to_camp', 'target': [40, 40],
                                    'last_seen': [40, 40], 'steps': 0,
                                    'camp_world': [130, 80]}
    recall_flow.camp_recall_step(MAP, mem, FRAME)
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_position_unconfirmed')
    assert record['observed_metric']['cursor'] == [10, 10]
    assert record['observed_metric']['missing'] == 1
    assert record['observed_metric']['camp_world'] == [130, 80]


def test_a_cursor_within_the_sprite_can_open_the_last_confirmed_tent(monkeypatch):
    """g1163 2026-11-14 00:22: camp [84,73], cursor [90,78], tent covered."""
    monkeypatch.setattr(recall_flow, 'own_camps', lambda frame: [])
    mem = _mem()
    _flow(mem)['recall'] = {'stage': 'to_camp', 'target': [84, 73],
                            'last_seen': [84, 73], 'steps': 0}
    screen = Screen([], None, '', kind='map', cursor=(90, 78))
    assert recall_flow.camp_recall_step(screen, mem, FRAME) == [recall_flow.pad('a')]
    enter = next(r for r in mem['_records'] if r['decision'] == 'camp_enter')
    assert enter['observed_metric'] == {'camp': [84, 73], 'occluded': True,
                                        'cursor': [90, 78], 'last_seen': [84, 73],
                                        'target': [84, 73]}


def test_a_recall_near_the_month_end_waits_for_the_scene(monkeypatch):
    """g1163 2026-11-14 01:35: the 42-8 scene cut the destination picker."""
    monkeypatch.setattr(recall_flow, 'own_camps', lambda frame: [dict(CAMP)])
    mem = _mem(tick=2950, month_start_tick=2000, month_durations=[950])
    assert recall_flow._month_end_soon(mem) is True
    assert recall_flow.camp_recall_step(MAP, mem, FRAME) is None
    assert 'recall' not in _flow(mem)
    mem['tick'] = 2400
    assert recall_flow._month_end_soon(mem) is False
    assert recall_flow.camp_recall_step(MAP, mem, FRAME) is not None


def test_the_first_month_after_a_restart_uses_a_conservative_default(monkeypatch):
    """g1163 2026-11-14 02:0x: month_durations is still empty at 42-10."""
    monkeypatch.setattr(recall_flow, 'own_camps', lambda frame: [dict(CAMP)])
    early = _mem(tick=100 + 100, month_start_tick=100)
    assert recall_flow._month_end_soon(early) is False
    late = _mem(tick=100 + 200, month_start_tick=100)
    assert recall_flow._month_end_soon(late) is True
    assert recall_flow.camp_recall_step(MAP, late, FRAME) is None


def test_the_month_end_margin_covers_the_recall_and_the_transition():
    """g1163 2026-11-14 01:56: camp at start+124; the dest was cut at the transition.

    The recall took ~90 ticks (camp to dest) and the month-end transition about
    150 more; the old 150-tick margin let that attempt start and be cut.
    """
    assert recall_flow.MONTH_END_MARGIN == 200
    mem = _mem(tick=124, month_start_tick=0, month_durations=[299])
    assert recall_flow._month_end_soon(mem) is True
    mem['tick'] = 90
    assert recall_flow._month_end_soon(mem) is False


def test_a_camp_found_records_where_it_sits_in_the_month(monkeypatch):
    """The month timing decides whether the recall can finish before the scene."""
    monkeypatch.setattr(recall_flow, 'own_camps', lambda frame: [dict(CAMP)])
    mem = _mem(tick=150, month_start_tick=100, month_durations=[299])
    recall_flow.camp_recall_step(MAP, mem, FRAME)
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_found')
    assert record['observed_metric']['month_elapsed'] == 50
    assert record['observed_metric']['month_estimate'] == 299


def test_a_roof_destination_is_named_so_the_arrival_is_read(monkeypatch):
    """g1163 2026-11-13 15:5x: recalls never read where they landed."""
    monkeypatch.setattr(recall_flow, 'castle_roofs',
                        lambda frame, exclude=None: [{'kind': 'own', 'target': (90, 80),
                                                      'clipped': False}])
    monkeypatch.setattr(recall_flow, '_recall_goal', lambda mem: 'タオ')
    mem = _mem(tick=100)
    mem['flow'] = {'recall': {'stage': 'dest', 'steps': 0, 'target': [84, 73]}}
    screen = Screen([], None, '', kind='map_target', marker=(90, 80))
    recall_flow.camp_recall_step(screen, mem, FRAME)
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_recall_requested')
    assert record['observed_metric']['castle'] == 'タオ'
    assert mem['roster_read_due']['タオ'] == 100 + recall_flow.RECALL_READ_DELAY


def test_a_camp_found_records_whether_the_camera_was_localised(monkeypatch):
    """The 2026-11-14 finds had camp_world null: no roofs agreed on the camera."""
    monkeypatch.setattr(recall_flow, 'own_camps', lambda frame: [dict(CAMP)])
    monkeypatch.setattr(recall_flow, 'castle_roofs',
                        lambda frame, exclude=None: [{'kind': 'own', 'target': (1, 1),
                                                      'clipped': False}])
    monkeypatch.setattr(recall_flow, '_observe_recall_origin',
                        lambda screen, mem, frame, state: state.update(origin=[50, 60]))
    mem = _mem(tick=100, month_start_tick=100, month_durations=[299])
    recall_flow.camp_recall_step(MAP, mem, FRAME)
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_found')
    assert record['observed_metric']['roofs'] == 1
    assert record['observed_metric']['origin'] == [50, 60]


def test_an_empty_field_records_a_throttled_hunt_sample(monkeypatch):
    """54 minutes without a camp_found: the field frames themselves are the evidence."""
    monkeypatch.setattr(recall_flow, 'own_camps', lambda frame: [])
    mem = _mem(tick=400, month_start_tick=0, month_durations=[1000])
    assert recall_flow.camp_recall_step(MAP, mem, FRAME) is None
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_hunt')
    assert record['observed_metric']['kind'] == 'map'
    assert record['observed_metric']['cursor'] == [10, 10]
    assert record['observed_metric']['month_elapsed'] == 400
    mem['tick'] = 450
    before = len([r for r in mem['_records'] if r['decision'] == 'camp_hunt'])
    recall_flow.camp_recall_step(MAP, mem, FRAME)
    assert len([r for r in mem['_records'] if r['decision'] == 'camp_hunt']) == before
    mem['tick'] = 520
    recall_flow.camp_recall_step(MAP, mem, FRAME)
    assert len([r for r in mem['_records'] if r['decision'] == 'camp_hunt']) == before + 1


def test_a_battle_during_the_destination_records_the_pause(monkeypatch):
    """g1163 2026-11-14 05:57-06:01: the dest sat at steps 1 for four minutes."""
    mem = _mem(tick=400)
    mem['flow'] = {'recall': {'stage': 'dest', 'steps': 1, 'target': [84, 73]}}
    battle = Screen([], None, '', kind='battle')
    assert recall_flow.camp_recall_step(battle, mem, FRAME) is None
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_recall_battle')
    assert record['observed_metric'] == {'stage': 'dest', 'kind': 'battle'}
    mem['tick'] = 430
    recall_flow.camp_recall_step(battle, mem, FRAME)
    assert len([r for r in mem['_records'] if r['decision'] == 'camp_recall_battle']) == 1


def test_a_hunt_sample_records_whether_the_camera_was_localised(monkeypatch):
    """The hunt frames carried 0-1 roofs, so the world anchor never set."""
    from docich.hanjuku.os_layer import world
    monkeypatch.setattr(recall_flow, 'own_camps', lambda frame: [])
    monkeypatch.setattr(world, 'observed_cursor_cell',
                        lambda screen, mem, frame, **kw: [70, 20])
    mem = _mem(tick=400, month_start_tick=0, month_durations=[1000])
    recall_flow.camp_recall_step(MAP, mem, FRAME)
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_hunt')
    assert record['observed_metric']['origin'] == [70, 20]


def test_a_camp_found_records_the_integrated_cursor_cell(monkeypatch):
    """The roof origin and the integrated cursor are the two camera sources."""
    monkeypatch.setattr(recall_flow, 'own_camps', lambda frame: [dict(CAMP)])
    mem = _mem(tick=100, month_start_tick=0, month_durations=[1000], cursor=[70, 20])
    recall_flow.camp_recall_step(MAP, mem, FRAME)
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_found')
    assert record['observed_metric']['cursor_cell'] == [70, 20]


def test_a_hunt_sample_records_the_roof_cells(monkeypatch):
    """Two roofs can still fail the constellation: the cells are the evidence."""
    from docich.hanjuku.os_layer import world
    monkeypatch.setattr(recall_flow, 'own_camps', lambda frame: [])
    monkeypatch.setattr(recall_flow, 'castle_roofs',
                        lambda frame, exclude=None: [
                            {'kind': 'own', 'target': (10, 20), 'clipped': False},
                            {'kind': 'enemy', 'target': (30, 40), 'clipped': False}])
    monkeypatch.setattr(world, 'observed_cursor_cell', lambda *a, **k: None)
    mem = _mem(tick=400, month_start_tick=0, month_durations=[1000])
    recall_flow.camp_recall_step(MAP, mem, FRAME)
    record = next(r for r in mem['_records'] if r['decision'] == 'camp_hunt')
    assert record['observed_metric']['roofs'] == 2
    assert record['observed_metric']['roofs_detail'] == [
        {'kind': 'own', 'target': [10, 20]}, {'kind': 'enemy', 'target': [30, 40]}]
