"""Contract tests for docich.puyo_chat_inbox (docich#3372).

The writer is the producer half of the reviewed ``chat_inbox.jsonl``
contract (spec 2026-10-18-puyo-puyo-clone-corner-design.md): it must write
``{"user_id","display_name","text"}`` rows into the *live* puyo-view
slot's runtime dir, and write nothing at all when no puyo slot is
resolvable (fail-closed / no-op).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import game_switch  # noqa: E402
from docich import puyo_chat_inbox as inbox  # noqa: E402
from docich.naming import runtime_directory, runtime_names  # noqa: E402
from docich.puyo_view import VIEW_NAME  # noqa: E402


def _runtime(generation: int = 1, game: str = VIEW_NAME) -> dict:
    names = runtime_names(generation)
    return {
        "game": game,
        "adapter": "program",
        "generation": generation,
        "runtime_id": f"g{generation}-abcdef",
        "lease_id": "01234567-89ab-cdef-0123-456789abcdef",
        "game_window": names.game_window,
        "agent_window": names.agent_window,
        "adapter_session": names.adapter_session,
        "started_at": "2026-11-04T10:00:00Z",
    }


class ChatInboxTestBase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tempdir.name) / "run"
        self.store = game_switch.GameSwitchStore(self.state_dir)

    def tearDown(self):
        self.tempdir.cleanup()

    def _activate(self, game: str = VIEW_NAME, *, make_dir: bool = True):
        """Put ``game`` into the canonical active slot like a committed switch."""
        self.store.initialize()
        state, _ = self.store.canonical.load()
        state["phase"] = "ready"
        state["active"] = _runtime(game=game)
        state["next_generation"] = state["active"]["generation"] + 1
        self.store.canonical.save(state)
        if make_dir:
            runtime_directory(self.state_dir, state["active"]["runtime_id"]).mkdir(
                parents=True)
        return state["active"]

    def _inbox(self, runtime) -> Path:
        return runtime_directory(self.state_dir, runtime["runtime_id"]) / "chat_inbox.jsonl"

    def _lines(self, runtime) -> list[dict]:
        path = self._inbox(runtime)
        if not path.exists():
            return []
        return [json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()]


class TestAppendChat(ChatInboxTestBase):
    def test_appends_schema_row(self):
        runtime = self._activate()
        result = inbox.append_chat(
            self.state_dir, user_id="12345",
            display_name="viewer", text="!puyo join", msg_id="m1")
        self.assertEqual(result, {"status": "appended"})
        lines = self._lines(runtime)
        self.assertEqual(len(lines), 1)
        # The reviewed contract is exactly these three keys.
        self.assertEqual(set(lines[0]), {"user_id", "display_name", "text"})
        self.assertEqual(
            lines[0],
            {"user_id": "12345", "display_name": "viewer", "text": "!puyo join"})

    def test_text_is_truncated_at_200_chars(self):
        runtime = self._activate()
        inbox.append_chat(
            self.state_dir, user_id="1", display_name="v",
            text="あ" * 250, msg_id="m1")
        lines = self._lines(runtime)
        self.assertEqual(len(lines[0]["text"]), 200)

    def test_msg_id_is_idempotent(self):
        runtime = self._activate()
        first = inbox.append_chat(
            self.state_dir, user_id="1", display_name="v",
            text="!puyo join", msg_id="dup-1")
        second = inbox.append_chat(
            self.state_dir, user_id="1", display_name="v",
            text="!puyo join", msg_id="dup-1")
        self.assertEqual(first["status"], "appended")
        self.assertEqual(second, {"status": "skipped", "reason": "duplicate"})
        self.assertEqual(len(self._lines(runtime)), 1)

    def test_distinct_msg_ids_both_append(self):
        runtime = self._activate()
        inbox.append_chat(self.state_dir, user_id="1", display_name="v",
                          text="a", msg_id="m1")
        inbox.append_chat(self.state_dir, user_id="1", display_name="v",
                          text="b", msg_id="m2")
        self.assertEqual(len(self._lines(runtime)), 2)

    def test_missing_msg_id_still_appends(self):
        runtime = self._activate()
        result = inbox.append_chat(
            self.state_dir, user_id="1", display_name="v", text="hello")
        self.assertEqual(result["status"], "appended")
        self.assertEqual(len(self._lines(runtime)), 1)

    def test_append_preserves_existing_lines(self):
        runtime = self._activate()
        path = self._inbox(runtime)
        path.write_text(
            '{"user_id":"9","display_name":"old","text":"x"}\n',
            encoding="utf-8")
        inbox.append_chat(self.state_dir, user_id="1", display_name="v",
                          text="y", msg_id="m1")
        self.assertEqual(len(self._lines(runtime)), 2)

    def test_display_name_is_bounded(self):
        runtime = self._activate()
        inbox.append_chat(self.state_dir, user_id="1",
                          display_name="x" * 64, text="hi", msg_id="m1")
        self.assertEqual(len(self._lines(runtime)[0]["display_name"]), 32)


class TestFailClosed(ChatInboxTestBase):
    def test_no_canonical_state_is_noop(self):
        # 未初期化の state_dir では何も書かない。
        self.state_dir.mkdir(parents=True)
        result = inbox.append_chat(
            self.state_dir, user_id="1", display_name="v",
            text="!puyo join", msg_id="m1")
        self.assertEqual(result, {"status": "skipped", "reason": "no_puyo_slot"})
        self.assertEqual(list(self.state_dir.rglob("chat_inbox.jsonl")), [])

    def test_missing_state_dir_is_noop(self):
        result = inbox.append_chat(
            self.state_dir, user_id="1", display_name="v",
            text="!puyo join", msg_id="m1")
        self.assertEqual(result, {"status": "skipped", "reason": "no_puyo_slot"})

    def test_corrupt_canonical_is_noop(self):
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "game_switch.json").write_text("{broken",
                                                       encoding="utf-8")
        result = inbox.append_chat(
            self.state_dir, user_id="1", display_name="v",
            text="!puyo join", msg_id="m1")
        self.assertEqual(result, {"status": "skipped", "reason": "no_puyo_slot"})

    def test_other_game_active_is_noop(self):
        """他コーナー稼働中のチャットは puyo inbox に流さない。"""
        runtime = self._activate(game="nethack")
        result = inbox.append_chat(
            self.state_dir, user_id="1", display_name="v",
            text="!puyo join", msg_id="m1")
        self.assertEqual(result, {"status": "skipped", "reason": "no_puyo_slot"})
        self.assertFalse(self._inbox(runtime).exists())

    def test_missing_runtime_dir_is_noop(self):
        # canonical が puyo を指していても runtime_dir が無ければ書かない。
        runtime = self._activate(make_dir=False)
        result = inbox.append_chat(
            self.state_dir, user_id="1", display_name="v",
            text="!puyo join", msg_id="m1")
        self.assertEqual(result, {"status": "skipped", "reason": "no_puyo_slot"})
        self.assertFalse(self._inbox(runtime).exists())

    def test_empty_user_id_is_rejected(self):
        runtime = self._activate()
        result = inbox.append_chat(
            self.state_dir, user_id="", display_name="v",
            text="!puyo join", msg_id="m1")
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(self._inbox(runtime).exists())

    def test_empty_text_is_rejected(self):
        runtime = self._activate()
        result = inbox.append_chat(
            self.state_dir, user_id="1", display_name="v",
            text="", msg_id="m1")
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(self._inbox(runtime).exists())


class TestSeenSidecar(ChatInboxTestBase):
    def test_seen_file_stays_bounded(self):
        runtime = self._activate()
        seen = runtime_directory(
            self.state_dir, runtime["runtime_id"]) / "chat_inbox.seen"
        for i in range(inbox.SEEN_COMPACT_AT + 10):
            inbox.append_chat(self.state_dir, user_id="1", display_name="v",
                              text="x", msg_id=f"m{i}")
        ids = [line for line in
               seen.read_text(encoding="utf-8").splitlines() if line]
        # The file never exceeds the compaction threshold.
        self.assertLessEqual(len(ids), inbox.SEEN_COMPACT_AT)
        # The newest ids survive compaction; the oldest were evicted.
        self.assertIn(f"m{inbox.SEEN_COMPACT_AT + 9}", ids)
        self.assertNotIn("m0", ids)


class TestMain(unittest.TestCase):
    def test_main_appends_and_skips(self):
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td) / "run"
            store = game_switch.GameSwitchStore(state_dir)
            store.initialize()
            state, _ = store.canonical.load()
            state["phase"] = "ready"
            state["active"] = _runtime()
            state["next_generation"] = state["active"]["generation"] + 1
            store.canonical.save(state)
            runtime_directory(
                state_dir, state["active"]["runtime_id"]).mkdir(parents=True)

            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = inbox.main([
                    "--state-dir", str(state_dir),
                    "--user-id", "42",
                    "--display-name", "viewer",
                    "--text", "!puyo join",
                    "--msg-id", "abc-1",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(buf.getvalue())["status"], "appended")

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = inbox.main([
                    "--state-dir", str(state_dir),
                    "--user-id", "42",
                    "--display-name", "viewer",
                    "--text", "!puyo join",
                    "--msg-id", "abc-1",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(buf.getvalue()),
                             {"status": "skipped", "reason": "duplicate"})

    def test_main_unresolvable_config_is_noop(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = inbox.main([
                "--config", "/nonexistent/none.toml",
                "--user-id", "42", "--display-name", "v", "--text", "x",
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
