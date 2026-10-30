"""Compatibility facade for the layered Hanjuku policy (``docich.hanjuku``).

The chart-driven deterministic policy used to live in this single module. It
is now split by responsibility:

- ``docich.hanjuku.os_layer``  OS層: screen readers, world model, pad commands, map navigation
- ``docich.hanjuku.strategy``  戦略層: chart orders, sortie kits, replanning, monthly budget
- ``docich.hanjuku.judgment``  判断層: battle and event judgment
- ``docich.hanjuku.flows``     手順層: multi-step menu procedures
- ``docich.hanjuku.decision_log`` / ``docich.hanjuku.reporting``

Every name the old module defined is still reachable here (``p.menu_to``) and
rebinding one (``monkeypatch.setattr(policy, 'nav_step', ...)``) reaches every
layer module that uses it, exactly as in the single-module version. New code
should import the owning layer module instead. See
``docs/hanjuku_layered_architecture.md``.
"""
from __future__ import annotations

import random
import re
import sys
import time
from dataclasses import asdict

from . import hanjuku_chart as chart
from . import hanjuku_chart_adjust as chart_adjust
from . import hanjuku_experience as experience
from . import hanjuku_reference as reference
from .hanjuku_egg_stock import observed_uses
from .hanjuku_egg_reference import GENERAL_EGGS, enemy_egg_triggers, general_debut_chapter, general_max_hp
from .hanjuku_font import UNKNOWN, TextLine
from .hanjuku_pixels import Frame
from .hanjuku_screen import (HEADER as HEADER_RE, OKUNOTE_CHOICES,
                             SUMMER_BONUS_CHOICES, Screen, castle_roofs,
                             month_confirmation_positions, own_camps)
from .hanjuku import _compat
from .hanjuku import decision_log as _decision_log, reporting as _reporting
from .hanjuku.flows import (battle as _flow_battle, battle_menus as _flow_battle_menus,
                            camp_recheck as _flow_camp_recheck,
                            deploy as _flow_deploy, egg_recovery as _flow_egg_recovery,
                            house as _flow_house, map as _flow_map,
                            month as _flow_month, name as _flow_name, prompts as _flow_prompts,
                            recall as _flow_recall, shop as _flow_shop)
from .hanjuku.judgment import battle as _judgment_battle, events as _judgment_events
from .hanjuku.os_layer import (commands as _os_commands, navigation as _os_navigation,
                               readers as _os_readers, world as _os_world)
from .hanjuku.strategy import (campaign as _strategy_campaign, economy as _strategy_economy,
                               execution as _strategy_execution, goals as _strategy_goals,
                               kit as _strategy_kit,
                               orders as _strategy_orders, replan as _strategy_replan)

_LAYER_MODULES = (
    _decision_log,
    _os_readers, _os_world, _os_commands, _os_navigation,
    _strategy_execution, _strategy_kit, _strategy_orders, _strategy_replan, _strategy_economy, _strategy_campaign, _strategy_goals,
    _judgment_battle, _judgment_events,
    _flow_name, _flow_map, _flow_recall, _flow_deploy, _flow_battle, _flow_battle_menus,
    _flow_month, _flow_egg_recovery, _flow_shop, _flow_prompts,
    _reporting,
)

_compat.install(__name__, _LAYER_MODULES)


# A named retreat recheck intentionally has a finite UI budget. Before this
# guard, exhausting that budget left the queue row alive forever and
# ``_visible_camps`` hid the same tent forever as well. Do not discard the row
# before opening the camp: the anonymous path must still read the current
# occupant's real status (including egg quantity). Once that status identifies
# the same exhausted actor, release only the pre-dispatch row. Rows with an
# uncertain or confirmed dispatch stay protected so another return A cannot be
# sent accidentally.
_camp_recheck_step_without_exhausted_release = _flow_camp_recheck.step
_visible_camps_without_exhausted_release = _flow_camp_recheck._visible_camps


def _exhausted_predispatch_camp_row(mem, actor, row):
    now = mem.get('tick')
    return (isinstance(row, dict)
            and row.get('status') in ('needs_observation', 'anonymous_recheck_exhausted')
            and row.get('attempts') == _flow_camp_recheck.ATTEMPT_LIMIT
            and type(row.get('retry_after')) is int
            and type(now) is int and now >= row['retry_after']
            and _flow_camp_recheck._arrival_request(mem, actor, row) is None)


def _visible_camps_with_exhausted_fallback(mem, frame, queue):
    """Let an exhausted pre-dispatch camp be opened for a fresh status read."""
    softened = []
    for actor, row in list((queue or {}).items()):
        if _exhausted_predispatch_camp_row(mem, actor, row):
            softened.append((row, row['attempts']))
            row['attempts'] = _flow_camp_recheck.ATTEMPT_LIMIT - 1
    try:
        return _visible_camps_without_exhausted_release(mem, frame, queue)
    finally:
        for row, attempts in softened:
            row['attempts'] = attempts


def _release_exhausted_verified_camp_actor(screen, mem):
    """Drop a stale named budget only after the open tent names that actor."""
    state = mem.get('recall') or {}
    if not (state.get('anonymous_recheck') and state.get('stage') == 'recheck_read'
            and screen.kind == 'text'):
        return None
    info = _flow_house.general_status(screen)
    actor = info.get('general') if isinstance(info, dict) else None
    if not _flow_camp_recheck._clean_name(actor):
        return None
    queue = _flow_camp_recheck._scope(mem)
    row = queue.get(actor)
    if not _exhausted_predispatch_camp_row(mem, actor, row):
        return None
    queue.pop(actor, None)
    if not queue:
        mem.pop('retreat_rechecks', None)
        mem.pop('retreat_anonymous_budget', None)
    _decision_log._record(
        mem, 'retreat_recheck_budget_released', chart_step=None, general=actor,
        observed_metric={'attempts': row['attempts'], 'retry_after': row['retry_after']},
        resulting_event='verified_anonymous_camp_recall_allowed',
        reason='現在の野営ステータスで本人を実確認したため、使い切った旧再確認予算の永続抑止を解除する',
    )
    return actor


def _camp_recheck_step_with_exhausted_release(screen, mem, frame, existing):
    _release_exhausted_verified_camp_actor(screen, mem)
    return _camp_recheck_step_without_exhausted_release(screen, mem, frame, existing)


_flow_camp_recheck._visible_camps = _visible_camps_with_exhausted_fallback
_flow_camp_recheck.step = _camp_recheck_step_with_exhausted_release


# v350 can re-arm a 四季王 step after its march/entry goes stale, but a retry
# can still park when every cached garrison list says there is no legal
# companion. Returning/recruited generals may have changed the real home
# roster since that receipt. Force one bounded re-read of the home list so a
# stale retry cannot be abandoned solely because of old roster evidence.
BOSS_ROSTER_REPROBE_TICKS = 100
_next_order_without_boss_roster_reprobe = next_order


def _reprobe_stalled_season_boss_roster(mem) -> bool:
    if mem.get('chapter') != 4:
        return False
    states = mem.get('orders') or {}
    sorties = mem.get('sorties') or {}
    sites = set(chart.boss_sites(4))
    stalled = [order for order in chart.orders(4)
               if order.get('target') in sites
               and states.get(order['step']) == 'pending'
               and (sorties.get(order['step']) or {}).get('status') == 'overdue'
               and not _strategy_orders._site_king_down(mem, order['target'])]
    if not stalled:
        return False
    if any(_strategy_orders._boss_companion_source(order, mem) is not None
           for order in stalled):
        return False

    home = chart.home_castle(4)
    if home not in _os_world._owned(mem):
        return False
    garrison = mem.setdefault('garrison', {})
    if home not in garrison or garrison.get(home) is None:
        return False

    now = int(mem.get('tick') or 0)
    last = mem.get('boss_roster_reprobe_tick')
    if type(last) is int and now - last < BOSS_ROSTER_REPROBE_TICKS:
        return False

    previous = list(garrison.get(home) or ())
    garrison.pop(home, None)
    (mem.get('garrison_observed_at') or {}).pop(home, None)
    mem['boss_roster_reprobe_tick'] = now
    _decision_log._record(
        mem, 'boss_roster_reprobe',
        observed_metric={'home': home, 'cached_generals': previous,
                         'stalled_steps': [order['step'] for order in stalled]},
        reason='長時間停滞した四季王の再出撃候補が見つからないため、本城の将軍一覧を再確認する',
    )
    return True


def _next_order_with_boss_roster_reprobe(mem):
    order = _next_order_without_boss_roster_reprobe(mem)
    if order is None and _reprobe_stalled_season_boss_roster(mem):
        return _next_order_without_boss_roster_reprobe(mem)
    return order


# The compatibility facade deliberately forwards a rebinding to every layered
# module still holding the old function object. Use that contract here so the
# map flow and late imports both see the guarded next_order.
setattr(sys.modules[__name__], 'next_order', _next_order_with_boss_roster_reprobe)
