"""Regression coverage for player-obscuring resume pagers and viewer translation.

Frames are synthetic reproductions, not a capture of the 2026-10-15 incident.
"""
from html.parser import HTMLParser
from types import SimpleNamespace

from docich.adapters.base import Observation
from docich.agent.brains import build_brain

import pytest

from docich.nethack_observation import normalize_tty
from docich.nethack_startup import NethackStartup
from docich.nethack_spectator import blank_frame, parse_tty, render_html, render_live_shell


def tty_frame(kind="top", *, page=0, player=False, status=True):
    lines = [""] * 24
    if kind == "top":
        lines[0] = f"Welcome back to the dungeon, page {page}. --More--"
    elif kind == "next_row":
        lines[0] = "A" * 72
        lines[1] = "--More--"
    elif kind == "multiline":
        lines[0] = "A message from the dungeon."
        lines[1] = "There is more to read."
        lines[3] = "--More--"
    elif kind == "legacy_intro":
        lines[0] = "It is written in the Book of Anu:"
        lines[1] = "After the Creation, the cruel god Moloch rebelled."
        lines[2] = "Marduk the Creator banished him."
        lines[4] = "--More--"
    elif kind == "gameplay":
        lines[0] = "You hear a distant sound."
    elif kind == "full_row":
        # ``more()`` keeps the marker on the message row while the cursor is
        # below CO-8, so a ~71-column statement plus the marker fills row 0.
        lines[0] = "x" * 71 + "--More--"
    else:
        raise AssertionError(kind)
    lines[10] = " ---------"
    lines[11] = " |..@....|" if player else " |.......|"
    lines[12] = " ---------"
    if status:
        lines[22] = "Docich the Rambler St:13 Dx:14 Co:14 In:13 Wi:9 Ch:12 Neutral"
        lines[23] = "Dlvl:1 $:474 HP:8(10) Pw:2(2) AC:10 Xp:1"
    return "\n".join(lines)


@pytest.mark.parametrize("restore_seen", [False, True])
@pytest.mark.parametrize("kind", ["top", "next_row", "multiline", "legacy_intro"])
def test_resume_pager_without_visible_player_is_advanced(kind, restore_seen):
    startup = NethackStartup(enabled=True, clock=lambda: 0.0)
    if restore_seen:
        restore = normalize_tty("Restoring save file...--More--")
        assert [a.text for a in startup.consider(restore)] == [" "]
    obs = normalize_tty(tty_frame(kind))
    assert obs.prompt == "more" and obs.player is None
    assert not startup._creation_seen
    actions = startup.consider(obs)
    assert actions is not None and [a.text for a in actions] == [" "]
    assert all(a.type == "text" for a in actions)
    assert startup.actions == 1 + int(restore_seen)
    assert startup.state == "answering" and not startup._creation_seen
    # Dismissing the page is not a new run or a declaration of gameplay.
    gameplay = normalize_tty(tty_frame("gameplay", player=True))
    assert startup.consider(gameplay) is None
    assert startup.state == "gameplay"


def test_recognized_pager_after_restore_does_not_require_visible_status():
    startup = NethackStartup(enabled=True, clock=lambda: 0.0)
    assert [a.text for a in startup.consider(
        normalize_tty("Restoring save file...--More--")
    )] == [" "]
    obs = normalize_tty(tty_frame("multiline", status=False))
    assert obs.prompt == "more" and obs.vitals.hp is None
    assert [a.text for a in startup.consider(obs)] == [" "]
    assert startup.state == "answering"


def test_same_pager_is_not_blindly_repeated():
    startup = NethackStartup(enabled=True, clock=lambda: 0.0)
    obs = normalize_tty(tty_frame())
    assert [a.text for a in startup.consider(obs)] == [" "]
    for _ in range(5):
        assert startup.consider(obs) == []
    assert startup.actions == 1
    assert startup.state == "waiting"


def test_distinct_pagers_share_the_startup_action_budget():
    startup = NethackStartup(enabled=True, clock=lambda: 0.0)
    for page in range(startup.MAX_ACTIONS):
        obs = normalize_tty(tty_frame(page=page))
        assert [a.text for a in startup.consider(obs)] == [" "]
    assert startup.consider(normalize_tty(tty_frame(page=100))) == []
    assert startup.state == "exhausted"
    assert startup.actions == startup.MAX_ACTIONS


@pytest.mark.parametrize("limit", ["time", "observations", "actions"])
def test_resume_pager_never_rearms_an_exhausted_budget(limit):
    """#2271: post-exhaustion pagers delegate; they never renew the budget.

    The gate keeps every counter and its ``exhausted`` state; only the
    reviewed policy surface (one space per distinct page) takes over, so a
    resumed run cannot be wedged by a pager the gate itself answered before
    the budget ran out.
    """
    clock = [0.0]
    startup = NethackStartup(enabled=True, clock=lambda: clock[0])
    assert startup.consider(normalize_tty("Loading")) == []
    if limit == "time":
        clock[0] = startup.TIMEOUT_S
    elif limit == "observations":
        startup.observations = startup.MAX_OBSERVATIONS
    else:
        startup.actions = startup.MAX_ACTIONS
        question = normalize_tty("Shall I pick a character for you? [yn] (y)")
        assert startup.consider(question) == []
    if startup.state != "exhausted":
        # The frame that crosses the time/observation budget is consumed by the
        # budget check itself; the next observation is already post-exhaustion.
        assert startup.consider(normalize_tty(tty_frame(status=False))) == []
    assert startup.state == "exhausted"
    before = dict(startup.__dict__)
    assert startup.consider(normalize_tty(tty_frame(page=2))) is None
    assert startup.__dict__ == before


@pytest.mark.parametrize("message", [
    "Really save? [yn] (n) --More--",
    "Really attack the shopkeeper? [yn] (n) --More--",
    "What do you want to eat? --More--",
    "What do you want to call a potion? --More--",
    "Choose a spell [a-z] --More--",
    "Continue? --More--",
])
def test_question_with_more_text_is_not_a_resume_pager(message):
    lines = tty_frame().splitlines()
    lines[0] = message
    obs = normalize_tty("\n".join(lines))
    assert obs.prompt not in {"more", "none"}
    startup = NethackStartup(enabled=True, clock=lambda: 0.0)
    assert startup.consider(obs) == []
    assert startup.actions == 0
    assert not startup._creation_seen


def test_lower_row_question_keeps_pager_blocked():
    lines = tty_frame("multiline").splitlines()
    lines[1] = "Really save? [yn] (n)"
    obs = normalize_tty("\n".join(lines))
    assert obs.prompt == "unknown"
    startup = NethackStartup(enabled=True, clock=lambda: 0.0)
    assert startup.consider(obs) == []
    assert startup.actions == 0


class DocumentMarkers(HTMLParser):
    def __init__(self):
        super().__init__()
        self.roots = []
        self.meta = []

    def handle_starttag(self, tag, attrs):
        if tag == "html":
            self.roots.append(dict(attrs))
        elif tag == "meta":
            self.meta.append(dict(attrs))


def assert_notranslate(document):
    markers = DocumentMarkers()
    markers.feed(document)
    assert len(markers.roots) == 1
    assert markers.roots[0].get("translate") == "no"
    assert markers.roots[0].get("lang") == "ja"
    assert {"name": "google", "content": "notranslate"} in markers.meta
    assert '<meta name="google" content="notranslate">' in document.split("</head>")[0]


@pytest.mark.parametrize("visual_mode", ["tiles", "ascii"])
@pytest.mark.parametrize("kind", ["map", "text", "placeholder"])
def test_exported_nethack_pages_disable_translation(kind, visual_mode):
    if kind == "placeholder":
        frame = blank_frame("NetHack画面を準備しています。")
    else:
        frame = parse_tty(tty_frame("gameplay", player=True) if kind == "map" else tty_frame())
    assert frame.frame_kind == kind
    assert_notranslate(render_html(frame, visual_mode=visual_mode))


def test_live_nethack_shell_disables_translation_before_frames_arrive():
    document = render_live_shell(runtime_id="regression-2271", generation=1,
                                 presentation_epoch="viewer-1")
    assert_notranslate(document)
    # The viewer remains read-only, with unchanged message text and polling.
    assert "tty.textContent = payload.tty_lines.join" in document
    assert "location.reload" not in document


def test_playerless_pager_without_resume_evidence_holds():
    startup = NethackStartup(enabled=True, clock=lambda: 0.0)
    obs = normalize_tty(tty_frame("multiline", status=False))
    assert obs.prompt == "more"
    assert startup.consider(obs) == []
    assert startup.actions == 0


@pytest.mark.parametrize("status", [
    "Dlvl:1 HP:0(10)",
    "Dlvl:1 HP:8(0)",
    "Dlvl:1 Xp:1",
    "HP:8(10) Xp:1",
])
def test_incomplete_or_dead_vitals_do_not_prove_resume(status):
    lines = tty_frame().splitlines()
    lines[23] = status
    startup = NethackStartup(enabled=True, clock=lambda: 0.0)
    assert startup.consider(normalize_tty("\n".join(lines))) == []
    assert startup.actions == 0


def test_restore_evidence_does_not_rearm_exhaustion():
    clock = [0.0]
    startup = NethackStartup(enabled=True, clock=lambda: clock[0])
    assert [a.text for a in startup.consider(
        normalize_tty("Restoring save file...--More--")
    )] == [" "]
    clock[0] = startup.TIMEOUT_S
    assert startup.consider(normalize_tty(tty_frame(status=False))) == []
    assert startup.state == "exhausted" and startup.actions == 1


def startup_brain():
    game = SimpleNamespace(name="nethack", adapter="cli", raw={
        "cli": {"cols": 80, "rows": 24},
        "nethack": {"startup": {"enabled": True}},
    }, agent=SimpleNamespace(brain="nethack"))
    brain = build_brain(SimpleNamespace(), game)
    brain.startup.clock = lambda: 0.0
    return brain


def observation(text):
    return Observation(game="nethack", title="NetHack", adapter="cli",
                       ts=1, kind="text", text=text)


def test_rejected_restore_does_not_authorize_unproven_pager():
    brain = startup_brain()
    before = dict(brain.startup.__dict__)
    actions = brain.decide(observation("Restoring save file...--More--"))
    assert [a.text for a in actions] == [" "]
    different = observation("Restore save? [yn]")
    assert not brain.validate_action(actions[0], different)
    brain.discard_action_plan()
    assert brain.startup.__dict__ == before
    assert not getattr(brain.startup, "_restore_seen", False)
    assert brain.decide(observation(tty_frame(status=False))) == []
    assert brain.startup.actions == 0


def test_accepted_restore_allows_pager_but_revalidates_frame():
    brain = startup_brain()
    restore = observation("Restoring save file...--More--")
    action = brain.decide(restore)[0]
    assert brain.validate_action(action, restore)
    brain.action_sent(action)
    assert brain.startup.actions == 1
    page = observation(tty_frame("multiline", status=False))
    actions = brain.decide(page)
    assert [a.text for a in actions] == [" "]
    assert not brain.validate_action(actions[0], observation("Really save? [yn] (n)"))
    brain.discard_action_plan()
    assert brain.startup.actions == 1
    action = brain.decide(page)[0]
    assert brain.validate_action(action, page)
    brain.action_sent(action)
    assert brain.startup.actions == 2
    assert brain.decide(observation(tty_frame("gameplay", player=True)))
    assert brain.startup.state == "gameplay"


def test_full_width_message_row_with_trailing_marker_is_a_pager():
    """#2271: the wrap heuristic must not hide a same-row ``--More--``.

    A 71-column statement plus the marker fills row 0, and the row under it is
    the map, so the message looked wrapped: the frame classified ``unknown``
    forever, no key was ever sent, and the run stalled until the corner's
    stall guard fired. Only a trailing bare marker qualifies; questions keep
    their precedence.
    """
    obs = normalize_tty(tty_frame("full_row"))
    assert len(obs.message) == 79
    assert obs.prompt == "more"
    startup = NethackStartup(enabled=True, clock=lambda: 0.0)
    assert [a.text for a in startup.consider(obs)] == [" "]
    assert startup.actions == 1


def test_wrapped_question_with_trailing_marker_is_still_not_a_pager():
    lines = tty_frame("full_row").splitlines()
    lines[0] = "Really attack the " + "very " * 12 + "kitten? [yn] (n)"
    obs = normalize_tty("\n".join(lines))
    assert len(obs.message) >= 79
    assert obs.prompt == "unknown"


def test_exhausted_gate_delegates_the_pager_through_the_brain():
    """The end-to-end #2271 path: resume, budget spent, pager still advanced."""
    brain = startup_brain()
    # Spend the startup budget on an unrecognized screen, as the stuck runs did.
    assert brain.decide(observation("Character creation help screen")) == []
    brain.startup.state = "exhausted"
    page = observation(tty_frame(page=7))
    actions = brain.decide(page)
    assert actions is not None and [a.text for a in actions] == [" "]
    assert brain.last_progress_decision.intent == "advance_message"
    # The gate itself still sends nothing and keeps its exhausted state.
    assert brain.startup.state == "exhausted"
    assert brain.startup.actions == 0
    assert brain.validate_action(actions[0], page)
    brain.action_sent(actions[0])
    # The same unchanged page is re-sent a bounded number of times, then the
    # resolver holds instead of paging forever.
    for _ in range(3):
        actions = brain.decide(page)
        assert [a.text for a in actions] == [" "]
        assert brain.last_progress_decision.intent == "advance_message"
        assert brain.validate_action(actions[0], page)
        brain.action_sent(actions[0])
    assert brain.decide(page) == []
    assert brain.last_progress_decision.intent == "progress_blocked"


def test_unproven_pager_never_leaves_the_exhausted_gate(capsys):
    brain = startup_brain()
    brain.startup.state = "exhausted"
    page = observation(tty_frame(status=False))
    assert brain.decide(page) == []
    assert brain.startup.state == "exhausted"


def test_rejected_plan_reports_one_bounded_reason(capsys):
    """A silently rejected plan produced the same 0-action line as a hold."""
    brain = startup_brain()
    actions = brain.decide(observation("Restoring save file...--More--"))
    assert not brain.validate_action(actions[0], observation("Restore save? [yn]"))
    err = capsys.readouterr().err
    assert "[nethack-plan] rejected=frame_changed streak=1" in err
    # The reason is an enum: no terminal text from either frame appears.
    assert "Restore" not in err and "save" not in err
    # The streak keeps the trace bounded; a second rejection is suppressed.
    brain.discard_action_plan()
    assert not brain.validate_action(actions[0], observation("Restore save? [yn]"))
    assert capsys.readouterr().err == ""
    assert brain._plan_reject_streak == 2
    # Accepting a fresh plan clears the streak so the next rejection is loud.
    restore = observation("Restoring save file...--More--")
    action = brain.decide(restore)[0]
    assert brain.validate_action(action, restore)
    assert brain._plan_reject_streak == 0
