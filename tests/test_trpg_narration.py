"""Smoke tests for trpg_narration (PR-4, issue #684)."""
from docich import trpg_narration as tn


def test_constants():
    assert tn.MAX_SPEECH == 300
    assert tn.TITLE
    assert set(tn.CONTEXTS)


def test_fallback_text_bounds():
    assert len(tn.intro_text()) <= tn.MAX_SPEECH
    assert len(tn.scene_text("s", "t")) <= tn.MAX_SPEECH
    assert len(tn.choice_text()) <= tn.MAX_SPEECH
    assert len(tn.accusation_text()) <= tn.MAX_SPEECH
    assert len(tn.reveal_text("X", "Y", "Z")) <= tn.MAX_SPEECH


def test_narration_agents_missing_config():
    class G:
        config_path = "/nonexistent/path.toml"
    assert tn.narration_agents(G()) == ""
