"""Chapter 9 retakes of the next road castle go out as waves too.

Owner 2026-11-11: 移動し終えたわけじゃないのに画面ずっと放置していたのは何？
g1163 14:12:19-14:14:41 sat on the map: seven retakes of タオ were planned from
ハヤク, but ``_retake_reserved`` held them all while シナモン's retake was still
walking. v660 opened the planner to three expeditions at the next road castle;
the execution gate now follows it. #3944 unifies the wave on ``SQUAD`` (v671,
six): the planner and the gate count the same in-flight sorties, so a minted
retake is never held by the gate it just passed.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.strategy import corridor
from docich.hanjuku.strategy import orders as O

ROAD = chart.corridor(9)
FRONT, NEXT = ROAD[1], ROAD[2]
MARCHING_GENERALS = ['シナモン', 'ミント', 'ライム', 'タルタル', 'リーキ', 'ラズベリー']


def _mem(chapter=9, marching=1):
    sorties = {f'I:r{i}': {'general': g, 'source': FRONT, 'target': NEXT, 'purpose': 'retake',
                           'status': 'en_route', 'tick': 1000 + i}
               for i, g in enumerate(MARCHING_GENERALS[:marching])}
    return {'chapter': chapter, 'tick': 1100, 'captured': [FRONT], 'lost': [NEXT], 'orders': {},
            'garrison': {FRONT: ['ローズマリー', 'ベーグル']}, 'sorties': sorties, '_records': []}


def _order(corridor=True):
    # Corridor-issued retakes carry 'corridor': True (corridor.orders);
    # plain retakes omit it and stay under the one-retake gate (v674).
    return {'step': 'I:x:1', 'general': 'ローズマリー', 'source': FRONT, 'target': NEXT,
            'purpose': 'retake', 'cards': [], 'after': None, 'note': 't', 'corridor': corridor}


def _reserved(mem, corridor=True):
    with chart.map_context(mem):
        return O._retake_reserved(_order(corridor=corridor), mem)


def test_one_marching_retake_no_longer_holds_the_next_wave():
    assert _reserved(_mem(marching=1)) is False


@pytest.mark.parametrize('marching', range(corridor.SQUAD))
def test_a_minted_retake_passes_the_gate_while_the_wave_is_open(marching):
    # #3944: in-flight 0..5 used to mint a retake the gate then held
    # (57 order_start vs 53 sortie_held_target_reserved); the gate and the
    # planner now count the same sorties, so a minted retake passes.
    assert _reserved(_mem(marching=marching)) is False


def test_no_retake_is_minted_once_the_vanguard_is_full():
    # #3944: at SQUAD the planner stops minting instead of minting a retake
    # the gate then fails. The marching sorties here are plain ``I:r`` steps,
    # so this also pins the reserved-target counting (not just corridor ids).
    mem = _mem(marching=corridor.SQUAD)
    with chart.map_context(mem):
        assert [o for o in corridor.orders(mem) if o['purpose'] == 'retake'] == []


def test_the_wave_cap_still_holds_further_retakes():
    # At SQUAD the planner stops minting (pinned by the test above); a plain
    # retake at the same target is still held by the gate (c0a34557: the
    # corridor bypass applies to corridor orders only).
    assert _reserved(_mem(marching=corridor.SQUAD), corridor=False) is True


def test_other_chapters_keep_one_retake_at_a_time():
    mem = _mem(chapter=8)
    mem['captured'] = ['しらかわ']
    mem['lost'] = ['ながはま']
    mem['sorties']['I:r0'].update(source='しらかわ', target='ながはま')
    order = {**_order(corridor=False), 'source': 'しらかわ', 'target': 'ながはま'}
    with chart.map_context(mem):
        assert O._retake_reserved(order, mem) is True
