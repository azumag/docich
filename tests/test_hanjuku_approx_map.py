"""Source-seeded approximate map for chapters 3–12, off by default in tests."""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku_map_sources import SOURCE_CASTLES
from docich.hanjuku.orchestrator import decide
from test_hanjuku_map_discovery import IDENTITY, field


def test_approx_table_covers_every_source_castle_and_never_a_boss():
    assert set(chart.CASTLES_APPROX) == set(range(3, 13))
    for chapter, cells in chart.CASTLES_APPROX.items():
        assert set(cells) == set(SOURCE_CASTLES[chapter])
        assert 'ボス' not in cells
        for (left, top), (x, y) in zip(SOURCE_CASTLES[chapter].values(), cells.values()):
            assert (x, y) == (8 * (left + 1), 8 * (top + 3))


def test_approx_cells_are_not_visible_without_the_policy_flag():
    assert chart.castles(3) == {}
    assert chart.orders(3) == ()
    with chart.map_context({}, approx=True):
        assert chart.castles(3) == chart.CASTLES_APPROX[3]
        steps = {order['step'] for order in chart.orders(3)}
        assert {'3-Z1', '3-C1', '3-V1'} <= steps
        assert '3-B1' not in steps
    assert chart.castles(3) == {}
    assert chart.orders(3) == ()


def test_approx_never_overrides_measured_chapters():
    with chart.map_context({}, approx=True):
        assert chart.castles(1) == chart.CASTLES[1]
        assert chart.castles(2) == chart.CASTLES[2]


def test_approx_context_releases_a_chapter_three_order_in_decide():
    mem = {'chapter': 3, 'tick': 0, '_run_identity': dict(IDENTITY),
           'orders': {}, 'captured': [], '_records': [], 'variant': 'chart_unavailable'}
    _, state = decide(field(), {'policy': mem}, run_identity=IDENTITY, approx_map=True)
    policy = state['policy']
    assert policy.get('active') in {'3-Z1', '3-C1', '3-V1'}
    assert not any(r.get('decision') == 'chart_adjust_request'
                   and r.get('off_chart_reason') == 'chart_unavailable'
                   for r in state.get('_records', []))


def test_decide_without_approx_keeps_chapter_three_gated():
    mem = {'chapter': 3, 'tick': 0, '_run_identity': dict(IDENTITY),
           'orders': {}, 'captured': [], '_records': []}
    _, state = decide(field(), {'policy': mem}, run_identity=IDENTITY)
    assert not state['policy'].get('active')
    assert any(r.get('decision') in {'map_search_candidate', 'map_bootstrap_started'}
               for r in state.get('_records', []))


def test_approx_seed_localizes_a_matching_two_roof_frame():
    # Home/グリン screen positions; the seeded cells imply the camera.
    frame = field()
    mem = {'chapter': 3, 'tick': 0, '_run_identity': dict(IDENTITY),
           'orders': {}, 'captured': [], '_records': []}
    actions, state = decide(frame, {'policy': mem}, run_identity=IDENTITY, approx_map=True)
    policy = state['policy']
    assert policy.get('active') in {'3-Z1', '3-C1', '3-V1'}
    assert policy.get('cursor') == [712, 160]
    assert actions and actions[-1]['buttons'] == ['a']
    assert not policy.get('nav_search')


def test_approx_fallback_hides_seed_and_returns_to_native_survey():
    assert chart.castles(3) == {}
    with chart.map_context({'approx_fallback': True}, approx=True):
        assert chart.castles(3) == {}

    state = {'policy': {'chapter': 3, 'tick': 0, '_run_identity': dict(IDENTITY),
                        'orders': {}, 'captured': [], '_records': []}}
    # A cursor with no roof cannot anchor the source frame.
    for _ in range(4):
        _, state = decide(field(roofs=()), state, run_identity=IDENTITY, approx_map=True)
    assert state['policy'].get('approx_fallback') is True
    assert state['policy'].get('active') is None
    assert any(r.get('decision') == 'approx_map_fallback'
               for r in state.get('_records', []))

    # The next map frame is handled by native registration, not approx orders.
    _, state = decide(field(), state, run_identity=IDENTITY, approx_map=True)
    assert any(r.get('decision') in {'map_search_candidate', 'map_bootstrap_started',
                                     'map_bootstrap_exhausted'}
               for r in state.get('_records', []))
