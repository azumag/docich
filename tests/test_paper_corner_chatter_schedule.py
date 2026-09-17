import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.config import load_global
from docich.paper_corner import SCRIPT_SLOTS, PaperCornerManager


class Coordinator:
    pass


def _manager(tmp_path, *, overlay=None, speech=None):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'''[paths]\nstate_dir = "run"\n[trading]\npaper_worker_enabled = true\nnotifications_enabled = true\nnotification_speech_enabled = true\n[webui]\nsoren_root = "{tmp_path}/soren"\n[paper_corner]\nenabled = true\nstart_hour = 22\nduration_minutes = 30\n''',
        encoding="utf-8",
    )
    g = load_global(tmp_path, cfg)
    return PaperCornerManager(
        g,
        coordinator=Coordinator(),
        overlay=overlay or (lambda g, payload: None),
        speech=speech or (lambda g, text, **kwargs: None),
    )


def test_script_generation_prepares_but_does_not_frontload_speech(tmp_path, monkeypatch):
    overlays = []
    speech = []
    mgr = _manager(
        tmp_path,
        overlay=lambda g, payload: overlays.append(payload),
        speech=lambda g, text, **kwargs: speech.append(text),
    )

    from docich.trading import corner_script

    monkeypatch.setattr(
        corner_script,
        "generate_corner_script",
        lambda *args, **kwargs: {
            "source": "fixture",
            "segments": {
                "corner": "相場の話です。",
                "news": "ニュースの話です。",
                "chart": "チャートの話です。",
                "strategy": "戦略の話です。",
                "result": "結果の話です。",
                "fills": "約定の話です。",
                "review": "往復の話です。",
                "improve": "改善の話です。",
            },
        },
    )
    state = {"date": "2026-09-12", "reports": {}}
    mgr._announce_script(state)

    assert overlays == []
    assert speech == []
    assert state["script_segments"] == {
        "1": "相場の話です。",
        "2": "ニュースの話です。",
        "3": "チャートの話です。",
        "4": "戦略の話です。",
        "5": "結果の話です。",
        "6": "約定の話です。",
        "7": "往復の話です。",
        "8": "改善の話です。",
    }

    mgr._scheduled_narration(state, 1)
    assert state["reports"]["script:1"]["text"] == "相場の話です。"
    assert len(overlays) == len(speech) == 1


def test_periodic_messages_never_repeat_opening_catchphrase(tmp_path):
    spoken = []
    mgr = _manager(tmp_path, speech=lambda g, text, **kwargs: spoken.append(text))
    state = {"date": "2026-09-12", "reports": {}}

    mgr.deliver(state, "periodic", "いまは条件未達なので、値動きを見ながら待っています。")
    mgr._scheduled_narration(state, 2)

    assert len(spoken) == 2
    assert all("PAPER・暗号資産の模擬売買コーナーです" not in text for text in spoken)
    assert "chatter:2" in state["reports"]


def test_all_eight_segments_are_scheduled_early(tmp_path):
    mgr = _manager(tmp_path)
    state = {
        "date": "2026-09-12",
        "reports": {},
        "script_segments": {str(index): f"文{index}です。" for index in range(1, 9)},
    }
    # Eight substantive segments fill the corner (slots 1-8); chart is slot 3,
    # the "why" of each fill is slot 6 and the round-trip review is slot 7.
    assert SCRIPT_SLOTS[3] == 3
    assert SCRIPT_SLOTS[6] == 6
    assert SCRIPT_SLOTS[7] == 7
    for slot in range(1, 9):
        mgr._scheduled_narration(state, slot)
    assert state["reports"]["script:3"]["text"] == "文3です。"
    assert state["reports"]["script:6"]["text"] == "文6です。"
    assert state["reports"]["script:7"]["text"] == "文7です。"
    mgr._scheduled_narration(state, 9)
    assert "chatter:9" in state["reports"]


def test_missed_slot_catches_up_instead_of_being_lost(tmp_path):
    """A slow announce() overrunning its interval must not permanently skip a

    segment (the loop's slot index can jump ahead of the naive slot->segment
    mapping); the next scheduled call should catch the missed one up instead.
    """
    mgr = _manager(tmp_path)
    state = {
        "date": "2026-09-12",
        "reports": {},
        "script_segments": {str(index): f"文{index}です。" for index in range(1, 9)},
    }
    mgr._scheduled_narration(state, 1)
    assert "script:1" in state["reports"]
    # Slot 2 never fires (e.g. a slow prior announce() overran it); the loop's
    # next call jumps straight to slot 3.
    mgr._scheduled_narration(state, 3)
    assert state["reports"]["script:2"]["text"] == "文2です。", (
        "script:2 must be delivered on catch-up, not skipped"
    )
    assert "script:3" not in state["reports"], (
        "only one segment is delivered per call, so flooding never happens"
    )
    mgr._scheduled_narration(state, 3)
    assert state["reports"]["script:3"]["text"] == "文3です。"
