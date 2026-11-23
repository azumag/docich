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


def _write_doushi_state(state_dir):
    runtime_id = "g4-a1b2c3d4"
    runtime_dir = state_dir / "runtimes" / runtime_id
    runtime_dir.mkdir(parents=True)
    (state_dir / "doushi_corner.json").write_text(json.dumps({
        "doushi_runtime_identity": {"game": "doushi-view", "runtime_id": runtime_id,
                                    "generation": 4, "lease_id": "l1"}}), encoding="utf-8")
    (runtime_dir / "doushi_ranking.json").write_text(json.dumps({"payload": {
        "by_count": [{"username": "アリス", "redemption_count": 42}],
        "by_points": [{"username": "ボブ", "total_points": 1200}],
        "popular_cards": [{"card_name": "赤いカード", "actual_count": 30}]}}), encoding="utf-8")


def test_doushi_profile_uses_the_run_ranking_and_replaces_soviet_rules(tmp_path):
    _write_doushi_state(tmp_path)
    profile = game_profile.build("doushi-view", state_dir=tmp_path)
    assert profile is not None and profile["game"] == "doushi-view"
    assert "アリス" in profile["game_state"] and "42枚" in profile["game_state"]
    assert "ボブ" in profile["game_state"] and "1200ポイント" in profile["game_state"]
    assert "赤いカード" in profile["game_state"] and "30枚" in profile["game_state"]
    assert profile["ui_memo"] == ""
    assert "No separate rules memo" in profile["game_rules"]
    assert "ソ連ゲーム" not in profile["game_rules_ja"]
    assert len(profile["game_state"]) <= 600


def test_doushi_profile_falls_back_when_runtime_payload_is_missing(tmp_path):
    profile = game_profile.build("doushi-view", state_dir=tmp_path)
    assert profile is not None
    assert "同志カード大賞" not in profile["game_state"]
    assert "推測して補わないでください" in profile["game_state"]


def test_doushi_launcher_emits_the_same_profile_as_the_live_worker(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_doushi_state(state_dir)
    result = _run("doushi-view", cwd=tmp_path, env_extra={"DOCICH_STATE_DIR": str(state_dir)})
    assert result.returncode == 0, result.stderr
    profile = json.loads(result.stdout)
    assert profile["game"] == "doushi-view"
    assert "アリス" in profile["game_state"] and "42枚" in profile["game_state"]
    assert "赤いカード" in profile["game_state"]


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


def test_hanjuku_rules_slot_does_not_invent_rules_or_reuse_the_soviet_ones():
    profile = game_profile.build("hanjuku-hero")
    en, ja = profile["game_rules"], profile["game_rules_ja"]
    assert "no rules memo" in en and "Never explain it with the Soviet game's rules" in en
    assert "基本ルールのメモはありません" in ja and "ソ連ゲームの国・併合・得点のルールで説明しない" in ja
    for text in (en, ja):
        assert len(text) <= 1500  # the shell keeps at most 1500 characters per field
        assert not any(ch in text for ch in "$`\\<>\x7f")
        assert "16" not in text and "アルメニア" not in text and "Armenia" not in text
    assert all(line.startswith("\t") for line in ja.split("\n"))  # same indentation as the template block


def test_default_rules_slots_are_the_template_blocks_the_slots_replaced():
    from docich.comment import prompt
    assets = prompt.mode_assets("main")
    assert assets["_comment_game_rules"].startswith("【Main Game Basic Rules (not current Soren91 scores)】")
    assert assets["_comment_game_rules_ja"].startswith("\t【本編ゲームの基本ルール（盤面・併合・物理）】")
    assert prompt.mode_assets("soren91")["_comment_game_rules"] == assets["_comment_game_rules"]
    for name in ("comment_response_game.md", "comment_template.md"):
        text = prompt.asset(name)
        assert "16 countries" not in text and "16種類の国" not in text
    assert prompt.asset("comment_response_game.md").count("${_comment_game_rules}") == 1
    assert prompt.asset("comment_template.md").count("${_comment_game_rules_ja}") == 1
