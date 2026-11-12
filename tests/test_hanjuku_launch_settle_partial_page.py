"""A page of the source list settles a blind launch probe.

g1163 2026-11-12 14:0x: the world map drew no unit marker (``world.units``
stayed empty), so every committed departure went blind and waited
``LAUNCH_PROBE_ROSTER_TICKS`` for the source castle's roster. On a full page
that roster never arrives — ``GENERAL_LIST_FULL`` rows means "the list
scrolls", not "complete" — and the launches were rolled back: four orders
(I:c9:オシロガ>ドンドン:41-44) failed at the same second while ブカティーニ and
バーミセリ were already marching, and タオ>サヌト carried 13-15 launch misses.

A page still proves who is standing in the castle:
* the general is listed -> the departure never took him, roll it back;
* the general is absent and the probe is already blind -> the launch holds;
* the marker survey is not blind yet -> a missing name proves nothing.
"""
from docich.hanjuku.flows.map import settle_launch_probes_from_roster
from docich.hanjuku.state.regions import flow as _flow, plan as _plan_region
from docich.hanjuku.state.regions import world as _world

STEP = 'I:c9:オシロガ>ドンドン:48'


def memory(general='ブカティーニ', blind=True):
    mem = {'_records': []}
    _flow(mem)['launch_probe'] = {STEP: {'general': general, 'source': 'オシロガ',
                                         'target': 'ドンドン', 'reads': 5,
                                         'blind': blind, 'tick': 100}}
    _world(mem)['sorties'] = {STEP: {'general': general, 'source': 'オシロガ',
                                     'target': 'ドンドン', 'status': 'en_route',
                                     'tick': 100}}
    _world(mem)['garrison'] = {'オシロガ': []}
    _plan_region(mem)['orders'] = {STEP: 'launched'}
    return mem


def decided(mem, name):
    return [r for r in mem.get('_records') or [] if r.get('decision') == name]


def test_a_page_that_still_lists_the_general_rolls_the_launch_back():
    mem = memory()
    settle_launch_probes_from_roster(mem, 'オシロガ', ['ブカティーニ', 'オレガノ'],
                                     complete=False)
    assert STEP not in (_flow(mem).get('launch_probe') or {})
    assert decided(mem, 'sortie_launch_unverified')
    assert _plan_region(mem)['orders'][STEP] == 'pending'


def test_a_blind_probe_is_confirmed_by_the_generals_absence():
    mem = memory()
    settle_launch_probes_from_roster(mem, 'オシロガ', ['オレガノ', 'エピィ'], complete=False)
    assert STEP not in (_flow(mem).get('launch_probe') or {})
    confirmed = decided(mem, 'sortie_launch_confirmed')
    assert confirmed and confirmed[0]['observed_metric']['complete'] is False
    assert not decided(mem, 'sortie_launch_unverified')


def test_a_probe_that_still_has_marker_reads_waits_for_the_marker():
    mem = memory(blind=False)
    settle_launch_probes_from_roster(mem, 'オシロガ', ['オレガノ'], complete=False)
    assert STEP in (_flow(mem).get('launch_probe') or {})
    assert not decided(mem, 'sortie_launch_confirmed')


def test_a_complete_roster_still_confirms_by_absence():
    mem = memory()
    settle_launch_probes_from_roster(mem, 'オシロガ', ['オレガノ'])
    confirmed = decided(mem, 'sortie_launch_confirmed')
    assert confirmed and confirmed[0]['observed_metric']['complete'] is True
