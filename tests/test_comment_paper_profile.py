"""PAPER corner comment profile: measured P/L, PAPER disclaimer, launcher."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.comment import game_profile, paper_corner_profile  # noqa: E402
from docich.comment.sorengame_context import SorenGameContextProvider  # noqa: E402

LAUNCHER = ROOT / "bin" / "docich-comment-profile"


def _status(**overrides) -> dict:
    now = time.time()
    value = {
        "schema_version": 1,
        "mode": "paper",
        "worker_state": "paper_worker_running",
        "market_count": 5,
        "capital_reference": "10000",
        "deployed_reference": "2500",
        "open_positions": {"BTC/JPY": "0.001", "ETH/JPY": "0.02"},
        "recent_fills": [],
        "snapshot_generated_at": now - 20,
        "performance_summary": {
            "as_of": now - 20,
            "complete": True,
            "position_count": 2,
            "priced_positions": 2,
            "valued_positions": 2,
            "realized_total_jpy": "56.0",
            "interval_realized_pnl_jpy": "56.0",
            "unrealized_pnl_jpy": "67.45",
            "cumulative_pnl_jpy": "123.45",
            "equity_jpy": "10123.45",
        },
    }
    value.update(overrides)
    return value


def _write(root: Path, value) -> Path:
    (root / "trading").mkdir(parents=True, exist_ok=True)
    path = root / "trading" / "status.json"
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return path


def _profile(tmp_path: Path, *, now: float | None = None, status=None) -> dict:
    root = tmp_path / "state"
    _write(root, _status() if status is None else status)
    profile = game_profile.build("paper-view", state_dir=root, now=now)
    assert profile is not None
    return profile


def test_paper_profile_answers_money_questions_from_the_corner_status(tmp_path):
    now = time.time()
    text = _profile(tmp_path, now=now)["game_state"]
    # The measured numbers shown by the corner.
    assert "10000" not in text  # formatted, not a raw JSON dump
    assert "資金 10,000円" in text and "評価込み資産 10,123円" in text
    assert "累積損益 +123円で、元手より増えている" in text
    assert "区間損益(確定分) +56円 / 含み損益 +67円 / 確定損益の通算 +56円" in text
    # The reply must state PAPER first and then answer with the number.
    assert "最初にPAPER取引(仮想資金)であることを伝え" in text
    assert "コーナー内では増えている、マイナスなら減っている" in text
    assert "実際のお金は増えないという説明だけで終わらせない" in text
    # The Soviet memo's facts are not presented as this screen's state.
    assert "snapshot_score" not in text and "建国統計" not in text


def test_paper_profile_reports_a_loss_as_a_decrease(tmp_path):
    status = _status()
    status["performance_summary"] = {
        **status["performance_summary"],
        "cumulative_pnl_jpy": "-250.4",
        "unrealized_pnl_jpy": "-100.2",
        "interval_realized_pnl_jpy": "-50",
        "equity_jpy": "9749.6",
    }
    text = _profile(tmp_path, status=status)["game_state"]
    assert "累積損益 -250円で、元手より減っている" in text
    assert "元手より増えている" not in text


def test_incomplete_valuation_is_pending_not_zero(tmp_path):
    status = _status()
    status["performance_summary"] = {
        "as_of": time.time() - 20,
        "complete": False,
        "position_count": 2,
        "priced_positions": 1,
        "valued_positions": 1,
        "realized_total_jpy": "56.0",
        "interval_realized_pnl_jpy": "56.0",
        "unrealized_pnl_jpy": None,
        "cumulative_pnl_jpy": None,
        "equity_jpy": None,
    }
    text = _profile(tmp_path, status=status)["game_state"]
    assert "累積損益は評価待ち" in text
    assert "区間損益(確定分) +56円" in text
    assert "元手より増えている" not in text
    assert "累積損益 0円" not in text and "含み損益 0円" not in text


def test_stale_status_is_marked_as_reference(tmp_path):
    now = time.time()
    status = _status()
    status["snapshot_generated_at"] = now - 3600
    status["performance_summary"] = {**status["performance_summary"], "as_of": now - 3600}
    text = _profile(tmp_path, now=now, status=status)["game_state"]
    assert "最終更新が古い" in text and "参考値" in text
    assert "1時間前" in text


def test_missing_status_still_states_paper_without_inventing_money(tmp_path):
    profile = game_profile.build("paper-view", state_dir=tmp_path / "empty", now=time.time())
    text = profile["game_state"]
    assert "PAPER暗号資産コーナー" in text and "PAPER取引" in text
    assert "損益ステータスはまだ取得できていない" in text
    assert "推測で金額を作らない" in text
    assert "円" not in text.replace("金額", "")


@pytest.mark.parametrize("mode_value", ["live", "stock", None, 1])
def test_non_paper_status_is_not_used(tmp_path, mode_value):
    status = _status(mode=mode_value)
    text = game_profile.build("paper-view", state_dir=_write(tmp_path / "s", status))["game_state"]
    assert "損益ステータスはまだ取得できていない" in text and "+123円" not in text


@pytest.mark.parametrize("value", ["{broken", "[]", "null", '"text"'])
def test_malformed_status_is_not_used(tmp_path, value):
    text = game_profile.build("paper-view", state_dir=_write(tmp_path / "s", value))["game_state"]
    assert "損益ステータスはまだ取得できていない" in text


def test_absurd_numbers_and_timestamps_do_not_crash_or_claim_a_number(tmp_path):
    status = _status()
    status["snapshot_generated_at"] = 1e30
    status["performance_summary"] = {
        **status["performance_summary"],
        "as_of": -1,
        "cumulative_pnl_jpy": "1e100",
        "equity_jpy": "1e100",
        "interval_realized_pnl_jpy": "not-a-number",
    }
    text = game_profile.build("paper-view", state_dir=_write(tmp_path / "s", status))["game_state"]
    assert "元手より増えている" not in text
    assert "1e100" not in text and "+100" not in text
    assert "区間損益" not in text


def test_symlinked_and_oversized_status_are_rejected(tmp_path):
    real = tmp_path / "real.json"
    real.write_text(json.dumps(_status()), encoding="utf-8")
    root = tmp_path / "state"
    (root / "trading").mkdir(parents=True)
    os.symlink(real, root / "trading" / "status.json")
    assert "取得できていない" in game_profile.build("paper-view", state_dir=root)["game_state"]

    big = tmp_path / "big"
    (big / "trading").mkdir(parents=True)
    (big / "trading" / "status.json").write_text("{" + " " * (300 * 1024) + "}", encoding="utf-8")
    assert "取得できていない" in game_profile.build("paper-view", state_dir=big)["game_state"]


def test_profile_slots_are_bounded_and_shell_safe(tmp_path):
    profile = _profile(tmp_path)
    for key in ("game_state", "ui_memo", "game_rules", "game_rules_ja"):
        text = profile[key]
        assert len(text) <= paper_corner_profile.FIELD_LIMIT
        assert not any(ch in text for ch in "$`\\<>\x7f"), key
    assert len(profile["ops_lines"]) <= 16
    assert all(len(line) <= 600 for line in profile["ops_lines"])
    assert profile["schema"] == 1 and profile["game"] == "paper-view"
    assert profile["persona_replace"]
    assert profile["ui_memo"] and "PAPER" in profile["ui_memo"]
    assert "PAPER" in profile["game_rules"] and "PAPER" in profile["game_rules_ja"]
    assert "PAPER" in profile["ops_lines"][0]
    assert "このコーナーの話ではない" in "".join(profile["ops_lines"])


def test_paper_rules_do_not_reuse_the_soviet_game_rules(tmp_path):
    profile = _profile(tmp_path)
    assert "16 countries" not in profile["game_rules"]
    assert "アルメニア" not in profile["game_rules_ja"]
    assert "virtual funds" in profile["game_rules"]
    assert "仮想資金" in profile["game_rules_ja"]


def test_provider_uses_the_paper_corner_status_for_its_game_state(tmp_path):
    root = tmp_path / "state"
    (root / "trading").mkdir(parents=True)
    (root / "trading" / "status.json").write_text(json.dumps(_status()), encoding="utf-8")
    canonical = root / "game_switch.json"
    canonical.write_text(json.dumps({
        "phase": "ready",
        "active": {"game": "paper-view"},
    }), encoding="utf-8")
    env = {
        "DOCICH_GAME_SWITCH_CANONICAL_FILE": str(canonical),
    }
    context = SorenGameContextProvider(root, env).build(host_mode="main")
    assert "累積損益 +123円で、元手より増えている" in context.game_state_context
    assert "PAPER取引" in context.game_state_context
    assert "paper-view" in context.comment_ops_context
    assert "PAPER暗号資産コーナー" in context.comment_ops_context


def test_launcher_prints_the_paper_profile_from_the_state_dir(tmp_path):
    root = tmp_path / "state"
    _write(root, _status())
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "DOCICH_STATE_DIR", "DOCICH_GAME_SWITCH_CANONICAL_FILE")}
    env["DOCICH_STATE_DIR"] = str(root)
    result = subprocess.run(
        [str(LAUNCHER), "paper-view"], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    profile = json.loads(result.stdout)
    assert profile["game"] == "paper-view"
    assert "累積損益 +123円で、元手より増えている" in profile["game_state"]
    assert "PAPER" in profile["game_rules"]


def test_launcher_resolves_the_state_dir_from_the_canonical_file(tmp_path):
    root = tmp_path / "state"
    _write(root, _status())
    canonical = root / "game_switch.json"
    canonical.write_text(json.dumps({"phase": "ready", "active": {"game": "paper-view"}}),
                          encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "DOCICH_STATE_DIR", "DOCICH_GAME_SWITCH_CANONICAL_FILE")}
    env["DOCICH_GAME_SWITCH_CANONICAL_FILE"] = str(canonical)
    result = subprocess.run(
        [str(LAUNCHER), "paper-view"], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "累積損益 +123円" in json.loads(result.stdout)["game_state"]


def test_general_question_prompt_carries_the_measured_paper_facts(tmp_path):
    """The category used for a short money question must contain the memo."""
    from docich.comment import prompt

    root = tmp_path / "state"
    _write(root, _status())
    memo = game_profile.build("paper-view", state_dir=root)["game_state"]
    context = prompt.with_defaults({
        "_comment_persona": "persona",
        "current_time": "22:30",
        "time_period": "夜",
        "game_state_context": memo,
        "comment_ops_context": "(ops)",
    })
    text = prompt.build_prompt(
        "general_question", context,
        comments_block="nimdavirus: お金増えた？",
        classifications="[1] nimdavirus: お金増えた？ -> general_question -> japanese/other",
        gacha_list_file=tmp_path / "gacha.txt",
    )
    assert "PAPER取引" in text
    assert "累積損益 +123円で、元手より増えている" in text
    assert "全体平均=" not in text and "snapshot_score=" not in text


def test_chitchat_prompt_also_carries_the_money_answer(tmp_path):
    """A short reaction without '?' is classified chitchat, whose template has
    no game-state slot. The dynamic screen memo must still carry the P&L."""
    from docich.comment import prompt

    root = tmp_path / "state"
    _write(root, _status())
    profile = game_profile.build("paper-view", state_dir=root)
    context = prompt.with_defaults({
        "_comment_persona": "persona",
        "_comment_ui_memo": profile["ui_memo"],
        "current_time": "22:30",
        "time_period": "夜",
        "comment_ops_context": "(ops)",
    })
    text = prompt.build_prompt(
        "chitchat", context,
        comments_block="nimdavirus: お金増えた",
        classifications="[1] nimdavirus: お金増えた -> chitchat -> japanese/other",
        gacha_list_file=tmp_path / "gacha.txt",
    )
    assert "game_state_context" not in text
    assert "累積損益は+123円で元手より増えている" in text
    assert "PAPER取引" in text


def test_sub_yen_pnl_is_displayed_as_unchanged_not_increased(tmp_path):
    status = _status()
    status["performance_summary"] = {
        **status["performance_summary"],
        "cumulative_pnl_jpy": "0.4",
        "unrealized_pnl_jpy": "0.4",
        "equity_jpy": "10000.4",
    }
    text = _profile(tmp_path, status=status)["game_state"]
    assert "累積損益 0円で、元手より変わっていない" in text
    assert "元手より増えている" not in text


def test_equity_that_does_not_match_capital_plus_pnl_is_not_used(tmp_path):
    status = _status()
    status["performance_summary"] = {
        **status["performance_summary"],
        "cumulative_pnl_jpy": "123.45",
        "equity_jpy": "20000",  # not capital(10000) + cumulative(123.45)
    }
    text = _profile(tmp_path, status=status)["game_state"]
    assert "累積損益は取得できていない" in text
    assert "元手より増えている" not in text
