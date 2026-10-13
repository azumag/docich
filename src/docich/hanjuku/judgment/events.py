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
