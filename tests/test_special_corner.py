import json
import subprocess
from pathlib import Path
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


# --- manual start (PR 2) ---------------------------------------------------

REPO = Path(__file__).resolve().parents[1]


def _ctl_g(tmp_path, listen_ip="100.71.107.106"):
    config = tmp_path / "docich.toml"
    body = "" if listen_ip is None else f'[external_video_corner]\nlisten_ip = "{listen_ip}"\n'
    config.write_text(body)
    state = tmp_path / "run"
    state.mkdir()
    return SimpleNamespace(state_dir=state, config_path=config, repo_root=tmp_path)


def _receiver(monkeypatch, **state):
    import docich.external_video_receiver as receiver
    from docich.external_video_receiver import ExternalVideoError

    def fake(g, *, expected=None, fresh=False, now=None):
        if not state:
            raise ExternalVideoError("external video state is missing or invalid")
        return dict(state)

    monkeypatch.setattr(receiver, "read_receiver", fake)


def _corner(g, status):
    (Path(g.state_dir) / sc.CORNER_FILE).write_text(json.dumps({"status": status, "start_request_id": "x"}))


@pytest.mark.parametrize("state,label", [
    ({}, "none"),
    (dict(alive=True, status="waiting", fresh=False, expires_at=2000), "waiting"),
    (dict(alive=True, status="receiving", fresh=True, audio_present=False, expires_at=2000), "receiving"),
    (dict(alive=True, status="receiving", fresh=True, audio_present=True, expires_at=2000), "receiving_audio"),
    (dict(alive=True, status="receiving", fresh=False, audio_present=True, expires_at=2000), "stale"),
    (dict(alive=False, status="launching", fresh=False, expires_at=2000), "launching"),
    (dict(alive=False, status="stopped", fresh=False, expires_at=2000), "stopped"),
    (dict(alive=False, status="launching", fresh=False, expires_at=500), "stopped"),
])
def test_receiver_view_labels_without_identities(tmp_path, monkeypatch, state, label):
    g = _ctl_g(tmp_path)
    full = dict(state, receiver_id="11111111-1111-4111-8111-111111111111", listen_ip="100.71.107.106",
                pid=4242) if state else {}
    _receiver(monkeypatch, **full)
    view = sc.receiver_view(g, now=1000)
    assert view["state"] == label
    assert set(view) == {"state", "audio", "expires_in_s"}


def test_control_view_gates(tmp_path, monkeypatch):
    g = _ctl_g(tmp_path)
    _receiver(monkeypatch)
    view = sc.control_view(g, now=1000)
    assert view["can_prepare"] and not view["can_start"] and not view["can_stop_receiver"]
    _receiver(monkeypatch, alive=True, status="receiving", fresh=True, audio_present=True, expires_at=9e9)
    view = sc.control_view(g, now=1000)
    assert not view["can_prepare"] and view["can_start"] and view["can_stop_receiver"]
    for status in ("waiting", "active", "failed"):
        _corner(g, status)
        view = sc.control_view(g, now=1000)
        assert not (view["can_prepare"] or view["can_start"] or view["can_stop_receiver"]), status
    _corner(g, "completed")
    (Path(g.state_dir) / "corners").mkdir()
    (Path(g.state_dir) / sc.PAUSE_MARKER).write_text("")
    view = sc.control_view(g, now=1000)
    assert view["paused"] and not view["can_start"] and not view["can_prepare"]


def test_prepare_uses_cli_with_absolute_config_and_configured_ip(tmp_path, monkeypatch):
    g = _ctl_g(tmp_path)
    _receiver(monkeypatch)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    sc.prepare_receiver(g, run=run, now=1000)
    argv, kwargs = calls[0]
    assert argv == [str(tmp_path.resolve() / "bin" / "docich"), "--config", str(g.config_path.resolve()),
                    "external-video-corner", "prepare", "--listen-ip", "100.71.107.106",
                    "--wait-minutes", "1500"]
    assert Path(argv[2]).is_absolute()
    assert "XDG_RUNTIME_DIR" in kwargs["env"]
    assert kwargs["timeout"] == sc.CLI_TIMEOUT_S


def test_prepare_refuses_without_listen_ip_or_when_cli_refuses(tmp_path, monkeypatch):
    _receiver(monkeypatch)
    for index, bad in enumerate((None, "0.0.0.0", "100.71.107.106?passphrase=x")):
        base = tmp_path / f"case{index}"
        base.mkdir()
        g = _ctl_g(base, listen_ip=bad)
        with pytest.raises(sc.SpecialCornerControlError) as caught:
            sc.prepare_receiver(g, run=lambda *a, **k: pytest.fail("must not run"), now=1000)
        assert caught.value.code == "listen_ip_not_configured"
    (tmp_path / "ok").mkdir()
    g = _ctl_g(tmp_path / "ok")

    def refused(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr="receiver or relay port is occupied\n")

    with pytest.raises(sc.SpecialCornerControlError, match="port is occupied"):
        sc.prepare_receiver(g, run=refused, now=1000)


def test_launch_reserves_in_independent_unit_only_when_receiving_audio(tmp_path, monkeypatch):
    g = _ctl_g(tmp_path)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    _receiver(monkeypatch, alive=True, status="receiving", fresh=True, audio_present=False, expires_at=9e9)
    with pytest.raises(sc.SpecialCornerControlError) as caught:
        sc.launch_corner(g, run=run, now=1000)
    assert caught.value.code == "receiver_not_ready" and not calls
    _receiver(monkeypatch, alive=True, status="receiving", fresh=True, audio_present=True, expires_at=9e9)
    unit = sc.launch_corner(g, run=run, now=1000, unit_suffix="abc")
    argv, kwargs = calls[0]
    assert unit == "docich-external-video-corner-abc"
    assert argv[:5] == ["systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}"]
    assert f"--property=RuntimeMaxSec={(120 + 1440 + 30) * 60}" in argv
    assert "--property=KillMode=control-group" in argv
    tail = argv[argv.index(str(tmp_path.resolve() / "bin" / "docich")):]
    assert tail == [str(tmp_path.resolve() / "bin" / "docich"), "--config", str(g.config_path.resolve()),
                    "external-video-corner", "start", "--duration-minutes", "1440",
                    "--wait-for-idle", "--wait-minutes", "120"]
    assert "XDG_RUNTIME_DIR" in kwargs["env"]
    _corner(g, "waiting")
    with pytest.raises(sc.SpecialCornerControlError) as caught:
        sc.launch_corner(g, run=run, now=1000)
    assert caught.value.code == "busy" and len(calls) == 1


def test_runner_arguments_are_accepted_by_the_corner_cli():
    from docich import external_video_corner
    from docich.external_video_receiver import MAX_RECEIVER_MINUTES

    assert sc.RUNNER_DURATION_MINUTES == external_video_corner.MAX_DURATION_MINUTES
    assert sc.RECEIVER_WAIT_MINUTES == MAX_RECEIVER_MINUTES
    assert 1 <= sc.RUNNER_WAIT_MINUTES <= 120


def test_live_config_declares_a_valid_listen_ip():
    g = SimpleNamespace(config_path=REPO / "config" / "docich.soren-live.toml")
    assert sc.configured_listen_ip(g).startswith("100.")


def test_stop_receiver_refuses_while_reserved(tmp_path, monkeypatch):
    g = _ctl_g(tmp_path)
    _receiver(monkeypatch, alive=True, status="receiving", fresh=True, audio_present=True, expires_at=9e9)
    _corner(g, "waiting")
    with pytest.raises(sc.SpecialCornerControlError):
        sc.stop_receiver(g, run=lambda *a, **k: pytest.fail("must not run"), now=1000)
    _corner(g, "interrupted")
    seen = []
    sc.stop_receiver(g, run=lambda argv, **k: seen.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""),
                     now=1000)
    assert seen[0][-1] == "receiver-stop"


# --- comment-reply memo (PR 4) ---------------------------------------------

def test_saving_settings_writes_the_gated_comment_memo(tmp_path):
    sc.save_settings(tmp_path, _valid(comment_prompt="ゲームAの映像です。\n目標はクリア。"))
    memo = (tmp_path / sc.MEMO_FILE).read_text()
    lines = memo.splitlines()
    assert lines[0].startswith("- 【いまのメイン画面が external-video-view（特別コーナー）のときだけ有効】")
    assert "「ゲームA」" in lines[0]
    assert lines[1:3] == ["  ゲームAの映像です。", "  目標はクリア。"]
    assert lines[-1] == "  いまのメイン画面が external-video-view 以外のときは、この節を無視する"
    assert all(line.startswith("  ") for line in lines[1:])
    assert "$" not in memo and "`" not in memo
    assert (tmp_path / sc.MEMO_FILE).stat().st_mode & 0o777 == 0o600


def test_default_memo_describes_flyhome_and_empty_prompt_keeps_the_guards(tmp_path):
    sc.write_comment_memo(tmp_path)
    assert "「Fly Me To The Home!」" in (tmp_path / sc.MEMO_FILE).read_text()
    memo = sc.render_comment_memo(dict(_valid(comment_prompt="")))
    assert len(memo.splitlines()) == 4 and "推測で補わず" in memo


def test_launch_writes_the_memo_before_reserving(tmp_path, monkeypatch):
    g = _ctl_g(tmp_path)
    _receiver(monkeypatch, alive=True, status="receiving", fresh=True, audio_present=True, expires_at=9e9)
    sc.launch_corner(g, run=lambda argv, **k: subprocess.CompletedProcess(argv, 0, "", ""), now=1000)
    assert "Fly Me To The Home!" in (Path(g.state_dir) / sc.MEMO_FILE).read_text()
