"""Fresh, bounded roster receipts. No historical egg cache or paid head counts."""
from __future__ import annotations

from copy import deepcopy
import os
import re

from ..identity import run_identity
from ..state.regions import world as _world

MAX_GENERALS = 32
# hanjuku_house._names_at reads the name column at y=39..151 in steps of 16,
# so the roster shows this many names at once. A page shorter than this ends
# on screen and cannot hide a further entry.
VISIBLE_NAME_ROWS = 8
# The frozen observation card keeps showing the last complete roster
# (labelled as the previous month) until this month's complete walk.
LAST_COMPLETE_KEY = 'roster_last_complete'
# Confirmed 加入 announcements since that complete list, so the status
# card can show 名簿人数＋加入人数 instead of dropping to 不明.
PENDING_JOINS_KEY = 'roster_pending_joins'
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


def country_count(mem, kind):
    """A national-status count from this run/chapter/month, else unknown."""
    key, maximum = {'generals': ('country_general_count', MAX_GENERALS),
                    'soldiers': ('country_soldier_count', 99999)}[kind]
    row = mem.get(key)
    now = mem.get('tick')
    if (not isinstance(row, dict)
            or row.get('identity') != run_identity(mem.get('_run_identity'))
            or not isinstance(row.get('scope'), list) or len(row['scope']) != 2
            or not _same_scope(*row['scope'], mem)
            or type(row.get('tick')) is not int or type(now) is not int
            or not 0 <= row['tick'] <= now
            or type(row.get('count')) is not int or not 0 <= row['count'] <= maximum):
        return None
    return row['count']


def roles(raw):
    if not isinstance(raw, dict) or set(raw) != {'per_castle', 'attack'}:
        raise ValueError('invalid recruitment roles')
    if (type(raw['per_castle']) is not int or not 1 <= raw['per_castle'] <= 4
            or type(raw['attack']) is not int or not 1 <= raw['attack'] <= 8):
        raise ValueError('invalid recruitment roles')
    return dict(raw)


def _remember_complete(mem, receipt):
    """Keep a bounded copy of the last complete roster for the status card.

    The card must not fall back to 不明 when a partial page replaces
    ``recruit_roster`` or the month rolls over. Membership-changing events
    still call ``invalidate()``, which drops this copy with the receipts.
    """
    identity = run_identity(mem.get('_run_identity'))
    if not isinstance(receipt, dict) or receipt.get('complete') is not True:
        return
    chapter, month = receipt.get('chapter'), receipt.get('month')
    names, tick = receipt.get('names'), receipt.get('tick')
    if (identity is None or type(chapter) is not int or chapter <= 0
            or not isinstance(month, str)
            or re.fullmatch(r'[1-9][0-9]*-(?:[1-9]|1[0-2])', month) is None
            or not isinstance(names, list) or not 1 <= len(names) <= MAX_GENERALS
            or any(not isinstance(name, str) or not 1 <= len(name) <= 32 for name in names)
            or len(set(names)) != len(names) or type(tick) is not int):
        return
    mem[LAST_COMPLETE_KEY] = {'schema': 1, 'identity': dict(identity),
                             'chapter': chapter, 'month': month, 'tick': tick,
                             'complete': True, 'names': list(names)}
    mem.pop(PENDING_JOINS_KEY, None)  # this list already contains the joins


def _persistent_status(mem):
    """Verified per-general readings survive month boundaries, never game changes."""
    row = mem.get('general_statuses')
    if (not isinstance(row, dict) or row.get('complete') is not True
            or row.get('identity') != run_identity(mem.get('_run_identity'))
            or not isinstance(row.get('scope'), list) or len(row['scope']) != 2
            or row['scope'][0] != mem.get('chapter')
            or not isinstance(row.get('statuses'), dict)
            or not isinstance(row.get('dirty', {}), dict)):
        return None
    return row


def _dirty_persistent(mem, names=None, fields=('hp', 'location', 'egg')):
    cache = _persistent_status(mem)
    if cache is None:
        return
    chosen = list(cache['statuses']) if names is None else names
    for name in chosen:
        if name in cache['statuses']:
            stale = set(cache.setdefault('dirty', {}).get(name) or ())
            cache['dirty'][name] = sorted(stale | set(fields))


def record_join(mem, name):
    """A confirmed 加入告知 adds one general until the next complete list.

    Payment or a return never proves an extra general (that is why a recruit
    invalidates the receipts), but the game announces the joined name
    explicitly. The status card adds these names to the last complete list
    and keeps showing a count. ``invalidate()`` keeps both, a new complete
    list clears them, and a confirmed departure removes the name from the
    list it belongs to (see ``record_departure``).
    """
    record = mem.get(LAST_COMPLETE_KEY)
    if isinstance(name, str) and 1 <= len(name) <= 32:
        cache = _persistent_status(mem)
        if cache is not None and name not in cache['statuses']:
            joins = cache.setdefault('pending_joins', [])
            if name not in joins:
                joins.append(name)
    if (not isinstance(name, str) or not 1 <= len(name) <= 32
            or not isinstance(record, dict) or not isinstance(record.get('names'), list)):
        return False
    if name in record['names']:
        return False
    pending = [n for n in mem.get(PENDING_JOINS_KEY) or []
               if isinstance(n, str) and 1 <= len(n) <= 32]
    if name in pending:
        return False
    mem[PENDING_JOINS_KEY] = (pending + [name])[:MAX_GENERALS]
    return True


def record_departure(mem, name):
    """A confirmed departure subtracts one general from the kept count.

    Only a full list that no longer holds a defeated general confirms this
    (``forget_defeated``). Merely losing a battle is not proof of death, so
    ``defeated`` keeps the count.
    """
    if not isinstance(name, str):
        return False
    record = mem.get(LAST_COMPLETE_KEY)
    if isinstance(record, dict) and isinstance(record.get('names'), list) and name in record['names']:
        record['names'] = [n for n in record['names'] if n != name]
        return True
    pending = [n for n in mem.get(PENDING_JOINS_KEY) or [] if isinstance(n, str)]
    if name in pending:
        mem[PENDING_JOINS_KEY] = [n for n in pending if n != name]
        return True
    return False


def _purge_verified_departure(mem, name):
    """A confirmed absent roster member must not remain actionable anywhere."""
    cache = _persistent_status(mem)
    if cache is not None:
        cache['statuses'].pop(name, None)
        cache.setdefault('dirty', {}).pop(name, None)
        for key in ('pending_joins', 'suspected_departures'):
            if isinstance(cache.get(key), list):
                cache[key] = [g for g in cache[key] if g != name]
    for residents in (_world(mem).get('garrison') or {}).values():
        if isinstance(residents, list) and name in residents:
            residents.remove(name)
    for key in ('house_eggs', 'egg_types', 'egg_uses'):
        if isinstance(mem.get(key), dict):
            mem[key].pop(name, None)
    for key in ('egg_recheck', PENDING_JOINS_KEY):
        if isinstance(mem.get(key), list):
            mem[key] = [g for g in mem[key] if g != name]
    # general_location_unknown lives in the world region (#3193 W3); a flat
    # mem access would always miss the migrated copy (#3923).
    unknown_list = _world(mem).get('general_location_unknown')
    if isinstance(unknown_list, list):
        _world(mem)['general_location_unknown'] = [g for g in unknown_list if g != name]


def invalidate(mem):
    mem.pop('roster_carry_source', None)  # only observed_member() re-stashes it
    mem.pop('recruit_roster', None)
    mem.pop('roster_survey_scope', None)
    mem.pop('roster_survey', None)
    mem.pop('roster_status_walk', None)
    # Keep LAST_COMPLETE_KEY/PENDING_JOINS_KEY: the status card shows the
    # last complete list plus confirmed joins until a new complete list.
    mem.pop('house_status_recheck', None)
    mem['recruit_roster_recheck'] = True
    if mem.get('house'):
        mem['house']['roster_invalidated'] = True


def defeated(mem, name):
    """HP 0 is a question about one general, not about the other seventeen.

    g1097 2026-11-02: all four defeats removed exactly that general from the
    next walk, and each cost a second full status walk (17-19 reads, two
    minutes) in the same month. Keep the survey and let the partial walk look
    for the name. Without a complete survey there is nothing worth keeping.
    """
    dirty_status(mem, [name])
    cache = _persistent_status(mem)
    if cache is not None and name in cache['statuses']:
        lost = cache.setdefault('suspected_departures', [])
        if name not in lost:
            lost.append(name)
    survey = completed_survey(mem)
    if survey is None or name not in survey['statuses']:
        invalidate(mem)
        return
    survey['defeated'] = sorted({*(survey.get('defeated') or ()), name})


def forget_defeated(mem, name):
    """The whole list was read and a defeated general is not on it."""
    survey = completed_survey(mem)
    if survey is None or name not in (survey.get('defeated') or ()):
        return False
    for receipt in (survey['receipt'], mem.get('recruit_roster')):
        if not isinstance(receipt, dict):
            continue
        if isinstance(receipt.get('names'), list) and name in receipt['names']:
            receipt['names'].remove(name)
        if isinstance(receipt.get('wages'), dict):
            receipt['wages'].pop(name, None)
    survey['statuses'].pop(name, None)
    for key in ('dirty', 'dirty_revisions', 'read_revisions'):
        survey.get(key, {}).pop(name, None)
    survey['defeated'].remove(name)
    record_departure(mem, name)  # subtract; a confirmed departure is not 不明
    _purge_verified_departure(mem, name)
    unknown = _world(mem).get('general_location_unknown')
    if isinstance(unknown, list) and name in unknown:
        unknown.remove(name)
    _remember_complete(mem, survey['receipt'])  # the fresh list no longer holds them
    mem['house_status_recheck'] = bool(survey.get('dirty'))
    return True


def _payroll_hold_wages(mem):
    hold = mem.get('payroll_membership_hold')
    identity = run_identity(mem.get('_run_identity'))
    if (identity is None or not isinstance(hold, dict) or hold.get('identity') != identity
            or type(hold.get('schema')) is not int or hold['schema'] != 1
            or hold.get('scope') != [mem.get('chapter'), mem.get('month')]):
        return None
    wages = hold.get('wages')
    if (not isinstance(wages, dict) or not 1 <= len(wages) <= MAX_GENERALS
            or any(not isinstance(n, str) or not 1 <= len(n) <= 32 for n in wages)
            or any(type(v) is not int or not 0 <= v <= MAX_RECRUIT_WAGE for v in wages.values())
            or type(hold.get('saturated')) is not bool):
        return None
    return wages


def payroll_hold(mem):
    """A retained bill is a spending floor, never a current roster receipt."""
    wages = _payroll_hold_wages(mem)
    if wages is None:
        return 0
    return (MAX_GENERALS * MAX_RECRUIT_WAGE
            if mem['payroll_membership_hold']['saturated'] else sum(wages.values()))


def observed_member(mem, names):
    """New actual members invalidate membership without forgetting known wages."""
    if (not names or len(names) > MAX_GENERALS or len(set(names)) != len(names)
            or any(not isinstance(n, str) or not 1 <= len(n) <= 32 for n in names)):
        return
    # Count newly observed members independently of payroll/status reuse.
    # After a month change the retained complete list can still drive the
    # status card even when no current survey or retained bill exists.
    for name in names:
        record_join(mem, name)
    survey = completed_survey(mem)
    held = _payroll_hold_wages(mem)
    if survey is None and held is None:
        return
    if survey is not None and set(names) <= set(survey['receipt']['names']):
        return
    if survey is None and held is not None and set(names) <= set(held):
        # Issue #3194: these names are already on the retained bill. Re-
        # invalidating on every page made every later names-only count in the
        # month incomplete (g1097 18:35: roster_complete false), so the month
        # could never be carried and the full status walk ran again.
        return
    source = deepcopy(survey) if survey is not None and month_carry_enabled() else None
    wages = dict(held or {})
    if survey is not None:
        receipt = survey['receipt']
        actual = receipt.get('wages')
        if (isinstance(actual, dict) and set(actual) == set(receipt['names'])
                and all(type(v) is int and 0 <= v <= MAX_RECRUIT_WAGE for v in actual.values())):
            wages.update(actual)
    saturated = bool(held and mem['payroll_membership_hold']['saturated'])
    for name in names:
        if name in wages:
            continue
        if len(wages) == MAX_GENERALS:
            saturated = True
            continue
        value = fixed_wage(name)
        wages[name] = MAX_RECRUIT_WAGE if value is None else value
    mem['payroll_membership_hold'] = {
        'schema': 1, 'identity': run_identity(mem.get('_run_identity')),
        'scope': [mem.get('chapter'), mem.get('month')],
        'wages': wages, 'saturated': saturated}
    invalidate(mem)
    if source is not None:
        # A join adds a general; it does not change what was read about the
        # others. The rows wait here for this month's next complete names-only
        # count (carry_forward) instead of costing a second full status walk.
        mem['roster_carry_source'] = source


def surveyed(mem):
    """The known current roster's statuses were walked; total may be unknown."""
    if run_identity(mem.get('_run_identity')) is not None:
        return reusable_survey(mem) is not None
    if (mem.get('survey_run_identity') is not None or mem.get('roster_survey') is not None
            or mem.get('roster_status_walk') is not None):
        return False
    scope = mem.get('roster_survey_scope')
    return (mem.get('month') is not None and scope == [mem.get('chapter'), mem.get('month')])


def begin(mem, state):
    for key in ('roster_end_observed', 'roster_end_observations', 'roster_first_general'):
        state.pop(key, None)
    state['roster_started'] = int(mem.get('tick') or 0)
    state['roster_month'] = mem.get('month')
    mem['recruit_roster'] = {'chapter': mem.get('chapter'), 'month': mem.get('month'),
                             'tick': state['roster_started'], 'names': [], 'wages': {}, 'complete': False,
                             'identity': run_identity(mem.get('_run_identity'))}


def _same_scope(chapter, month, mem):
    return (type(chapter) is int and type(mem.get('chapter')) is int
            and chapter > 0 and chapter == mem['chapter']
            and type(month) is str and type(mem.get('month')) is str
            and re.fullmatch(r'[1-9][0-9]*-(?:[1-9]|1[0-2])', month) is not None
            and month == mem['month'])


def completed_survey(mem):
    """Only a complete named-status walk, in the current observation's run."""
    return _status_survey(mem, 'roster_survey', complete=True)


def reusable_survey(mem):
    """Reuse actual individual readings without claiming global membership."""
    return (completed_survey(mem)
            or _status_survey(mem, 'roster_status_walk', complete=False))


def _status_survey(mem, key, *, complete):
    identity = run_identity(mem.get('_run_identity'))
    survey = mem.get(key)
    if (identity is None or not isinstance(survey, dict) or type(survey.get('schema')) is not int
            or survey['schema'] != 1
            or survey.get('identity') != identity
            or not isinstance(survey.get('scope'), list) or len(survey['scope']) != 2
            or not _same_scope(*survey['scope'], mem)
            or not isinstance(mem.get('month'), str) or not mem['month']):
        return None
    receipt = survey.get('receipt')
    statuses, dirty = survey.get('statuses'), survey.get('dirty')
    if (not isinstance(receipt, dict) or not isinstance(statuses, dict)
            or not isinstance(dirty, dict)):
        return None
    names = receipt.get('names')
    if (receipt.get('complete') is not complete or not isinstance(names, list)
            or not 1 <= len(names) <= MAX_GENERALS
            or any(not isinstance(n, str) or not 1 <= len(n) <= 32 or '\ufffd' in n for n in names)
            or len(set(names)) != len(names) or set(statuses) != set(names)
            or receipt.get('identity') != identity
            or not _same_scope(receipt.get('chapter'), receipt.get('month'), mem)
            or type(receipt.get('tick')) is not int or type(mem.get('tick')) is not int
            or not 0 <= receipt['tick'] <= mem['tick']
            or not set(dirty) <= set(names)
            or any(not isinstance(fields, list) or any(not isinstance(f, str) for f in fields)
                   or not set(fields) <= {'hp', 'location', 'egg'}
                   for fields in dirty.values())
            or any(not isinstance(row, dict) or row.get('general') != name
                   or row.get('month') != mem.get('month')
                   or not _same_scope(row.get('observed_chapter'), row.get('month'), mem)
                   or type(row.get('observed_tick')) is not int
                   or not receipt['tick'] <= row['observed_tick'] <= mem['tick']
                   or type(row.get('broken')) is not bool for name, row in statuses.items())):
        return None
    for key in ('dirty_revisions', 'read_revisions'):
        revisions = survey.get(key, {})
        if (not isinstance(revisions, dict) or not set(revisions) <= set(names)
                or any(type(v) is not int or not 0 <= v <= 10**6 for v in revisions.values())):
            return None
    return survey


# Issue #3194: unchanged readings may be carried indefinitely. Month changes
# alone are not evidence that a general's HP, location or egg has changed.
# A detected event marks the affected fields dirty; run/chapter identity still
# guards against reusing readings from another playthrough.
MONTH_CARRY_ENV = 'DOCICH_HANJUKU_MONTH_CARRY'


def month_carry_enabled():
    return os.environ.get(MONTH_CARRY_ENV) != '0'


def _previous_month(month):
    m = re.fullmatch(r'([1-9][0-9]*)-([1-9]|1[0-2])', month) if isinstance(month, str) else None
    if m is None:
        return None
    year, mon = int(m.group(1)), int(m.group(2))
    return f'{year}-{mon - 1}' if mon > 1 else (f'{year - 1}-12' if year > 1 else None)


def _carry_eligible(row, dirty_fields):
    """Any verified, unchanged resident or carry waiter is reusable."""
    if not isinstance(row, dict) or dirty_fields:
        return False
    hp, maximum = row.get('hp'), row.get('max_hp')
    if row.get('location') == 'castle':
        return (row.get('location_observed') is True
                and type(row.get('broken')) is bool
                and type(hp) is int and type(maximum) is int
                and maximum > 0 and 0 < hp <= maximum)
    # A chapter carry-over waiter is a known queue state, not an unread field
    # location.  The hero-castle manifest refreshes this row each month, so a
    # clean waiting row can cross the month boundary without another status A.
    return (row.get('location') in ('field', 'carry')
            and row.get('carry_waiting') is True
            and row.get('location_observed') is True
            and type(row.get('broken')) is bool
            and type(hp) is int and type(maximum) is int
            and maximum > 0 and 0 < hp <= maximum)


def carry_forward(mem):
    """Start this month's survey from last month's rows, reopening only the rest.

    The roster names come from this month's complete names-only count, so
    membership is always re-read. A previous-month row is reused only if the
    general was a verified castle resident (even wounded or with a broken egg)
    or a known chapter carry-over waiter, and no battle/sortie/recall/egg event
    marked it dirty since.
    Every other general gets a placeholder row that is dirty in all fields, so
    the ordinary partial walk opens exactly those. Returns ``None`` when
    nothing was done, else ``{'carried': [...], 'reopen': [...]}``.
    """
    if not month_carry_enabled() or reusable_survey(mem) is not None:
        return None
    identity = run_identity(mem.get('_run_identity'))
    chapter, month = mem.get('chapter'), mem.get('month')
    previous = _previous_month(month)
    prev = mem.get('roster_survey')
    source = mem.get('roster_carry_source')
    if isinstance(source, dict) and source.get('scope') not in ([chapter, previous], [chapter, month]):
        mem.pop('roster_carry_source', None)  # an older month's rows are never reused
        source = None
    if (not isinstance(prev, dict) or prev.get('scope') != [chapter, previous]) and source is not None:
        prev = source  # a join invalidated the survey after it was read/carried
    same_month = prev is source and isinstance(prev, dict) and prev.get('scope') == [chapter, month]
    receipt = fresh(mem, include_completed=False)
    if (identity is None or previous is None or not isinstance(prev, dict)
            or prev.get('schema') != 1 or prev.get('identity') != identity
            or (prev.get('scope') != [chapter, previous] and not same_month)
            or not isinstance(prev.get('statuses'), dict) or not isinstance(prev.get('dirty'), dict)
            or receipt is None or receipt.get('complete') is not True
            or not isinstance(receipt.get('wages'), dict)):
        return None
    rtick = receipt['tick']
    statuses, dirty, carried, reopen = {}, {}, [], []
    carried_rows = {}
    for name in receipt['names']:
        row = prev['statuses'].get(name)
        if _carry_eligible(row, prev['dirty'].get(name)):
            new = deepcopy(row)
            new.update(month=month, observed_chapter=chapter, observed_tick=rtick)
            if not same_month:  # a same-month row was read (or carried) this month already
                new.update(carried_from=previous, carry_depth=row.get('carry_depth', 0) + 1)
            statuses[name] = carried_rows[name] = new
            carried.append(name)
        else:
            statuses[name] = {'general': name, 'month': month, 'observed_chapter': chapter,
                              'observed_tick': rtick, 'broken': False, 'hp': None, 'max_hp': None,
                              'location': None, 'location_observed': False, 'egg': None,
                              'uses': None, 'wage': receipt['wages'].get(name)}
            dirty[name] = ['hp', 'location', 'egg']
            reopen.append(name)
    saved = {k: deepcopy(mem.get(k)) for k in ('roster_survey', 'roster_status_walk', 'house_eggs')}
    mem['roster_survey'] = {'schema': 1, 'identity': identity, 'scope': [chapter, month],
                            'receipt': deepcopy(receipt), 'statuses': statuses, 'dirty': dirty,
                            'dirty_revisions': {n: 1 for n in dirty}, 'read_revisions': {}}
    mem.pop('roster_status_walk', None)
    if reusable_survey(mem) is None:  # any invalid shape: leave everything as it was
        for k, v in saved.items():
            if v is None:
                mem.pop(k, None)
            else:
                mem[k] = v
        return None
    eggs = mem.setdefault('house_eggs', {})
    for name, row in carried_rows.items():
        eggs[name] = deepcopy(row)
    mem['house_status_recheck'] = bool(dirty)
    mem.pop('roster_carry_source', None)
    return {'carried': carried, 'reopen': reopen}


def dirty_status(mem, names=None, fields=('hp', 'location', 'egg')):
    """A change invalidates only the participant's affected status fields."""
    _dirty_persistent(mem, names, fields)
    survey = reusable_survey(mem)
    if survey is None:
        source = mem.get('roster_carry_source')  # waiting for the next names-only count
        if isinstance(source, dict) and isinstance(source.get('statuses'), dict):
            for name in (list(source['statuses']) if names is None else names):
                if name in source['statuses']:
                    dirty = set(source.setdefault('dirty', {}).get(name) or [])
                    source['dirty'][name] = sorted(dirty | set(fields))
        return
    selected = survey['receipt']['names'] if names is None else names
    for name in selected:
        if name in survey['statuses']:
            dirty = set(survey.setdefault('dirty', {}).get(name) or [])
            survey['dirty'][name] = sorted(dirty | set(fields))
            revisions = survey.setdefault('dirty_revisions', {})
            revisions[name] = revisions.get(name, 0) + 1
    mem['house_status_recheck'] = bool(survey.get('dirty'))


def _update_persistent_observation(mem, info):
    """Merge direct HP/egg evidence without claiming an unobserved castle."""
    cache = _persistent_status(mem)
    name = info.get('general')
    if cache is None or name not in cache['statuses']:
        return
    row = cache['statuses'][name]
    stale = set(cache.setdefault('dirty', {}).get(name) or ())
    if type(info.get('broken')) is bool:
        row.update(broken=info['broken'], egg=info.get('egg'), uses=info.get('uses'))
        stale.discard('egg')
    hp, maximum = info.get('hp'), info.get('max_hp')
    if (type(hp) is int and type(maximum) is int
            and maximum > 0 and 0 <= hp <= maximum):
        row.update(hp=hp, max_hp=maximum)
        stale.discard('hp')
    # The generic status reader labels camp/march as 'field', so it cannot
    # resolve either kind of movement. Only a separately named castle proof
    # can clear a stale location; do not reuse an old castle after departure.
    if (info.get('location') == 'castle' and info.get('location_observed') is True
            and info.get('castle_observed') is True and info.get('castle')):
        row.update(location='castle', castle=info['castle'])
        stale.discard('location')
    elif info.get('location') == 'field' and info.get('location_observed') is True:
        # The person is visibly outside a castle, but the generic reader
        # cannot differentiate camp from march. Keep the location unresolved.
        row.update(location='unknown')
        row.pop('castle', None)
        stale.add('location')
        for residents in (_world(mem).get('garrison') or {}).values():
            if isinstance(residents, list) and name in residents:
                residents.remove(name)
    if stale:
        cache['dirty'][name] = sorted(stale)
    else:
        cache['dirty'].pop(name, None)


def status_observed(mem, info):
    _update_persistent_observation(mem, info)
    survey = reusable_survey(mem)
    name = info.get('general')
    if survey is None or name not in survey['statuses']:
        return
    survey['statuses'][name] = deepcopy(info)
    if name in (survey.get('defeated') or ()):
        survey['defeated'].remove(name)  # still on the list after the defeat
    missing = [] if (type(info.get('hp')) is int and type(info.get('max_hp')) is int
                     and 0 <= info['hp'] <= info['max_hp'] and info['max_hp'] > 0) else ['hp']
    if (info.get('location_observed') is False
            or ('location' in survey.get('dirty', {}).get(name, [])
                and info.get('location_observed') is not True)):
        missing.append('location')
    if missing:
        survey.setdefault('dirty', {})[name] = missing
    else:
        survey.setdefault('dirty', {}).pop(name, None)
    survey.setdefault('read_revisions', {})[name] = survey.get('dirty_revisions', {}).get(name, 0)
    mem['house_status_recheck'] = bool(survey['dirty'])


def recheck_targets(mem):
    """One read per actual change; unknown fields stay unknown after a read."""
    survey = reusable_survey(mem)
    if survey is None:
        return []
    return [name for name in survey['dirty']
            if name not in survey.get('read_revisions', {})
            or survey.get('read_revisions', {}).get(name)
               != survey.get('dirty_revisions', {}).get(name, 0)]


def fresh(mem, *, include_completed=True):
    survey = completed_survey(mem) if include_completed else None
    if survey is not None and 'roster_started' not in (mem.get('house') or {}):
        return survey['receipt']  # names/fixed wages, never cached HP/soldiers
    receipt = mem.get('recruit_roster')
    if not isinstance(receipt, dict):
        return None
    names, tick, now = receipt.get('names'), receipt.get('tick'), mem.get('tick')
    identity = run_identity(mem.get('_run_identity'))
    if ((identity is not None or receipt.get('identity') is not None)
            and receipt.get('identity') != identity):
        return None
    if (not _same_scope(receipt.get('chapter'), receipt.get('month'), mem)
            or type(tick) is not int or type(now) is not int or not 0 <= now - tick < FRESH_TICKS
            or not isinstance(names, list) or not 1 <= len(names) <= MAX_GENERALS
            or any(not isinstance(n, str) or not n for n in names)
            or len(set(names)) != len(names)):
        return None
    return receipt


def page(mem, names):
    if (names is None or not names or len(set(names)) != len(names)
            or any(not isinstance(n, str) or fixed_wage(n) is None for n in names)):
        return
    observed_member(mem, names)
    survey = reusable_survey(mem)
    if survey is not None and not set(names) <= set(survey['receipt']['names']):
        invalidate(mem)  # a new actual name needs a new complete membership walk
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
                                 'complete': False, 'identity': run_identity(mem.get('_run_identity'))}


def complete(mem, state):
    receipt = fresh(mem, include_completed=False)
    # Measured roster: Down on the final row never wraps the cursor
    # (tests/test_hanjuku_recruit_roster.py::_month_roster), so the wrap is
    # not the only proof of a full walk. Reading every listed name on a page
    # that ends on screen proves there is no further entry; a page still
    # filling the last row needs a wrap or the native scroll-end proof.
    whole_page = receipt is not None and len(receipt['names']) < VISIBLE_NAME_ROWS
    full = (receipt and not state.get('roster_invalidated')
            and (state.get('roster_wrapped') or state.get('roster_end_observed') or whole_page)
            and receipt['tick'] == state.get('roster_started')
            and set(receipt['names']) == set(state.get('seen') or ()))
    if full:
        receipt['complete'] = True
        _remember_complete(mem, receipt)
        # Prior-stage employed names may still be waiting for arrival. The
        # active list cannot prove they stopped receiving wages. Resolve only
        # by observing every retained name in this full global scan.
        pending = mem.get('recruit_payroll_pending') or []
        if pending and set(pending) <= set(receipt['names']):
            mem.pop('recruit_payroll_pending', None)
        mem['recruit_roster_recheck'] = False
        mem['roster_survey_scope'] = [receipt.get('chapter'), receipt.get('month')]
    identity = run_identity(mem.get('_run_identity'))
    if (receipt and not state.get('roster_invalidated')
            and receipt['tick'] == state.get('roster_started')
            and set(receipt['names']) == set(state.get('seen') or ())):
        statuses = {name: (mem.get('house_eggs') or {}).get(name) for name in receipt['names']}
        if (identity is not None and all(isinstance(row, dict)
                and row.get('general') == name and row.get('month') == mem.get('month')
                and row.get('observed_chapter') == mem.get('chapter')
                and type(row.get('observed_tick')) is int
                and receipt['tick'] <= row['observed_tick'] <= int(mem.get('tick') or 0)
                for name, row in statuses.items())):
            key = 'roster_survey' if full else 'roster_status_walk'
            mem[key] = {'schema': 1, 'identity': identity,
                'scope': [mem.get('chapter'), mem.get('month')], 'receipt': deepcopy(receipt),
                'statuses': deepcopy(statuses), 'dirty': {}}
            if full:
                mem.pop('roster_status_walk', None)
                wages = receipt.get('wages')
                if (isinstance(wages, dict) and set(wages) == set(receipt['names'])
                        and all(type(v) is int and 0 <= v <= MAX_RECRUIT_WAGE
                                for v in wages.values())):
                    mem.pop('payroll_membership_hold', None)
            for info in statuses.values():
                status_observed(mem, info)
    if full:
        return True
    mem['recruit_roster_recheck'] = True
    return False


def wage(mem, info):
    receipt = fresh(mem, include_completed=False)
    state = mem.get('house') or {}
    value = info.get('wage')
    if (receipt and state.get('phase') == 'status' and not state.get('roster_invalidated')
            and state.get('roster_started') == receipt['tick']
            and info.get('general') == state.get('selected')
            and type(value) is int and 0 <= value <= MAX_RECRUIT_WAGE):
        receipt.setdefault('wages', {})[info['general']] = value


def economics(mem, owned, *, lower_bound=False):
    """Fresh complete actual wages and current-owned castle income only.

    ``lower_bound``: an owned castle without a valid reading is left out
    instead of voiding the whole result. Income is never negative, so the sum
    of the readings is a sound lower bound (g684 2-1..2-2: ゴーメン changed
    hands repeatedly, was never read, and every month's recruitment was held
    although the read castles alone covered the payroll). Wages stay exact.
    """
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
            if lower_bound:
                continue
            return None
        recent = row.get('month') == mem.get('month') and mem['tick'] - row['tick'] < FRESH_TICKS
        expected = NORMAL_CASTLE_INCOME.get(mem.get('chapter'), {}).get(castle)
        property_read = (expected is not None and row['income'] == expected
                         and owned_fresh(mem, castle))
        if not recent and not property_read:
            if lower_bound:
                continue
            return None
        observed.append(row['income'])
    total = sum(observed)
    result = {'income': total, 'wages': sum(wages.values()), 'additional_wage_max': MAX_RECRUIT_WAGE}
    if lower_bound:
        result['income_lower_bound'] = True
        result['castles_read'] = len(observed)
    return result


def owner(mem, castle, kind):
    if kind in ('own', 'enemy'):
        mem.setdefault('castle_ownership', {})[castle] = {
            'chapter': mem.get('chapter'), 'tick': mem.get('tick'), 'owner': kind}


def owned_fresh(mem, castle):
    proof = (mem.get('castle_ownership') or {}).get(castle) or {}
    return (proof.get('chapter') == mem.get('chapter') and proof.get('owner') == 'own'
            and type(proof.get('tick')) is int
            and 0 <= mem['tick'] - proof['tick'] < FRESH_TICKS)
