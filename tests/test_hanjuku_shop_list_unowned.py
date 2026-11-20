"""docich#4650: unowned shop list is exited early."""
from docich.hanjuku import orchestrator as O


def test_the_unowned_shop_list_is_exited_after_a_short_settle():
    assert O.shop_list_unowned_input("shop_list", 1) is None
    assert O.shop_list_unowned_input("shop_list", O.SHOP_LIST_HOLD_MAX) is None
    assert O.shop_list_unowned_input("shop_list",
                                    O.SHOP_LIST_HOLD_MAX + 1) == "b"


def test_the_settle_is_far_inside_the_no_input_backstop():
    assert O.SHOP_LIST_HOLD_MAX < O.NO_INPUT_HOLD_MAX


def test_other_screens_are_never_answered_by_this_rule():
    for kind in ("shop_exit_confirm", "shop_quantity", "gift_request",
                 "yes_no", "map", "text"):
        assert O.shop_list_unowned_input(kind, O.SHOP_LIST_HOLD_MAX + 5) is None


def test_guarded_holds_stay_fail_closed():
    streak = O.SHOP_LIST_HOLD_MAX + 5
    assert O.shop_list_unowned_input("shop_list", streak, guarded=True) is None
    assert O.shop_list_unowned_input("shop_list", streak, army_owned=True) is None
    assert O.shop_list_unowned_input("shop_list", streak,
                                     map_registration_hold=True) is None
    assert O.shop_list_unowned_input("shop_list", streak,
                                     orphan_battle_menu=True) is None
