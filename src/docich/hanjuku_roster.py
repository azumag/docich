"""Fresh, bounded roster receipts. No historical egg cache or paid head counts."""
from __future__ import annotations

MAX_GENERALS = 32
# hanjuku_house._names_at reads the name column at y=39..151 in steps of 16,
# so the roster shows this many names at once. A page shorter than this ends
# on screen and cannot hide a further entry.
VISIBLE_NAME_ROWS = 8
# SFC ID0..127 canonical char.csv: candidate wage is not known before payment.
MAX_RECRUIT_WAGE = 15
FRESH_TICKS = 400
# Stage castle properties (SFC): gcgx 01.html/02.html. These validate an
# actual income receipt; they never supply an unobserved income themselves.
# castle.html reports no income increase from level, and separates harvest
# multipliers from the normal sum. Unsupported stages retain bounded reads.
NORMAL_CASTLE_INCOME = {
    1: {'アルマムーン': 22, 'ナキューメラ': 13, 'キカンドン': 14, 'ジョンリギ': 16,
        'カストーラ': 18, 'スペンソニア': 22, 'ゴーメン': 17},
    2: {'アルマムーン': 30, 'ハドリバーグ': 27, 'フーリック': 22, 'グロン': 30,
        'ドミノーラ': 32, 'ウラノポリス': 24, 'スペランザ': 38, 'アウスパジア': 39},
}

# Fixed SFC wages, char.csv at 5e982942ec24fb559f58b29250560fd784e5ae5c.
# Only freshly identified actual roster members contribute to the total.
GENERAL_WAGES = {
    'しゅじんこう': 0,  # 0
    'キャラウェイ': 6,  # 1
    'クミン': 4,  # 2
    'コリアンダー': 5,  # 3
    'バジル': 7,  # 4
    'ミント': 6,  # 5
    'パプリカ': 5,  # 6
    'シナモン': 4,  # 7
    'ヘーゼル': 3,  # 8
    'ガルバンゾー': 2,  # 9
    'ラズベリー': 7,  # 10
    'ピスタチオ': 9,  # 11
    'マカデミア': 6,  # 12
    'カシュー': 5,  # 13
    'クランベリー': 6,  # 14
    'ガスパチョ': 10,  # 15
    'ビシソワーズ': 4,  # 16
    'タピオカ': 3,  # 17
    'キッシュ': 9,  # 18
    'ロックフォール': 4,  # 19
    'シェーブル': 8,  # 20
    'アマンディーヌ': 10,  # 21
    'チコリ': 2,  # 22
    'ビーツ': 4,  # 23
    'セルリアク': 3,  # 24
    'アンディーブ': 4,  # 25
    'リーキ': 5,  # 26
    'トレビス': 4,  # 27
    'アルファルファ': 4,  # 28
    'タルタル': 6,  # 29
    'ヘルメス': 3,  # 30
    'リースリング': 6,  # 31
    'デュオニソス': 8,  # 32
    'アルテミス': 4,  # 33
    'キャンディー': 8,  # 34
    'シャルドネ': 7,  # 35
    'ソーピニヨン': 8,  # 36
    'ピオーネ': 6,  # 37
    'ヘラ': 6,  # 38
    'セミヨン': 4,  # 39
    'ポワソン': 4,  # 40
    'デーメーテール': 5,  # 41
    'ユイートル': 9,  # 42
    'ヘパイストス': 8,  # 43
    'エシャロット': 7,  # 44
    'ミュスカデ': 5,  # 45
    'アテナ': 6,  # 46
    'エピィ': 7,  # 47
    'ポセイドン': 8,  # 48
    'ペコリーノ': 9,  # 49
    'アポロン': 10,  # 50
    'ジェラート': 4,  # 51
    'ミルフィーユ': 2,  # 52
    'ヘスティア': 3,  # 53
    'キール': 14,  # 54
    'ライム': 10,  # 55
    'レモン': 9,  # 56
    'フェットチーネ': 8,  # 57
    'バーミセリ': 12,  # 58
    'カペリーニ': 9,  # 59
    'ブカティーニ': 11,  # 60
    'ナストリーニ': 9,  # 61
    'ラビオリ': 6,  # 62
    'フェデリーニ': 4,  # 63
    'カシス': 15,  # 64
    'グレナデン': 12,  # 65
    'チキータ': 11,  # 66
    'ランプータン': 10,  # 67
    'ドリアン': 5,  # 68
    'マスカット': 15,  # 69
    'マンゴスチン': 5,  # 70
    'バタール': 10,  # 71
    'バゲット': 11,  # 72
    'ブリオッシュ': 13,  # 73
    'バトウラ': 9,  # 74
    'ブレッツェル': 8,  # 75
    'マフィン': 9,  # 76
    'ベーグル': 7,  # 77
    'ミモザ': 6,  # 78
    'ショコラ': 6,  # 79
    'プラリネ': 10,  # 80
    'ブラマンジェ': 7,  # 81
    'シフォン': 10,  # 82
    'シュゼット': 8,  # 83
    'ブラウニー': 6,  # 84
    'キャロット': 14,  # 85
    'マサラ': 15,  # 86
    'バラクーダ': 12,  # 87
    'コンポート': 14,  # 88
    'ギー': 10,  # 89
    'グリッシーニ': 15,  # 90
    'シュガー': 15,  # 91
    'オレガノ': 15,  # 92
    'ジキタリス': 6,  # 93
    'ローズマリー': 7,  # 94
    'マーマレード': 8,  # 95
    'サフラン': 12,  # 96
    'アニス': 14,  # 97
    'エストラゴン': 11,  # 98
    'エッジ': 12,  # 99
    'リディア': 3,  # 100
    'ガーラント': 14,  # 101
    'カイン': 15,  # 102
    'グレイ': 15,  # 103
    'レオンハルト': 6,  # 104
    'フリオニール': 7,  # 105
    'サムソー': 7,  # 106
    'ハバティー': 12,  # 107
    'マリボー': 3,  # 108
    'ラクレット': 12,  # 109
    'エダム': 13,  # 110
    'リゴット': 8,  # 111
    'ブリー': 9,  # 112
    'マルガリータ': 15,  # 113
    'ジン': 15,  # 114
    'マラスキーノ': 15,  # 115
    'アクアビット': 15,  # 116
    'ラ ターシュ': 14,  # 117
    'バランタイン': 15,  # 118
    'カミュ': 15,  # 119
    'クイーン': 10,  # 120
    'プリンス': 10,  # 121
    'にせヒーロー': 10,  # 122
    'せいめいたい': 10,  # 123
    'だいじん': 0,  # 124
    'ココット': 1,  # 125
    'ヴィーナス': 3,  # 126
    'ゼウス': 4,  # 127
}


def fixed_wage(name):
    return GENERAL_WAGES.get('しゅじんこう' if name == 'どうし' else name)


def roles(raw):
    if not isinstance(raw, dict) or set(raw) != {'per_castle', 'attack'}:
        raise ValueError('invalid recruitment roles')
    if (type(raw['per_castle']) is not int or not 1 <= raw['per_castle'] <= 4
            or type(raw['attack']) is not int or not 1 <= raw['attack'] <= 8):
        raise ValueError('invalid recruitment roles')
    return dict(raw)


def invalidate(mem):
    mem.pop('recruit_roster', None)
    mem.pop('roster_survey_scope', None)
    mem['recruit_roster_recheck'] = True
    if mem.get('house'):
        mem['house']['roster_invalidated'] = True


def surveyed(mem):
    """A completed walk of the current chapter/month roster already exists."""
    scope = mem.get('roster_survey_scope')
    return (mem.get('month') is not None and scope == [mem.get('chapter'), mem.get('month')])


def begin(mem, state):
    state['roster_started'] = int(mem.get('tick') or 0)
    state['roster_month'] = mem.get('month')
    mem['recruit_roster'] = {'chapter': mem.get('chapter'), 'month': mem.get('month'),
                             'tick': state['roster_started'], 'names': [], 'wages': {}, 'complete': False}


def fresh(mem):
    receipt = mem.get('recruit_roster')
    if not isinstance(receipt, dict):
        return None
    names, tick, now = receipt.get('names'), receipt.get('tick'), mem.get('tick')
    if (receipt.get('chapter') != mem.get('chapter') or receipt.get('month') != mem.get('month')
            or type(tick) is not int or type(now) is not int or not 0 <= now - tick < FRESH_TICKS
            or not isinstance(names, list) or not 1 <= len(names) <= MAX_GENERALS
            or any(not isinstance(n, str) or not n for n in names)
            or len(set(names)) != len(names)):
        return None
    return receipt


def page(mem, names):
    if names is None or not names or len(set(names)) != len(names):
        return
    state = mem.get('house') or {}
    scanning = state.get('phase') in {'roster', 'roster_next', 'roster_advance', 'status'}
    receipt = mem.get('recruit_roster')
    if scanning:
        if (state.get('roster_invalidated') or 'roster_started' not in state
                or state.get('chapter') != mem.get('chapter')
                or state.get('roster_month') != mem.get('month')
                or not isinstance(receipt, dict) or receipt.get('tick') != state['roster_started']):
            return
        joined = list(dict.fromkeys([*receipt['names'], *names]))
        if len(joined) <= MAX_GENERALS:
            receipt['names'] = joined
        return
    # Outside a scan, only this one page is evidence. Do not merge pages from
    # separate menu visits or turn a small page into a claimed total.
    if not state:
        mem['recruit_roster'] = {'chapter': mem.get('chapter'), 'month': mem.get('month'),
                                 'tick': int(mem.get('tick') or 0), 'names': list(names),
                                 'complete': False}


def complete(mem, state):
    receipt = fresh(mem)
    # Measured roster: Down on the final row never wraps the cursor
    # (tests/test_hanjuku_recruit_roster.py::_month_roster), so the wrap is
    # not the only proof of a full walk. Reading every listed name on a page
    # that ends on screen proves there is no further entry; a page still
    # filling the last row keeps the conservative wrap proof only.
    whole_page = receipt is not None and len(receipt['names']) < VISIBLE_NAME_ROWS
    if (receipt and not state.get('roster_invalidated')
            and (state.get('roster_wrapped') or whole_page)
            and receipt['tick'] == state.get('roster_started')
            and set(receipt['names']) == set(state.get('seen') or ())):
        receipt['complete'] = True
        # Prior-stage employed names may still be waiting for arrival. The
        # active list cannot prove they stopped receiving wages. Resolve only
        # by observing every retained name in this full global scan.
        pending = mem.get('recruit_payroll_pending') or []
        if pending and set(pending) <= set(receipt['names']):
            mem.pop('recruit_payroll_pending', None)
        mem['recruit_roster_recheck'] = False
        mem['roster_survey_scope'] = [receipt.get('chapter'), receipt.get('month')]
        return True
    mem['recruit_roster_recheck'] = True
    return False


def wage(mem, info):
    receipt = fresh(mem)
    state = mem.get('house') or {}
    value = info.get('wage')
    if (receipt and state.get('phase') == 'status' and not state.get('roster_invalidated')
            and state.get('roster_started') == receipt['tick']
            and info.get('general') == state.get('selected')
            and type(value) is int and 0 <= value <= MAX_RECRUIT_WAGE):
        receipt.setdefault('wages', {})[info['general']] = value


def economics(mem, owned):
    """Fresh complete actual wages and current-owned castle income only."""
    receipt = fresh(mem)
    if (not receipt or receipt.get('complete') is not True
            or mem.get('recruit_payroll_pending')):
        return None
    wages = receipt.get('wages')
    if (not isinstance(wages, dict) or set(wages) != set(receipt['names'])
            or any(type(v) is not int or not 0 <= v <= MAX_RECRUIT_WAGE for v in wages.values())):
        return None
    incomes = mem.get('castle_income') or {}
    observed = []
    for castle in owned:
        row = incomes.get(castle)
        if (not isinstance(row, dict) or row.get('chapter') != mem.get('chapter')
                or type(row.get('tick')) is not int or not 0 <= row['tick'] <= mem['tick']
                or type(row.get('income')) is not int or not 0 <= row['income'] <= 999):
            return None
        recent = row.get('month') == mem.get('month') and mem['tick'] - row['tick'] < FRESH_TICKS
        expected = NORMAL_CASTLE_INCOME.get(mem.get('chapter'), {}).get(castle)
        property_read = (expected is not None and row['income'] == expected
                         and owned_fresh(mem, castle))
        if not recent and not property_read:
            return None
        observed.append(row['income'])
    total = sum(observed)
    return {'income': total, 'wages': sum(wages.values()), 'additional_wage_max': MAX_RECRUIT_WAGE}


def owner(mem, castle, kind):
    if kind in ('own', 'enemy'):
        mem.setdefault('castle_ownership', {})[castle] = {
            'chapter': mem.get('chapter'), 'tick': mem.get('tick'), 'owner': kind}


def owned_fresh(mem, castle):
    proof = (mem.get('castle_ownership') or {}).get(castle) or {}
    return (proof.get('chapter') == mem.get('chapter') and proof.get('owner') == 'own'
            and type(proof.get('tick')) is int
            and 0 <= mem['tick'] - proof['tick'] < FRESH_TICKS)
