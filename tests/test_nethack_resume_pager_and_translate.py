"""Regression coverage for player-obscuring resume pagers and viewer translation.

Frames are synthetic reproductions, not a capture of the 2026-10-15 incident.
"""
from html.parser import HTMLParser

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


def test_recognized_pager_does_not_require_a_visible_status_block():
    startup = NethackStartup(enabled=True, clock=lambda: 0.0)
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
    assert startup.consider(normalize_tty(tty_frame())) == []
    assert startup.state == "exhausted"
    before = dict(startup.__dict__)
    assert startup.consider(normalize_tty(tty_frame(page=2))) == []
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
