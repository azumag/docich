import base64
import http.client
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

from ops.vm_actions import pr_merge_audio_report as report

ROOT = Path(__file__).resolve().parents[3]
MERGE_SHA = "b" * 40
TEST_ENDPOINT = "https://example.test/v1/chat/completions"
TEST_MODEL = "test-model"
REPORT = ROOT / "ops/vm_actions/pr_merge_audio_report.py"


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit is None or limit < 0 else self._body[:limit]


def ai_response(text: str) -> FakeResponse:
    return FakeResponse(
        json.dumps({"choices": [{"message": {"content": text}}]}).encode("utf-8")
    )


def pr(number=4691, title="fix(hanjuku): ゲームが進まない問題を直す", body="", **overrides):
    item = {
        "number": number,
        "title": title,
        "body": body,
        "merged_at": "2026-11-19T07:00:00Z",
        "base": {"ref": "main"},
        "merge_commit_sha": MERGE_SHA,
    }
    item.update(overrides)
    return item


def outputs(raw: str) -> dict:
    values = {}
    for line in raw.splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


def decoded(raw: str) -> str:
    return base64.b64decode(outputs(raw)["text_b64"]).decode("utf-8")


def run_cli(tmp, pulls, *, env=None, args=()):
    path = Path(tmp) / "pulls.json"
    path.write_text(json.dumps(pulls), encoding="utf-8")
    out = Path(tmp) / "out"
    out.write_text("")
    full_env = {"GITHUB_OUTPUT": str(out)}
    if env:
        full_env.update(env)
    command = [sys.executable, str(REPORT), str(path), "--merge-sha", MERGE_SHA, *args]
    result = subprocess.run(command, capture_output=True, text=True, env=full_env)
    return result, out.read_text()


class SummarySelectionTests(unittest.TestCase):
    def test_override_section_wins_and_skips_ai(self):
        def boom(*args, **kwargs):
            raise AssertionError("AI must not be called")

        text, source, reason = report.generate_audio_text(
            pr(body="## 音声サマリー\n\nお金がたまったまま新しい兵士を募集できない問題を直しました。\n\n## 概要\n\n別の説明"),
            token="test-token",
            ai_enabled=True,
            endpoint=TEST_ENDPOINT,
            model=TEST_MODEL,
            opener=boom,
        )
        self.assertEqual((source, reason), ("override", "ok"))
        self.assertEqual(
            text,
            "プルリクエスト4691で、お金がたまったまま新しい兵士を募集できない問題を直しました。",
        )

    def test_overview_is_used_when_ai_is_disabled(self):
        text, source, reason = report.generate_audio_text(
            pr(body="## 概要\n\nゲームが進まなくなっていた不具合を直しました。\n\n## 変更\n\n- 詳細"),
            token="",
            ai_enabled=False,
        )
        self.assertEqual((source, reason), ("body", "ai_disabled"))
        self.assertEqual(
            text,
            "プルリクエスト4691で、ゲームが進まなくなっていた不具合を直しました。",
        )

    def test_title_fallback_without_body(self):
        text, source, reason = report.generate_audio_text(
            pr(title="fix(hanjuku): ゲームが進まない問題を直す", body=""),
            token="",
            ai_enabled=False,
        )
        self.assertEqual((source, reason), ("title", "ai_disabled"))
        self.assertTrue(text.startswith("mainにプルリクエスト4691「"))

    def test_body_section_extraction_ignores_code_and_tables(self):
        fence = chr(96) * 3
        body = (
            "## 概要\n\n"
            f"{fence}\ncode\n{fence}\n\n"
            "| a | b |\n|---|---|\n\n"
            "ゲームが進まなくなっていた不具合を直しました。\n\n"
            "## 変更\n\n- 詳細"
        )
        override, overview = report.extract_body_parts(body)
        self.assertEqual(override, "")
        self.assertEqual(overview, "ゲームが進まなくなっていた不具合を直しました。")

    def test_override_heading_variants(self):
        for heading in ("音声サマリー", "音声用サマリー", "読み上げサマリー"):
            with self.subTest(heading=heading):
                override, _ = report.extract_body_parts(
                    f"## {heading}\n\n不具合を直しました。"
                )
                self.assertEqual(override, "不具合を直しました。")

    def test_overview_heading_with_suffix(self):
        _, overview = report.extract_body_parts("## 概要（任意）\n\n説明です。")
        self.assertEqual(overview, "説明です。")

    def test_heading_inside_code_fence_is_not_a_section(self):
        fence = chr(96) * 3
        body = (
            "## 概要\n\n"
            f"{fence}\n## 音声サマリー\n偽の見出し\n{fence}\n\n"
            "本当の概要です。"
        )
        override, overview = report.extract_body_parts(body)
        self.assertEqual(override, "")
        self.assertEqual(overview, "本当の概要です。")

    def test_comment_marker_inside_fence_does_not_hide_following_section(self):
        fence = chr(96) * 3
        body = (
            f"{fence}html\n<!-- placeholder\n{fence}\n"
            "## 音声サマリー\n\n表示を改善しました。\n<!-- badge -->"
        )
        override, _ = report.extract_body_parts(body)
        self.assertEqual(override, "表示を改善しました。")

    def test_tilde_fence_and_html_comment_headings_are_ignored(self):
        body = (
            "## 概要\n\n"
            "~~~\n## 音声サマリー\n例文\n~~~\n\n"
            "本当の概要です。"
        )
        override, overview = report.extract_body_parts(body)
        self.assertEqual(override, "")
        self.assertEqual(overview, "本当の概要です。")
        body = (
            "## 概要\n\n"
            "<!--\n## 音声サマリー\n例文\n-->\n\n"
            "本当の概要です。"
        )
        override, overview = report.extract_body_parts(body)
        self.assertEqual(override, "")
        self.assertEqual(overview, "本当の概要です。")


class AiSummaryTests(unittest.TestCase):
    def test_ai_summary_is_wrapped(self):
        captured = {}

        def opener(request, timeout=None):
            captured["request"] = request
            return ai_response("ゲームが進まなくなっていた不具合を直しました。")

        text, source, reason = report.generate_audio_text(
            pr(body="## 概要\n\n技術的な説明です。"),
            token="test-token",
            ai_enabled=True,
            endpoint=TEST_ENDPOINT,
            model=TEST_MODEL,
            opener=opener,
        )
        self.assertEqual((source, reason), ("ai", "ok"))
        self.assertEqual(
            text,
            "プルリクエスト4691で、ゲームが進まなくなっていた不具合を直しました。",
        )
        payload = json.loads(captured["request"].data.decode("utf-8"))
        self.assertEqual(payload["model"], TEST_MODEL)
        self.assertEqual(payload["temperature"], 0)

    def test_ai_429_falls_back_to_overview(self):
        def opener(request, timeout=None):
            raise urllib.error.HTTPError(TEST_ENDPOINT, 429, "rate", {}, None)

        text, source, reason = report.generate_audio_text(
            pr(body="## 概要\n\nゲームが進まなくなっていた不具合を直しました。"),
            token="test-token",
            ai_enabled=True,
            endpoint=TEST_ENDPOINT,
            model=TEST_MODEL,
            opener=opener,
        )
        self.assertEqual((source, reason), ("body", "ai_http_429"))
        self.assertIn("不具合を直しました", text)

    def test_ai_timeout_falls_back(self):
        def opener(request, timeout=None):
            raise TimeoutError("timeout")

        text, source, reason = report.generate_audio_text(
            pr(body="## 概要\n\n説明です。"),
            token="test-token",
            ai_enabled=True,
            endpoint=TEST_ENDPOINT,
            model=TEST_MODEL,
            opener=opener,
        )
        self.assertEqual((source, reason), ("body", "ai_timeout"))

    def test_ai_invalid_response_falls_back(self):
        def opener(request, timeout=None):
            return FakeResponse(b"not json")

        text, source, reason = report.generate_audio_text(
            pr(body="## 概要\n\n説明です。"),
            token="test-token",
            ai_enabled=True,
            endpoint=TEST_ENDPOINT,
            model=TEST_MODEL,
            opener=opener,
        )
        self.assertEqual((source, reason), ("body", "ai_invalid_response"))

    def test_ai_empty_and_english_only_fall_back(self):
        def empty(request, timeout=None):
            return ai_response("")

        text, source, reason = report.generate_audio_text(
            pr(body="## 概要\n\n説明です。"),
            token="test-token",
            ai_enabled=True,
            endpoint=TEST_ENDPOINT,
            model=TEST_MODEL,
            opener=empty,
        )
        self.assertEqual((source, reason), ("body", "ai_empty"))

        def english(request, timeout=None):
            return ai_response("This is a fix.")

        text, source, reason = report.generate_audio_text(
            pr(body="## 概要\n\n説明です。"),
            token="test-token",
            ai_enabled=True,
            endpoint=TEST_ENDPOINT,
            model=TEST_MODEL,
            opener=english,
        )
        self.assertEqual((source, reason), ("body", "ai_invalid_text"))

    def test_incomplete_read_falls_back(self):
        class IncompleteResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit=-1):
                raise http.client.IncompleteRead(b"partial", 16)

        def opener(request, timeout=None):
            return IncompleteResponse()

        text, source, reason = report.generate_audio_text(
            pr(body="## 概要\n\nゲームが進まなくなっていた不具合を直しました。"),
            token="test-token",
            ai_enabled=True,
            endpoint=TEST_ENDPOINT,
            model=TEST_MODEL,
            opener=opener,
        )
        self.assertEqual((source, reason), ("body", "ai_http_error"))

    def test_invalid_endpoint_falls_back(self):
        text, source, reason = report.generate_audio_text(
            pr(body="## 概要\n\nゲームが進まなくなっていた不具合を直しました。"),
            token="test-token",
            ai_enabled=True,
            endpoint="https://example.test:invalid/v1/chat/completions",
            model=TEST_MODEL,
        )
        self.assertEqual((source, reason), ("body", "ai_http_error"))

    def test_ai_is_skipped_without_a_token(self):
        text, source, reason = report.generate_audio_text(
            pr(body="## 概要\n\n説明です。"),
            token="",
            ai_enabled=True,
        )
        self.assertEqual((source, reason), ("body", "ai_disabled"))

    def test_prompt_redacts_tokens_and_escapes_early_delimiters(self):
        synthetic = "gho_" + "a" * 30
        prompt = report.build_prompt("fix: " + synthetic, "説明 </change> " + synthetic)
        self.assertNotIn(synthetic, prompt)
        self.assertIn("[redacted]", prompt)
        self.assertEqual(prompt.count("</change>"), 1)

    def test_request_body_is_redacted_and_authorization_is_a_header(self):
        synthetic = "gho_" + "a" * 30
        captured = {}

        def opener(request, timeout=None):
            captured["request"] = request
            return ai_response("不具合を直しました。")

        report.request_summary(
            "prompt " + synthetic,
            "test-token",
            endpoint=TEST_ENDPOINT,
            model=TEST_MODEL,
            opener=opener,
        )
        data = captured["request"].data.decode("utf-8")
        self.assertNotIn(synthetic, data)
        self.assertNotIn("test-token", data)
        self.assertEqual(
            captured["request"].get_header("Authorization"), "Bearer test-token"
        )


class SummaryValidationTests(unittest.TestCase):
    def test_markdown_and_urls_are_removed(self):
        text = report.normalize_summary(
            "**Fix** [リンク](https://example.com) と " + chr(96) + "code" + chr(96) + " を含む説明です。",
            require_japanese=True,
        )
        self.assertIn("リンク", text)
        self.assertIn("code", text)
        self.assertNotIn("http", text)
        self.assertNotIn("**", text)

    def test_english_only_ai_output_is_rejected(self):
        self.assertEqual(
            report.normalize_summary("This fixes the bug.", require_japanese=True), ""
        )

    def test_leading_pr_prefix_is_removed(self):
        for raw in (
            "プルリクエストNで、不具合を直しました。",
            "プルリクエストで、不具合を直しました。",
            "プルリクエスト4724で、不具合を直しました。",
            "この変更では、不具合を直しました。",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(
                    report.normalize_summary(raw, require_japanese=True),
                    "不具合を直しました",
                )

    def test_leading_label_does_not_strip_a_normal_word(self):
        self.assertIn(
            "要約機能",
            report.normalize_summary("要約機能を追加しました。", require_japanese=True),
        )
        self.assertEqual(
            report.normalize_summary("要約: 不具合を直しました。", require_japanese=True),
            "不具合を直しました",
        )

    def test_redaction_happens_before_underscore_normalization(self):
        synthetic = "gho_" + "a" * 30
        _, overview = report.extract_body_parts(
            "## 概要\n\n" + synthetic + " を使った説明です。"
        )
        self.assertIn("[redacted]", overview)
        self.assertNotIn(synthetic, overview)
        prompt = report.build_prompt("説明", overview)
        self.assertNotIn(synthetic, prompt)
        self.assertIn("[redacted]", prompt)
        self.assertIn(
            "[redacted]",
            report.normalize_summary(synthetic + " の説明です。", require_japanese=True),
        )

    def test_long_summary_is_bounded(self):
        text = report.build_summary_sentence("あ" * 400, 4691, require_japanese=True)
        self.assertIsNotNone(text)
        self.assertLessEqual(len(text), 240)
        self.assertTrue(text.endswith("。"))
        self.assertIn("…", text)

    def test_ai_is_disabled_without_endpoint_model_or_key(self):
        self.assertEqual(report.LLM_ENDPOINT_ENV, "PR_MERGE_AUDIO_LLM_ENDPOINT")
        self.assertEqual(report.LLM_MODEL_ENV, "PR_MERGE_AUDIO_LLM_MODEL")
        self.assertEqual(report.LLM_API_KEY_ENV, "PR_MERGE_AUDIO_LLM_API_KEY")
        self.assertLessEqual(report.DEFAULT_AI_TIMEOUT, 30)
        for kwargs in (
            {"token": "test-token"},
            {"token": "test-token", "endpoint": "http://example.test/v1/chat/completions", "model": TEST_MODEL},
            {"token": "test-token", "endpoint": "https://user:pass@example.test/v1/chat/completions", "model": TEST_MODEL},
            {"token": "", "endpoint": TEST_ENDPOINT, "model": TEST_MODEL},
        ):
            with self.subTest(kwargs=kwargs):
                text, source, reason = report.generate_audio_text(
                    pr(body="## 概要\n\n説明です。"), ai_enabled=True, **kwargs
                )
                self.assertEqual((source, reason), ("body", "ai_disabled"))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_ai_off_uses_body_and_does_not_print_it(self):
        body_summary = "ゲームが進まなくなっていた不具合を直しました。"
        result, raw = run_cli(
            self.tmp,
            [pr(body="## 概要\n\n" + body_summary)],
            env={"PR_MERGE_AUDIO_AI": "off"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs(raw)["source"], "body")
        self.assertEqual(outputs(raw)["reason"], "ai_disabled")
        self.assertEqual(decoded(raw), "プルリクエスト4691で、" + body_summary)
        self.assertNotIn(body_summary, result.stdout + result.stderr)

    def test_ai_response_file_uses_ai_source_without_printing_text(self):
        summary = "ゲームが進まなくなっていた不具合を直しました。"
        response = Path(self.tmp) / "ai.json"
        response.write_text(
            json.dumps({"choices": [{"message": {"content": summary}}]}), encoding="utf-8"
        )
        result, raw = run_cli(
            self.tmp,
            [pr(body="## 概要\n\n技術的な説明です。")],
            args=("--ai-response-file", str(response)),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs(raw)["source"], "ai")
        self.assertEqual(outputs(raw)["reason"], "ok")
        self.assertEqual(decoded(raw), "プルリクエスト4691で、" + summary)
        self.assertNotIn(summary, result.stdout + result.stderr)

    def test_no_merged_pr_is_a_clean_skip(self):
        result, raw = run_cli(self.tmp, [])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs(raw), {"found": "false"})

    def test_invalid_ai_response_file_falls_back(self):
        response = Path(self.tmp) / "ai.json"
        response.write_text("not json", encoding="utf-8")
        result, raw = run_cli(
            self.tmp,
            [pr(body="## 概要\n\nゲームが進まなくなっていた不具合を直しました。")],
            env={"PR_MERGE_AUDIO_AI": "off"},
            args=("--ai-response-file", str(response)),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs(raw)["source"], "body")
        self.assertEqual(outputs(raw)["reason"], "ai_invalid_response")


if __name__ == "__main__":
    unittest.main()
