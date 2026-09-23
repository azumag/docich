"""docich-owned comment prompt layer (#829 PR-3a): byte parity with the legacy
soviet_now shell, pinned by tests/fixtures/comment_prompt_golden.json.
No network, no secrets, no game checkout needed (the drift guard skips)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.comment import prompt  # noqa: E402

GOLDEN = json.loads((ROOT / "tests/fixtures/comment_prompt_golden.json").read_text(encoding="utf-8"))
SOVIET_NOW = ROOT / "games/soviet_now"


def test_golden_is_from_a_real_legacy_run():
    assert len(GOLDEN["provenance"]["soviet_now_commit"]) == 40
    assert len(GOLDEN["prompts"]) == 30
    assert sum("prompt" in case for case in GOLDEN["prompts"]) == 2


@pytest.mark.parametrize("case", GOLDEN["prompts"], ids=[f"{c['mode']}-{c['category']}" for c in GOLDEN["prompts"]])
def test_full_prompt_is_byte_identical_to_the_legacy_shell(case, tmp_path):
    ctx = GOLDEN["contexts"][case["mode"]]
    built = prompt.build_prompt(case["category"], ctx, comments_block=GOLDEN["comments_block"],
                                classifications=GOLDEN["classifications"],
                                gacha_list_file=tmp_path / "gacha.txt")
    rendered = prompt.with_reply_contract(built)
    if "prompt" in case:
        assert rendered == case["prompt"]
    raw = rendered.encode("utf-8")
    assert (len(raw), hashlib.sha256(raw).hexdigest()) == (case["bytes"], case["sha256"])


def test_mode_assets_equal_the_legacy_context_values():
    for mode in prompt.MODES:
        expected = GOLDEN["contexts"][mode]
        for name, value in prompt.mode_assets(mode).items():
            assert value == expected[name], (mode, name)


@pytest.mark.parametrize("case", GOLDEN["sanitize"])
def test_sanitize_context(case):
    assert prompt.sanitize_context(case["input"]) == case["output"]


@pytest.mark.parametrize("case", GOLDEN["prompt_defaults"])
def test_defaults_resolution(case):
    resolved = prompt.with_defaults(case["input"])
    assert {name: resolved[name] for name in case["output"]} == case["output"]


@pytest.mark.parametrize("case", GOLDEN["dominant_category"])
def test_dominant_category(case):
    expected = case["output"].rstrip("\n") if case["rc"] == 0 else None
    assert prompt.dominant_category(case["classification"]) == expected


def test_dominant_category_rejects_malformed_rows_like_the_legacy_exit_1():
    assert prompt.dominant_category(["not-a-dict"]) is None
    assert prompt.dominant_category("[]") is None


@pytest.mark.parametrize("case", GOLDEN["english_count"])
def test_english_count(case):
    assert str(prompt.english_count(case["classification"])) == case["output"].strip()


@pytest.mark.parametrize("case", GOLDEN["formatted_classifications"])
def test_format_classifications(case):
    assert prompt.format_classifications(case["classification"], case["batch_lines"]) == case["output"].rstrip("\n")


def test_format_classifications_failure_is_empty_as_a_whole():
    assert prompt.format_classifications([{"index": 1}, "bad"], ["u: c"]) == ""


@pytest.mark.parametrize("case", GOLDEN["gacha_completion_note"])
def test_gacha_completion_note_and_its_list_file(case, tmp_path):
    list_file = tmp_path / "gacha.txt"
    list_file.write_text(case["known"], encoding="utf-8")
    assert prompt.gacha_completion_note(case["block"], list_file) == case["note"]
    assert list_file.read_text(encoding="utf-8") == case["known_after"]


def test_time_period_for_every_hour():
    assert {f"{h:02d}": prompt.time_period(h) for h in range(24)} == GOLDEN["time_period"]


def test_retry_prompt_appends_the_legacy_addendum_and_mode_policy():
    assert prompt.retry_prompt("P", "main") == "P" + GOLDEN["retry_addendum_heredoc"]
    assert prompt.retry_prompt("P", "soren91") == ("P" + GOLDEN["retry_addendum_heredoc"]
                                                   + GOLDEN["soren91_retry_length_policy"] + "\n")


def test_envsubst_semantics():
    names = ("a", "b_1")
    assert prompt.envsubst("$a ${a} $b_1x ${b_1} $c ${c} $$a ${a", names, {"a": "$b_1", "b_1": "B"}) \
        == "$b_1 $b_1 $b_1x B $c ${c} $$b_1 ${a"
    assert prompt.envsubst("$aの", names, {"a": "A"}) == "Aの"  # ASCII identifiers only
    assert prompt.envsubst("[$a]", names, {}) == "[]"  # listed but unset -> empty


def test_gacha_list_file_default_matches_the_legacy_resolution():
    assert prompt.gacha_list_file({}) == Path("tmp/history/gacha_completed_users.txt")
    assert prompt.gacha_list_file({"TMP_HISTORY_DIR": "h"}) == Path("h/gacha_completed_users.txt")
    assert prompt.gacha_list_file({"GACHA_COMPLETED_USERS_FILE": "g.txt", "TMP_HISTORY_DIR": "h"}) == Path("g.txt")


# ------------------------------------------------------------------ drift guard

LEGACY_ASSETS = [p.name for p in prompt.PROMPTS_DIR.glob("*.md")
                 if p.name.startswith(("comment_template", "comment_response_", "comment_persona_",
                                       "comment_ui_memo_", "comment_channel_intro_", "speech_vocabulary_rule"))]


@pytest.mark.parametrize("name", sorted(LEGACY_ASSETS))
def test_legacy_copy_has_not_drifted_while_soviet_now_still_serves_replies(name):
    """Until the comment cutover, soviet_now still renders live replies from its
    own copies: a prompt edit there must be mirrored here (and vice versa)."""
    legacy = SOVIET_NOW / "prompts" / name
    if not legacy.is_file():
        pytest.skip("games/soviet_now not checked out")
    assert (prompt.PROMPTS_DIR / name).read_bytes() == legacy.read_bytes()


def test_extracted_heredocs_have_not_drifted():
    comment_sh = SOVIET_NOW / "broadcast/comment.sh"
    if not comment_sh.is_file():
        pytest.skip("games/soviet_now not checked out")
    for source, asset, marker in (("broadcast/comment.sh", "comment_reply_contract.md", "COMMENTREPLYCONTRACT"),
                                  ("broadcast/comment.sh", "comment_retry_addendum.md", "RETRYCOMMENT"),
                                  ("broadcast/comment_runtime_policy.sh", "comment_reply_policy_contract.md",
                                   "COMMENTRUNTIMEPOLICY")):
        text = (SOVIET_NOW / source).read_text(encoding="utf-8")
        start = text.index(f"<<'{marker}'\n") + len(marker) + 5
        assert text[start:text.index(f"{marker}\n", start)] == prompt.asset(asset), asset


def test_only_the_reply_contract_is_rewrapped_by_later_legacy_layers():
    """The golden loads eloop_lib.sh like production; if another layer starts
    wrapping a prompt function, the port (and this list) must be revisited."""
    if not (SOVIET_NOW / "eloop_lib.sh").is_file():
        pytest.skip("games/soviet_now not checked out")
    import subprocess
    wrapped = subprocess.run(
        ["git", "-C", str(SOVIET_NOW), "grep", "-l", "-E",
         r"^(_build_category_prompt|_append_comment_reply_contract|_build_gacha_completion_note|"
         r"_sanitize_comment_prompt_context|_comment_classification_english_count)\(\)", "--", "*.sh", ":!tests"],
        capture_output=True, text=True).stdout.split()
    assert sorted(wrapped) == ["broadcast/comment.sh", "broadcast/comment_runtime_policy.sh"]
