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
# Exact observed display spelling (g747 native frames 8867/8868). Keep the
# reference name and unrelated monster policies unchanged; no fuzzy matching.
_MONSTER_DISPLAY_ALIASES = {'フリージボーイ': 'フリージーボーイ'}
_MONSTER_EFFECTS = frozenset(_fold_skill(s) for s in reference.MONSTER_EFFECT_SKILLS)
_MONSTER_HEALS = frozenset(_fold_skill(s) for s in reference.MONSTER_HEAL_SKILLS)


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
    if not isinstance(entry, (tuple, list)) or len(entry) < 4:
        return 0
    _name, vs_monster, vs_general, hits = entry[0], entry[1], entry[2], entry[3]
    is_all = entry[4] if len(entry) > 4 else False
    self_damage = entry[5] if len(entry) > 5 else False
    if self_damage:
        return -1
    base = vs_monster if opponent_kind == 'monster' else vs_general
    if opponent_kind == 'general' and not multiple:
        return base
    score = base * (hits if isinstance(hits, int) and hits > 0 else 1)
    if opponent_kind == 'general' and multiple and is_all:
        score += 100000
    return score


def _monster_attack_policy_default(owner, ally, enemy, skill_lines, first, second,
                                    ally_hp, enemy_hp, mem):
    """Choose from verified attack data; unread/unknown menus keep old default."""
    if (owner != 'ally' or ally is None or enemy is None or not second
            or not (type(ally_hp) is int and ally_hp > 0
                    and type(enemy_hp) is int and enemy_hp > 0)):
        return None
    attack_data = getattr(reference, 'MONSTER_ATTACK_DATA', None)
    if not isinstance(attack_data, dict):
        return None
    data = attack_data.get(ally.name) or attack_data.get(_monster_display_name(ally.name))
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

    if 0 < enemy_hp <= 10:
        return 'skill2' if _is_self_damage_skill(first) else 'skill1'

    opponent_kind = 'monster' if _is_monster_opponent(enemy) else 'general'
    multiple = False
    battle = mem.get('battle') if isinstance(mem, dict) else None
    if isinstance(battle, dict):
        es = battle.get('enemy_soldiers')
        multiple = type(es) is int and es > 0

    s1 = _score_monster_attack(data['skill1'], opponent_kind, multiple)
    s2 = _score_monster_attack(data['skill2'], opponent_kind, multiple)
    if s1 < 0 and s2 >= 0:
        return 'skill2'
    if s2 < 0 and s1 >= 0:
        return 'skill1'
    if s1 != s2:
        return 'skill1' if s1 > s2 else 'skill2'
    return None
