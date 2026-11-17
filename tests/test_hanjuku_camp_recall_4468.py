"""#4468 — a recall A must only leave on a real picker, and interruptions are free.

g1190 2026-11-17: the department G badge on a field general read as the いどう
destination marker, so the recall sent A on a screen with no picker open and
the general stayed camped. A month-start scene cut also spent the month's
recall retry budget, unlike a battle cut. The gateway chapter kept walking the
whole-island ring instead of using a visible own castle.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator as O
from docich.hanjuku.flows import recall as recall_flow
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku.state.regions import flow as _flow

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'


def test_a_field_general_badge_is_not_an_open_picker():
    frame = read_png(FIXTURES / 'g1190-ch9-desert-field.png')
    screen = parse(frame)
    assert screen.kind == 'map_target' and screen.marker == (140, 120)
    # The red flag above the G proves it is the army sprite's badge.
    assert recall_flow._picker_marker(screen, frame) is None

    mem = {'chapter': 9, 'tick': 100, 'captured': [], '_records': []}
    _flow(mem)['recall'] = {'stage': 'dest', 'steps': 1, 'target': [90, 120], 'goal': 'タオ'}
    recall_flow.camp_recall_step(screen, mem, frame)
    assert any(r['decision'] == 'camp_recall_picker_not_open' for r in mem['_records'])
    assert not any(r['decision'] == 'camp_recall_requested' for r in mem['_records'])
    assert 'recall' not in _flow(mem)


def test_a_genuine_destination_marker_keeps_the_picker():
    for name, marker in (
        ('g1190-ch9-riesling-fort-picker.png', (129, 112)),
        ('g1133-ch6-oceana-target-marker.png', (141, 110)),
        ('g1075-target-marker-beside-rigua.png', (128, 118)),
    ):
        frame = read_png(FIXTURES / name)
        screen = parse(frame)
        assert screen.marker == marker
        assert recall_flow._picker_marker(screen, frame) == marker


def test_a_scene_abandonment_marks_the_recall_interrupted():
    from docich.hanjuku.flows import post_month_status as post
    from docich.hanjuku_screen import Screen

    recall = {'stage': 'dest', 'steps': 4, 'target': [175, 84], 'scene_resumes': 2}
    mem = {'tick': 100, 'chapter': 11, 'month': '61-2', 'gold': 250,
           'flow': {'recall': recall}, '_records': []}
    assert O._recall_after_scene(mem, recall, 'text', 'concert') is None
    assert _flow(mem).get('recall_interrupted') == 100
    assert any(r['decision'] == 'camp_recall_scene_abandoned' for r in mem['_records'])

    # The monthly task re-queues the never-dispatched recall without charging
    # RECALL_TASK_RETRIES — the same free retry a battle interruption gets.
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='wait_recall', started_tick=100,
                 job={'kind': 'recall', 'name': 'ヴィーナス'}, tasks=[])
    state['recall_task_retries'] = {'ヴィーナス': post.RECALL_TASK_RETRIES}
    assert post.step(Screen([], None, '', kind='map'), mem, None) == []
    assert state['tasks'] == [{'kind': 'recall', 'name': 'ヴィーナス'}]
    assert state['recall_task_retries'] == {'ヴィーナス': post.RECALL_TASK_RETRIES}
    assert any(r['decision'] == 'post_month_recall_requeued' for r in mem['_records'])


def test_gateway_visible_candidates_keep_the_camp_on_its_own_side(monkeypatch):
    cells = chart.castles(11)
    front, back, hub = 'アルマムーン', 'クラボニア', 'タタール'
    candidates = [{'castle': front, 'world': list(cells[front]), 'screen': [10, 10]},
                  {'castle': back, 'world': list(cells[back]), 'screen': [20, 20]},
                  {'castle': hub, 'world': list(cells[hub]), 'screen': [30, 30]}]
    monkeypatch.setattr(recall_flow, '_visible_own_candidates', lambda mem, frame: candidates)
    mem = {'chapter': 11, 'captured': [front, back, hub]}
    # No `target`, so _bind_camp_origin leaves the measured origin alone.
    state = {'origin': list(cells[back])}
    kept = [c['castle'] for c in recall_flow._visible_recall_candidates(mem, state, None)]
    assert kept == [back]   # same-side castle wins; the hub is not a safe shortcut
    assert front not in kept


def test_gateway_hub_is_not_selected_before_a_same_side_castle(monkeypatch):
    """DSH #4480: a back-half camp must not be marched at タタール's cliff."""
    cells = chart.castles(11)
    back, hub = 'クラボニア', 'タタール'
    # The hub is first in the roof scan order.
    candidates = [{'castle': hub, 'world': list(cells[hub]), 'screen': [10, 10]},
                  {'castle': back, 'world': list(cells[back]), 'screen': [20, 20]}]
    monkeypatch.setattr(recall_flow, '_visible_own_candidates', lambda mem, frame: candidates)
    mem = {'chapter': 11, 'captured': [back, hub]}
    state = {'origin': list(cells[back])}
    kept = [c['castle'] for c in recall_flow._visible_recall_candidates(mem, state, None)]
    assert kept == [back]


def test_a_hub_only_view_falls_back_to_the_picker(monkeypatch):
    cells = chart.castles(11)
    hub = 'タタール'
    candidates = [{'castle': hub, 'world': list(cells[hub]), 'screen': [10, 10]}]
    monkeypatch.setattr(recall_flow, '_visible_own_candidates', lambda mem, frame: candidates)
    mem = {'chapter': 11, 'captured': [hub]}
    state = {'origin': list(cells['クラボニア'])}
    assert recall_flow._visible_recall_candidates(mem, state, None) == []


def test_no_origin_keeps_every_visible_gateway_candidate(monkeypatch):
    cells = chart.castles(11)
    front, back = 'アルマムーン', 'クラボニア'
    candidates = [{'castle': front, 'world': list(cells[front]), 'screen': [10, 10]},
                  {'castle': back, 'world': list(cells[back]), 'screen': [20, 20]}]
    monkeypatch.setattr(recall_flow, '_visible_own_candidates', lambda mem, frame: candidates)
    mem = {'chapter': 11, 'captured': [front, back]}
    kept = [c['castle'] for c in recall_flow._visible_recall_candidates(mem, {}, None)]
    assert kept == [front, back]
