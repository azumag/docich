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


class _WebUI:
    soren_root = "/nonexistent"


class _FakeG:
    """Minimal stand-in for GlobalConfig."""
    config_path = "/nonexistent/path.toml"
    webui = _WebUI()
    repo_root = "/nonexistent"


def _fake_overlay(root, event):
    pass


def _make_fake_speech(captured):
    def fake_speech(g, text, *, context, speaker="", runtime_fence=None):
        captured["text"] = text
        captured["context"] = context
        captured["speaker"] = speaker
        captured["runtime_fence"] = runtime_fence
    return fake_speech


def test_publish_forwards_runtime_fence():
    captured = {}
    fence = {
        "game": "trpg", "runtime_id": "rt-1", "generation": 1,
        "lease_id": "lease-1", "expires_at": 9999999999,
    }
    result = tn.publish(
        _FakeG(), kind="scene", title="T", overlay_body="b", speech_text="hello",
        request_id="rt-1:123", audio_enabled=True, speaker="spk",
        runtime_fence=fence, overlay=_fake_overlay, speech=_make_fake_speech(captured),
    )
    assert result["speech"]["sent"] == 1
    assert result["overlay"]["sent"] == 1
    assert captured["runtime_fence"] == fence
    assert captured["text"] == "hello"
    assert captured["context"] == "trpg:scene"
    assert captured["speaker"] == "spk"


def test_publish_without_runtime_fence_is_default_none():
    captured = {}
    result = tn.publish(
        _FakeG(), kind="intro", title="T", overlay_body="b", speech_text="hi",
        request_id="r:1", audio_enabled=True,
        overlay=_fake_overlay, speech=_make_fake_speech(captured),
    )
    assert result["speech"]["sent"] == 1
    assert captured["runtime_fence"] is None


def test_publish_audio_disabled_skips_speech():
    captured = {}
    result = tn.publish(
        _FakeG(), kind="scene", title="T", overlay_body="b", speech_text="x",
        request_id="r:1", audio_enabled=False,
        overlay=_fake_overlay, speech=_make_fake_speech(captured),
    )
    assert result["speech"]["skipped"] == 1
    assert captured == {}
