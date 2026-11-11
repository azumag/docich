"""Chapter 9 fills the front castle instead of relaying one general at a time.

Owner 2026-11-11: 前線への中継移動も今やって. g1163 11:20 held 17 generals at
アルマムーン and only ビーツ at ハヤク: a move already heading to ハヤク blocked
the next one and one leg carried one move per plan, so the road waves had
nobody at the front to send.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401  (package import side effects)
from docich.hanjuku.strategy import replan

HOME = chart.home_castle(9)
FRONT = chart.corridor(9)[1]          # ハヤク
REAR = ['どうし', 'ミント', 'ジェラート', 'ガルバンゾー', 'シナモン', 'マフィン',
        'アルファルファ', 'ユイートル', 'マカデミア']


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def memory(front, incoming=()):
    return {'chapter': 9, 'tick': 35000, 'month': '18-1', 'captured': [FRONT], 'orders': {},
            'garrison': {HOME: list(REAR), FRONT: list(front)},
            'sorties': {f'I:move{i}': {'general': g, 'source': HOME, 'target': FRONT,
                                       'status': 'en_route', 'tick': 34900 + i}
                        for i, g in enumerate(incoming)},
            '_records': []}


def plan(mem):
    with chart.map_context(mem):
        return list(replan.interim_candidates(mem).values())


def staging(orders):
    return [o for o in orders if o['purpose'] == 'move' and o['target'] == FRONT]


def test_the_front_is_filled_in_one_plan():
    # ビーツ is this plan's attacker, so he does not count as staying at the front.
    orders = plan(memory(['ビーツ']))
    moves = staging(orders)
    assert len(moves) == replan.CORRIDOR_FRONT_STAGE
    assert len({o['general'] for o in moves}) == len(moves)
    assert all(o['source'] == HOME and o['general'] != 'どうし' for o in moves)
    # The front's own attack still runs before the extra relays.
    kinds = [(o['purpose'], o['target']) for o in orders]
    first_attack = kinds.index(('attack', chart.corridor(9)[2]))
    assert first_attack < orders.index(moves[1])


def test_marching_relays_count_toward_the_front():
    orders = plan(memory(['ビーツ'], incoming=['ライム']))
    moves = staging(orders)
    assert len(moves) == replan.CORRIDOR_FRONT_STAGE - 1
    assert 'ライム' not in {o['general'] for o in moves}


def test_a_full_front_with_relays_marching_sends_no_more():
    full = ['ビーツ', 'ペコリーノ', 'リーキ', 'タルタル', 'ヘルメス', 'ライム']
    orders = plan(memory(full, incoming=['ミモザ', 'キャロット']))
    assert staging(orders) == []


def test_a_relay_marching_no_longer_blocks_the_next_one():
    orders = plan(memory(['ビーツ', 'ペコリーノ'], incoming=['ライム', 'ミモザ']))
    assert len(staging(orders)) >= 1


def test_other_chapters_keep_their_single_relay():
    assert replan.CORRIDOR_FRONT_STAGE == replan.CORRIDOR_WAVE_INFLIGHT + 1
    mem = {'chapter': 3, 'tick': 1, '_records': []}
    assert not chart.corridor(3)
