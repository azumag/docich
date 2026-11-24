"""Which flow, if any, owns the general status panel on screen right now.

``flows.battle_menus.orphan_battle_menu_step`` closes a general's status page
with B.  That page is opened by the monthly roster walk, the house roster
walk, the arrival probe, a camp re-check and the boss fort route.  Until
2026-11-24 the orphan close ran before every one of those owners and took the
panel away before its reader could act: post_month_status failed every month
with ``status_unreadable`` (cached 0, count_mismatch true), and the other
menu readers sit after the same dispatch line.

This module is the single read-only predicate the orchestrator snapshots
before any flow runs.  It sits in the flows layer (a sibling of the owners) so
``battle_menus`` can call it without importing the orchestrator, and it calls
the owners' existing checks (``post_month_status``, ``house``,
``camp_recheck``) instead of re-implementing them.

Every condition is strict: unreadable or mismatched evidence returns False so
the orphan close keeps its pre-2026-11-24 behaviour.  A false positive would
hold a panel that no flow finishes, which is the g1190 A-forever failure in a
new costume.
"""
from __future__ import annotations

# camp_recheck.py reads the named status card in these two stages
# (flow['recall'].stage; ``_owner_shape`` also validates the camp identity).
CAMP_RECHECK_STATUS_STAGES = frozenset({'recheck_read', 'recheck_close'})
# house.py opens the individual status card from the roster walk (status) and
# from the post-purchase verification (verify_status).
HOUSE_STATUS_PHASES = frozenset({'status', 'verify_status'})


def status_panel_owner_active(mem, screen):
    """True only while a flow is reading this general status page.

    ``mem`` must already carry this observation's ``_run_identity`` and
    current ``chapter``/``month`` (the orchestrator sets both before the
    dispatch).  The checks are ordered cheapest first; the first matching
    owner wins because the caller only needs presence.
    """
    if not isinstance(mem, dict) or screen is None:
        return False
    # Lazy imports: flows import state and os_layer, and this module is also
    # reachable from flows.battle_menus during a battle decision.
    from . import house, post_month_status

    state = mem.get('post_month_status')
    if isinstance(state, dict) and state.get('stage') == 'status':
        # The same scope/identity check that makes post_month_status.step drop
        # a stale or foreign scan.  A leftover state must not suppress the B.
        target = state.get('selected')
        if (post_month_status.state_is_current(mem, state)
                and isinstance(target, str) and target
                and house.general_status_name(screen) == target):
            return True

    house_state = mem.get('house')
    if (isinstance(house_state, dict)
            and house_state.get('phase') in HOUSE_STATUS_PHASES
            and house_state.get('chapter') == mem.get('chapter')):
        # The owner reads through house.general_status; a panel it cannot name
        # (or another general's) is not its page, so the orphan B still runs.
        info = house.general_status(screen)
        target = (house_state.get('selected')
                  if house_state.get('phase') == 'status'
                  else house_state.get('general'))
        if info is not None and isinstance(target, str) and info.get('general') == target:
            return True

    probe = mem.get('arrival_status_probe')
    if isinstance(probe, dict) and probe.get('phase') == 'status':
        return True

    from ..state.regions import flow as _flow
    recall_state = _flow(mem).get('recall') or {}
    if (isinstance(recall_state, dict)
            and recall_state.get('stage') in CAMP_RECHECK_STATUS_STAGES):
        # _owner_shape validates the camp identity/chapter/tick and the stage's
        # fields; a malformed re-check falls back to the orphan B.
        from . import camp_recheck
        if camp_recheck._owner_shape(mem, recall_state):
            return True

    probe = mem.get('boss_pursuit')
    if (isinstance(probe, dict) and mem.get('chapter') == 9
            and probe.get('fort_stage') == 'status'):
        # The 砦's status card belongs to this pursuit only when its named
        # general matches the one selected from the fort roster.
        target = probe.get('general')
        if (isinstance(target, str) and target
                and house.general_status_name(screen) == target):
            return True
    return False
