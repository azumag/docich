import json
from types import SimpleNamespace

import pytest

import docich.external_video_closing as closing


def g_with(tmp_path, agents=None):
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[external_video_corner]\nclosing_agents = "{agents}"\n' if agents else "")
    return SimpleNamespace(config_path=cfg)


GOOD = "五十面クリアおめでとうございます。最後まで集中が途切れず、家にたどり着いた瞬間は本当にほっとしました。ご視聴ありがとうございました。"


def test_no_agents_speaks_fixed_fallback_without_generating(tmp_path):
    spoken = []
    out = closing.speak_closing(g_with(tmp_path), 42, generate=lambda *a: (_ for _ in ()).throw(AssertionError),
                                enqueue=lambda g, t, **kw: spoken.append(t))
    assert out == "spoken:fallback" and "42分" in spoken[0] and "五十面クリア" in spoken[0]


def test_generated_text_is_spoken(tmp_path):
    spoken = []
    out = closing.speak_closing(g_with(tmp_path, "a:b"), 10, generate=lambda g, a, m: GOOD,
                                enqueue=lambda g, t, **kw: spoken.append(t))
    assert out == "spoken:ai" and spoken == [GOOD]


def test_generation_failure_falls_back(tmp_path):
    spoken = []

    def boom(*_a):
        raise RuntimeError("x")

    out = closing.speak_closing(g_with(tmp_path, "a:b"), 10, generate=boom, enqueue=lambda g, t, **kw: spoken.append(t))
    assert out == "spoken:fallback" and "五十面クリア" in spoken[0]


def test_sanitize_rejects_unsafe_output():
    assert closing.sanitize(GOOD) == GOOD
    for bad in ["", "短い", "I am thinking about the answer " * 3, GOOD + " https://x.example", "a" * 100,
                "<thinking>" + GOOD, "```" + GOOD, GOOD * 5]:
        assert closing.sanitize(bad) is None


def _settings(**overrides):
    data = {"title": "ゲームA", "title_suffix": "初見クリアに挑戦", "category_id": "1",
            "category_name": "Game A", "comment_prompt": "配信者がゲームAを遊んでいる。\n目標はクリア。",
            "closing_prompt": ""}
    data.update(overrides)
    return data


def _write_corner_state(g, state):
    (g.state_dir / closing.CORNER_FILE).write_text(json.dumps(state), encoding="utf-8")


def _g_settings(tmp_path, *, agents=None, **overrides):
    from docich.special_corner import save_settings

    state = tmp_path / "run"
    data = _settings(**overrides)
    save_settings(state, data)
    g = g_with(tmp_path, agents)
    g.state_dir = state
    _write_corner_state(g, {"status": "active", "special_corner_snapshot_required": True,
                            "special_corner_settings": data})
    return g


def test_default_settings_keep_the_flyhome_prompt_and_fallback(tmp_path):
    g = g_with(tmp_path)
    g.state_dir = tmp_path / "empty"
    settings = closing.corner_settings(g)
    assert "全50面クリア" in closing.build_prompt(5, settings)
    assert closing.build_prompt(5, settings) == closing.build_prompt(5)
    assert "五十面クリア" in closing.fallback_text(5, settings)


def test_committed_settings_drive_prompt_and_fallback(tmp_path):
    g = _g_settings(tmp_path, closing_prompt="最後のボス戦について\n触れる")
    settings = closing.corner_settings(g)
    prompt = closing.build_prompt(30, settings)
    assert "『ゲームA』" in prompt and "約30分" in prompt
    assert "コーナーの説明: 配信者がゲームAを遊んでいる。 目標はクリア。" in prompt
    assert "感想で触れてほしいこと: 最後のボス戦について 触れる" in prompt
    assert "Fly Me" not in prompt and "50面" not in prompt
    spoken = []
    out = closing.speak_closing(g, 30, generate=lambda *a: None, enqueue=lambda g, t, **kw: spoken.append(t))
    assert out == "spoken:fallback"
    assert spoken[0].startswith("ゲームAの特別コーナー、約30分") and "五十面" not in spoken[0]


def test_generation_uses_the_settings_prompt(tmp_path, monkeypatch):
    import docich.ai_generate as ai_generate
    from types import SimpleNamespace as NS

    g = _g_settings(tmp_path, closing_prompt="感謝を伝える")
    seen = {}

    def fake_run_prompt(g, **kwargs):
        seen.update(kwargs)
        return NS(returncode=0, output=GOOD)

    monkeypatch.setattr(ai_generate, "run_prompt", fake_run_prompt)
    assert closing._generate(g, "a:b", 12) == GOOD
    assert "感謝を伝える" in seen["prompt_text"] and "『ゲームA』" in seen["prompt_text"]
    assert seen["agents"] == "a:b"
    assert seen["timeout"] == closing.GENERATE_TIMEOUT_S
    assert seen["timeout_sec"] == float(closing.GENERATE_TIMEOUT_S + 30)
    assert seen["env"]["DOCICH_ALLOW_REAL_AI"] == "1"


def test_active_snapshot_ignores_saved_future_settings(tmp_path, monkeypatch):
    import docich.ai_generate as ai_generate
    from docich.special_corner import save_settings

    g = _g_settings(tmp_path, agents="a:b", closing_prompt="ゲームAの最後に感謝を伝える")
    save_settings(g.state_dir, _settings(title="ゲームB", category_id="2", category_name="Game B",
                                         comment_prompt="次はゲームBを遊ぶ。", closing_prompt="ゲームBの感想"))
    seen, spoken = {}, []

    def fail_run_prompt(g, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=1, output="")

    monkeypatch.setattr(ai_generate, "run_prompt", fail_run_prompt)
    assert closing.speak_closing(g, 30, enqueue=lambda g, t, **kw: spoken.append(t)) == "spoken:fallback"
    assert "『ゲームA』" in seen["prompt_text"] and "ゲームAの最後に感謝を伝える" in seen["prompt_text"]
    assert "ゲームB" not in seen["prompt_text"]
    assert spoken[0].startswith("ゲームAの特別コーナー、約30分")
    assert "ゲームB" not in spoken[0] and "五十面" not in spoken[0]


@pytest.mark.parametrize("returncode, output", [(1, ""), (0, "thinking " + GOOD)])
def test_failed_generation_reuses_one_resolved_snapshot(tmp_path, monkeypatch, returncode, output):
    import docich.ai_generate as ai_generate
    from docich.special_corner import save_settings

    g = _g_settings(tmp_path, agents="a:b", closing_prompt="ゲームAへの感謝")
    reads, prompts, spoken = [], [], []
    resolve = closing.corner_settings

    def count_read(g):
        reads.append(g)
        return resolve(g)

    def change_settings_then_fail(g, **kwargs):
        prompts.append(kwargs["prompt_text"])
        future = _settings(title="ゲームB", category_id="2", category_name="Game B",
                           comment_prompt="ゲームBを遊ぶ。", closing_prompt="ゲームBの感想")
        save_settings(g.state_dir, future)
        _write_corner_state(g, {"status": "active", "special_corner_snapshot_required": True,
                                "special_corner_settings": future})
        return SimpleNamespace(returncode=returncode, output=output)

    monkeypatch.setattr(closing, "corner_settings", count_read)
    monkeypatch.setattr(ai_generate, "run_prompt", change_settings_then_fail)
    assert closing.speak_closing(g, 10, enqueue=lambda g, t, **kw: spoken.append(t)) == "spoken:fallback"
    assert reads == [g]
    assert "『ゲームA』" in prompts[0] and "ゲームAへの感謝" in prompts[0]
    assert spoken[0].startswith("ゲームAの特別コーナー、約10分") and "ゲームB" not in spoken[0]
    assert resolve(g)["title"] == "ゲームB"


def test_injected_generator_keeps_three_arguments_and_original_fallback(tmp_path):
    g = _g_settings(tmp_path, agents="a:b")
    calls, spoken = [], []

    def generate(g, agents, minutes):
        calls.append((g, agents, minutes))
        _write_corner_state(g, {"special_corner_snapshot_required": True,
                                "special_corner_settings": _settings(title="ゲームB")})
        raise RuntimeError("generation failed")

    assert closing.speak_closing(g, 10, generate=generate,
                                enqueue=lambda g, t, **kw: spoken.append(t)) == "spoken:fallback"
    assert calls == [(g, "a:b", 10)]
    assert spoken[0].startswith("ゲームAの特別コーナー、約10分")


@pytest.mark.parametrize("state", [{"status": "active"},
                                   {"status": "active", "special_corner_settings": {}}])
def test_legacy_run_without_required_snapshot_keeps_historical_defaults(tmp_path, state):
    from docich.special_corner import save_settings

    g = _g_settings(tmp_path)
    _write_corner_state(g, state)
    save_settings(g.state_dir, _settings(title="ゲームB"))
    settings = closing.corner_settings(g)
    assert settings["is_default"] is True
    assert closing.build_prompt(5, settings) == closing.build_prompt(5)
    spoken = []
    assert closing.speak_closing(g, 5, enqueue=lambda g, t, **kw: spoken.append(t)) == "spoken:fallback"
    assert spoken == [closing.fallback_text(5)]


@pytest.mark.parametrize("snapshot", [None, {}, [], _settings(title="<ゲームA>"),
                                       _settings(category_id="bad"), _settings(title="あ" * 61),
                                       {k: v for k, v in _settings().items() if k != "closing_prompt"},
                                       dict(_settings(), is_default=False)])
def test_required_missing_or_invalid_snapshot_uses_neutral_fallback(tmp_path, snapshot):
    g = _g_settings(tmp_path)
    state = {"status": "active", "special_corner_snapshot_required": True}
    if snapshot is not None:
        state["special_corner_settings"] = snapshot
    _write_corner_state(g, state)
    settings = closing.corner_settings(g)
    assert settings["category_id"] == settings["category_name"] == ""
    prompt = closing.build_prompt(5, settings)
    spoken = []
    assert closing.speak_closing(g, 5, enqueue=lambda g, t, **kw: spoken.append(t)) == "spoken:fallback"
    assert spoken[0].startswith("今回のゲームの特別コーナー、約5分")
    assert len(spoken[0]) <= closing.MAX_CHARS
    for text in (prompt, spoken[0]):
        assert "Fly Me" not in text and "50面" not in text and "五十面" not in text and "ゲームA" not in text


@pytest.mark.parametrize("raw", ["{broken", "[]", "{}" + " " * 32768])
def test_corrupt_corner_state_uses_neutral_fallback(tmp_path, raw):
    g = _g_settings(tmp_path)
    (g.state_dir / closing.CORNER_FILE).write_text(raw, encoding="utf-8")
    settings = closing.corner_settings(g)
    assert "Fly Me" not in closing.fallback_text(5, settings)
    assert "50面" not in closing.build_prompt(5, settings)


def test_committed_snapshot_is_normalized_by_shared_validator(tmp_path):
    g = _g_settings(tmp_path)
    _write_corner_state(g, {"special_corner_snapshot_required": True,
                            "special_corner_settings": _settings(title="  ゲームA  ",
                                                                  closing_prompt="\r\n感謝する\r\n")})
    settings = closing.corner_settings(g)
    assert settings["title"] == "ゲームA" and settings["closing_prompt"] == "感謝する"
