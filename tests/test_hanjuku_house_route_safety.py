"""A repair destination must never authorize a trip through an enemy castle."""
from types import SimpleNamespace

import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import house
from docich.hanjuku.os_layer import commands, navigation, readers, world


@pytest.fixture
def route(monkeypatch):
    castles = {'source': (800, 800), 'crossing': (960, 960), 'away': (1600, 160)}
    monkeypatch.setattr(chart, 'castles', lambda chapter: castles if chapter == 1 else {})
    monkeypatch.setattr(chart, 'CASTLE_NAMES', {1: tuple(castles)})
    monkeypatch.setattr(navigation, 'Y_JUMP_OFFSET', {1: (0, 0)})
    monkeypatch.setattr(house, 'HOUSE_TARGET', {1: (140, 140)})
    monkeypatch.setattr(world, '_owned', lambda mem: set(mem['owned']))
    monkeypatch.setattr(commands, 'menu_to', lambda *args, **kwargs: 'here')
    monkeypatch.setattr(readers, '_empty_sortie_inventory', lambda screen: True)
    monkeypatch.setattr(house, 'general_status', lambda screen: {
        'general': 'ゼウス', 'broken': True, 'hp': 85, 'max_hp': 85})
    monkeypatch.setattr(house, '_record', lambda mem, event, **fields:
                        mem.setdefault('_records', []).append({'decision': 'house_' + event, **fields}))
    monkeypatch.setattr(readers, 'world_cursor', lambda frame: (140, 140))
    monkeypatch.setattr(readers, 'world_flags', lambda *args: {})
    mem = {'chapter': 1, 'month': '2-6', 'tick': 500, 'gold': 250,
           'owned': ['source'], 'garrison': {'source': ['ゼウス', 'どうし']},
           'house_eggs': {'ゼウス': {'broken': True}},
           'world_flags': {'source': {'kind': 'own', 'chapter': 1, 'tick': 500},
                           'crossing': {'kind': 'enemy', 'chapter': 1, 'tick': 500}},
           'house': {'phase': 'castle_confirm', 'source': 'source', 'general': 'ゼウス',
                     'chapter': 1, 'roster_month': '2-6', 'age': 0, 'total': 0, 'pending': []}}
    return mem, castles


def screen(kind):
    return SimpleNamespace(kind=kind, text='', lines=[], hand=None, header=None,
                           marker=(100, 100) if kind == 'map_target' else None, cursor=None)


def confirm(mem, phase='castle_confirm'):
    mem['house']['phase'] = phase
    return house._repair_step(screen('sortie_confirm' if phase == 'castle_confirm' else 'map_target'),
                              mem, None)


def make_crossing_friendly(mem):
    mem['owned'].append('crossing')
    mem['world_flags']['crossing'] = {'kind': 'own', 'chapter': 1, 'tick': mem['tick']}


@pytest.mark.parametrize('phase', ['castle_confirm', 'house_confirm'])
def test_enemy_on_repair_route_cannot_be_confirmed(route, phase):
    mem, _ = route
    assert confirm(mem, phase) == []
    assert mem['house']['phase'] == 'close'
    assert not mem['house'].get('departure_pending')
    assert mem['garrison']['source'] == ['ゼウス', 'どうし']
    assert not mem.get('sorties')
    assert any(r['decision'] == 'house_route_deferred' and 'crossing' in r['observed_metric']['blocked_castles']
               for r in mem['_records'])


def test_cursor_at_house_is_not_the_generals_origin(route):
    mem, _ = route
    mem['house']['phase'] = 'house_view'
    assert house._view_move(screen('world_map'), mem, None, (140, 140), 'house_confirm') == []
    assert mem['house']['phase'] == 'close'


def test_enemy_well_outside_route_does_not_block_repair(route):
    mem, castles = route
    castles['crossing'] = (1600, 160)
    assert confirm(mem) == [commands.pad('a')]
    assert mem['house']['phase'] == 'out_target'


def test_freshly_observed_friendly_castle_permits_route(route, monkeypatch):
    mem, _ = route
    make_crossing_friendly(mem)
    assert confirm(mem) == [commands.pad('a')]
    # This route contract assumes the separately tested current field fix.
    monkeypatch.setattr(world, 'observed_cursor_cell', lambda *args, **kwargs: [1120, 1120])
    assert confirm(mem, 'house_confirm') == [commands.pad('a')]
    assert mem['house']['phase'] == 'travel'
    assert mem['house']['departure_pending'] is True


@pytest.mark.parametrize('observation', [None, {'kind': 'own', 'chapter': 1, 'tick': 0},
    {'kind': 'own', 'chapter': 2, 'tick': 500}, {'kind': 'own', 'chapter': 1, 'tick': 501},
    {'kind': 'own', 'chapter': 1, 'tick': '500'}])
def test_old_capture_or_unread_flag_is_not_permission(route, observation):
    mem, _ = route
    mem['owned'].append('crossing')
    if observation is None:
        mem['world_flags'].pop('crossing')
    else:
        mem['world_flags']['crossing'] = observation
    assert confirm(mem) == []
    assert mem['house']['phase'] == 'close'


@pytest.mark.parametrize('source', [None, 'unknown'])
def test_unlocated_field_general_is_deferred_not_guessed_from_cursor(route, source):
    mem, _ = route
    mem['house']['source'] = source
    assert confirm(mem, 'house_confirm') == []
    assert mem['house']['phase'] == 'close'


def test_lost_source_is_not_accepted_from_an_old_plan(route):
    mem, _ = route
    make_crossing_friendly(mem)
    mem['owned'].remove('source')
    assert confirm(mem) == []


def test_map_data_must_include_all_known_castles(route):
    mem, castles = route
    castles.pop('crossing')
    assert confirm(mem) == []


def test_destination_confirmation_rechecks_changed_ownership(route):
    mem, _ = route
    make_crossing_friendly(mem)
    assert confirm(mem) == [commands.pad('a')]
    mem['world_flags']['crossing']['kind'] = 'enemy'
    assert confirm(mem, 'house_confirm') == []
    assert not mem['house'].get('departure_pending')


def test_route_envelope_covers_axis_first_movement_not_just_diagonal(route):
    mem, castles = route
    castles['crossing'] = (800, 1120)  # (100, 140): away from the diagonal, inside the route envelope
    assert confirm(mem) == []


def test_clearance_includes_nearby_castle_footprint(route):
    mem, castles = route
    castles['crossing'] = (800, 1160)  # (100, 145): five view pixels outside the nominal route
    assert confirm(mem) == []


def test_deferral_releases_flow_preserves_broken_egg_and_uses_existing_cooldown(route):
    mem, _ = route
    mem['house']['departure_pending'] = True  # old serialized intent is not permission
    assert confirm(mem, 'house_confirm') == []
    assert not mem['house'].get('departure_pending')
    house._finish(mem)
    assert 'house' not in mem
    assert mem['house_field_scan'] == {'scope': [1, '2-6'], 'tick': 500}
    assert mem['house_eggs']['ゼウス']['broken'] is True
    assert mem['gold'] == 250
    assert not mem.get('sorties')


def test_a_later_safe_assessment_is_not_permanently_blacklisted(route):
    mem, _ = route
    assert confirm(mem) == []
    make_crossing_friendly(mem)
    assert confirm(mem) == [commands.pad('a')]


def test_nonrepair_map_cursor_navigation_is_unchanged(route):
    mem, _ = route
    assert house._view_move(screen('world_map'), mem, None, (140, 140), 'income_open') == [commands.pad('a')]
    assert mem['house']['phase'] == 'income_open'
