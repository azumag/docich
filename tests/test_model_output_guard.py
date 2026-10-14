"""docich 正典の AI 出力ガード (C4, common_parts_chat_c4.md C-S2)。

soviet_now `lib/model_output_guard.py` の移植。保守的契約を維持する。
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import cli, model_output_guard  # noqa: E402


class ModelOutputGuardTests(unittest.TestCase):
    def test_clean_radio_script_is_unchanged(self) -> None:
        source = "こんにちは。今日のニュースです。\n\n===SUMMARY===\n科学,宇宙"
        self.assertEqual(model_output_guard.extract_final_text(source), source)

    def test_untagged_web_research_notes_before_divider_are_not_spoken(self) -> None:
        source = """WebFetchが使えない環境なので、自分の知識で候補を選びます。
確実性が高いのは以下のあたりです。
- 候補A

---

こんばんは。ここからが今日の本題です。

===SUMMARY===
本題,要約
"""
        self.assertEqual(
            model_output_guard.extract_final_text(source),
            "こんばんは。ここからが今日の本題です。\n\n===SUMMARY===\n本題,要約",
        )

    def test_tool_only_output_is_discarded(self) -> None:
        source = """<function_calls>
<invoke name="exec_command">
<parameter name="cmd">curl https://example.invalid</parameter>
</invoke>
</tool_call>"""
        self.assertEqual(model_output_guard.extract_final_text(source), "")

    def test_unmarked_work_note_is_discarded(self) -> None:
        self.assertEqual(model_output_guard.extract_final_text("WebFetchをもう少し試してみます。"), "")

    def test_explicit_final_container_wins_over_analysis(self) -> None:
        source = "<analysis>検索します。</analysis><final>完成した本文です。</final>"
        self.assertEqual(model_output_guard.extract_final_text(source), "完成した本文です。")

    def test_work_note_mislabeled_as_final_is_discarded(self) -> None:
        source = "<final>WebFetchをもう少し試してみます。</final>"
        self.assertEqual(model_output_guard.extract_final_text(source), "")

    def test_fact_check_envelope_drops_preamble_but_keeps_issues(self) -> None:
        source = """材料を確認します。
===SAFE_SCRIPT===
放送する完成原稿です。
===ISSUES===
なし
"""
        self.assertEqual(
            model_output_guard.extract_final_text(source),
            "===SAFE_SCRIPT===\n放送する完成原稿です。\n===ISSUES===\nなし",
        )

    def test_malformed_tool_protocol_does_not_leak_trailing_text(self) -> None:
        source = "<tool_call>\n検索コマンド\n完成したように見える文です。"
        self.assertEqual(model_output_guard.extract_final_text(source), "")


class TestCliAiGuard(unittest.TestCase):
    def test_stdin_guard_filters_output(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("<analysis>検索します。</analysis><final>本題です。</final>")
        try:
            with redirect_stdout(out):
                rc = cli.main(["ai-guard"])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), "本題です。")


if __name__ == "__main__":
    unittest.main()
