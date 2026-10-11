import json
import subprocess
from types import SimpleNamespace

import pytest

from docich import special_corner as sc


def _valid(**overrides):
    data = {
        "title": "ゲームA",
        "title_suffix": "初見クリアに挑戦",
        "category_id": "12345",
        "category_name": "Game A",
        "comment_prompt": "配信者が遊んでいるゲームAの映像です。\n目標はクリア。",
        "closing_prompt": "",
    }
    data.update(overrides)
    return data


def test_missing_settings_fall_back_to_flyhome_defaults(tmp_path):
    loaded = sc.load_settings(tmp_path)
    assert loaded["is_default"] is True
    assert loaded["title"] == "Fly Me To The Home!"
    assert loaded["title_suffix"] == "全50面クリアに挑戦"
    assert loaded["category_id"] == "1202315854"
    # The defaults themselves must pass validation (they are what the UI shows).
    sc.validate_settings({k: sc.DEFAULTS[k] for k in sc.LIMITS})


def test_save_roundtrip_is_private_and_normalized(tmp_path):
    saved = sc.save_settings(tmp_path, _valid(title="  ゲームA  ", comment_prompt="\n行1\r\n行2  \n\n"),
                             clock=lambda: 100.0)
    assert saved["title"] == "ゲームA"
    assert saved["comment_prompt"] == "行1\n行2"
    path = tmp_path / sc.SETTINGS_FILE
    assert path.stat().st_mode & 0o777 == 0o600
    stored = json.loads(path.read_text())
    assert stored["schema_version"] == 1 and stored["updated_at"] == 100.0
    loaded = sc.load_settings(tmp_path)
    assert loaded["is_default"] is False
    assert loaded["title"] == "ゲームA" and loaded["updated_at"] == 100.0


@pytest.mark.parametrize("field,value", [
    ("title", ""),
    ("title", "x" * 61),
    ("title", "改行\nあり"),
    ("title", "変数$HOME"),
    ("title_suffix", "`cmd`"),
    ("category_id", "12a"),
    ("category_id", "１２３"),
    ("category_name", "<script>"),
    ("comment_prompt", "x" * 801),
    ("comment_prompt", "\n".join(["行"] * 21)),
    ("comment_prompt", "制御\x07文字"),
    ("comment_prompt", "ゼロ幅​スペース"),
    ("comment_prompt", "${_comment_persona}"),
    ("closing_prompt", "バックスラッシュ\\n"),
    ("closing_prompt", 3),
])
def test_invalid_values_are_rejected_with_the_field(field, value):
    with pytest.raises(sc.SpecialCornerSettingsError) as caught:
        sc.validate_settings(_valid(**{field: value}))
    assert caught.value.field == field


def test_unknown_and_missing_fields_are_rejected():
    with pytest.raises(sc.SpecialCornerSettingsError):
        sc.validate_settings(dict(_valid(), extra="x"))
    data = _valid()
    data.pop("closing_prompt")
    with pytest.raises(sc.SpecialCornerSettingsError):
        sc.validate_settings(data)


def test_corrupt_or_tampered_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / sc.SETTINGS_FILE
    path.write_text("{not json")
    assert sc.load_settings(tmp_path)["is_default"] is True
    path.write_text(json.dumps(dict(_valid(title="$x"), schema_version=1)))
    assert sc.load_settings(tmp_path)["title"] == "Fly Me To The Home!"
    path.write_text(json.dumps(dict(_valid(), schema_version=2)))
    assert sc.load_settings(tmp_path)["is_default"] is True


@pytest.mark.parametrize("query", ["", " ", "x" * 51, "-rf", "a$b", "a\nb", "a`b", 5])
def test_category_query_validation(query):
    with pytest.raises(sc.SpecialCornerSettingsError):
        sc.validate_query(query)


def test_parse_candidates_keeps_only_id_name_rows():
    out = "1202315854 | Fly Me To The Home!\n(no candidates)\nERROR: x\n33214 | Fortnite\nabc | bad\n9 | <b>\n"
    assert sc.parse_candidates(out) == [
        {"id": "1202315854", "name": "Fly Me To The Home!"},
        {"id": "33214", "name": "Fortnite"},
    ]


def _g_with_script(tmp_path, monkeypatch):
    script = tmp_path / "soren" / "update_stream_game.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    import docich.stream_category as stream_category
    monkeypatch.setattr(stream_category, "script_path", lambda g: script)
    return SimpleNamespace(), script


def test_search_runs_fixed_resolve_argv_without_stderr(tmp_path, monkeypatch):
    g, script = _g_with_script(tmp_path, monkeypatch)
    monkeypatch.setenv("TWITCH_GAME_TOKEN", "must-not-leak")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="1202315854 | Fly Me To The Home!\n")

    assert sc.search_categories(g, " Fly Me ", run=run) == [
        {"id": "1202315854", "name": "Fly Me To The Home!"}]
    argv, kwargs = calls[0]
    assert argv == [str(script), "--resolve", "Fly Me"]
    assert kwargs["cwd"] == str(script.parent)
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert "TWITCH_GAME_TOKEN" not in kwargs["env"]
    assert set(kwargs["env"]) <= {"PATH", "LANG", "HOME"}


def test_search_failures_are_generic(tmp_path, monkeypatch):
    g, _ = _g_with_script(tmp_path, monkeypatch)

    def failing(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 3, stdout="")

    with pytest.raises(sc.CategorySearchError, match="rc=3"):
        sc.search_categories(g, "x", run=failing)

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 45)

    with pytest.raises(sc.CategorySearchError, match="タイムアウト"):
        sc.search_categories(g, "x", run=slow)
