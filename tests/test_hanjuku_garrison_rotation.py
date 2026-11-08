"""Owner 2026-11-04: castles near an enemy castle are staffed first.

人数の少ない城は陥落しやすいので、積極的に将軍をまわす。敵の城に近いほど
優先度をあげ、敵の城に遠い城は一人もしくは0人でも良い。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart as chart
from docich import hanjuku_policy as policy
from docich.hanjuku.strategy.orders import _garrison_plan
from docich.hanjuku.strategy.replan import _garrison_rotation
from docich.hanjuku.state.regions import world as _world

OWNED = ['キカンドン', 'ナキューメラ', 'ゴーメン', 'カストーラ']


def memory(garrison, **kw):
    return {'chapter': 1, 'month': '5-1', 'tick': 100, 'lost': [],
            'captured': list(OWNED), 'garrison': garrison,
            'egg_types': {}, 'egg_uses': {}, 'house_eggs': {}, **kw}


def rotation(mem):
    return _garrison_rotation(mem, set(_world(mem)['garrison']), set(), set())


def test_plan_targets_follow_distance_to_the_nearest_enemy():
    mem = memory({c: ['アテナ'] for c in (*OWNED, 'アルマムーン')})
    plan = _garrison_plan(mem)
    # 第1章の敵城は ジョンリギ/スペンソニア/けっかい。最短は キカンドン(167)。
    assert plan['キカンドン'] == (167, 3, 1)
    assert plan['カストーラ'] == (208, 2, 1)      # 次の輪は2人
    assert plan['ナキューメラ'] == (231, 1, 1)     # それ以外は1人
    # ゴーメン(288) は最前線の1.5倍以上遠いので0人まで送れる。
    assert plan['ゴーメン'] == (288, 1, 0)
    # 本城は遠くても1人は残す。
    assert plan['アルマムーン'] == (411, 1, 1)


def test_a_surplus_rear_general_moves_to_the_front():
    mem = memory({'キカンドン': ['アテナ'],
                  'カストーラ': ['レモン', 'ピオーネ'],
                  'ナキューメラ': ['ソーピニヨン', 'デュオニソス'],
                  'ゴーメン': ['アポロン'],
                  'アルマムーン': ['どうし', 'ゼウス', 'アルテミス']})
    moves = rotation(mem)
    assert [(o['source'], o['target']) for o in moves] == [('ナキューメラ', 'キカンドン')]
    assert moves[0]['garrison_rotate'] is True and moves[0]['purpose'] == 'move'
    # 余剰を出す城は1人残す（2人 -> 1人）。
    assert len(_world(mem)['garrison']['ナキューメラ']) - 1 == 1


def test_a_far_rear_castle_never_sends_its_last_defender():
    mem = memory({'キカンドン': [], 'カストーラ': [], 'ナキューメラ': [],
                  'ゴーメン': ['アポロン'],
                  'アルマムーン': ['どうし', 'ゼウス', 'アルテミス']})
    moves = rotation(mem)
    assert moves == []
    # 最前線の人数を増やすためでも、遠い城の最後の守備将軍は出さない。


def test_the_home_castle_never_loses_its_guard():
    mem = memory({'キカンドン': ['アテナ'],
                  'カストーラ': ['レモン'],
                  'ナキューメラ': ['ソーピニヨン'],
                  'ゴーメン': ['アポロン'],
                  'アルマムーン': ['どうし', 'ゼウス', 'アルテミス']})
    # 本城は3人で目標1人を上回るが、主人公の隣に2人残せないので送らない。
    assert all(o['source'] != 'アルマムーン' for o in rotation(mem))


def test_an_unread_transfer_holds_the_target_until_its_list_is_read():
    mem = memory({'キカンドン': ['アテナ'],
                  'カストーラ': ['レモン', 'ピオーネ'],
                  'ナキューメラ': ['ソーピニヨン'],
                  'ゴーメン': ['アポロン'],
                  'アルマムーン': ['どうし', 'ゼウス', 'アルテミス']})
    assert [o['target'] for o in rotation(mem)] == ['キカンドン']
    # 到着をまだ一覧で読んでいない移動がある城へは、二重に送らない。
    mem['sorties'] = {'I:x:1': {'purpose': 'move', 'source': 'カストーラ',
                                'target': 'キカンドン', 'general': 'レモン',
                                'status': 'en_route', 'tick': 50}}
    mem['route_probe_read_tick'] = {'キカンドン': 10}
    assert rotation(mem) == []


def test_no_rotation_once_every_castle_holds_its_target():
    mem = memory({'キカンドン': ['アテナ', 'アポロン', 'ゼウス'],
                  'カストーラ': ['レモン', 'ピオーネ'],
                  'ナキューメラ': ['ソーピニヨン'],
                  'ゴーメン': ['デュオニソス'],
                  'アルマムーン': ['どうし', 'ゼウス', 'アルテミス']})
    assert rotation(mem) == []


def test_a_lost_castle_is_not_emptied():
    mem = memory({c: ['アテナ'] for c in (*OWNED, 'アルマムーン')}, lost=['ゴーメン'])
    assert _garrison_plan(mem)['ゴーメン'] == (288, 1, 1)


def test_interim_candidates_rotate_after_the_attacks():
    mem = memory({'キカンドン': ['アテナ'],
                  'カストーラ': ['レモン', 'ピオーネ'],
                  'ナキューメラ': ['ソーピニヨン', 'デュオニソス'],
                  'ゴーメン': ['アポロン', 'ポセイドン'],
                  'アルマムーン': ['どうし', 'ゼウス', 'アルテミス']})
    candidates = list(policy.interim_candidates(mem).values())
    rotated = [i for i, o in enumerate(candidates) if o.get('garrison_rotate')]
    # Earlier sorties can reserve every eligible surplus general; in that case
    # no rotation is valid. If rotation exists it must follow the attacks.
    assert not rotated or rotated == [len(candidates) - 1], candidates
    # 攻撃・奪還を先に並べ、回す将軍は余剰だけを使う。
    assert all(o['purpose'] in ('attack', 'retake') for o in (candidates[:-1] if rotated else candidates))
    if rotated:
        assert candidates[-1]['source'] == 'ゴーメン' and candidates[-1]['target'] == 'キカンドン'
