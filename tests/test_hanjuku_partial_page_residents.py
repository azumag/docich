"""A full page proves who stands here, even though it is not the roster.

g1163 2026-11-12 10:3x: タオ／ハヤク／アルマムーン all read しょうぐん0めい while
リーキ and コリアンダー marched out of one of them. The list read that produced
those actors was a full page, which the deploy treated as incomplete and threw
away. `garrison` must stay untouched (a page never claims the roster), so the
lower bound is kept beside it in `garrison_partial` — and candidate selection
names the page's generals instead of treating the castle as unread
(docich#3996, docich#3997). These tests run the real path end to end:
``_observe_garrison`` on a full-page screen, then ``interim_candidates``.
"""
import pytest

from docich import hanjuku_chart as chart
from docich import hanjuku_policy as policy
from docich.hanjuku.flows import deploy
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen
from docich.hanjuku.state.regions import world as _world
from docich.hanjuku.strategy import replan

ROAD = chart.corridor(9)
HOME = chart.home_castle(9)
PAGE = ['リーキ', 'コリアンダー', 'キャラウェイ', 'クミン']   # one full page of the list


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def _list_screen(names):
    lines = [TextLine(47, tuple((64 + 8 * i, ch) for i, ch in enumerate('しゅつげき'))),
             TextLine(63, tuple((64 + 8 * i, ch) for i, ch in enumerate('ステータス')))]
    lines += [TextLine(39 + 16 * n, tuple((144 + 8 * i, ch) for i, ch in enumerate(name)))
              for n, name in enumerate(names)]
    return Screen(lines=lines, hand=(122, 33, 139, 45), kind='general_list',
                  text='しゅつげきステータス' + ''.join(names))


def _memory():
    """Own the first three road castles; ハヤク's list was never read whole."""
    owned = ROAD[:3]
    return {'chapter': 9, 'tick': 1000, 'month': '1-7',
            'captured': [c for c in owned if c != HOME], 'orders': {},
            'garrison': {HOME: ['どうし', 'ゼウス'], 'タオ': ['ココット']},
            '_records': []}


def _observe(mem, castle, names):
    order = {'step': 'T:1', 'source': castle, 'target': 'タオ',
             'general': names[0] if names else policy.MOVE_ANY_GENERAL,
             'cards': [], 'note': 'test'}
    # deploy's own rule: a page of GENERAL_LIST_FULL rows is not a roster.
    policy._observe_garrison(_list_screen(names), mem, order,
                             complete=len(names) < deploy.GENERAL_LIST_FULL)


def _moves_from(mem, castle):
    with chart.map_context(mem):
        candidates = replan.interim_candidates(mem)
    return [o for o in candidates.values()
            if o['purpose'] == 'move' and o['source'] == castle]


def test_a_full_page_names_its_generals_for_the_next_castle():
    mem = _memory()
    _observe(mem, 'ハヤク', PAGE)
    world = _world(mem)
    # The page is a lower bound beside the roster, never the roster itself,
    # and it earns no completeness stamp.
    assert 'ハヤク' not in (world.get('garrison') or {})
    assert world['garrison_partial']['ハヤク'] == PAGE
    assert 'ハヤク' not in (world.get('garrison_observed_at') or {})
    moves = _moves_from(mem, 'ハヤク')
    # Exactly the page's names march — nobody else's absence or presence is
    # inferred — and the castle is no longer probed as if unread.
    assert {o['general'] for o in moves} == set(PAGE)
    assert not any(o.get('source_probe') for o in moves)


def test_a_complete_read_replaces_the_pages_lower_bound():
    mem = _memory()
    _observe(mem, 'ハヤク', PAGE)
    _observe(mem, 'ハヤク', ['ヘラ', 'セージ'])
    assert _world(mem)['garrison']['ハヤク'] == ['ヘラ', 'セージ']
    moves = _moves_from(mem, 'ハヤク')
    assert {o['general'] for o in moves} == {'ヘラ', 'セージ'}


def test_a_name_read_into_another_castle_is_not_picked_from_the_page():
    mem = _memory()
    mem['garrison'][HOME] = ['どうし', 'リーキ']   # a complete read puts リーキ at home
    _observe(mem, 'ハヤク', PAGE)
    moves = _moves_from(mem, 'ハヤク')
    # The stale page evidence for リーキ is dropped; the rest of the page stands.
    assert {o['general'] for o in moves} == {'コリアンダー', 'キャラウェイ', 'クミン'}


def test_a_wave_spare_can_come_from_a_full_page():
    mem = _memory()
    del mem['garrison']['タオ']
    _observe(mem, 'タオ', PAGE)
    picked = replan._wave_pick(mem, 'サヌト', set(ROAD[:3]), set(), False)
    assert picked is not None
    source, general = picked
    assert source == 'タオ' and general in PAGE and general != chart.HERO


def _front_memory(full, partial, full_tick=None, partial_tick=None):
    world = {'garrison': {'オオキク': list(full)},
             'garrison_partial': {'オオキク': list(partial)}}
    if full_tick is not None:
        world['garrison_tick'] = {'オオキク': full_tick}
    if partial_tick is not None:
        world['garrison_partial_tick'] = {'オオキク': partial_tick}
    return {'chapter': 9, 'tick': 6000, 'world': world}


def test_the_newer_complete_read_wins_over_an_older_page():
    """g1163 2026-11-13 15:0x: a stale four-name page hid a two-name front.

    ``_front_names`` reported four, so the pair wait let the attack leave one
    defender and オオキク fell to the counter-attack.
    """
    from docich.hanjuku.strategy import corridor
    mem = _front_memory(['キール', 'ロックフォール'], PAGE,
                        full_tick=5000, partial_tick=4000)
    assert corridor._front_names(mem, 'オオキク') == ['キール', 'ロックフォール']


def test_a_newer_page_still_wins_over_an_older_complete_read():
    from docich.hanjuku.strategy import corridor
    mem = _front_memory(['キール'], PAGE, full_tick=4000, partial_tick=5000)
    assert corridor._front_names(mem, 'オオキク') == PAGE


def test_unticked_data_keeps_the_longest_rule():
    """The v700 rule stays for state written before the ticks existed."""
    from docich.hanjuku.strategy import corridor
    mem = _front_memory(['キール'], PAGE)
    assert corridor._front_names(mem, 'オオキク') == PAGE


def test_a_move_updates_the_page_for_both_castles():
    """The move is authoritative for the mover; the other names stay.

    g1163 2026-11-13 20:2x: オシロガ's four-name page vanished after one move,
    the front saw no reinforcements and marched its last resident out.
    """
    from docich.hanjuku.strategy import orders as O
    mem = {'chapter': 9, 'tick': 100, '_records': [], 'world': {
        'garrison': {'ドンドン': ['アルテミス', 'ペコリーノ']},
        'garrison_partial': {'ドンドン': ['ジェラート', 'ラビオリ', 'クランベリー', 'ペコリーノ'],
                             'オシロガ': ['ガスパチョ']},
        'garrison_partial_tick': {'ドンドン': 90, 'オシロガ': 90}}}
    O._garrison_move(mem, 'ペコリーノ', source='ドンドン', target='オシロガ')
    assert mem['world']['garrison_partial']['ドンドン'] == ['ジェラート', 'ラビオリ', 'クランベリー']
    assert mem['world']['garrison_partial']['オシロガ'] == ['ガスパチョ', 'ペコリーノ']
    assert mem['world']['garrison']['オシロガ'] == ['ペコリーノ']


def test_a_lost_castle_drops_its_page_and_marks_members_unknown():
    """g1163 2026-11-13 22:3x: ドンドン's page outlived its capture."""
    from docich.hanjuku.os_layer.world import garrison_lost
    mem = {'chapter': 9, 'tick': 100, 'world': {
        'garrison': {'ドンドン': ['キャロット']},
        'garrison_partial': {'ドンドン': ['キャロット', 'カシス']},
        'garrison_partial_tick': {'ドンドン': 90}}}
    assert garrison_lost(mem, 'ドンドン') == ['キャロット']
    assert 'ドンドン' not in mem['world']['garrison_partial']
    assert 'ドンドン' not in mem['world']['garrison_partial_tick']
    assert 'キャロット' in mem['world']['general_location_unknown']
