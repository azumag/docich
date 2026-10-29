"""Source-seeded approximate map for chapters 3–12, off by default in tests."""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku_map_sources import SOURCE_CASTLES
from docich.hanjuku.flows.map import APPROX_LOCALIZE_LIMIT
from docich.hanjuku.orchestrator import decide
from test_hanjuku_map_discovery import IDENTITY, field, unmeasured_chapter_three  # noqa: F401

# Chapter 3 as shipped (measured 2026-10-28), captured before the module-wide
# ``unmeasured_chapter_three`` fixture rewrites it into a source-only chapter.
_CHAPTER_THREE_MEASURED = (dict(chart.CASTLES[3]), chart.WORLD_MAP_OFFSET[3], chart.Y_JUMP_OFFSET[3])


@pytest.fixture
def measured_chapter_three(monkeypatch):
    """Undo the module-wide fixture for one test: chapter 3 is measured."""
    castles, world_offset, y_offset = _CHAPTER_THREE_MEASURED
    monkeypatch.setitem(chart.CASTLES, 3, dict(castles))
    monkeypatch.setitem(chart.WORLD_MAP_OFFSET, 3, world_offset)
    monkeypatch.setitem(chart.Y_JUMP_OFFSET, 3, y_offset)
    monkeypatch.delitem(chart.CASTLES_APPROX, 3)


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


def _feed_roofless_map(state, *, until):
    """Feed roof-less map frames until ``until(records)`` holds; bounded."""
    records = []
    actives = []
    for _ in range(24):
        _, state = decide(field(roofs=()), state, run_identity=IDENTITY, approx_map=True)
        records += state.get('_records', [])
        actives.append(state['policy'].get('active'))
        if until(records):
            break
    return state, records, actives


def test_approx_chapter_is_membership_of_the_seed_table(measured_chapter_three):
    assert not chart.approx_chapter(1)
    assert not chart.approx_chapter(2)
    assert not chart.approx_chapter(3)          # measured: removed from the seed
    assert all(chart.approx_chapter(chapter) for chapter in range(4, 13))
    assert not chart.approx_chapter(None)
    assert not chart.approx_chapter(0)


def test_measured_chapter_keeps_its_order_without_roof_anchors(measured_chapter_three):
    # g1075 2026-10-29 09:14:55-09:15:56 JST: chapter 3 is measured, yet
    # ``approx_map = true`` ran the seed fallback after four roof-less map
    # frames (cursor null, screen_cursor (232, 8)), dropped the interim order,
    # re-picked it (``order_start`` again) and held for ~1 min until the
    # periodic SELECT. The fallback belongs to seed-driven chapters only.
    assert 3 not in chart.CASTLES_APPROX and chart.castles(3) == chart.CASTLES[3]
    state = {'policy': {'chapter': 3, 'tick': 0, '_run_identity': dict(IDENTITY),
                        'orders': {}, 'captured': [], '_records': []}}
    state, records, actives = _feed_roofless_map(
        state, until=lambda recs: sum(r.get('decision') == 'situation_held' for r in recs)
        > APPROX_LOCALIZE_LIMIT)
    held = [r for r in records if r.get('decision') == 'situation_held']
    assert len(held) > APPROX_LOCALIZE_LIMIT        # the roof-less map path ran past the limit
    assert not any(r.get('decision') == 'approx_map_fallback' for r in records)
    assert sum(r.get('decision') == 'order_start' for r in records) == 1
    first = next(i for i, active in enumerate(actives) if active)
    assert all(active == actives[first] for active in actives[first:])
    assert state['policy'].get('approx_fallback') is None
    assert 'approx_localize_miss' not in state['policy']


def test_source_chapter_still_falls_back_after_the_localize_limit():
    # Chapters 4-12 navigate on the seed table: the bounded fallback stays.
    assert 5 in chart.CASTLES_APPROX
    state = {'policy': {'chapter': 5, 'tick': 0, '_run_identity': dict(IDENTITY),
                        'orders': {}, 'captured': [], '_records': []}}
    state, records, actives = _feed_roofless_map(
        state, until=lambda recs: any(r.get('decision') == 'approx_map_fallback' for r in recs))
    fallback = [r for r in records if r.get('decision') == 'approx_map_fallback']
    assert len(fallback) == 1
    assert fallback[0]['observed_metric']['misses'] == APPROX_LOCALIZE_LIMIT
    assert str(fallback[0].get('chart_step', '')).startswith('5-')
    assert state['policy'].get('approx_fallback') is True
    assert state['policy'].get('active') is None
    assert actives[-1] is None and any(actives)
