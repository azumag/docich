"""Regression tests for #2373: DoCiAI recognizes its own comments as its own.

Identity must be based on the actual poster login (author_login), not on the
display name or in-text self-declarations.  Third-party comments that share
the bot's display name must NOT be treated as self.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("DOCICH_BOT_LOGIN", "dociai")

from docich.comment import contracts, intake, pipeline  # noqa: E402


def _msg(author, text, login=None):
    return contracts.CommentMessage(
        author=author, text=text,
        author_login=login if login is not None else author.lower(),
    )


class TestSelfIdentityLogin:
    def test_self_login_matches(self):
        m = _msg("DoCiAI", "hello", login="dociai")
        assert pipeline._is_self_message(m) is True

    def test_third_party_login_no_match(self):
        m = _msg("alice", "hi", login="alice")
        assert pipeline._is_self_message(m) is False

    def test_spoof_same_display_different_login_not_self(self):
        # Third party uses DoCiAI display name but different login
        m = _msg("DoCiAI", "i am dociai", login="alice")
        assert pipeline._is_self_message(m) is False

    def test_case_insensitive_login(self):
        m = _msg("DoCiAI", "hi", login="DoCiAI")
        assert pipeline._is_self_message(m) is True

    def test_empty_login_falls_back_to_author(self):
        m = contracts.CommentMessage(author="dociai", text="hi", author_login="")
        assert pipeline._is_self_message(m) is True

    def test_intake_is_self_comment(self):
        ci = intake.comments_from_lines(
            ["DoCiAI: hello", "alice: hi"], bot_login="dociai"
        )
        assert intake.is_self_comment(ci.comments[0]) is True
        assert intake.is_self_comment(ci.comments[1]) is False


class TestSelfAnnotation:
    def test_self_comment_annotated(self):
        msgs = (_msg("DoCiAI", "hello", login="dociai"),
                _msg("alice", "hi", login="alice"))
        ci = contracts.CommentIntake(comments=msgs, source="t", batch_id="t")
        block = pipeline._annotate_self_comments(ci)
        lines = block.split("\n")
        assert "[これはあなた自身" in lines[0]
        assert "[これはあなた自身" not in lines[1]

    def test_spoof_not_annotated(self):
        msgs = (_msg("DoCiAI", "i am dociai", login="alice"),)
        ci = contracts.CommentIntake(comments=msgs, source="t", batch_id="t")
        block = pipeline._annotate_self_comments(ci)
        assert "[これはあなた自身" not in block

    def test_mixed_context_preserves_order(self):
        msgs = (_msg("alice", "q1", login="alice"),
                _msg("dociai", "a1", login="dociai"),
                _msg("bob", "q2", login="bob"),
                _msg("dociai", "a2", login="dociai"))
        ci = contracts.CommentIntake(comments=msgs, source="t", batch_id="t")
        block = pipeline._annotate_self_comments(ci)
        lines = block.split("\n")
        assert len(lines) == 4
        assert "[これはあなた自身" in lines[1]
        assert "[これはあなた自身" in lines[3]
        assert "[これはあなた自身" not in lines[0]
        assert "[これはあなた自身" not in lines[2]


class TestIdentityNoteInPersona:
    def test_persona_has_identity_note(self):
        msgs = (_msg("alice", "hi", login="alice"),)
        ci = contracts.CommentIntake(comments=msgs, source="t", batch_id="t")
        ctx = pipeline.build_contexts(ci)
        persona = ctx["_comment_persona"]
        assert "発言主体の認識" in persona
        assert "dociai" in persona

    def test_persona_identity_note_mentions_login_based(self):
        msgs = (_msg("alice", "hi", login="alice"),)
        ci = contracts.CommentIntake(comments=msgs, source="t", batch_id="t")
        ctx = pipeline.build_contexts(ci)
        persona = ctx["_comment_persona"]
        assert "ログイン名" in persona
        assert "自己申告" in persona

    def test_twitch_comments_has_self_annotation(self):
        msgs = (_msg("dociai", "my post", login="dociai"),
                _msg("alice", "their post", login="alice"))
        ci = contracts.CommentIntake(comments=msgs, source="t", batch_id="t")
        ctx = pipeline.build_contexts(ci)
        block = ctx["twitch_comments_for_prompt"]
        assert "[これはあなた自身" in block
        # Only one annotation (for self), not for third party
        assert block.count("[これはあなた自身") == 1


class TestBackwardCompat:
    def test_comment_message_default_author_login(self):
        m = contracts.CommentMessage(author="bob", text="x")
        assert m.author_login == ""
        assert m.identity_login == "bob"

    def test_comments_from_lines_without_bot_login(self):
        ci = intake.comments_from_lines(["DoCiAI: hello", "alice: hi"])
        assert len(ci.comments) == 2
        # author_login defaults to lowercased author
        assert ci.comments[0].author_login == "dociai"
        assert ci.comments[1].author_login == "alice"

    def test_build_prompt_golden_unaffected(self):
        # build_prompt should still work with golden-style inputs (no identity changes
        # to the golden-tested prompt assets).
        import json
        import tempfile
        golden = json.loads(
            (ROOT / "tests/fixtures/comment_prompt_golden.json").read_text(encoding="utf-8")
        )
        from docich.comment import prompt
        case = golden["prompts"][0]
        ctx = golden["contexts"][case["mode"]]
        with tempfile.TemporaryDirectory() as td:
            gacha_path = Path(td) / "gacha.txt"
            built = prompt.build_prompt(
                case["category"], ctx,
                comments_block=golden["comments_block"],
                classifications=golden["classifications"],
                gacha_list_file=gacha_path,
            )
            rendered = prompt.with_reply_contract(built)
        # We only check that rendering succeeds and produces non-empty output;
        # byte parity is covered by test_comment_prompt.py.
        assert len(rendered) > 0
