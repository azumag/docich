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
