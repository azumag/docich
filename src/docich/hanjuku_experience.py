"""Persistent battle experience for independent judgment under the chart.

The base chart remains the plan of record. When the chart has no command for
a situation (enemy summon menu, a battle menu with no due tactic), the policy
may choose independently. Each choice that reaches a measured win/loss is
stored under a stable situation key so the next matching situation can prefer
what worked. Chart tactics always win when they are due.

No model, provider or network. Deterministic JSON under ``run/``.
"""
from __future__ import annotations

import json
from pathlib import Path

from .game_switch import atomic_write_json
from .hanjuku_chart import HERO

SCHEMA = 1
EXPERIENCE_FILE = 'hanjuku_experience.json'
MAX_SITUATIONS = 200
MAX_ACTIONS = 8

# Known independent choices per situation kind, used when the default is
# losing and an alternative has not been tried yet. Map to the original six
# melee patterns: egg_summon/battle_menu only ever expose ⑥ (use_egg) vs
# ①③ (pass/attack=continue melee); ②④⑤ are chart-directed cards and are
# never independent alternatives. monster_menu has no original pattern: it
# exposes the first skill, the special second skill and retreating to the
# egg.
ALTERNATIVES = {
    'egg_summon': ('use_egg', 'attack'),
    'battle_menu': ('use_egg', 'pass'),
    'monster_menu': ('skill1', 'skill2', 'retreat'),
}


def empty() -> dict:
    return {'schema': SCHEMA, 'situations': {}}


def _hp_band(enemy_hp, ally_hp) -> str:
    if type(enemy_hp) is not int or type(ally_hp) is not int:
        return 'unknown'
    if ally_hp < enemy_hp:
        return 'behind'
    if ally_hp > enemy_hp:
        return 'ahead'
    return 'even'


def _resource_band(value) -> str:
    """Unknown is not zero; booleans are not measured resource counts."""
    if type(value) is not int or not 0 <= value <= 999:
        return 'unknown'
    return 'empty' if value == 0 else 'available'


def _risk_band(mem: dict, battle: dict) -> str:
    hp = battle.get('ally_hp')
    if type(hp) is not int or hp <= 0:
        return 'unknown'
    hero = battle.get('ally') == HERO
    full = mem.get('hero_max_hp') if hero else battle.get('start_ally_hp')
    known_full = type(full) is int and full >= hp
    floor = 20 if hero else 4
    if hp <= floor:
        return 'critical'
    if not known_full:
        return 'unknown'
    return 'critical' if hp <= max(floor, full // (2 if hero else 4)) else 'normal'


def situation_key(kind: str, mem: dict) -> str:
    """Stable fingerprint of one independent-judgment situation."""
    battle = mem.get('battle') if isinstance(mem.get('battle'), dict) else {}
    parts = [
        kind,
        str(mem.get('chapter') or ''),
        str(battle.get('enemy') or ''),
        str(battle.get('ally') or ''),
        str(battle.get('step') or mem.get('active') or ''),
    ]
    if kind in ('battle_menu', 'egg_summon'):
        parts.append(_hp_band(battle.get('enemy_hp'), battle.get('ally_hp')))
    if kind == 'monster_menu':
        # The summoned monsters decide the skill menu, not the hero battle.
        panel = mem.get('monster_panel') if isinstance(mem.get('monster_panel'), dict) else {}
        parts.append(str(panel.get('ally') or ''))
        parts.append(str(panel.get('enemy') or ''))
        parts.append(_hp_band(panel.get('enemy_hp'), panel.get('ally_hp')))
    # Version the fingerprint, not the persisted document: old evidence remains
    # available for inspection but cannot silently govern a different context.
    side = battle.get('side')
    side = side if side in ('attack', 'defense') else 'unknown'
    eggs = mem.get('egg_uses')
    eggs = eggs if isinstance(eggs, dict) else {}
    recheck = mem.get('egg_recheck')
    recheck = recheck if isinstance(recheck, (list, tuple)) else ()
    ally = battle.get('ally')
    ally = ally if isinstance(ally, str) else None
    egg_count = None if ally in recheck else eggs.get(ally)
    parts.extend(('ctx2', f'side={side}', f'risk={_risk_band(mem, battle)}',
                  f'egg={_resource_band(egg_count)}',
                  f'ally_soldiers={_resource_band(battle.get("ally_soldiers"))}',
                  f'enemy_soldiers={_resource_band(battle.get("enemy_soldiers"))}'))
    return '|'.join(part.replace('|', '/') for part in parts)


def _stats(st) -> tuple[int, int, int, float | None]:
    if not isinstance(st, dict):
        return 0, 0, 0, None
    wins = st.get('wins')
    losses = st.get('losses')
    wins = wins if type(wins) is int and wins >= 0 else 0
    losses = losses if type(losses) is int and losses >= 0 else 0
    trials = wins + losses
    return wins, losses, trials, (wins / trials if trials else None)


def _rate(actions: dict, action: str) -> float | None:
    return _stats(actions.get(action))[3]


def preferred(exp: dict | None, key: str, *, default: str, kind: str) -> str:
    """Best recorded action for ``key``, else ``default``.

    A measured non-default only replaces the default when it has a strictly
    higher win rate. If the default is losing (rate below 0.5) and an
    alternative has never been tried, that alternative is selected so the bot
    can learn beyond the built-in default, except in measured defense, critical
    or unknown contexts. Emergency default summons are never replaced by a
    learned pass/attack. Actions outside this menu's vocabulary are ignored.
    An untried default stays default.
    """
    allowed = ALTERNATIVES.get(kind, ())
    if default not in allowed or not isinstance(key, str) or not isinstance(exp, dict):
        return default
    context = key.split('|')
    constrained = ('ctx2' in context and
                   ('risk=critical' in context or 'risk=unknown' in context or
                    'side=defense' in context or 'side=unknown' in context))
    # Keep a policy-selected emergency summon. This function chooses a label;
    # the caller must still verify the real menu and egg availability.
    if constrained and 'risk=critical' in context and default == 'use_egg':
        return default
    row = exp.get('situations')
    row = row.get(key) if isinstance(row, dict) else None
    actions = row.get('actions') if isinstance(row, dict) and isinstance(row.get('actions'), dict) else {}
    default_rate = _rate(actions, default)
    if default_rate is None:
        return default
    best_action, best_rate, best_trials = None, default_rate, -1
    for action, st in actions.items():
        if not isinstance(action, str) or action == default or action not in allowed:
            continue
        _, _, trials, rate = _stats(st)
        if trials <= 0 or rate is None:
            continue
        if rate > default_rate or (rate == default_rate and trials > best_trials):
            if rate > best_rate or (rate == best_rate and trials > best_trials):
                best_action, best_rate, best_trials = action, rate, trials
    if best_action is not None and best_rate > default_rate:
        return best_action
    if default_rate < 0.5 and not constrained:
        for alt in ALTERNATIVES.get(kind, ()):
            if alt == default:
                continue
            _, _, alt_trials, _ = _stats(actions.get(alt))
            if alt_trials == 0:
                return alt
    return default


def record(mem: dict, key: str, action: str, outcome: str) -> bool:
    """Count one measured outcome under ``key``/``action``. Returns whether stored."""
    if outcome not in ('win', 'loss') or not isinstance(key, str) or not key:
        return False
    if not isinstance(action, str) or not action:
        return False
    exp = mem.get('_experience')
    if not isinstance(exp, dict) or exp.get('schema') != SCHEMA:
        exp = mem['_experience'] = empty()
    situations = exp.setdefault('situations', {})
    if not isinstance(situations, dict):
        situations = exp['situations'] = {}
    if key not in situations and len(situations) >= MAX_SITUATIONS:
        # Bounded: drop one oldest-looking key (insertion order) rather than grow.
        for old in list(situations.keys()):
            if old != key:
                situations.pop(old, None)
                break
    row = situations.get(key)
    if not isinstance(row, dict):
        row = situations[key] = {'actions': {}}
    actions = row.get('actions')
    if not isinstance(actions, dict):
        actions = row['actions'] = {}
    if action not in actions and len(actions) >= MAX_ACTIONS:
        return False
    st = actions.get(action)
    if not isinstance(st, dict):
        st = actions[action] = {'wins': 0, 'losses': 0}
    field = 'wins' if outcome == 'win' else 'losses'
    st[field] = (st.get(field) if type(st.get(field)) is int and st[field] >= 0 else 0) + 1
    return True


def _clean(raw: dict) -> dict:
    if not isinstance(raw, dict) or raw.get('schema') != SCHEMA:
        return empty()
    situations = raw.get('situations')
    if not isinstance(situations, dict):
        return empty()
    clean = {}
    for key, row in list(situations.items())[:MAX_SITUATIONS]:
        if not isinstance(key, str) or not isinstance(row, dict):
            continue
        actions = row.get('actions')
        if not isinstance(actions, dict):
            continue
        clean_actions = {}
        for action, st in list(actions.items())[:MAX_ACTIONS]:
            if not isinstance(action, str) or not isinstance(st, dict):
                continue
            wins, losses, _, _ = _stats(st)
            clean_actions[action] = {'wins': wins, 'losses': losses}
        if clean_actions:
            clean[key] = {'actions': clean_actions}
    return {'schema': SCHEMA, 'situations': clean}


def load(path: Path) -> dict:
    """Read the experience file; a missing or invalid file is simply empty."""
    path = Path(path)
    if path.is_symlink():
        return empty()
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return empty()
    return _clean(raw)


def save(path: Path, exp: dict) -> None:
    """Atomically replace the experience file after schema validation."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError('experience file may not be a symlink')
    if not isinstance(exp, dict) or exp.get('schema') != SCHEMA:
        raise ValueError('invalid experience schema')
    atomic_write_json(path, _clean(exp))
