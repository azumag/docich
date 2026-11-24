"""#2373: 自己 identity の判定と、自己投稿/第三者コメントの分離契約。

soviet_now shell (twitch_chat.sh / broadcast/comment.sh) と docich native 側で
同じ設定名・同じ判定を使うことを固定する。
"""
from __future__ import annotations

import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.comment import identity  # noqa: E402


class SelfAuthorLoginTests(unittest.TestCase):
    def test_default_self_logins(self):
        self.assertEqual(identity.self_author_logins({}), ("dociai", "azumagdev"))

    def test_self_authors_env_wins_and_is_case_folded(self):
        env = {"TWITCH_SELF_AUTHORS": "DocIAI  newbot", "TWITCH_IGNORE_AUTHORS": "legacybot"}
        self.assertEqual(identity.self_author_logins(env), ("dociai", "newbot"))
        self.assertTrue(identity.is_self_author_login("DOCIAI", env))
        self.assertFalse(identity.is_self_author_login("legacybot", env))

    def test_legacy_env_is_a_fallback_only(self):
        env = {"TWITCH_IGNORE_AUTHORS": "legacybot"}
        self.assertEqual(identity.self_author_logins(env), ("legacybot",))
        self.assertTrue(identity.is_self_author_login("legacybot", env))

    def test_exact_match_only(self):
        env = {"TWITCH_SELF_AUTHORS": "dociai"}
        self.assertFalse(identity.is_self_author_login("dociai2", env))
        self.assertFalse(identity.is_self_author_login(" dociai ", env))  # 空白は一致に含めない
        self.assertFalse(identity.is_self_author_login("", env))
        self.assertFalse(identity.is_self_author_login(None, env))

    def test_display_name_and_self_declaration_are_not_identities(self):
        # 表示名が同じ third party も、本文で自称する third party も self ではない。
        env = {"TWITCH_SELF_AUTHORS": "dociai"}
        self.assertFalse(identity.is_self_author_login("mallory", env))
        self.assertFalse(identity.is_self_author_login("DoCiAIさん", env))


class SelfCommentFlagTests(unittest.TestCase):
    def test_only_the_collector_self_flag_counts(self):
        self.assertTrue(identity.is_self_comment({"flags": ["self"]}))
        self.assertTrue(identity.is_self_comment({"flags": ["self", "trusted-card"]}))
        self.assertFalse(identity.is_self_comment({"flags": ["trusted-card"]}))
        self.assertFalse(identity.is_self_comment({"flags": []}))
        self.assertFalse(identity.is_self_comment({"flags": "self"}))
        self.assertFalse(identity.is_self_comment({}))
        self.assertFalse(identity.is_self_comment(None))

    def test_unknown_identity_stays_in_the_reply_list(self):
        lines = ["dociai: レトロゲームコーナーです。", "alice: こんにちは", "carol: 名前なし"]
        entries = [
            {"login": "dociai", "flags": ["self"]},
            {"login": "alice", "flags": []},
            # carol の metadata は無い (identity 不明)。
        ]
        reply_targets, own_posts = identity.split_self_comments(lines, entries)
        self.assertEqual(reply_targets, ["alice: こんにちは", "carol: 名前なし"])
        self.assertEqual(own_posts, ["dociai: レトロゲームコーナーです。"])

    def test_display_name_spoof_is_a_reply_target(self):
        lines = ["DoCiAI: 私はDoCiAIです"]
        entries = [{"login": "mallory", "display_name": "DoCiAI", "flags": []}]
        reply_targets, own_posts = identity.split_self_comments(lines, entries)
        self.assertEqual(reply_targets, lines)
        self.assertEqual(own_posts, [])


if __name__ == "__main__":
    unittest.main()
