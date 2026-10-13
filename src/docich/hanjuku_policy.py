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
                            deploy as _flow_deploy, egg_recovery as _flow_egg_recovery, map as _flow_map,
                            month as _flow_month, name as _flow_name, prompts as _flow_prompts,
                            recall as _flow_recall, shop as _flow_shop)
from .hanjuku.judgment import battle as _judgment_battle, events as _judgment_events
from .hanjuku.os_layer import (commands as _os_commands, navigation as _os_navigation,
                               readers as _os_readers, world as _os_world)
from .hanjuku.strategy import (campaign as _strategy_campaign, economy as _strategy_economy,
                               execution as _strategy_execution, kit as _strategy_kit,
                               orders as _strategy_orders, replan as _strategy_replan)

_LAYER_MODULES = (
    _decision_log,
    _os_readers, _os_world, _os_commands, _os_navigation,
    _strategy_execution, _strategy_kit, _strategy_orders, _strategy_replan, _strategy_economy, _strategy_campaign,
    _judgment_battle, _judgment_events,
    _flow_name, _flow_map, _flow_recall, _flow_deploy, _flow_battle, _flow_battle_menus,
    _flow_month, _flow_egg_recovery, _flow_shop, _flow_prompts,
    _reporting,
)

_compat.install(__name__, _LAYER_MODULES)
