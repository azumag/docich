"""An unverified target must reconcile the general by the X status menu.

These tests deliberately provide no roof pixels. The general status sentence,
not a triangle, establishes whether the named actor is still in a castle.
"""
from docich.hanjuku.flows import arrival_status as A
from docich.hanjuku.flows import map as M
from docich.hanjuku.state.regions import world as _world
from docich.hanjuku.os_layer.font import TextLine
from docich.hanjuku.os_layer.screen import Screen


def line(y, x, word):
    return TextLine(y, tuple((x + 8 * i, ch) for i, ch in enumerate(word)))


def screen(kind, rows=(), *, selected=None, hand_y=39):
    lines = [line(y, x, word) for y, x, word in rows]
    return Screen(lines=lines, hand=(146, hand_y - 6, 166, hand_y + 7),
                  selected=selected, text=' '.join(w for _, _, w in rows),
                  kind=kind, cursor=(80, 90) if kind == 'map' else None,
                  marker=(80, 90) if kind == 'map_target' else None)


def main(hand_y=39):
    return screen('main_menu', [(39, 168, 'ステータス'),
                                (55, 168, 'しょうぐん'),
                                (71, 168, 'しろ')], hand_y=hand_y)


def roster(selected='ココット', hand_y=55):
    lines = [(39, 168, 'どうし'), (55, 168, 'ココット'),
             (183, 24, 'Aボタンでステータスひょうじ'),
             (199, 24, 'Bボタンでコマンドにもどりますじゃ!')]
    return screen('text', lines, selected=selected, hand_y=hand_y)


def status(general='ココット', location='しんぐんちゅうです'):
    # Native OCR produces a single TextLine per y, even when the cells have
    # two separate text spans. Do not model the two y=39 spans as two lines.
    chars = tuple((24 + 8 * i, ch) for i, ch in enumerate(general))
    chars += tuple((88 + 8 * i, ch) for i, ch in enumerate('しょうぐん'))
    return Screen(lines=[TextLine(39, chars), line(151, 24, location)],
                  hand=None, text=f'{general} しょうぐん {location}', kind='text')


def memory():
    return {'chapter': 1, 'tick': 55, 'active': 'J1', 'orders': {},
            'target_miss': {'J1': M.TARGET_MISS_LIMIT - 1},
            'garrison': {'アルマムーン': ['どうし', 'ココット']},
            'sortie_general': {'J1': 'ココット'}, '_records': []}


def decisions(mem, kind):
    return [r for r in mem['_records'] if r.get('decision') == kind]


def test_cancelled_unverified_target_queues_named_probe_not_false_departure():
    mem = memory()
    order = {'step': 'J1', 'general': 'ココット',
             'source': 'アルマムーン', 'target': 'ジョンリギ',
             'cards': [], 'after': None, 'note': 'test'}
    assert M._unverified_target(screen('map_target'), mem, order) == [M.pad('b')]
    assert mem['active'] is None
    assert mem['orders']['J1'] == 'pending'
    assert mem['arrival_status_probe']['general'] == 'ココット'
    assert not mem.get('sorties')
    assert decisions(mem, 'arrival_status_probe_queued')


def test_x_general_status_march_removes_stale_resident_then_reanchors():
    mem = memory()
    order = {'step': 'J1', 'general': 'ココット',
             'source': 'アルマムーン', 'target': 'ジョンリギ',
             'cards': [], 'after': None, 'note': 'test'}
    M._unverified_target(screen('map_target'), mem, order)
    assert A.step(screen('map_target'), mem) == [A.pad('b')]
    assert A.step(screen('map'), mem) == [A.pad('x')]
    assert A.step(main(39), mem) == [A.pad('down')]
    assert A.step(main(55), mem) == [A.pad('a')]
    assert A.step(roster(selected='どうし', hand_y=39), mem) == [A.pad('down')]
    assert A.step(roster(), mem) == [A.pad('a')]
    assert A.step(status(), mem) == [A.pad('b')]
    assert mem['arrival_general_observations']['ココット']['location'] == 'march'
    assert _world(mem)['garrison']['アルマムーン'] == ['どうし']
    assert 'ココット' in _world(mem)['general_location_unknown']
    assert decisions(mem, 'arrival_status_probe_verified')
    assert A.step(roster(), mem) == [A.pad('b')]
    assert A.step(main(), mem) == [A.pad('b')]
    assert A.step(screen('map'), mem) is None
    assert 'arrival_status_probe' not in mem
    assert mem['nav_search'] is True
    assert mem['uncertain'] is True
    assert decisions(mem, 'arrival_status_probe_done')


def test_castle_status_does_not_guess_which_castle():
    mem = memory()
    assert A.queue(mem, {'step': 'J1'}, 'ココット')
    mem['arrival_status_probe']['phase'] = 'status'
    assert A.step(status(location='しろのなかにいます'), mem) == [A.pad('b')]
    assert mem['arrival_general_observations']['ココット']['location'] == 'castle'
    assert _world(mem)['garrison']['アルマムーン'] == ['どうし', 'ココット']
    assert not _world(mem).get('general_location_unknown')


def test_unreadable_or_wrong_person_does_not_change_garrison():
    mem = memory()
    assert A.queue(mem, {'step': 'J1'}, 'ココット')
    mem['arrival_status_probe']['phase'] = 'status'
    wrong = status(general='どうし', location='しんぐんちゅうです')
    for _ in range(A.STATUS_READ_LIMIT):
        A.step(wrong, mem)
    assert mem['arrival_status_probe']['phase'] == 'close'
    assert _world(mem)['garrison']['アルマムーン'] == ['どうし', 'ココット']
    assert not mem.get('arrival_general_observations')
    assert decisions(mem, 'arrival_status_probe_unverified')


def test_probe_is_bounded_and_respects_month_owner():
    mem = memory()
    assert A.queue(mem, {'step': 'J1'}, 'ココット')
    assert not A.queue(mem, {'step': 'J1'}, 'ココット')
    mem['post_month_status'] = {'stage': 'roster'}
    assert A.step(screen('map'), mem) is None
    assert mem['arrival_status_probe']['phase'] == 'backout'
    mem.pop('post_month_status')
    mem.pop('arrival_status_probe')
    assert not A.queue(mem, {'step': 'J1'}, 'ココット')
