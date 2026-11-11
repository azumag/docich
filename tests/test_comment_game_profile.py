"""Game-owned comment profile: content, config-driven state and the real launcher."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.comment import game_profile  # noqa: E402

LAUNCHER = ROOT / "bin" / "docich-comment-profile"
ENABLED = '[hanjuku.chart_adjust]\nenabled = true\nagents = "a:b"\n'


def _config(tmp_path, text):
    path = tmp_path / "hanjuku-hero.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_hanjuku_profile_carries_the_owner_specified_explanation(tmp_path):
    profile = game_profile.build("hanjuku-hero", config=_config(tmp_path, ENABLED))
    text = "\n".join(profile["ops_lines"])
    for needle in ("ヒューリスティック", "動作ログを見て定期的に改善", "保守的", "ハイブリッド",
                   "アルゴリズムへ置き換えられる", "レイテンシ", "半熟BOTの話ではない"):
        assert needle in text, needle
    assert profile["schema"] == 1 and profile["game"] == "hanjuku-hero"


def test_profile_replaces_soviet_slots_without_inventing_screen_facts(tmp_path):
    profile = game_profile.build("hanjuku-hero", config=_config(tmp_path, ENABLED))
    assert profile["ui_memo"] == ""
    state = profile["game_state"]
    assert "半熟英雄" in state
    assert "ソ連ゲームのスコア・盤面・ピース・建国統計はこのゲームの状況ではありません" in state
    assert "推測して補わないでください" in state
    for old, new in profile["persona_replace"]:
        assert old and new and "ソ連ゲーム" not in new


def test_persona_sentence_to_replace_exists_in_the_main_persona():
    persona = (ROOT / "src/docich/comment/prompts/comment_persona_main.md").read_text(encoding="utf-8")
    assert all(old in persona for old, _ in game_profile.PERSONA_REPLACE)


@pytest.mark.parametrize("config, expected, forbidden", [
    (ENABLED, "LLMの動的判断は有効", "止めていて"),
    ('[hanjuku.chart_adjust]\nenabled = false\nagents = "a:b"\n', "一時的に止めていて", "動的判断は有効"),
    ('[hanjuku.chart_adjust]\nenabled = true\nagents = ""\n', "一時的に止めていて", "動的判断は有効"),
    ('[hanjuku.chart_adjust]\nenabled = true\nagents = "a:b"\ndirect_enabled = true\ndirect_agents = ""\n',
     "一時的に止めていて", "動的判断は有効"),
])
def test_llm_state_follows_the_measured_config(tmp_path, config, expected, forbidden):
    text = "\n".join(game_profile.build("hanjuku-hero", config=_config(tmp_path, config))["ops_lines"])
    assert expected in text and forbidden not in text


@pytest.mark.parametrize("config", [None, "enabled = [broken", '[hanjuku.chart_adjust]\nenabled = "yes"\n'])
def test_llm_state_is_omitted_when_the_config_is_unreadable(tmp_path, config):
    path = tmp_path / "missing.toml" if config is None else _config(tmp_path, config)
    text = "\n".join(game_profile.build("hanjuku-hero", config=path)["ops_lines"])
    assert "ハイブリッド" in text and "いまの状態" not in text


@pytest.mark.parametrize("game", ["sorengame", "robots", "nethack", "soren91", "external-video-view", "", "unknown"])
def test_games_without_a_profile_get_none(game):
    assert game_profile.build(game) is None


def test_profile_text_has_no_template_or_shell_metacharacters():
    blob = json.dumps(game_profile.build("hanjuku-hero"), ensure_ascii=False)
    for ch in "$`\\<>\x7f":
        assert ch not in json.loads(blob)["game_state"] + "".join(json.loads(blob)["ops_lines"])
    assert len(blob) < 4000


def _run(*args, cwd, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(env_extra or {})
    return subprocess.run([str(LAUNCHER), *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=30)


def test_launcher_prints_the_profile_from_any_cwd_and_ignores_a_shadowing_package(tmp_path):
    shadow = tmp_path / "docich"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise RuntimeError('shadow package imported')\n")
    result = _run("hanjuku-hero", cwd=tmp_path, env_extra={"PYTHONPATH": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    profile = json.loads(result.stdout)
    assert profile["game"] == "hanjuku-hero" and profile["ops_lines"]


def test_launcher_prints_nothing_for_an_unsupported_game(tmp_path):
    result = _run("sorengame", cwd=tmp_path)
    assert result.returncode == 0 and result.stdout == ""


def test_launcher_rejects_a_wrong_argument_count(tmp_path):
    assert _run(cwd=tmp_path).returncode == 2
    assert _run("a", "b", cwd=tmp_path).returncode == 2


def test_launcher_reads_the_config_override_for_the_state_line(tmp_path):
    cfg = _config(tmp_path, '[hanjuku.chart_adjust]\nenabled = false\nagents = "a:b"\n')
    result = _run("hanjuku-hero", cwd=tmp_path, env_extra={"DOCICH_HANJUKU_CONFIG_FILE": str(cfg)})
    assert "一時的に止めていて" in json.loads(result.stdout)["ops_lines"][-1]


def test_launcher_is_executable():
    assert os.access(LAUNCHER, os.X_OK)
