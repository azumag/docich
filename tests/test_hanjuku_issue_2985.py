"""Issue #2985: keep strong defenders at home during the post-conquest boss wait."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart as chart
from docich.hanjuku.strategy.orders import (_defense_move_pick, _defense_priority_castles,
                                            _post_conquest_wait)
from docich.hanjuku.strategy.replan import interim_candidates

NORMAL3 = sorted(set(chart.CASTLE_NAMES[3]) - {chart.home_castle(3), chart.boss_castle(3)})


def _mem(garrison, captured=None, **kw):
    return {'chapter': 3, 'month': '22-2', 'tick': 100,
            'captured': list(NORMAL3) if captured is None else captured,
            'garrison': garrison, 'lost': [], 'egg_types': {}, 'egg_uses': {},
            'house_eggs': {}, **kw}


def test_wait_gate_needs_full_capture():
    g = {'アルマムーン': ['セルリアク']}
    assert _post_conquest_wait(_mem(g))
    assert not _post_conquest_wait(_mem(g, captured=NORMAL3[:-1]))
    assert not _post_conquest_wait(_mem(g, chapter=1))


def test_priority_castles_are_home_and_boss_nearest():
    g = {c: ['ミュスカデ'] for c in NORMAL3} | {'アルマムーン': ['セルリアク']}
    pri = _defense_priority_castles(_mem(g))
    assert 'アルマムーン' in pri
    # ボス(306,141) に最も近い所有城は リグア(133,281)
    assert pri == {'アルマムーン', 'リグア'}


def test_pick_outside_wait_keeps_list_head():
    mem = _mem({}, captured=NORMAL3[:-1])
    assert _defense_move_pick(mem, 'アルマムーン', 'ボス',
                              ['アテナ', 'セルリアク']) == 'アテナ'


def test_priority_source_sends_weakest_and_keeps_strongest():
    mem = _mem({'アルマムーン': ['アテナ', 'セルリアク'],
                'リグア': ['レモン', 'ピオーネ']},
               egg_types={'アテナ': 'スーパーエッグ'}, egg_uses={'アテナ': 4})
    # アルマムーン (priority) から出す側: 最弱 セルリアク を送り アテナ を残す
    assert _defense_move_pick(mem, 'アルマムーン', 'グリン',
                              ['アテナ', 'セルリアク']) == 'セルリアク'
    # 非 priority 同士は従来どおり先頭
    assert _defense_move_pick(mem, 'グリン', 'ドンダム',
                              ['アテナ', 'セルリアク']) == 'アテナ'


def test_weak_priority_target_gets_strongest_spare():
    mem = _mem({'アルマムーン': ['セルリアク'],
                'グリン': ['リースリング', 'アテナ']},
               egg_types={'アテナ': 'スーパーエッグ'}, egg_uses={'アテナ': 4})
    assert _defense_move_pick(mem, 'グリン', 'アルマムーン',
                              ['リースリング', 'アテナ']) == 'アテナ'


def test_unmeasured_stats_keep_observed_order():
    mem = _mem({'アルマムーン': ['ナゾ１', 'ナゾ２']})
    # general_strength=None の同点は一覧順のまま (非退行)
    assert _defense_move_pick(mem, 'アルマムーン', 'グリン',
                              ['ナゾ２', 'ナゾ１']) == 'ナゾ１'


def test_rebalance_moves_strongest_into_weak_home():
    garrison = {'アルマムーン': ['セルリアク'],
                'グリン': ['リースリング', 'アテナ'],
                'リグア': ['レモン', 'ピオーネ'],
                'コラダイン': ['ミュスカデ'], 'イーピヨル': ['ピオーネ'],
                'ドンダム': ['ピオーネ'], 'メガパタ': ['デュオニソス'],
                'パタゴーヌ': ['アポロン'], 'アバロン': ['ソーピニヨン']}
    mem = _mem(garrison, egg_types={'アテナ': 'スーパーエッグ'},
               egg_uses={'アテナ': 4})
    moves = [o for o in interim_candidates(mem).values()
             if o.get('purpose') == 'move' and o['target'] == 'アルマムーン']
    assert moves and moves[0]['general'] == 'アテナ'
    assert moves[0]['source'] == 'グリン'


def test_no_rebalance_before_full_capture():
    garrison = {'アルマムーン': ['セルリアク'], 'グリン': ['リースリング', 'アテナ']}
    mem = _mem(garrison, captured=NORMAL3[:-1])
    assert not any(o.get('defense_rebalance')
                   for o in interim_candidates(mem).values())


def test_donor_keeps_defender_and_hero_is_never_sent():
    # ドナーは残り1人なので送れない -> 候補なし
    mem = _mem({'アルマムーン': ['セルリアク'], 'グリン': ['アテナ']})
    assert not any(o.get('purpose') == 'move' and o['target'] == 'アルマムーン'
                   for o in interim_candidates(mem).values())
    # 主人公は送り対象外: ホームがドナーでも どうし は送られない
    mem2 = _mem({'アルマムーン': ['どうし', 'アテナ', 'セルリアク'],
                 'リグア': ['レモン']})
    assert _defense_move_pick(mem2, 'アルマムーン', 'ボス',
                              ['どうし', 'アテナ', 'セルリアク']) == 'セルリアク'
