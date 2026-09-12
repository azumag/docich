import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.config import load_global
from docich.paper_corner import PaperCornerManager


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
                "strategy": "戦略の話です。",
                "result": "結果の話です。",
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
        "2": "戦略の話です。",
        "3": "結果の話です。",
        "4": "改善の話です。",
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
