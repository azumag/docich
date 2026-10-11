from types import SimpleNamespace

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


def _g_settings(tmp_path, **overrides):
    from docich.special_corner import save_settings

    state = tmp_path / "run"
    data = {"title": "ゲームA", "title_suffix": "初見クリアに挑戦", "category_id": "1",
            "category_name": "Game A", "comment_prompt": "配信者がゲームAを遊んでいる。\n目標はクリア。",
            "closing_prompt": ""}
    data.update(overrides)
    save_settings(state, data)
    g = g_with(tmp_path)
    g.state_dir = state
    return g


def test_default_settings_keep_the_flyhome_prompt_and_fallback(tmp_path):
    g = g_with(tmp_path)
    g.state_dir = tmp_path / "empty"
    settings = closing.corner_settings(g)
    assert "全50面クリア" in closing.build_prompt(5, settings)
    assert closing.build_prompt(5, settings) == closing.build_prompt(5)
    assert "五十面クリア" in closing.fallback_text(5, settings)


def test_saved_settings_drive_prompt_and_fallback(tmp_path):
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
