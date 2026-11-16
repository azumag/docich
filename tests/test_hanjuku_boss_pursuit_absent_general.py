"""An absent roster name at the end of a full top-to-end walk marks it lost.

ch9 2026-11-17 (driver, origin/main b90bb2f67): all 15 boss pursuits closed
general_not_found. The two generals had left the country list, but nothing
wrote generals_lost, so the wave kept them and every later pursuit failed.
"""
from pathlib import Path
from types import SimpleNamespace

from docich.hanjuku.flows import boss_pursuit as bp
from docich.hanjuku.flows import house
from docich.hanjuku.orchestrator import decide
from test_hanjuku_chart_bot import Canvas

LIST = ['どうし', 'ココット', 'レモン']
IDENTITY = {'game': 'hanjuku-hero', 'generation': 900,
            'runtime_id': 'g900-synthetic', 'lease_id': 'test-lost-reset'}


def memory(general='ポワソン'):
    return {'chapter': 9, 'tick': 5000,
            bp.LIST_KEY: {general: {'step': 'A', 'launched': 3000}},
            'boss_pursuit': {'general': general, 'step': 'A', 'phase': 'pick',
                             'reads': 0, 'seeks': 0, 'x_retries': 0}}


def screen(selected):
    return SimpleNamespace(kind='text', hand=None, selected=selected, text='')


def feed(monkeypatch, names, selected):
    """Feed one roster read per step, with a controllable cursor position."""
    it = iter(names)
    monkeypatch.setattr(house, 'roster', lambda _screen: next(it))
    return [screen(s) for s in selected]


def test_end_of_list_with_general_absent_marks_lost(monkeypatch):
    mem = memory()
    probe = mem['boss_pursuit']
    screens = feed(monkeypatch, [LIST] * 5,
                   ['どうし', 'ココット', 'レモン', 'レモン', 'レモン'])
    for s in screens:
        bp.step(s, mem, None)
    assert 'ポワソン' in (mem.get('generals_lost') or [])
    assert probe['phase'] == 'close'
    assert probe['outcome'] == 'general_absent_from_roster'
    assert 'ポワソン' not in (bp.track(mem) or {})


def test_scrolling_list_does_not_mark_lost(monkeypatch):
    mem = memory()
    probe = mem['boss_pursuit']
    pages = [['どうし', 'ココット'], ['ココット', 'レモン'],
             ['レモン', 'X'], ['X', 'Y']]
    screens = feed(monkeypatch, pages, ['どうし', 'レモン', 'X', 'Y'])
    for s in screens:
        bp.step(s, mem, None)
    assert not mem.get('generals_lost')
    assert probe['phase'] == 'pick'


def test_list_opened_mid_way_never_marks_absent(monkeypatch):
    """A list opened below its top cannot prove a general is missing."""
    mem = memory()
    probe = mem['boss_pursuit']
    screens = feed(monkeypatch, [['ココット', 'レモン']] * 4, ['レモン'] * 4)
    actions = [bp.step(s, mem, None) for s in screens]
    assert actions == [[bp.pad('up')]] * 4
    assert not mem.get('generals_lost')
    assert probe['phase'] == 'pick'
    assert probe.get('outcome') != 'general_absent_from_roster'


FIXTURE = Path(__file__).parent / 'fixtures' / 'hanjuku' / 'roster_absent_end'


def test_real_roster_frames_mark_absent_only_at_list_end():
    """Captured 2026-11-17 r2 roster walk, 32 names, general ポワソン absent.

    The cursor reaches the physical scroll end at f027 and reads stay
    identical; mid-list the same page can repeat once only (f009). Lost must
    fire at the third identical end read (f030), never mid-list.
    """
    from PIL import Image
    from docich.hanjuku.os_layer.pixels import Frame
    from docich.hanjuku.os_layer.screen import parse
    # The captured walk starts on the first page with どうし selected: the
    # top-of-list premise is measured from the frames, not assumed.
    first_im = Image.open(FIXTURE / 'f004.png').convert('RGB')
    first_frame = Frame(first_im.width, first_im.height, first_im.tobytes())
    first_screen = parse(first_frame)
    assert house.roster(first_screen)[0] == 'どうし'
    assert first_screen.selected == 'どうし'

    mem = memory()
    fired = None
    for path in sorted(FIXTURE.glob('f*.png')):
        im = Image.open(path).convert('RGB')
        frame = Frame(im.width, im.height, im.tobytes())
        screen = parse(frame)
        bp.step(screen, mem, frame)
        if path.name == 'f004.png':
            assert mem['boss_pursuit'].get('roster_top') is True
        if mem.get('generals_lost') and fired is None:
            fired = path.name
    assert fired == 'f030.png'
    assert mem['generals_lost'] == ['ポワソン']


def test_roster_top_unconfirmed_closes_without_recording_a_loss(monkeypatch):
    """A list that never reaches its top ends as unconfirmed, not as a loss."""
    mem = memory()
    probe = mem['boss_pursuit']
    pages = [['ココット', 'レモン']] * (bp._roster.MAX_GENERALS + 2)
    screens = feed(monkeypatch, pages, ['レモン'] * len(pages))
    for s in screens:
        bp.step(s, mem, None)
        if probe['phase'] == 'close':
            break
    assert probe['outcome'] == 'roster_top_unconfirmed'
    assert probe['top_seeks'] == bp._roster.MAX_GENERALS + 1
    assert not mem.get('generals_lost')


def test_run_identity_change_clears_generals_lost():
    """A saved loss record must not survive into a different run."""
    mem = memory()
    mem['generals_lost'] = ['ポワソン']
    mem['_run_identity'] = dict(IDENTITY)
    mem['survey_run_identity'] = dict(IDENTITY)
    frame = Canvas((64, 140, 40)).frame()
    changed = decide(frame, {'policy': mem},
                     run_identity={**IDENTITY, 'lease_id': 'another'})[1]
    assert 'generals_lost' not in changed['policy']
    same = decide(frame, {'policy': memory_with_loss()},
                  run_identity=dict(IDENTITY))[1]
    assert same['policy'].get('generals_lost') == ['ポワソン']


def memory_with_loss():
    mem = memory()
    mem['generals_lost'] = ['ポワソン']
    mem['_run_identity'] = dict(IDENTITY)
    mem['survey_run_identity'] = dict(IDENTITY)
    return mem


def test_general_back_in_a_garrison_is_removed_from_lost():
    mem = memory()
    mem['generals_lost'] = ['ポワソン']
    mem['world'] = {'sorties': {}, 'garrison': {'イデ！': ['ポワソン']}}
    bp.track(mem)
    assert mem['generals_lost'] == []
