"""An absent roster name at the end of the list marks that general lost.

ch9 2026-11-17 (driver, origin/main b90bb2f67): all 15 boss pursuits closed
general_not_found. The two generals had left the country list, but nothing
wrote generals_lost, so the wave kept them and every later pursuit failed.
"""
from pathlib import Path
from types import SimpleNamespace

from docich.hanjuku.flows import boss_pursuit as bp
from docich.hanjuku.flows import house

LIST = ['どうし', 'ココット', 'レモン']


def memory(general='ポワソン'):
    return {'chapter': 9, 'tick': 5000,
            bp.LIST_KEY: {general: {'step': 'A', 'launched': 3000}},
            'boss_pursuit': {'general': general, 'step': 'A', 'phase': 'pick',
                             'reads': 0, 'seeks': 0, 'x_retries': 0}}


def test_end_of_list_with_general_absent_marks_lost(monkeypatch):
    mem = memory()
    probe = mem['boss_pursuit']
    reads = [LIST, LIST, LIST, LIST]
    it = iter(reads)
    monkeypatch.setattr(house, 'roster', lambda screen: next(it))
    screen = SimpleNamespace(kind='text', hand=None, selected='レモン', text='')
    phase_ok = probe['phase'] == 'pick'
    for _ in range(4):
        bp.step(screen, mem, None)
    assert phase_ok
    assert 'ポワソン' in (mem.get('generals_lost') or [])
    assert probe['phase'] == 'close'
    assert probe['outcome'] == 'general_absent_from_roster'
    assert 'ポワソン' not in (bp.track(mem) or {})


def test_scrolling_list_does_not_mark_lost(monkeypatch):
    mem = memory()
    probe = mem['boss_pursuit']
    reads = [['どうし', 'ココット'], ['ココット', 'レモン'], ['レモン', 'X'], ['X', 'Y']]
    it = iter(reads)
    monkeypatch.setattr(house, 'roster', lambda screen: next(it))
    screen = SimpleNamespace(kind='text', hand=None, selected='Y', text='')
    for _ in range(4):
        bp.step(screen, mem, None)
    assert not mem.get('generals_lost')
    assert probe['phase'] == 'pick'


FIXTURE = Path(__file__).parent / 'fixtures' / 'hanjuku' / 'roster_absent_end'


def test_real_roster_frames_mark_absent_only_at_list_end():
    """Captured 2026-11-17 r2 roster walk, 32 names, general ポワソン absent.

    The cursor stops on the last name (f027..f034) and reads stay identical;
    mid-list the same page can repeat once only (f009). Lost must fire at
    the third identical end read (f030), never mid-list.
    """
    from PIL import Image
    from docich.hanjuku.os_layer.pixels import Frame
    from docich.hanjuku.os_layer.screen import parse
    mem = memory()
    fired = None
    for path in sorted(FIXTURE.glob('f*.png')):
        im = Image.open(path).convert('RGB')
        screen = parse(Frame(im.width, im.height, im.tobytes()))
        bp.step(screen, mem, None)
        if mem.get('generals_lost') and fired is None:
            fired = path.name
    assert fired == 'f030.png'
    assert mem['generals_lost'] == ['ポワソン']


def test_general_back_in_a_garrison_is_removed_from_lost():
    mem = memory()
    mem['generals_lost'] = ['ポワソン']
    mem['world'] = {'sorties': {}, 'garrison': {'イデ！': ['ポワソン']}}
    bp.track(mem)
    assert mem['generals_lost'] == []
