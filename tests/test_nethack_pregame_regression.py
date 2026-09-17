from docich.adapters.nethack import _is_character_creation_screen


def test_welcome_banner_is_not_character_creation() -> None:
    # NetHack prints this after character creation, and it can remain in the
    # pane alongside the first map/status line. Treating it as pregame would
    # skip the durable save boundary for a live adventure.
    pane = "Welcome to NetHack!\n  -----\n  |.@.|\n  -----\nDlvl:1 HP:18(18) Pw:5(5) AC:10 Exp:1\n"
    assert not _is_character_creation_screen(pane)


def test_actual_character_creation_prompts_remain_pregame() -> None:
    assert _is_character_creation_screen("Shall I pick a character for you? [ynq]")
    assert _is_character_creation_screen("Pick a role or press ? for more info.")
