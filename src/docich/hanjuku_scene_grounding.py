"""Bind spoken quantities and battle outcomes to their observed subjects.

Free wording is allowed, but a number borrowed from another general or metric
is not evidence. Ambiguous quantitative clauses are silent rather than guessed.
"""
from __future__ import annotations

import re
import unicodedata


def _mentions(text, names, *, subject=False):
    found = []
    for name in names:
        suffix = r'(?:が|は)' if subject else ''
        found += [(m.start(), m.end(), name) for m in re.finditer(re.escape(name) + suffix, text)]
    return sorted(found)


def _outcomes_match(text, facts):
    results = [fact for fact in facts if fact['kind'] == 'battle_result']
    names = {fact[key] for fact in results for key in ('ally', 'enemy')}
    # These formulations can mean death, planned damage or a result. Do not
    # promote them to an observed win merely because an HP value is known.
    if re.search(r'倒し(?:た|ました)|撃破(?:した|しました)|打ち負か|退け(?:た|ました)', text):
        return False
    verbs = re.compile(r'勝|制し|撃退(?:した|しました)|負け|敗[れ北戦]')
    for match in verbs.finditer(text):
        if not results:
            return False
        before = re.split(r'[。！？]', text[:match.start()])[-1]
        subjects = _mentions(before, names, subject=True)
        # Xの攻撃 is an object/modifier, not the subject of a following loss.
        # Only the direct nominal predicate Xの勝利/敗北 binds X via の.
        direct = [name for name in names if before.endswith(name + 'の')]
        if direct:
            subject = max(direct, key=len)
            subjects = [(len(before) - len(subject) - 1, len(before), subject)]
        if not subjects:
            return False
        subject = subjects[-1][2]
        win = not re.match(r'負け|敗', match.group())
        candidates = [fact for fact in results
                      if subject == fact['ally' if (fact['outcome'] == 'win') == win else 'enemy']]
        if not candidates:
            return False
        opponents = [(m.start(), name) for name in names
                     for m in re.finditer(re.escape(name) + 'に', before[subjects[-1][1]:])]
        if opponents and all(max(opponents)[1] != fact['enemy' if subject == fact['ally'] else 'ally']
                             for fact in candidates):
            return False
        after = re.split(r'[、,。！？]', text[match.start():])[0]
        # Accept a positive result formulation, not arbitrary conjugations of
        # 勝. A win fact cannot license "勝てません" or "勝たなかった".
        if (not re.match(r'勝(?:ち|った|利|てた)|制し|撃退(?:した|しました)|負け|敗[れ北戦]', after)
                or re.search(r'ない|なかった|ません|ず|ぬ|なさそう', after)):
            return False
    return True


def _number_matches(text, match, facts):
    value = int(match.group())
    before = re.split(r'[、,。！？]', text[:match.start()])[-1]
    sentence = re.split(r'[。！？]', text[:match.start()])[-1]
    moments = re.findall(r'戦闘開始時|戦闘終了時|開戦時|終了時', sentence)
    moment = moments[-1] if moments else None
    after = text[match.end():]
    all_names = {fact[key] for fact in facts for key in ('ally', 'enemy', 'castle', 'card') if key in fact}
    names = _mentions(before, all_names)
    owner = names[-1][2] if names else None
    tail = before[names[-1][1]:] if names else before

    for fact in facts:
        kind = fact['kind']
        if kind in {'battle_start', 'battle_result'}:
            allowed_moments = {'開戦時', '戦闘開始時'} if kind == 'battle_start' else {'終了時', '戦闘終了時'}
            if (owner in {fact['ally'], fact['enemy']} and moment in allowed_moments
                    and re.search(r'HP|体力', before)
                    and re.fullmatch(r'[のはが：:=\s]*(?:(?:開戦時|戦闘開始時|終了時|戦闘終了時)[のはが\s]*)?'
                                     r'(?:HP|体力)[のはが：:=\s]*', tail)):
                key = 'ally_hp' if owner == fact['ally'] else 'enemy_hp'
                if fact.get(key) == value:
                    return True
        elif kind == 'soldier_refill_receipt':
            if (re.fullmatch(r'(?:(?:自軍|味方|こちら)の)?(?:補充後の)?'
                             r'(?:総兵士数|兵士総数|総数|合計|兵士数)[はが：:=\s]*', before)
                    and fact['soldiers_after'] == value):
                return True
            if (re.fullmatch(r'(?:(?:自軍|味方|こちら)の)?兵士[をはが\s]*', before)
                    and re.match(r'人(?:を)?(?:補充|購入)', after)
                    and fact['qty'] == value):
                return True
        elif kind == 'egg_priority_replan':
            if (re.fullmatch(r'(?:(?:自軍|味方|こちら)の)?兵士(?:数)?[はが：:=\s]*', before)
                    and fact['soldiers'] == value):
                return True
        elif kind == 'chikujou':
            if (owner == fact['castle'] and re.search(r'(?:築城費|費用|支払額)[はが：:=\s]*$', tail)
                    and fact['cost'] == value):
                return True
        elif kind == 'card_decision':
            if owner != fact['card']:
                continue
            labels = {
                'raw_damage_min': r'吸収前の(?:ダメージ|損害)(?:下限)?',
                'enemy_soldier_hp_upper': r'敵兵士の(?:HP|吸収)(?:上限)?',
                'damage_lower_bound': r'(?:ダメージ|損害)下限',
                'remaining_hp_upper': r'残りHP上限',
            }
            labelled = [(m.end() - m.start(), key) for key, pattern in labels.items()
                        for m in re.finditer(pattern + r'(?:は|が|の|計算上|見積もりで|：|:|=|\s)*$', tail)]
            if labelled and fact.get(max(labelled)[1]) == value:
                return True
    return False


def _castle_results_match(text, facts):
    castles = {fact['castle'] for fact in facts if 'castle' in fact}
    for match in re.finditer(r'占領|奪取|奪還|制圧|奪われ|失っ|失い|陥落|落城', text):
        kind = ('castle_lost_observed' if re.match(r'奪われ|失|陥落|落城', match.group())
                else 'castle_owned_observed')
        before = re.split(r'[、,。！？]', text[:match.start()])[-1]
        names = _mentions(before, castles)
        if not names or not any(fact['kind'] == kind and fact.get('castle') == names[-1][2] for fact in facts):
            return False
        after = re.split(r'[、,。！？]', text[match.start():])[0]
        if re.search(r'ない|なかった|ません|ず|ぬ', after):
            return False
    return True


def claims_match(text, facts):
    text = unicodedata.normalize('NFKC', text)
    if (re.search(r'[-−+＋][0-9]|[0-9]\.[0-9]', text)
            or not _outcomes_match(text, facts) or not _castle_results_match(text, facts)):
        return False
    kinds = {fact['kind'] for fact in facts}
    if (re.search(r'回復(?:しました|した|完了|済み)|回復させ', text)
            or 'soldier_refill_receipt' not in kinds
            and re.search(r'補充(?:した|しました|済み|完了)|購入(?:した|しました)', text)
            or 'chikujou' not in kinds
            and re.search(r'(?:築城|増築)(?:した|しました|済み|完了)', text)):
        return False
    return all(_number_matches(text, match, facts) for match in re.finditer(r'[0-9]+', text))
