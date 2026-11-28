"""docich#4650: an unowned field map is recentered early."""
from docich.hanjuku import orchestrator as O


def test_the_unowned_map_is_recentered_after_a_short_settle():
    assert O.map_unowned_input("map", 1) is None
    assert O.map_unowned_input("map", O.MAP_UNOWNED_HOLD_MAX) is None
    assert O.map_unowned_input("map",
                               O.MAP_UNOWNED_HOLD_MAX + 1) == "select"


def test_the_settle_is_far_inside_the_no_input_backstop():
    assert O.MAP_UNOWNED_HOLD_MAX < O.NO_INPUT_HOLD_MAX


def test_the_recenter_fires_once_per_settle_window_not_every_observation():
    # g1288 2026-11-20 13:57: the map stayed unowned, so the caller's streak kept
    # rising (24, 25, ...) and this rule pressed SELECT on every observation.
    # A settled map now recenters once per 11 observations instead.
    settle = O.MAP_UNOWNED_HOLD_MAX + 1
    assert O.map_unowned_input("map", settle) == "select"
    assert O.map_unowned_input("map", settle + 1) is None
    assert O.map_unowned_input("map", settle + 5) is None
    assert O.map_unowned_input("map", settle * 2) == "select"


def test_other_screens_are_never_answered_by_this_rule():
    for kind in ("shop_exit_confirm", "shop_list", "gift_request",
                 "yes_no", "text"):
        assert O.map_unowned_input(kind, O.MAP_UNOWNED_HOLD_MAX + 5) is None


def test_guarded_holds_stay_fail_closed():
    streak = O.MAP_UNOWNED_HOLD_MAX + 5
    assert O.map_unowned_input("map", streak, guarded=True) is None
    assert O.map_unowned_input("map", streak, army_owned=True) is None
    assert O.map_unowned_input("map", streak,
                               map_registration_hold=True) is None
    assert O.map_unowned_input("map", streak,
                               orphan_battle_menu=True) is None


def test_unowned_recenter_invalidates_the_old_route_until_map_receipt():
    mem = {'tick': 77, 'chapter': 3, 'nav_last': {'screen': [4, 5]},
           'uncertain': False, 'select_focus_tick': 10}
    O._mark_unowned_map_recenter(mem)
    assert mem['nav_last'] is None
    assert mem['uncertain'] is True
    assert mem['select_focus_tick'] == 77
    assert mem['select_centered'] == 77


def test_unowned_recenter_without_a_chapter_does_not_keep_a_stale_receipt():
    mem = {'tick': 78, 'select_centered': 10}
    O._mark_unowned_map_recenter(mem)
    assert 'select_centered' not in mem
