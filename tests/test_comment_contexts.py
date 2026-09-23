"""Game-independent native comment contexts (#829 PR-3d)."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.comment import contexts  # noqa: E402
from docich.comment.sorengame_context import SorenGameContextProvider  # noqa: E402

GOLDEN = json.loads((ROOT / "tests/fixtures/comment_context_golden.json").read_text(encoding="utf-8"))


def _input_root(tmp_path: Path) -> Path:
    root = tmp_path / "fixture"
    for relative, content in GOLDEN["inputs"]["files"].items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.replace("@ROOT@", str(root)), encoding="utf-8")
    for relative, timestamp in GOLDEN["inputs"]["mtimes"].items():
        os.utime(root / relative, (timestamp, timestamp))
    return root


def _provider_env(root: Path) -> dict[str, str]:
    def p(path: str) -> str:
        return str(root / path)

    return {
        "COMMENT_OPS_CONTEXT_MAX_CHARS": "1200",
        "COMMENT_OPS_BRIEF_ITEMS": "2",
        "COMMENT_CELEBRATION_HISTORY_ITEMS": "2",
        "MIN_GAMES_BEFORE_IMPROVE": "12",
        "TMP_HISTORY_DIR": "history",
        # The legacy builder ignores SCORE_HISTORY_FILE and reads this fixed
        # filename from its root. Keep a decoy override to pin that behavior.
        "SCORE_HISTORY_FILE": p("wrong_scores.txt"),
        "RUSSIA_CREATION_HISTORY_FILE": p("russia.tsv"),
        "SOVIET_CREATION_HISTORY_FILE": p("soviet.tsv"),
        "SOVIET_CREATION_ARCHIVE_FILE": p("archive.tsv"),
        "AB_STATE_FILE": p("ab.json"),
        "PREDICTION_WORKER_PAUSED_FILE": p("prediction.paused"),
        "DOCICH_GAME_SWITCH_CANONICAL_FILE": p("canonical.json"),
        "SOREN_GAME_LIFECYCLE_DIR": p("lifecycle"),
        "SOREN_GAME_STATE_FILE": p("game_state.json"),
        "SOREN_GAME_COUNT_FILE": p("game_count.txt"),
        "SOREN_IMPROVE_PAUSED_FILE": p("improve.paused"),
        "CODEX_WORK_OVERLAY_STATE_FILE": p("work.json"),
        "IMPROVE_STATE_FILE": p("improve.json"),
        "COMMENT_OPS_BRIEF_FILE": p("ops_brief.md"),
        "SOREN91_ENABLED": "0",
        "SOREN91_DAILY_ENABLED": "1",
    }


def test_game_independent_context_helpers_match_legacy_golden(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    root = _input_root(tmp_path)
    history = root / "history"
    current = root / "comments/comment_current.txt"
    expected = GOLDEN["outputs"]

    assert contexts.format_batch_context("alice: one\nbob: two\nalice: three\n") == expected["batch"]
    assert contexts.recent_spoken_context(
        history, history_limit=3, total_limit=500, item_limit=100,
        current_file=str(current), mode="main", include_current=True,
    ) == expected["recent_main"]
    assert contexts.recent_spoken_context(
        history, history_limit=3, total_limit=500, item_limit=100,
        current_file=str(current), mode="soren91", include_current=False,
    ) == expected["recent_soren91"]
    assert contexts.followup_hints(
        root / "batch.txt", history, history_limit=3,
        current_file=str(current), mode="main", include_current=True,
    ) == expected["followup_main"]
    assert contexts.followup_hints(
        root / "batch.txt", history, history_limit=3,
        current_file=str(current), mode="soren91", include_current=False,
    ) == expected["followup_soren91"]
    assert contexts.advice_context_tail(root / "advice.txt", 4) == expected["advice_tail"]
    extracted = contexts.extract_structured_advice(root / "advice_comments.txt", fallback_mode="main")
    assert "\n".join(item.as_legacy_row() for item in extracted) == expected["structured_advice"]
    past_topics = contexts.past_topics_block(root / "radio_topics.txt", limit=3)
    assert past_topics == expected["past_topics"]
    assert "RAW_TOPIC_PAYLOAD" not in past_topics


def test_typed_soren_provider_matches_legacy_context_golden(tmp_path):
    root = _input_root(tmp_path)
    env = _provider_env(root)
    env["OPENAI_API_KEY"] = "synthetic-secret-marker-never-retain"
    provider = SorenGameContextProvider(root, env)
    assert isinstance(provider, contexts.GameContextProvider)
    assert "OPENAI_API_KEY" not in provider.env
    main = provider.build(host_mode="main")
    soren91 = provider.build(host_mode="soren91")
    assert main == contexts.GameContext(
        game_state_context=GOLDEN["outputs"]["game_main"],
        comment_ops_context=GOLDEN["outputs"]["ops_main"],
        celebration_history_context=GOLDEN["outputs"]["celebration"],
    )
    assert "synthetic-secret-marker" not in main.comment_ops_context
    assert soren91 == contexts.GameContext(
        game_state_context=(
            "- いまのメイン画面はソ連ゲーム91(対戦版/メリケンAI)です。このゲームにはスコアの概念がなく、"
            "順位(何人抜きで何位だったか)で振り返ります。本編(ソレンゲーム)のスコア・建国統計は、"
            "いまの画面のゲームのものではないので、91のスコアとして読み上げないこと。"
        ),
        comment_ops_context=GOLDEN["outputs"]["ops_soren91"],
        celebration_history_context=GOLDEN["outputs"]["celebration"],
    )

    # The stopped handover branch must report the saved final score, while a
    # user-owned improve pause remains explicit and is never auto-cleared.
    (root / "canonical.json").write_text("{}\n", encoding="utf-8")
    (root / "lifecycle/ack.json").write_text('{"status":"stopped"}\n', encoding="utf-8")
    (root / "improve.paused").touch()
    stopped = provider.build(host_mode="main")
    assert stopped.comment_ops_context == GOLDEN["outputs"]["ops_stopped"]


def test_provider_protocol_normalizes_mode_and_calls_once():
    source = ast.parse((ROOT / "src/docich/comment/contexts.py").read_text(encoding="utf-8"))
    imported_modules = set()
    for node in ast.walk(source):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
    assert not any("sorengame_context" in module for module in imported_modules)

    class Provider:
        def __init__(self):
            self.modes = []

        def build(self, *, host_mode):
            self.modes.append(host_mode)
            return contexts.GameContext(game_state_context=host_mode)

    provider = Provider()
    assert isinstance(provider, contexts.GameContextProvider)
    assert contexts.build_game_context(provider, host_mode="other") == contexts.GameContext(
        game_state_context="main"
    )
    assert provider.modes == ["main"]


def test_golden_regenerates_against_checked_out_legacy_on_linux(tmp_path):
    sn = ROOT / "games/soviet_now"
    if sys.platform != "linux" or not (sn / "eloop_lib.sh").is_file():
        pytest.skip("requires the pinned soviet_now checkout and Linux legacy shell tools")
    output = tmp_path / "comment_context_golden.json"
    subprocess.run([
        sys.executable, str(ROOT / "scripts/golden/comment_context_golden.py"),
        "--soviet-now", str(sn), "--out", str(output),
    ], check=True, capture_output=True, text=True)
    fresh = json.loads(output.read_text(encoding="utf-8"))
    pinned = json.loads((ROOT / "tests/fixtures/comment_context_golden.json").read_text(encoding="utf-8"))
    fresh["provenance"].pop("soviet_now_commit", None)
    fresh["provenance"].pop("platform", None)
    pinned["provenance"].pop("soviet_now_commit", None)
    pinned["provenance"].pop("platform", None)
    assert fresh == pinned
