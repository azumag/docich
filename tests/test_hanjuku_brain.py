"""Unit tests for brains/hanjuku/brain.py (docs/hanjuku_brain.md).

Loads brain.py via importlib (it lives outside the docich package, under
brains/) and exercises its functions directly. No network and no X server are
required. All state-directory I/O goes through tempfile.TemporaryDirectory()
so the repository's real run/ tree is never touched.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
BRAIN_PATH = REPO_ROOT / "brains" / "hanjuku" / "brain.py"


def _load_brain_module():
    spec = importlib.util.spec_from_file_location("docich_hanjuku_brain_under_test", BRAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses (brain.py が `from __future__ import annotations` を使うため) は
    # クラス定義時に sys.modules[cls.__module__] を引くので、exec_module 前に登録しておく。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


brain = _load_brain_module()


def _obs(**overrides) -> dict:
    base = {
        "game": "hanjuku-hero",
        "title": "半熟英雄 (SFC)",
        "adapter": "retroarch",
        "ts": 1755150000.0,
        "kind": "screenshot",
        "text": None,
        "screenshot": None,
        "meta": {},
    }
    base.update(overrides)
    return base


class HanjukuBrainTestCase(unittest.TestCase):
    """Base class providing an isolated tmpdir repo_root/state_dir per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmp.name)
        self.state_dir = self.repo_root / "run" / "brain" / "hanjuku"

    def tearDown(self):
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# 知識選択 (stage -> チャートパス、欠損時の代替文)
# ---------------------------------------------------------------------------


class TestClampStage(unittest.TestCase):
    def test_within_range_unchanged(self):
        self.assertEqual(brain.clamp_stage(5), 5)

    def test_clamps_above_max(self):
        self.assertEqual(brain.clamp_stage(99), 12)

    def test_clamps_below_min(self):
        self.assertEqual(brain.clamp_stage(0), 1)
        self.assertEqual(brain.clamp_stage(-3), 1)

    def test_non_int_defaults_to_one(self):
        self.assertEqual(brain.clamp_stage("stage-3"), 1)
        self.assertEqual(brain.clamp_stage(None), 1)

    def test_numeric_string_is_accepted(self):
        self.assertEqual(brain.clamp_stage("7"), 7)


class TestKnowledgeFiles(unittest.TestCase):
    def test_paths_include_readme_overview_and_stage_chart(self):
        paths = brain.knowledge_files(5)
        self.assertEqual(paths["README.md"], brain.KNOWLEDGE_ROOT / "README.md")
        self.assertEqual(paths["charts/overview.md"], brain.KNOWLEDGE_ROOT / "charts" / "overview.md")
        self.assertEqual(paths["charts/5.md"], brain.KNOWLEDGE_ROOT / "charts" / "5.md")

    def test_stage_changes_which_chart_is_selected(self):
        paths_stage1 = brain.knowledge_files(1)
        paths_stage9 = brain.knowledge_files(9)
        self.assertIn("charts/1.md", paths_stage1)
        self.assertIn("charts/9.md", paths_stage9)
        self.assertNotIn("charts/9.md", paths_stage1)


class TestLoadKnowledge(HanjukuBrainTestCase):
    def test_missing_submodule_falls_back_without_crashing(self):
        # tmpdir には games/hanjuku-sfc-speedrun が存在しない (サブモジュール未取得を模す)
        knowledge = brain.load_knowledge(self.repo_root, 1)
        self.assertEqual(set(knowledge), {"README.md", "charts/overview.md", "charts/1.md"})
        for label, content in knowledge.items():
            self.assertTrue(content.startswith("知識ファイルなし: "), (label, content))

    def test_reads_real_submodule_files_when_present(self):
        # brain.REPO_ROOT は brain.py 自身の実パスから解決される実リポジトリ (このリポジトリ)
        knowledge = brain.load_knowledge(brain.REPO_ROOT, 1)
        self.assertFalse(knowledge["README.md"].startswith("知識ファイルなし: "))
        self.assertIn("半熟英雄", knowledge["README.md"])
        self.assertFalse(knowledge["charts/overview.md"].startswith("知識ファイルなし: "))
        self.assertFalse(knowledge["charts/1.md"].startswith("知識ファイルなし: "))

    def test_partial_availability_mixes_real_content_and_fallback(self):
        # overview.md だけ用意し、README.md と charts/1.md は欠損のまま
        base = self.repo_root / brain.KNOWLEDGE_ROOT / "charts"
        base.mkdir(parents=True)
        (base / "overview.md").write_text("概要テキスト", encoding="utf-8")
        knowledge = brain.load_knowledge(self.repo_root, 1)
        self.assertEqual(knowledge["charts/overview.md"], "概要テキスト")
        self.assertTrue(knowledge["README.md"].startswith("知識ファイルなし: "))
        self.assertTrue(knowledge["charts/1.md"].startswith("知識ファイルなし: "))


# ---------------------------------------------------------------------------
# スクリーンショットパスの解決
# ---------------------------------------------------------------------------


class TestResolveScreenshot(HanjukuBrainTestCase):
    def test_none_returns_none(self):
        self.assertIsNone(brain.resolve_screenshot(self.repo_root, None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(brain.resolve_screenshot(self.repo_root, ""))

    def test_missing_file_returns_none(self):
        self.assertIsNone(brain.resolve_screenshot(self.repo_root, "run/screenshots/latest.png"))

    def test_relative_path_resolved_against_repo_root(self):
        shot = self.repo_root / "run" / "screenshots" / "latest.png"
        shot.parent.mkdir(parents=True)
        shot.write_bytes(b"\x89PNG\r\n")
        resolved = brain.resolve_screenshot(self.repo_root, "run/screenshots/latest.png")
        self.assertEqual(resolved, shot)

    def test_absolute_path_used_as_is(self):
        shot = self.repo_root / "abs.png"
        shot.write_bytes(b"\x89PNG\r\n")
        resolved = brain.resolve_screenshot(self.repo_root, str(shot))
        self.assertEqual(resolved, shot)


# ---------------------------------------------------------------------------
# build_prompt: notes 末尾・state・スクリーンショットが本文に入る
# ---------------------------------------------------------------------------


class TestBuildPrompt(unittest.TestCase):
    def setUp(self):
        self.knowledge = {"README.md": "卵落ち判定の説明", "charts/overview.md": "概要"}

    def test_notes_tail_is_included(self):
        text = brain.build_prompt(_obs(), self.knowledge, {"stage": 1}, "前回はスペンソニアへ向かった")
        self.assertIn("前回はスペンソニアへ向かった", text)

    def test_state_json_is_included(self):
        text = brain.build_prompt(_obs(), self.knowledge, {"stage": 4, "hp": 12}, "")
        self.assertIn(json.dumps({"stage": 4, "hp": 12}, ensure_ascii=False), text)

    def test_knowledge_labels_and_content_are_included(self):
        text = brain.build_prompt(_obs(), self.knowledge, {}, "")
        self.assertIn("README.md", text)
        self.assertIn("卵落ち判定の説明", text)
        self.assertIn("charts/overview.md", text)
        self.assertIn("概要", text)

    def test_empty_notes_tail_has_placeholder(self):
        text = brain.build_prompt(_obs(), self.knowledge, {}, "")
        self.assertIn("まだメモはありません", text)

    def test_screenshot_present_mentions_absolute_path_and_read_tool(self):
        text = brain.build_prompt(_obs(screenshot="/tmp/x/latest.png"), self.knowledge, {}, "")
        self.assertIn("/tmp/x/latest.png", text)
        self.assertIn("Read ツール", text)

    def test_screenshot_absent_says_none(self):
        text = brain.build_prompt(_obs(screenshot=None), self.knowledge, {}, "")
        self.assertIn("スクリーンショットなし", text)

    def test_response_format_example_is_included(self):
        text = brain.build_prompt(_obs(), self.knowledge, {}, "")
        self.assertIn('"note"', text)
        self.assertIn('"state_patch"', text)
        self.assertIn('"actions"', text)


class TestBuildApiContent(HanjukuBrainTestCase):
    def test_with_screenshot_has_image_block_then_text_block(self):
        shot = self.repo_root / "shot.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        blocks = brain.build_api_content("PROMPT", shot)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["type"], "image")
        self.assertEqual(blocks[0]["source"]["media_type"], "image/png")
        self.assertEqual(blocks[0]["source"]["type"], "base64")
        self.assertEqual(blocks[1], {"type": "text", "text": "PROMPT"})

    def test_without_screenshot_has_only_text_block(self):
        blocks = brain.build_api_content("PROMPT", None)
        self.assertEqual(blocks, [{"type": "text", "text": "PROMPT"}])


# ---------------------------------------------------------------------------
# 応答解析: 素の JSON / フェンス付き / note 欠落 / JSON 壊れ
# ---------------------------------------------------------------------------


class TestParseLlmResponse(unittest.TestCase):
    def test_plain_json(self):
        raw = '{"note": "ok", "actions": [{"type": "wait", "ms": 1}]}'
        parsed = brain.parse_llm_response(raw)
        self.assertEqual(parsed["note"], "ok")
        self.assertEqual(parsed["actions"], [{"type": "wait", "ms": 1}])
        self.assertIsNone(parsed["state_patch"])

    def test_markdown_fenced_json(self):
        raw = '```json\n{"note": "ok", "actions": []}\n```'
        parsed = brain.parse_llm_response(raw)
        self.assertEqual(parsed["note"], "ok")
        self.assertEqual(parsed["actions"], [])

    def test_fenced_json_with_surrounding_prose(self):
        raw = 'わかりました、次のように動きます:\n```json\n{"note": "ok", "actions": []}\n```\n以上です。'
        parsed = brain.parse_llm_response(raw)
        self.assertEqual(parsed["note"], "ok")

    def test_state_patch_is_carried_through(self):
        raw = '{"note": "ok", "state_patch": {"stage": 2}, "actions": []}'
        parsed = brain.parse_llm_response(raw)
        self.assertEqual(parsed["state_patch"], {"stage": 2})

    def test_missing_note_raises(self):
        with self.assertRaises(brain.BrainResponseError):
            brain.parse_llm_response('{"actions": []}')

    def test_empty_note_raises(self):
        with self.assertRaises(brain.BrainResponseError):
            brain.parse_llm_response('{"note": "", "actions": []}')

    def test_missing_actions_raises(self):
        with self.assertRaises(brain.BrainResponseError):
            brain.parse_llm_response('{"note": "ok"}')

    def test_broken_json_raises(self):
        with self.assertRaises(brain.BrainResponseError):
            brain.parse_llm_response('{"note": "ok", "actions": [')

    def test_no_json_at_all_raises(self):
        with self.assertRaises(brain.BrainResponseError):
            brain.parse_llm_response("画面が読めませんでした")

    def test_non_object_state_patch_raises(self):
        with self.assertRaises(brain.BrainResponseError):
            brain.parse_llm_response('{"note": "ok", "state_patch": [1,2], "actions": []}')

    def test_top_level_array_raises(self):
        with self.assertRaises(brain.BrainResponseError):
            brain.parse_llm_response('[{"note": "ok", "actions": []}]')


# ---------------------------------------------------------------------------
# actions のサニタイズ: 上限切り捨て + hold_ms/wait 丸め
# ---------------------------------------------------------------------------


class TestSanitizeActions(unittest.TestCase):
    def test_truncates_to_max_actions(self):
        raw = [{"type": "wait", "ms": i} for i in range(20)]
        sanitized, truncated = brain.sanitize_actions(raw, 8)
        self.assertEqual(len(sanitized), 8)
        self.assertTrue(truncated)
        self.assertEqual([a["ms"] for a in sanitized], list(range(8)))

    def test_no_truncation_flag_when_within_limit(self):
        raw = [{"type": "wait", "ms": 1}]
        sanitized, truncated = brain.sanitize_actions(raw, 8)
        self.assertEqual(len(sanitized), 1)
        self.assertFalse(truncated)

    def test_exactly_at_limit_is_not_truncated(self):
        raw = [{"type": "wait", "ms": i} for i in range(8)]
        sanitized, truncated = brain.sanitize_actions(raw, 8)
        self.assertEqual(len(sanitized), 8)
        self.assertFalse(truncated)

    def test_pad_hold_ms_clamped_to_2000(self):
        raw = [{"type": "pad", "buttons": ["a"], "hold_ms": 5000}]
        sanitized, _ = brain.sanitize_actions(raw, 8)
        self.assertEqual(sanitized[0]["hold_ms"], 2000)

    def test_key_hold_ms_clamped_to_2000(self):
        raw = [{"type": "key", "keys": ["Up"], "hold_ms": 999999}]
        sanitized, _ = brain.sanitize_actions(raw, 8)
        self.assertEqual(sanitized[0]["hold_ms"], 2000)

    def test_hold_ms_within_bounds_is_untouched(self):
        raw = [{"type": "pad", "buttons": ["a"], "hold_ms": 120}]
        sanitized, _ = brain.sanitize_actions(raw, 8)
        self.assertEqual(sanitized[0]["hold_ms"], 120)

    def test_wait_ms_clamped_to_10000(self):
        raw = [{"type": "wait", "ms": 999999}]
        sanitized, _ = brain.sanitize_actions(raw, 8)
        self.assertEqual(sanitized[0]["ms"], 10000)

    def test_wait_ms_within_bounds_is_untouched(self):
        raw = [{"type": "wait", "ms": 300}]
        sanitized, _ = brain.sanitize_actions(raw, 8)
        self.assertEqual(sanitized[0]["ms"], 300)

    def test_bool_hold_ms_is_not_treated_as_int(self):
        # isinstance(True, int) は True になるため明示的に除外していることの確認
        raw = [{"type": "pad", "buttons": ["a"], "hold_ms": True}]
        sanitized, _ = brain.sanitize_actions(raw, 8)
        self.assertIs(sanitized[0]["hold_ms"], True)

    def test_non_dict_items_pass_through_untouched(self):
        raw = ["not-a-dict"]
        sanitized, truncated = brain.sanitize_actions(raw, 8)
        self.assertEqual(sanitized, ["not-a-dict"])
        self.assertFalse(truncated)


# ---------------------------------------------------------------------------
# notes.md への追記 + 8000 字切り詰め
# ---------------------------------------------------------------------------


class TestNotesAppend(HanjukuBrainTestCase):
    def test_append_creates_file_with_iso8601_heading(self):
        self.state_dir.mkdir(parents=True)
        brain.append_note(self.state_dir, "スペンソニアへ移動中")
        text = (self.state_dir / "notes.md").read_text(encoding="utf-8")
        self.assertIn("## ", text)
        self.assertIn("スペンソニアへ移動中", text)
        # ISO8601 のタイムスタンプ見出しであること
        heading = [line for line in text.splitlines() if line.startswith("## ")][0]
        ts_text = heading[3:]
        import datetime as _dt

        _dt.datetime.fromisoformat(ts_text)  # 解析できれば ISO8601 として妥当

    def test_append_accumulates_multiple_notes(self):
        self.state_dir.mkdir(parents=True)
        brain.append_note(self.state_dir, "1つ目のメモ")
        brain.append_note(self.state_dir, "2つ目のメモ")
        text = (self.state_dir / "notes.md").read_text(encoding="utf-8")
        self.assertIn("1つ目のメモ", text)
        self.assertIn("2つ目のメモ", text)

    def test_file_is_truncated_to_tail_8000_chars(self):
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "notes.md").write_text("x" * 9000, encoding="utf-8")
        brain.append_note(self.state_dir, "最新のメモ")
        text = (self.state_dir / "notes.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text), 8000)
        self.assertIn("最新のメモ", text)
        # 末尾側 (最新側) が残り、先頭側が切り落とされていること
        self.assertTrue(text.endswith("最新のメモ\n"))

    def test_load_notes_tail_returns_last_n_chars(self):
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "notes.md").write_text("a" * 100, encoding="utf-8")
        tail = brain.load_notes_tail(self.state_dir, max_chars=10)
        self.assertEqual(tail, "a" * 10)

    def test_load_notes_tail_missing_file_returns_empty_string(self):
        self.assertEqual(brain.load_notes_tail(self.state_dir), "")


# ---------------------------------------------------------------------------
# state_patch の浅いマージ / state.json の読み書き
# ---------------------------------------------------------------------------


class TestMergeStatePatch(unittest.TestCase):
    def test_shallow_merge_overwrites_and_keeps_other_keys(self):
        merged = brain.merge_state_patch({"stage": 1, "gold": 100}, {"stage": 2})
        self.assertEqual(merged, {"stage": 2, "gold": 100})

    def test_none_patch_returns_copy_of_state(self):
        state = {"stage": 1}
        merged = brain.merge_state_patch(state, None)
        self.assertEqual(merged, state)
        self.assertIsNot(merged, state)

    def test_empty_patch_returns_copy_of_state(self):
        state = {"stage": 1}
        merged = brain.merge_state_patch(state, {})
        self.assertEqual(merged, state)

    def test_does_not_mutate_input_state(self):
        state = {"stage": 1}
        brain.merge_state_patch(state, {"stage": 9})
        self.assertEqual(state, {"stage": 1})

    def test_adds_new_keys(self):
        merged = brain.merge_state_patch({"stage": 1}, {"note_count": 3})
        self.assertEqual(merged, {"stage": 1, "note_count": 3})


class TestLoadSaveState(HanjukuBrainTestCase):
    def test_missing_file_returns_empty_dict(self):
        self.assertEqual(brain.load_state(self.state_dir), {})

    def test_roundtrip(self):
        self.state_dir.mkdir(parents=True)
        brain.save_state(self.state_dir, {"stage": 4, "gold": 12})
        self.assertEqual(brain.load_state(self.state_dir), {"stage": 4, "gold": 12})

    def test_corrupt_file_returns_empty_dict(self):
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "state.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(brain.load_state(self.state_dir), {})

    def test_non_object_json_returns_empty_dict(self):
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "state.json").write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(brain.load_state(self.state_dir), {})


class TestEnsureStateDirAndLog(HanjukuBrainTestCase):
    def test_ensure_state_dir_creates_tree(self):
        self.assertFalse(self.state_dir.exists())
        brain.ensure_state_dir(self.state_dir)
        self.assertTrue(self.state_dir.is_dir())

    def test_append_log_writes_one_json_line_with_expected_fields(self):
        brain.ensure_state_dir(self.state_dir)
        brain.append_log(
            self.state_dir, ts=123.0, backend="fake:x", latency_ms=5,
            n_actions=1, truncated=False, error=None,
        )
        lines = (self.state_dir / "brain.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(
            record,
            {"ts": 123.0, "backend": "fake:x", "latency_ms": 5, "n_actions": 1, "truncated": False, "error": None},
        )

    def test_append_log_accumulates_one_line_per_call(self):
        brain.ensure_state_dir(self.state_dir)
        for i in range(3):
            brain.append_log(
                self.state_dir, ts=float(i), backend="fake:x", latency_ms=1,
                n_actions=0, truncated=False, error=None,
            )
        lines = (self.state_dir / "brain.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)


# ---------------------------------------------------------------------------
# fake:<path> バックエンド (.json 固定応答 / .jsonl+cursor の順送り+wrap)
# ---------------------------------------------------------------------------


class TestFakeBackend(HanjukuBrainTestCase):
    def test_json_returns_same_content_every_call(self):
        path = self.repo_root / "resp.json"
        path.write_text('{"note": "ok", "actions": []}', encoding="utf-8")
        cursor_path = self.repo_root / "fake_cursor"
        first = brain.call_fake(str(path), self.repo_root, cursor_path)
        second = brain.call_fake(str(path), self.repo_root, cursor_path)
        self.assertEqual(first, '{"note": "ok", "actions": []}')
        self.assertEqual(first, second)
        self.assertFalse(cursor_path.exists())  # .json はカーソルを使わない

    def test_jsonl_advances_cursor_each_call(self):
        path = self.repo_root / "resp.jsonl"
        path.write_text(
            '{"note": "one", "actions": []}\n{"note": "two", "actions": []}\n', encoding="utf-8"
        )
        cursor_path = self.repo_root / "fake_cursor"
        first = brain.call_fake(str(path), self.repo_root, cursor_path)
        second = brain.call_fake(str(path), self.repo_root, cursor_path)
        self.assertIn('"one"', first)
        self.assertIn('"two"', second)

    def test_jsonl_wraps_around_after_last_line(self):
        path = self.repo_root / "resp.jsonl"
        path.write_text('{"note": "one", "actions": []}\n{"note": "two", "actions": []}\n', encoding="utf-8")
        cursor_path = self.repo_root / "fake_cursor"
        results = [brain.call_fake(str(path), self.repo_root, cursor_path) for _ in range(3)]
        self.assertIn('"one"', results[0])
        self.assertIn('"two"', results[1])
        self.assertIn('"one"', results[2])  # 2行しかないので3回目で先頭へ戻る

    def test_relative_path_resolved_against_repo_root(self):
        (self.repo_root / "fixtures").mkdir()
        (self.repo_root / "fixtures" / "resp.json").write_text('{"note":"ok","actions":[]}', encoding="utf-8")
        out = brain.call_fake("fixtures/resp.json", self.repo_root, self.repo_root / "fake_cursor")
        self.assertEqual(out, '{"note":"ok","actions":[]}')

    def test_missing_file_raises_brain_llm_error(self):
        with self.assertRaises(brain.BrainLLMError):
            brain.call_fake("does/not/exist.json", self.repo_root, self.repo_root / "fake_cursor")

    def test_unknown_extension_raises_brain_llm_error(self):
        path = self.repo_root / "resp.txt"
        path.write_text("hello", encoding="utf-8")
        with self.assertRaises(brain.BrainLLMError):
            brain.call_fake(str(path), self.repo_root, self.repo_root / "fake_cursor")

    def test_empty_jsonl_raises_brain_llm_error(self):
        path = self.repo_root / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        with self.assertRaises(brain.BrainLLMError):
            brain.call_fake(str(path), self.repo_root, self.repo_root / "fake_cursor")

    def test_uses_shipped_fixture_and_matches_expected_actions(self):
        # tests/fixtures/hanjuku_fake_brain.jsonl の実物を使い、送出用アクション JSON まで確認する
        fixture = REPO_ROOT / "tests" / "fixtures" / "hanjuku_fake_brain.jsonl"
        cursor_path = self.repo_root / "fake_cursor"
        first = json.loads(brain.call_fake(str(fixture), self.repo_root, cursor_path))
        second = json.loads(brain.call_fake(str(fixture), self.repo_root, cursor_path))
        self.assertEqual(first["actions"], [{"type": "pad", "buttons": ["down"], "hold_ms": 150}])
        self.assertEqual(second["actions"], [{"type": "pad", "buttons": ["up"], "hold_ms": 150}])


# ---------------------------------------------------------------------------
# claude-cli バックエンド: argv 構築 (env 上書き含む) と subprocess.run 経由の呼び出し
# ---------------------------------------------------------------------------


class TestClaudeCliArgv(unittest.TestCase):
    def test_default_argv(self):
        cfg = brain.BrainConfig.from_env({})
        self.assertEqual(
            brain._claude_cli_argv(cfg),
            ["claude", "-p", "--model", "claude-opus-5", "--output-format", "text"],
        )

    def test_env_overrides_claude_bin_and_model(self):
        env = {"DOCICH_BRAIN_CLAUDE_BIN": "/opt/bin/claude", "DOCICH_BRAIN_MODEL": "claude-haiku-5"}
        cfg = brain.BrainConfig.from_env(env)
        self.assertEqual(
            brain._claude_cli_argv(cfg),
            ["/opt/bin/claude", "-p", "--model", "claude-haiku-5", "--output-format", "text"],
        )

    def test_from_env_reads_all_fields(self):
        env = {
            "DOCICH_BRAIN_LLM": "fake:x.json",
            "DOCICH_BRAIN_MODEL": "claude-haiku-5",
            "DOCICH_BRAIN_CLAUDE_BIN": "/usr/local/bin/claude",
            "DOCICH_BRAIN_LLM_TIMEOUT_S": "42",
            "DOCICH_BRAIN_MAX_ACTIONS": "3",
            "DOCICH_BRAIN_THINKING": "adaptive",
        }
        cfg = brain.BrainConfig.from_env(env)
        self.assertEqual(cfg.llm, "fake:x.json")
        self.assertEqual(cfg.model, "claude-haiku-5")
        self.assertEqual(cfg.claude_bin, "/usr/local/bin/claude")
        self.assertEqual(cfg.llm_timeout_s, 42.0)
        self.assertEqual(cfg.max_actions, 3)
        self.assertEqual(cfg.thinking, "adaptive")

    def test_from_env_defaults_when_unset(self):
        cfg = brain.BrainConfig.from_env({})
        self.assertEqual(cfg.llm, "claude-cli")
        self.assertEqual(cfg.model, "claude-opus-5")
        self.assertEqual(cfg.claude_bin, "claude")
        self.assertEqual(cfg.llm_timeout_s, 100.0)
        self.assertEqual(cfg.max_actions, 8)
        self.assertEqual(cfg.thinking, "off")

    def test_from_env_ignores_malformed_numeric_values(self):
        cfg = brain.BrainConfig.from_env({"DOCICH_BRAIN_LLM_TIMEOUT_S": "not-a-number"})
        self.assertEqual(cfg.llm_timeout_s, 100.0)


class TestCallClaudeCli(unittest.TestCase):
    def test_prompt_is_passed_via_stdin_and_stdout_is_returned(self):
        cfg = brain.BrainConfig(llm="claude-cli", claude_bin="claude", model="claude-opus-5")
        captured = {}

        def fake_run(argv, *, input, capture_output, text, timeout):
            captured["argv"] = argv
            captured["input"] = input
            captured["capture_output"] = capture_output
            captured["text"] = text
            captured["timeout"] = timeout
            return subprocess.CompletedProcess(argv, 0, stdout='{"note":"ok","actions":[]}', stderr="")

        with mock.patch.object(brain.subprocess, "run", side_effect=fake_run):
            out = brain.call_claude_cli("THIS IS THE PROMPT", cfg)

        self.assertEqual(captured["input"], "THIS IS THE PROMPT")
        self.assertEqual(captured["argv"], ["claude", "-p", "--model", "claude-opus-5", "--output-format", "text"])
        self.assertTrue(captured["capture_output"])
        self.assertTrue(captured["text"])
        self.assertEqual(captured["timeout"], cfg.llm_timeout_s)
        self.assertEqual(out, '{"note":"ok","actions":[]}')

    def test_nonzero_exit_raises_brain_llm_error(self):
        cfg = brain.BrainConfig()

        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

        with mock.patch.object(brain.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(brain.BrainLLMError):
                brain.call_claude_cli("prompt", cfg)

    def test_timeout_raises_brain_llm_error(self):
        cfg = brain.BrainConfig()

        def fake_run(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 1))

        with mock.patch.object(brain.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(brain.BrainLLMError):
                brain.call_claude_cli("prompt", cfg)

    def test_missing_binary_raises_brain_llm_error(self):
        cfg = brain.BrainConfig()

        def fake_run(argv, **kwargs):
            raise FileNotFoundError(argv[0])

        with mock.patch.object(brain.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(brain.BrainLLMError):
                brain.call_claude_cli("prompt", cfg)


# ---------------------------------------------------------------------------
# api バックエンド: anthropic 未導入時のふるまい
# ---------------------------------------------------------------------------


class TestCallApiMissingSdk(unittest.TestCase):
    def test_missing_anthropic_raises_brain_llm_error_mentioning_pip_install(self):
        cfg = brain.BrainConfig(llm="api")
        with mock.patch.dict(sys.modules, {"anthropic": None}):
            with self.assertRaises(brain.BrainLLMError) as ctx:
                brain.call_api("prompt", None, cfg)
        self.assertIn("pip install anthropic", str(ctx.exception))


# ---------------------------------------------------------------------------
# run_cycle: claude-cli を subprocess.run モックで一周させる統合テスト
# ---------------------------------------------------------------------------


class TestRunCycleClaudeCliBackend(HanjukuBrainTestCase):
    def test_full_cycle_prompt_reaches_stdin_and_output_is_sanitized(self):
        cfg = brain.BrainConfig(llm="claude-cli", claude_bin="claude", model="claude-opus-5", max_actions=8)
        captured = {}

        def fake_run(argv, *, input, capture_output, text, timeout):
            captured["argv"] = argv
            captured["input"] = input
            response = {
                "note": "テスト応答: 城の前まで移動した",
                "state_patch": {"stage": 3},
                "actions": [{"type": "pad", "buttons": ["a"], "hold_ms": 9999}],
            }
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(response), stderr="")

        with mock.patch.object(brain.subprocess, "run", side_effect=fake_run):
            result = brain.run_cycle(_obs(), repo_root=self.repo_root, state_dir=self.state_dir, cfg=cfg)

        # プロンプトが stdin (input=) 経由で claude CLI に渡っている
        self.assertIn("進行状態", captured["input"])
        self.assertIn("スクリーンショットなし", captured["input"])
        self.assertEqual(
            captured["argv"], ["claude", "-p", "--model", "claude-opus-5", "--output-format", "text"]
        )

        # hold_ms は上限 2000 に丸められている
        self.assertEqual(result["actions"], [{"type": "pad", "buttons": ["a"], "hold_ms": 2000}])
        self.assertFalse(result["truncated"])

        # 副作用: state.json に state_patch が反映され、notes.md に note が追記されている
        state = json.loads((self.state_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["stage"], 3)
        notes = (self.state_dir / "notes.md").read_text(encoding="utf-8")
        self.assertIn("テスト応答: 城の前まで移動した", notes)

        # brain.log は run_cycle 単体では書かれない (main() の責務)
        self.assertFalse((self.state_dir / "brain.log").exists())

    def test_action_count_truncated_and_logged_to_stderr(self):
        cfg = brain.BrainConfig(llm="claude-cli", max_actions=2)

        def fake_run(argv, **kwargs):
            response = {"note": "ok", "actions": [{"type": "wait", "ms": 1} for _ in range(5)]}
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(response), stderr="")

        buf = io.StringIO()
        with mock.patch.object(brain.subprocess, "run", side_effect=fake_run), redirect_stderr(buf):
            result = brain.run_cycle(_obs(), repo_root=self.repo_root, state_dir=self.state_dir, cfg=cfg)

        self.assertEqual(len(result["actions"]), 2)
        self.assertTrue(result["truncated"])
        self.assertIn("切り捨て", buf.getvalue())

    def test_invalid_action_fails_self_validation_as_response_error(self):
        cfg = brain.BrainConfig(llm="claude-cli")

        def fake_run(argv, **kwargs):
            response = {"note": "ok", "actions": [{"type": "pad", "buttons": ["turbo"]}]}
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(response), stderr="")

        with mock.patch.object(brain.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(brain.BrainResponseError):
                brain.run_cycle(_obs(), repo_root=self.repo_root, state_dir=self.state_dir, cfg=cfg)

    def test_llm_failure_propagates_as_brain_llm_error(self):
        cfg = brain.BrainConfig(llm="claude-cli")

        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="claude: auth error")

        with mock.patch.object(brain.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(brain.BrainLLMError):
                brain.run_cycle(_obs(), repo_root=self.repo_root, state_dir=self.state_dir, cfg=cfg)


class TestRunCycleFakeBackend(HanjukuBrainTestCase):
    def test_fixture_jsonl_drives_two_cycles(self):
        fixture = REPO_ROOT / "tests" / "fixtures" / "hanjuku_fake_brain.jsonl"
        cfg = brain.BrainConfig(llm=f"fake:{fixture}")

        first = brain.run_cycle(_obs(), repo_root=self.repo_root, state_dir=self.state_dir, cfg=cfg)
        second = brain.run_cycle(_obs(), repo_root=self.repo_root, state_dir=self.state_dir, cfg=cfg)

        self.assertEqual(first["actions"], [{"type": "pad", "buttons": ["down"], "hold_ms": 150}])
        self.assertEqual(second["actions"], [{"type": "pad", "buttons": ["up"], "hold_ms": 150}])


# ---------------------------------------------------------------------------
# parse_observation (stdin の Observation JSON)
# ---------------------------------------------------------------------------


class TestParseObservation(unittest.TestCase):
    def test_valid_object(self):
        obs = brain.parse_observation(json.dumps(_obs()))
        self.assertEqual(obs["game"], "hanjuku-hero")

    def test_empty_stdin_raises(self):
        with self.assertRaises(brain.BrainInputError):
            brain.parse_observation("")

    def test_whitespace_only_stdin_raises(self):
        with self.assertRaises(brain.BrainInputError):
            brain.parse_observation("   \n")

    def test_invalid_json_raises(self):
        with self.assertRaises(brain.BrainInputError):
            brain.parse_observation("{not json")

    def test_non_object_top_level_raises(self):
        with self.assertRaises(brain.BrainInputError):
            brain.parse_observation("[1, 2, 3]")


# ---------------------------------------------------------------------------
# main(): stdin -> stdout の完全なサイクルと終了コード (repo_root は tmpdir に差し替え)
# ---------------------------------------------------------------------------


class TestMainEndToEnd(HanjukuBrainTestCase):
    def _run_main(self, stdin_text: str, env: dict) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        stdin = io.StringIO(stdin_text)
        with mock.patch.object(sys, "stdin", stdin), redirect_stdout(stdout), redirect_stderr(stderr):
            with mock.patch.dict(os.environ, env, clear=False):
                code = brain.main(repo_root=self.repo_root)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_fake_json_backend_success_exit_0(self):
        fixture = self.repo_root / "resp.json"
        fixture.write_text(json.dumps({"note": "ok", "actions": [{"type": "wait", "ms": 1}]}), encoding="utf-8")
        code, out, err = self._run_main(json.dumps(_obs()), {"DOCICH_BRAIN_LLM": f"fake:{fixture}"})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"actions": [{"type": "wait", "ms": 1}]})
        # brain.log に1行だけ記録される
        log_lines = (self.state_dir / "brain.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log_lines), 1)
        record = json.loads(log_lines[0])
        self.assertEqual(record["n_actions"], 1)
        self.assertIsNone(record["error"])

    def test_broken_stdin_exit_2_and_no_stdout(self):
        code, out, err = self._run_main("not json at all", {})
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertFalse(self.state_dir.exists())  # 入力エラー時は状態ディレクトリを作らない

    def test_unknown_backend_exit_3_and_logged(self):
        code, out, err = self._run_main(json.dumps(_obs()), {"DOCICH_BRAIN_LLM": "bogus-backend"})
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        log_lines = (self.state_dir / "brain.log").read_text(encoding="utf-8").splitlines()
        record = json.loads(log_lines[-1])
        self.assertIsNotNone(record["error"])

    def test_malformed_response_exit_4(self):
        fixture = self.repo_root / "resp.json"
        fixture.write_text("not-json-at-all-no-braces", encoding="utf-8")
        code, out, err = self._run_main(json.dumps(_obs()), {"DOCICH_BRAIN_LLM": f"fake:{fixture}"})
        self.assertEqual(code, 4)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
