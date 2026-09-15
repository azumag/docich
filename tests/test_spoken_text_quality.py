from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import spoken_text_quality  # noqa: E402


class SpokenTextQualityTests(unittest.TestCase):
    def test_short_natural_reply_is_valid(self) -> None:
        self.assertEqual(spoken_text_quality.comment_reply_issues("そうですね。"), ())

    def test_long_reply_with_natural_commas_is_valid(self) -> None:
        text = "同志A、これは少し長めの説明ですが、意味の切れ目に読点があるので自然に読み上げられます。"
        self.assertEqual(spoken_text_quality.comment_reply_issues(text), ())

    def test_long_japanese_run_without_comma_is_rejected(self) -> None:
        text = "同志Aそうですねこれはかなり面白い動きなので次の展開まで落ち着いて見届けたいと思っています。"
        self.assertIn(
            "long_japanese_run_without_comma",
            spoken_text_quality.comment_reply_issues(text),
        )

    def test_address_comma_does_not_hide_a_later_long_run(self) -> None:
        text = "同志A、これはかなり面白い動きなので次の展開がどう変わるのか落ち着いて最後まで見届けたいと思っています。"
        self.assertIn(
            "long_japanese_run_without_comma",
            spoken_text_quality.comment_reply_issues(text),
        )

    def test_missing_terminal_punctuation_is_rejected(self) -> None:
        self.assertIn(
            "missing_terminal_punctuation",
            spoken_text_quality.comment_reply_issues("これは短い返信です"),
        )

    def test_punctuation_in_earlier_sentence_does_not_hide_unpunctuated_ending(self) -> None:
        self.assertIn(
            "missing_terminal_punctuation",
            spoken_text_quality.comment_reply_issues("最初の文です。最後の文には句点がありません"),
        )

    def test_terminal_punctuation_before_closing_quote_is_valid(self) -> None:
        self.assertEqual(spoken_text_quality.comment_reply_issues("「そうですね。」"), ())

    def test_english_only_reply_is_rejected(self) -> None:
        self.assertIn(
            "missing_japanese",
            spoken_text_quality.comment_reply_issues("Thanks for watching."),
        )

    def test_cli_explain_reports_stable_issue_code(self) -> None:
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(
            "同志Aそうですねこれはかなり面白い動きなので次の展開まで落ち着いて見届けたいと思っています。"
        )
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                rc = spoken_text_quality.main(["--profile", "comment", "--explain"])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(rc, 1)
        self.assertIn("long_japanese_run_without_comma", err.getvalue())


if __name__ == "__main__":
    unittest.main()
