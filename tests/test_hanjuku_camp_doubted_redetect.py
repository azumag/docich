"""docich#4938: a camp cell that never opens its menu is a doubted detection.

g1288 2026-11-21 (chapter 9): a dome-shaped castle-sprite detection at
[134,117] (kind='dome', roofs=1) answered every A with the bare map. Dome
detections registered no camp_body, so no re-aim ever ran and all three
reopens pressed the same dead cell before camp_recall_aborted; the real camp
beside it at [121,141] then opened on the first press and recalled. Now the
dome registers its door as the re-aim cell, and a sighting whose own cells
all failed is doubted and dropped from re-detection so another visible camp
is used instead.
"""
from test_hanjuku_chart_bot import Canvas, camp_menu

from docich import hanjuku_policy as policy
from docich.hanjuku.flows import recall
from docich.hanjuku.os_layer.screen import camp_body_target, castle_roofs, own_camps
from docich.hanjuku.state.regions import flow as _flow
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen


def _roof(c):
    """The roofs=1 context of the [134,117] detection: an own-roof blob."""
    for yy in range(90, 111):
        for xx in range(200, 236):
            c.put(xx, yy, (230, 56, 80))


def _dome(c):
    """The dome-shaped sprite at target (134,117) with its door at (145,129)."""
    for xx in range(135, 147):              # dark outline (135..146, 118..132)
        c.put(xx, 118, (32, 32, 32))
        c.put(xx, 132, (32, 32, 32))
    for yy in range(118, 133):
        c.put(135, yy, (32, 32, 32))
        c.put(146, yy, (32, 32, 32))
    for yy in range(120, 127):              # light-blue dome body
        for xx in range(137, 144):
            c.put(xx, yy, (131, 198, 222))
    for yy in range(128, 132):              # brown door, 5x4
        for xx in range(143, 148):
            c.put(xx, yy, (123, 88, 0))
    for xx in range(138, 144):              # red/orange flag above the door
        c.put(xx, 115, (255, 0, 0))
        c.put(xx, 116, (238, 113, 57))


def _tent(c):
    """A real tent at target (121,141): _camp_frame's sprite shifted +95,+75."""
    for yy in range(147, 156):
        for xx in range(125, 134):
            c.put(xx, yy, (238, 198, 65) if yy < 151 else (238, 113, 57))
    for yy in (152, 153):
        for xx in range(126, 134):
            c.put(xx, yy, (131, 198, 222))
    for p in ((129, 143), (130, 143), (129, 144)):
        c.put(p[0], p[1], (255, 0, 0))


def _lone_dome_frame():
    c = Canvas()
    _roof(c)
    _dome(c)
    return c.frame()


def _two_camp_frame():
    c = Canvas()
    _roof(c)
    _dome(c)
    _tent(c)
    return c.frame()


def _fort_menu(selected=0):
    rows = ['しゅつげき', 'こうかん']
    return Screen([TextLine(39 + 16 * i, tuple((80 + 8 * j, ch) for j, ch in enumerate(word)))
                   for i, word in enumerate(rows)],
                  (58, 33 + 16 * selected, 75, 45 + 16 * selected),
                  ''.join(rows), kind='text')


def memory():
    return {'chapter': 1, 'tick': 100, '_records': []}


def decisions(mem, name):
    return [r for r in mem['_records'] if r['decision'] == name]


def on(cell):
    return Screen([], None, '', kind='map', cursor=tuple(cell))


def test_the_dome_detection_registers_its_door_as_the_reaim_cell():
    frame = _two_camp_frame()
    camps = own_camps(frame)
    assert camps == [{'target': (121, 141), 'clipped': False},
                     {'target': (134, 117), 'clipped': False, 'kind': 'dome'}]
    body = camp_body_target(frame, (134, 117))
    assert body == [145, 129]
    assert castle_roofs(frame)              # the roofs=1 context


def test_the_dome_reaims_to_its_door_and_the_fort_menu_opens():
    frame = _lone_dome_frame()
    mem = memory()
    assert policy.camp_recall_step(on((134, 117)), mem, frame) == [policy.pad('a')]
    state = _flow(mem)['recall']
    door = list(state['camp_body'])
    assert state['camp_kind'] == 'dome' and door != [134, 117]
    # The flag cell's press and the first reopen leave the bare map.
    for _ in range(recall.MENU_OPEN_WAIT):
        policy.camp_recall_step(on((134, 117)), mem, frame)
    assert policy.camp_recall_step(on((134, 117)), mem, frame) == [policy.pad('a')]
    for _ in range(recall.MENU_OPEN_WAIT):
        policy.camp_recall_step(on((134, 117)), mem, frame)
    # Two dead flag presses arm the door cell, and the press on it opens the
    # fort menu; the 砦 sortie route starts instead of a third dead retry.
    assert state['target'] == door
    assert decisions(mem, 'camp_enter_body_retry')
    assert policy.camp_recall_step(on(door), mem, frame) == [policy.pad('a')]
    assert policy.camp_recall_step(_fort_menu(), mem, frame) == [policy.pad('a')]
    assert state['stage'] == 'fort_list' and state['target'] == door


def test_a_doubted_dome_drops_out_and_the_real_camp_is_used():
    frame = _two_camp_frame()
    mem = memory()
    assert policy.camp_recall_step(on((134, 117)), mem, frame) == [policy.pad('a')]
    state = _flow(mem)['recall']
    door = list(state['camp_body'])
    # Two dead presses on the flag cell, then one on the door cell.
    for _ in range(recall.MENU_OPEN_WAIT):
        policy.camp_recall_step(on((134, 117)), mem, frame)
    assert policy.camp_recall_step(on((134, 117)), mem, frame) == [policy.pad('a')]
    for _ in range(recall.MENU_OPEN_WAIT):
        policy.camp_recall_step(on((134, 117)), mem, frame)
    assert state['target'] == door
    assert policy.camp_recall_step(on(door), mem, frame) == [policy.pad('a')]
    for _ in range(recall.MENU_OPEN_WAIT):
        policy.camp_recall_step(on(door), mem, frame)
    assert state['stage'] == 'to_camp'
    # The doubted sighting is dropped and the real camp takes over: its menu
    # opens on the first press and the recall proceeds to the destination leg.
    assert policy.camp_recall_step(on((121, 141)), mem, frame) == [policy.pad('a')]
    doubted = decisions(mem, 'camp_detection_doubted')
    assert doubted and doubted[-1]['observed_metric']['dropped'] == [[134, 117]]
    assert state['target'] == [121, 141] and 'camp_kind' not in state
    assert policy.camp_recall_step(camp_menu(0), mem, frame) == [policy.pad('a')]
    assert state['stage'] == 'dest'
    assert decisions(mem, 'camp_recall_cursor')


def test_a_doubted_only_detection_ends_the_recall_as_unobserved():
    frame = _lone_dome_frame()
    mem = memory()
    assert policy.camp_recall_step(on((134, 117)), mem, frame) == [policy.pad('a')]
    state = _flow(mem)['recall']
    door = list(state['camp_body'])
    for _ in range(recall.MENU_OPEN_WAIT):
        policy.camp_recall_step(on((134, 117)), mem, frame)
    assert policy.camp_recall_step(on((134, 117)), mem, frame) == [policy.pad('a')]
    for _ in range(recall.MENU_OPEN_WAIT):
        policy.camp_recall_step(on((134, 117)), mem, frame)
    assert policy.camp_recall_step(on(door), mem, frame) == [policy.pad('a')]
    for _ in range(recall.MENU_OPEN_WAIT):
        policy.camp_recall_step(on(door), mem, frame)
    # Nothing else is on the map: the doubted sighting is not treated as a
    # camp, so the recall ends as unobserved instead of a fourth dead press.
    for _ in range(3):
        assert policy.camp_recall_step(on((200, 200)), mem, frame) == []
    assert 'recall' not in _flow(mem)
    assert _flow(mem)['recall_verification']['status'] == 'camp_unobserved'
    assert _flow(mem).get('recall_skip') == {'tick': 100}
    assert decisions(mem, 'camp_detection_doubted')
