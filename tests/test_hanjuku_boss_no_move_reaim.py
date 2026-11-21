"""A general that did not move after いどう gets a different aim.

Live g1288 2026-11-21 19:0x-19:2x: three wave generals kept the same measured
cell across many redirects while their aims were on the track — the order was
not accepted (cliff/blocked), so the aim must be dropped and re-picked.
"""
from docich.hanjuku.flows import boss_pursuit as bp


def memory():
    return {'chapter': 9, 'tick': 10000, '_records': [],
            bp.LIST_KEY: {'ミント': {'step': 'A', 'launched': 9500,
                                    'ambush_cell': [674, 333],
                                    'move_from': [676, 336]}}}


def test_an_unchanged_unit_drops_the_failed_aim():
    mem = memory()
    entry = mem[bp.LIST_KEY]['ミント']
    metric = {'unit_world': [677, 337]}       # 2 px, not a march
    bp._note_redirect_move(mem, entry, {'step': 'A', 'general': 'ミント'}, metric)
    assert 'ambush_cell' not in entry
    assert metric['no_move'] is True
    assert entry['move_from'] == [677, 337]
    assert any(r['decision'] == 'boss_pursuit_no_move' for r in mem['_records'])


def test_a_real_move_keeps_the_aim():
    mem = memory()
    entry = mem[bp.LIST_KEY]['ミント']
    metric = {'unit_world': [700, 400]}       # moved 63 px
    bp._note_redirect_move(mem, entry, {'step': 'A', 'general': 'ミント'}, metric)
    assert entry['ambush_cell'] == [674, 333]
    assert 'no_move' not in metric
