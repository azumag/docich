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
