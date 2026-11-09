"""判断層: judgments for non-battle events (trade offers, monster skills).

Whether a traded general is weak enough to accept, and which monster skill
belongs to whom and helps or heals.
"""
from __future__ import annotations

from ... import hanjuku_chart as chart, hanjuku_reference as reference
from ..os_layer.readers import _fold_skill
from ..os_layer.world import NAME


TRADE_WEAK_MAX_HP = 50          # owner 2026-10-11: weak generals may be traded away
TRADE_WEAK_MAX_COMBAT = 10


def trade_accept_weak(mem, name):
    """(accept, evidence): only a clearly weak, non-chart, non-hero general.

    Owner (2026-10-11): 花いちもんめは強い将軍と弱い将軍が交換されがちなので、
    強い将軍が指定された時は断る / 弱い将軍なら受ける. Strength is the static
    gcgx shogun.html row (max HP, 戦闘). An unread or unknown name declines.
    """
    from ...hanjuku_reference import general_strength
    chart_generals = {o.get('general') for o in chart.all_orders(mem.get('chapter') or 0)}
    stats = general_strength(name)
    evidence = {'general': name, 'stats': list(stats[:2]) if stats else None,
                'max_hp_limit': TRADE_WEAK_MAX_HP, 'combat_limit': TRADE_WEAK_MAX_COMBAT}
    if not name or name == NAME or name in chart_generals or stats is None:
        evidence['rule'] = ('unread' if not name else 'hero' if name == NAME
                            else 'chart_general' if name in chart_generals else 'unknown')
        return False, evidence
    weak = stats[0] < TRADE_WEAK_MAX_HP and stats[1] < TRADE_WEAK_MAX_COMBAT
    evidence['rule'] = 'weak' if weak else 'strong'
    return weak, evidence
# Battle HP is represented on screen and throughout the policy as a four-digit
# value capped at 9999.  Balloon Finch's owner-directed tactic depends on the
# actual cap, not on the largest HP value observed so far in this battle.
BALLOON_FINCH_MAX_HP = 9999


_MONSTER_SKILLS = {name: frozenset(_fold_skill(s) for s in skills)
                   for name, skills in reference.MONSTER_SKILLS.items()}
# Exact observed display spellings (g747 native frames 8867/8868, g1153
# monster panels). Keep the reference name and unrelated monster policies
# unchanged; no fuzzy matching.
_MONSTER_DISPLAY_ALIASES = {
    'フリージボーイ': 'フリージーボーイ',
    'タマゴキャリー': 'たまごキャリー',
    'てつじん8ごう': 'てつじん８ごう',
}
_MONSTER_EFFECTS = frozenset(_fold_skill(s) for s in reference.MONSTER_EFFECT_SKILLS)
_MONSTER_HEALS = frozenset(_fold_skill(s) for s in reference.MONSTER_HEAL_SKILLS)
_MONSTER_INSTANT_DEATH = frozenset(_fold_skill(s)
                                   for s in reference.MONSTER_INSTANT_DEATH_SKILLS)


def _monster_owner(skill_lines, ally, enemy) -> str | None:
    """Which side the visible skill rows belong to, or None when ambiguous."""
    shown = {_fold_skill(''.join(line.known.split())) for line in skill_lines}
    ally_set = _MONSTER_SKILLS.get(
        _MONSTER_DISPLAY_ALIASES.get(ally.name, ally.name)) if ally else None
    enemy_set = _MONSTER_SKILLS.get(
        _MONSTER_DISPLAY_ALIASES.get(enemy.name, enemy.name)) if enemy else None
    hit_ally = bool(shown & ally_set) if ally_set else False
    hit_enemy = bool(shown & enemy_set) if enemy_set else False
    if hit_ally and not hit_enemy:
        return 'ally'
    if hit_enemy and not hit_ally:
        return 'enemy'
    return None


def _monster_menu_attackless(owner, ally, enemy, skills) -> bool:
    """Both visible skills are measured at zero damage against this opponent.

    A summon with two effect-only skills can spend every turn picking a
    command that cannot move the enemy panel: g1153 2026-11-08 17:45-17:47
    とうめいにんげん (イタズラがき / イナイイナイバア) faced a transformed
    ゼウス, kept it at 85 HP for seven own turns while its own HP fell
    44 -> 31 -> 21 -> 11 -> 1, and the summon was lost without damage.

    Only the transcribed 対モ/対将 table is accepted, and only for the exact
    two-skill display spelling of an owned menu. Unread labels, an unknown
    monster or a missing row keep the existing policy (fail closed).
    """
    if owner != 'ally' or ally is None or enemy is None or len(skills) != 2:
        return False
    attack_data = getattr(reference, 'MONSTER_ATTACK_DATA', None)
    if not isinstance(attack_data, dict):
        return False
    data = attack_data.get(ally.name) or attack_data.get(_monster_display_name(ally.name))
    if not (isinstance(data, dict) and 'skill1' in data and 'skill2' in data):
        return False
    rows = (data['skill1'], data['skill2'])
    if not all(isinstance(row, (tuple, list)) and len(row) >= 4 for row in rows):
        return False
    if [_fold_skill(s) for s in skills] != [_fold_skill(row[0]) for row in rows]:
        return False
    column = 1 if _is_monster_opponent(enemy) else 2
    return all(row[column] == 0 for row in rows)


def _monster_effectful(skill: str) -> bool:
    return _fold_skill(skill) in _MONSTER_EFFECTS


def _monster_heal(skill: str) -> bool:
    return _fold_skill(skill) in _MONSTER_HEALS


def _monster_damage_second(owner, ally, enemy, skills) -> bool:
    """Prefer an owned regular attack to instant death against a known monster.

    Preserve Excalibur's existing rule. Unicorn needs positive measured HP
    on both sides and its complete two-skill pair; unknown ownership or HP
    never enables the new rule. Retreat remains the caller's priority.
    """
    if (owner != 'ally' or ally is None or enemy is None
            or ally.name not in ('エクスカリバー', 'ユニコーン')
            or enemy.name not in _MONSTER_SKILLS):
        return False
    if ally.name == 'ユニコーン' and not (
            type(ally.hp) is int and ally.hp > 0
            and type(enemy.hp) is int and enemy.hp > 0):
        return False
    return (len(skills) == 2
            and [_fold_skill(s) for s in skills]
            == [_fold_skill(s) for s in reference.MONSTER_SKILLS[ally.name]])


def _monster_damage_first(owner, ally, enemy, skills) -> bool:
    """Naruto's owned complete menu attacks instead of repeating confusion.

    g749 Venus/Queen: repeated swirl selections left Hydra at 367 while
    Naruto fell 330 -> 146 -> 8. Require both current living monster panels;
    a general, unknown owner, partial menu or unread HP keeps existing policy.
    Returning to the egg remains the caller's decision.
    """
    return (owner == 'ally' and ally is not None and enemy is not None
            and ally.name == 'なると' and enemy.name in _MONSTER_SKILLS
            and type(ally.hp) is int and ally.hp > 0
            and type(enemy.hp) is int and enemy.hp > 0
            and len(skills) == 2
            and [_fold_skill(s) for s in skills]
            == [_fold_skill(s) for s in reference.MONSTER_SKILLS['なると']])


def _monster_chikyu_choice(owner, ally, enemy, skills):
    """ちきゅうちゃん's skill by opponent: 'skill2', 'skill1' or None (#3071).

    wikiwiki hjksfc 攻撃データ: a self-destruct wipes a general and his
    soldiers outright, hits a monster for the user's remaining HP and a boss
    for 1/8 of its max HP. おめでとり's あたためる (g1075 10-29 14:13 to
    10-30 06:05) won all six times against a lone general and left
    スプリミョーネ alive at 08:44. ハルマゲドン at HP 1 and 47 left
    ドラゴンフライ and ニンフ alive (10-30 08:08, 09:22) while だいじしん
    halves both sides, so a known general takes ハルマゲドン, a monster or
    boss だいじしん, and an unknown or unread panel keeps the old policy.
    """
    if not (owner == 'ally' and ally is not None and enemy is not None
            and ally.name == 'ちきゅうちゃん'
            and type(ally.hp) is int and ally.hp > 0
            and type(enemy.hp) is int and enemy.hp > 0
            and len(skills) == 2
            and [_fold_skill(s) for s in skills]
            == [_fold_skill(s) for s in reference.MONSTER_SKILLS['ちきゅうちゃん']]):
        return None
    if _is_monster_opponent(enemy):
        return 'skill1'
    if reference.general_strength(enemy.name) is not None:
        return 'skill2'
    return None


def _lampkin_menu(owner, ally, skills):
    """Our ランプキン with its owned two-skill menu (へんなおどり, かぼちゃ)."""
    return bool(owner == 'ally' and ally is not None and ally.name == 'ランプキン'
                and len(skills) == 2
                and [_fold_skill(s) for s in skills]
                == [_fold_skill(s) for s in reference.MONSTER_SKILLS['ランプキン']])


def _lampkin_forced_kind(enemy):
    """Why かぼちゃ is pointless this turn: 'boss' or 'hp1', else None.

    Owner 2026-11-02: ボスモンスターに「かぼちゃ」はきかないので「へんなおどり」
    一択。相手のHPが1なら、そのまま押し切れるので「へんなおどり」一択。

    Owner 2026-11-05: a boss castle's monster keeps changing form (第6話
   ハードマン→ハードロボ, 第12話 クーモン→スーモン→ボイルド), and the later
    forms are not the chapter boss name in ``chart.BOSSES``.  Missing them sent
    かぼちゃ to ハードロボ (live g1133, enemy HP 1127).  Use the reference
    tables' boss classification so every known boss monster counts.
    """
    if enemy is None:
        return None
    name = _monster_display_name(enemy.name)
    if name in chart.BOSSES.values() or reference.is_boss_target(name):
        return 'boss'
    if type(enemy.hp) is int and enemy.hp == 1:
        return 'hp1'
    return None


def _monster_lampkin_choice(owner, ally, enemy, skills):
    """ランプキン takes へんなおどり (skill1) when かぼちゃ is pointless.

    A chapter boss, a named boss general or boss monster, or an opponent left
    at 1 HP always takes skill1. Only an owned complete two-skill menu enables
    the override; any other turn follows the かぼちゃ cycle in ``battle_menus``.
    """
    if not _lampkin_menu(owner, ally, skills) or _lampkin_forced_kind(enemy) is None:
        return None
    return 'skill1'


def _monster_display_name(name):
    return _MONSTER_DISPLAY_ALIASES.get(name, name)


def _is_monster_opponent(enemy):
    """Egg monsters and chapter bosses both use the 対モ damage column."""
    if enemy is None:
        return False
    name = _monster_display_name(enemy.name)
    return (name in _MONSTER_SKILLS or name in reference.BOSS_GENERALS
            or name in chart.BOSSES.values())


def _is_self_damage_skill(skill):
    """Whether a skill damages the user itself.

    Primary source of truth is MONSTER_SELF_DAMAGE_SKILLS (wiki attack-data
    table rows explicitly marked 自爆 / 自分にNダメージ).  The text
    heuristics below are a conservative safety net for skills whose table
    row has not yet been transcribed or whose OCR spelling drifts; they only
    match obvious self-destruct wording and never turn a normal attack into
    self-damage.  Upstream label matching is fail-closed, so this only ever
    sees skill names actually read from the menu.
    """
    if not skill:
        return False
    if _fold_skill(skill) in {_fold_skill(s) for s in reference.MONSTER_SELF_DAMAGE_SKILLS}:
        return True
    text = skill
    return ('ばくはつ' in text or 'じばく' in text or '自爆' in text
            or '自滅' in text or '自己ダメージ' in text)


def _score_monster_attack(entry, opponent_kind, multiple):
    """Expected-damage rank of one menu row (wiki 対モ/対将威力 x 攻撃回数).

    Owner policy for issue #2835: a general battle prefers the skill with more
    attacks (and an ALL attack while enemy soldiers remain); a monster battle
    ranks the 対モ damage column.  The earlier general branch returned the
    per-hit power alone, so multi-hit skills never won against a general:
    ラビットサタン kept デスのカマ (11 x1) over ラビットキック (6 x4), and the
    whole 召喚獣 menu looked unchanged even after a damage table existed.
    """
    if not isinstance(entry, (tuple, list)) or len(entry) < 4:
        return 0
    _name, vs_monster, vs_general, hits = entry[0], entry[1], entry[2], entry[3]
    is_all = entry[4] if len(entry) > 4 else False
    self_damage = entry[5] if len(entry) > 5 else False
    if self_damage:
        return -1
    # 即死 is guaranteed against a lone general, so it outranks any damage
    # total; against monsters/bosses it stays an unquantified low chance and
    # must not displace a damaging skill (existing Excalibur/Unicorn policy).
    if (opponent_kind == 'general' and not multiple
            and _fold_skill(_name) in _MONSTER_INSTANT_DEATH):
        return 10 ** 6
    base = vs_monster if opponent_kind == 'monster' else vs_general
    score = base * (hits if isinstance(hits, int) and hits > 0 else 1)
    # Owner 2026-11-09: in a summon battle against another monster the ALL
    # attack is the pick. An ALL row clears the enemy monster's escort as well
    # as the monster, so it outranks a bigger single-target number; the damage
    # columns still order the ALL rows among themselves. General battles keep
    # the existing rule (ALL only while enemy soldiers remain).
    if is_all and (opponent_kind == 'monster' or (opponent_kind == 'general' and multiple)):
        score += 100000
    return score


def _monster_attack_policy_default(owner, ally, enemy, skill_lines, first, second,
                                    ally_hp, enemy_hp, mem, evidence=None):
    """Choose from verified attack data; unread/unknown menus keep old default.

    ``evidence`` is an optional mutable dict filled with the scored rows so the
    decision log can tell a data-driven pick from the generic first-skill
    fallback.  Production ran the #2835 policy for weeks without any such
    proof, which hid that only two monsters had attack rows.
    """
    if (owner != 'ally' or ally is None or enemy is None or not second
            or not (type(ally_hp) is int and ally_hp > 0
                    and type(enemy_hp) is int and enemy_hp > 0)):
        return None
    attack_data = getattr(reference, 'MONSTER_ATTACK_DATA', None)
    if not isinstance(attack_data, dict):
        return None
    data = attack_data.get(ally.name) or attack_data.get(_monster_display_name(ally.name))
    if not (isinstance(data, dict) and 'skill1' in data and 'skill2' in data):
        # The panel's monster name can differ from the table's key (a kana
        # variant, a truncation, or an extra る), while the menu's two skill
        # labels are the table's own strings. Match on those when exactly one
        # entry fits: g1163 2026-11-09 21:26 showed エクスカリバる/マサムネる
        # with no policy because the ally was not found by name.
        wanted = {_fold_skill(first), _fold_skill(second)}
        if all(wanted):
            matches = [row for row in attack_data.values()
                       if isinstance(row, dict) and row.get('skill1') and row.get('skill2')
                       and {_fold_skill(row['skill1'][0]), _fold_skill(row['skill2'][0])} == wanted]
            if len(matches) == 1:
                data = matches[0]
    if not (isinstance(data, dict) and 'skill1' in data and 'skill2' in data):
        return None

    # Fail closed unless both visible menu labels match the row we are about to
    # score. OCR/read drift must never turn the reference table into a guessed
    # button press.
    expected_first = data['skill1'][0] if data['skill1'] else None
    expected_second = data['skill2'][0] if data['skill2'] else None
    if (_fold_skill(first) != _fold_skill(expected_first)
            or _fold_skill(second) != _fold_skill(expected_second)):
        return None

    opponent_kind = 'monster' if _is_monster_opponent(enemy) else 'general'
    multiple = False
    battle = mem.get('battle') if isinstance(mem, dict) else None
    if isinstance(battle, dict):
        es = battle.get('enemy_soldiers')
        multiple = type(es) is int and es > 0

    s1 = _score_monster_attack(data['skill1'], opponent_kind, multiple)
    s2 = _score_monster_attack(data['skill2'], opponent_kind, multiple)

    def _decide(choice, rule):
        if isinstance(evidence, dict):
            evidence.update({
                'monster': ally.name,
                'opponent': enemy.name,
                'opponent_kind': opponent_kind,
                'enemy_soldiers': multiple,
                'skill1': expected_first,
                'skill2': expected_second,
                'score1': s1,
                'score2': s2,
                'choice': choice,
                'rule': rule,
            })
        return choice

    if 0 < enemy_hp <= 10:
        return _decide('skill2' if _is_self_damage_skill(first) else 'skill1',
                       'low_hp_finisher')
    if s1 < 0 and s2 >= 0:
        return _decide('skill2', 'self_damage')
    if s2 < 0 and s1 >= 0:
        return _decide('skill1', 'self_damage')
    if s1 != s2:
        return _decide('skill1' if s1 > s2 else 'skill2', 'damage_rank')
    return _decide(None, 'tie')
