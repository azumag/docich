"""docich-owned comment output guard / validation (#829 PR-3b): parity with the
legacy chain as production loads it, pinned by tests/fixtures/comment_guard_golden.json
(generated on Linux with DOCICH_BIN set, like the VM). No network, no secrets."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.comment import guard  # noqa: E402
from docich.onair_text import sanitize_onair_text  # noqa: E402

GOLDEN = json.loads((ROOT / "tests/fixtures/comment_guard_golden.json").read_text(encoding="utf-8"))
CASES = GOLDEN["cases"]
IDS = [f"{i}:{c['input'][:18]!r}" for i, c in enumerate(CASES)]
ENV = {}  # COMMENT_MIN_JP_RATIO unset, as in the golden run


def test_golden_is_from_a_real_legacy_run():
    assert len(GOLDEN["provenance"]["soviet_now_commit"]) == 40
    assert len(CASES) >= 48


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_text_stages(case):
    text = case["input"]
    assert guard.strip_worknote_head(text) == case["_comment_strip_worknote_head"]
    assert guard.strip_reasoning_tags(text) == case["_comment_strip_reasoning_tags"]
    assert guard.strip_nonjapanese_head(text, ENV) == case["_comment_strip_nonjapanese_head"]
    assert sanitize_onair_text(text) == case["_sanitize_onair_text"]
    assert guard.guard_model_text(text) == case["_comment_guard_model_text"]
    assert guard.guard_japanese_text(text, ENV) == case["_comment_guard_japanese_text"]
    assert guard.clean_comment_talk(text, False) == case["_clean_comment_talk_0"]
    assert guard.clean_comment_talk(text, True) == case["_clean_comment_talk_1"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_predicates(case):
    text = case["input"]
    assert guard.contains_provider_error_text(text) == (case["_contains_provider_error_text"] == "0")
    assert guard.is_valid_comment_talk(text) == (case["_is_valid_comment_talk"] == "0")
    assert guard.is_valid_generation_candidate(text, ENV) == (case["_comment_is_valid_generation_candidate"] == "0")


def test_the_pinned_worknote_false_positive_is_kept_not_fixed():
    """ai-guard rejects a plain reply saying "確認しますね" in production; the port
    reproduces it. Fixing it is a separate, reviewed change."""
    case = next(c for c in CASES if "確認しますね" in c["input"] and c["input"].startswith("同志"))
    assert case["_comment_guard_japanese_text"] == ""
    assert guard.guard_japanese_text(case["input"], ENV) == ""
    assert not guard.is_valid_generation_candidate(case["input"], ENV)


def test_min_jp_ratio_is_still_tunable():
    text = "mostly english words here あ\n\n同志ボブ、こんにちは。"
    assert guard.strip_nonjapanese_head(text, {"COMMENT_MIN_JP_RATIO": "0.01"}) == text
    assert guard.strip_nonjapanese_head(text, {}) == "同志ボブ、こんにちは。"


def test_golden_regenerates_identically_on_this_linux_host():
    """CI (Ubuntu, submodule checked out): the committed golden must equal a
    fresh run of the legacy, so it can never drift from what production does."""
    sn = ROOT / "games/soviet_now"
    if not (sn / "eloop_lib.sh").is_file() or sys.platform != "linux":
        pytest.skip("needs the soviet_now checkout on Linux (GNU grep, like production)")
    commit = subprocess.check_output(["git", "-C", str(sn), "rev-parse", "HEAD"], text=True).strip()
    if commit != GOLDEN["provenance"]["soviet_now_commit"]:
        pytest.skip("golden pinned to another soviet_now commit; the drift guard above covers content")
    out = Path(subprocess.check_output(["mktemp"], text=True).strip())
    subprocess.run([sys.executable, str(ROOT / "scripts/golden/comment_guard_golden.py"),
                    "--soviet-now", str(sn), "--out", str(out)], check=True, capture_output=True)
    assert json.loads(out.read_text(encoding="utf-8")) == GOLDEN
