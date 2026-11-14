"""A successfully read Y view need not be opened again on the next map."""
from copy import deepcopy

import pytest

from docich.hanjuku.flows import map as M
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku.state.regions import flow
from docich import hanjuku_chart as chart
from test_hanjuku_off_chart import _world_map_frame, map_screen, FRAME


def memory(previous=None):
    mem = {'chapter': 1, 'tick': 500, 'orders': {}, 'picked': [], '_records': []}
    if previous is not None:
        mem['world_map_tick'] = previous
    return mem


def frame():
    owners = {name: 'own' for name in chart.castles(1) if not chart.is_boss_castle(name)}
    owners['キカンドン'] = 'enemy'
    return _world_map_frame(owners)


@pytest.mark.parametrize('previous', [None, 0, 499])
def test_successful_survey_dates_the_read_and_defers_an_immediate_revisit(previous):
    image = frame()
    mem = memory(previous)
    assert M.world_map_step(parse(image), mem, image) == [pad('y')]
    assert mem['world_map_tick'] == 500
    mem['tick'] = 501
    assert M._world_map_wanted(mem) is False
    mem['tick'] = 500 + M.WORLD_SURVEY_TICKS
    assert M._world_map_wanted(mem) is True


def test_the_next_idle_map_does_not_reopen_the_view_that_was_just_read(monkeypatch):
    image = frame()
    mem = memory()
    assert M.world_map_step(parse(image), mem, image) == [pad('y')]
    mem['tick'] += 1
    monkeypatch.setattr(M, 'next_order', lambda _mem: None)
    actions = M.map_step(map_screen(100, 100), mem, FRAME)
    assert actions != [pad('y')]
    assert not any(r['decision'] == 'world_map_open' for r in mem['_records'])


@pytest.mark.parametrize('previous', [None, 0])
def test_an_unread_view_and_its_bounded_close_never_refresh_the_survey(previous):
    image = _world_map_frame({})
    mem = memory(previous)
    for i in range(M.WORLD_MAP_WAIT):
        mem['tick'] = 500 + i
        actions = M.world_map_step(parse(image), mem, image)
        assert actions == ([pad('y')] if i == M.WORLD_MAP_WAIT - 1 else [])
        assert mem.get('world_map_tick') == previous
    assert M._world_map_wanted(mem) is True


def test_an_explicit_forced_recheck_is_not_consumed_by_the_ordinary_read():
    image = frame()
    mem = memory()
    mem['world_map_due'] = True
    M.world_map_step(parse(image), mem, image)
    assert mem['world_map_due'] is True
    assert M._world_map_wanted(mem) is True


@pytest.mark.parametrize('previous', [None, 0])
def test_a_partial_flag_read_does_not_refresh_the_survey(previous):
    image = _world_map_frame({'アルマムーン': 'own'})
    mem = memory(previous)
    assert M.world_map_step(parse(image), mem, image) == [pad('y')]
    assert mem.get('world_map_tick') == previous


@pytest.mark.parametrize('previous', [None, 0, 499])
def test_pending_ownership_change_is_not_used_as_a_completed_survey(previous):
    image = frame()
    mem = memory(previous)
    mem['world_map_flags'] = {'キカンドン': 'own'}
    M.world_map_step(parse(image), mem, image)
    assert mem['world_map_pending'] == {'キカンドン': 'enemy'}
    assert mem.get('world_map_tick') == previous
    mem['tick'] += 1
    M.world_map_step(parse(image), mem, image)
    assert mem['world_map_pending'] == {}
    assert mem['world_map_tick'] == 501


def test_a_view_that_cannot_close_with_y_requires_a_new_survey_after_escape():
    mem = memory()
    image = frame()
    for i in range(M.WORLD_MAP_CLOSE_LIMIT):
        mem['tick'] = 500 + i
        assert M.world_map_step(parse(image), mem, image) == [pad('y')]
    mem['tick'] += 1
    assert M.world_map_step(parse(image), mem, image) == [pad('b')]
    assert mem['world_map_due'] is True
    assert M._world_map_wanted(mem) is True
    assert 'world_map_close' not in mem


@pytest.mark.parametrize('mode', ['map', 'target'])
def test_a_jump_is_not_a_completed_periodic_survey(monkeypatch, mode):
    mem = memory(0)
    mem['y_jump'] = {'mode': mode, 'step': 'test'}
    monkeypatch.setattr(M, '_y_jump_step', lambda _mem, _image: [pad('right')])
    image = frame()
    assert M.world_map_step(parse(image), mem, image) == [pad('right')]
    assert mem['world_map_tick'] == 0


def test_a_recall_dispatch_is_not_a_periodic_survey(monkeypatch):
    mem = memory(0)
    flow(mem)['recall'] = {'stage': 'await_dispatch'}
    monkeypatch.setattr(M, '_recall_dispatch_step', lambda *_: [])
    image = frame()
    assert M.world_map_step(parse(image), mem, image) == []
    assert mem['world_map_tick'] == 0


@pytest.mark.parametrize('chapter', [1, 4])
def test_flags_boss_or_season_sites_and_unit_markers_are_still_observed(monkeypatch, chapter):
    mem = memory(0)
    mem['chapter'] = chapter
    calls = []
    monkeypatch.setattr(M, 'world_flags', lambda *_: {
        name: 'own' for name in chart.castles(chapter) if not chart.is_boss_castle(name)})
    monkeypatch.setattr(M, '_apply_world_flags', lambda *_: calls.append('flags'))
    monkeypatch.setattr(M, 'season_sites', lambda *_: {})
    monkeypatch.setattr(M, '_apply_season_sites', lambda *_: calls.append('sites'))
    monkeypatch.setattr(M, '_apply_skull_boss', lambda *_: calls.append('boss'))
    monkeypatch.setattr(M, 'world_markers', lambda *_: [(20, 30, 24, 34)])
    monkeypatch.setattr(M, '_probe_launch_markers', lambda *_: calls.append('units'))
    image = frame()
    assert M.world_map_step(parse(image), mem, image) == [pad('y')]
    assert calls == ['flags', 'sites' if chapter == 4 else 'boss', 'units']
    assert mem['_world_units_prev'] == [[20, 30, 24, 34]]
    assert mem['world_map_tick'] == 500


def test_a_failed_observation_does_not_mark_the_survey_read(monkeypatch):
    mem = memory(0)
    def failed(*_):
        raise ValueError('synthetic observation failure')
    monkeypatch.setattr(M, '_apply_skull_boss', failed)
    image = frame()
    with pytest.raises(ValueError, match='synthetic observation failure'):
        M.world_map_step(parse(image), mem, image)
    assert mem['world_map_tick'] == 0


def test_reading_the_same_flags_still_refreshes_time_without_duplicate_owner_events():
    mem = memory()
    image = frame()
    M.world_map_step(parse(image), mem, image)
    before = deepcopy(mem.get('world_map_flags'))
    owner_records = sum(r['decision'] == 'world_map_owners' for r in mem['_records'])
    mem['tick'] = 505
    M.world_map_step(parse(image), mem, image)
    assert mem.get('world_map_flags') == before
    assert sum(r['decision'] == 'world_map_owners' for r in mem['_records']) == owner_records
    assert mem['world_map_tick'] == 505
