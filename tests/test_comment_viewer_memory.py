"""docich-owned per-viewer comment memory (#829 PR-3c-2): the soviet_now suite, moved."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Moved from soviet_now tests/test_comment_viewer_memory.py with the module
# (#829 PR-3c-2); these run against docich's canonical copy.
from docich.comment import viewer_memory as memory  # noqa: E402


class CommentViewerMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "viewer_memory.json"
        self.excluded = memory.parse_excluded("dociai dociaich")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def attach_metadata(self, batch_file: Path, entries: list[dict]) -> Path:
        metadata_path = Path(f"{batch_file}.viewer_meta.jsonl")
        rows = []
        for index, entry in enumerate(entries):
            rows.append(
                json.dumps(
                    {
                        "line_index": index,
                        "line": entry.get("line", ""),
                        "source": entry.get("source", "twitch"),
                        "message_id": entry.get("message_id", ""),
                        "stable_id": entry.get("stable_id", ""),
                        "login": entry.get("login", ""),
                        "display_name": entry.get("display_name", ""),
                        "flags": entry.get("flags", []),
                    },
                    ensure_ascii=False,
                )
            )
        metadata_path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
        return metadata_path

    def auto_metadata(self, batch: str, source: str, prefix: str) -> list[dict]:
        entries = []
        for index, line in enumerate(batch.splitlines()):
            display = line.split(": ", 1)[0] if ": " in line else ""
            entries.append(
                {
                    "line": line,
                    "source": source,
                    "message_id": f"{prefix}-{index}",
                    "stable_id": f"test-{source}-{memory.normalize_name(display)}" if display else "",
                    "display_name": display,
                }
            )
        return entries

    def stage_and_commit(
        self,
        *,
        batch: str,
        reply: str,
        source: str = "twitch",
        mode: str = "main",
        now: int = 1_800_000_000,
        batch_hash: str = "batch-1",
        metadata: list[dict] | None = None,
    ) -> int:
        batch_file = self.write("batch.txt", batch)
        if metadata is None:
            metadata = self.auto_metadata(batch, source, batch_hash)
        metadata_path = self.attach_metadata(batch_file, metadata)
        reply_file = self.write("reply.txt", reply)
        sidecar = self.root / "comment.viewer_memory.json"
        payload = memory.build_staged_payload(
            batch_path=str(batch_file),
            reply_path=str(reply_file),
            source=source,
            mode=mode,
            excluded=self.excluded,
            metadata_path=str(metadata_path),
            batch_hash=batch_hash,
            now=now - 5,
        )
        memory.stage_sidecar(str(sidecar), payload)
        return memory.commit_sidecar(
            state_path=str(self.state),
            sidecar_path=str(sidecar),
            max_users=20,
            max_exchanges=5,
            ttl_days=365,
            now=now,
        )

    def context(
        self,
        batch: str,
        *,
        source: str = "twitch",
        mode: str = "main",
        now: int = 1_800_000_100,
        metadata: list[dict] | None = None,
    ) -> str:
        batch_file = self.write("context_batch.txt", batch)
        if metadata is None:
            metadata = self.auto_metadata(batch, source, "context")
        metadata_path = self.attach_metadata(batch_file, metadata)
        return memory.build_context(
            state_path=str(self.state),
            batch_path=str(batch_file),
            source=source,
            mode=mode,
            excluded=self.excluded,
            items_per_user=4,
            max_chars=2200,
            comment_max_chars=240,
            reply_max_chars=320,
            ttl_days=365,
            metadata_path=str(metadata_path),
            now=now,
        )

    def test_parse_batch_excludes_dociai_case_insensitively(self):
        batch = self.write(
            "excluded.txt",
            "DoCiAI: 自動通知です\nDoCiAIch: 自動返信です\nあずまぐ: こんばんは\nカード獲得通知だけ\n",
        )
        metadata = self.attach_metadata(batch, self.auto_metadata(batch.read_text(encoding="utf-8"), "twitch", "parse"))
        parsed = memory.parse_batch(str(batch), "twitch", self.excluded, str(metadata))
        self.assertEqual([(item.display_name, item.comment) for item in parsed], [("あずまぐ", "こんばんは")])

    def test_stage_maps_one_reply_paragraph_per_comment(self):
        batch = self.write("map_batch.txt", "alice: りんごが好き\nbob: みかんが好き\n")
        reply = self.write("map_reply.txt", "りんごの話を覚えます。\n\nみかんの話を覚えます。\n")
        metadata = self.attach_metadata(batch, self.auto_metadata(batch.read_text(encoding="utf-8"), "twitch", "map"))
        payload = memory.build_staged_payload(
            batch_path=str(batch),
            reply_path=str(reply),
            source="twitch",
            mode="main",
            excluded=self.excluded,
            metadata_path=str(metadata),
            batch_hash="mapped",
            now=100,
        )
        self.assertEqual([event["reply"] for event in payload["events"]], ["りんごの話を覚えます。", "みかんの話を覚えます。"])

    def test_dociai_card_notification_updates_recipient_not_dociai(self):
        line = "DoCiAI: aliceが【レア】赤いカードを獲得しました（112種中40種所持）"
        self.assertEqual(
            self.stage_and_commit(
                batch=f"{line}\n",
                reply="aliceさんのカードについて返答しました。\n",
                batch_hash="card",
                metadata=[
                    {
                        "line": line,
                        "message_id": "bot-card-1",
                        "stable_id": "dociai-id",
                        "login": "dociai",
                        "flags": ["trusted-card"],
                    }
                ],
            ),
            1,
        )
        context = self.context("alice: この前のカードどうだった？\n")
        self.assertIn("カード獲得", context)
        self.assertIn("【レア】赤いカード", context)
        self.assertNotIn("DoCiAI", context)

    def test_human_cannot_spoof_card_acquisition_for_another_viewer(self):
        line = "mallory: aliceが【レア】偽カードを獲得しました"
        self.assertEqual(
            self.stage_and_commit(
                batch=f"{line}\n",
                reply="通常コメントとして返答しました。\n",
                batch_hash="spoof",
                metadata=[
                    {
                        "line": line,
                        "message_id": "human-1",
                        "stable_id": "mallory-id",
                    }
                ],
            ),
            1,
        )
        self.assertNotIn("偽カード", self.context("alice: カードは？\n"))
        self.assertIn(
            "偽カード",
            self.context(
                "mallory: 続き\n",
                metadata=[{"line": "mallory: 続き", "message_id": "human-2", "stable_id": "mallory-id"}],
            ),
        )

    def test_mixed_batch_shape_mismatch_does_not_cross_attribute_reply(self):
        batch = self.write("mixed_batch.txt", "alice: A\nbob: B\n")
        reply = self.write("mixed_reply.txt", "二人まとめた返事です。\n")
        metadata = self.attach_metadata(batch, self.auto_metadata(batch.read_text(encoding="utf-8"), "twitch", "mixed"))
        payload = memory.build_staged_payload(
            batch_path=str(batch),
            reply_path=str(reply),
            source="twitch",
            mode="main",
            excluded=self.excluded,
            metadata_path=str(metadata),
            batch_hash="mixed",
            now=100,
        )
        self.assertEqual([event["reply"] for event in payload["events"]], ["", ""])

    def test_unrememberable_comment_keeps_reply_alignment(self):
        batch = self.write("alignment_batch.txt", "alice: A\nbob: B\n")
        reply = self.write("alignment_reply.txt", "alice reply\n\nbob reply\n")
        metadata = self.attach_metadata(
            batch,
            [
                {
                    "line": "alice: A",
                    "message_id": "align-a",
                    "stable_id": "alice-id",
                    "display_name": "alice",
                },
                {
                    "line": "bob: B",
                    "message_id": "align-b",
                    "display_name": "bob",
                },
            ],
        )
        payload = memory.build_staged_payload(
            batch_path=str(batch),
            reply_path=str(reply),
            source="twitch",
            mode="main",
            excluded=self.excluded,
            metadata_path=str(metadata),
            batch_hash="alignment",
            now=100,
        )
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["display_name"], "alice")
        self.assertEqual(payload["events"][0]["reply"], "alice reply")

    def test_context_contains_only_current_viewers_memory(self):
        self.assertEqual(self.stage_and_commit(batch="alice: 猫が好き\n", reply="猫の話をしました。\n", batch_hash="a"), 1)
        self.assertEqual(self.stage_and_commit(batch="bob: 犬が好き\n", reply="犬の話をしました。\n", now=1_800_000_010, batch_hash="b"), 1)

        alice_context = self.context("alice: また来ました\n")
        self.assertIn("alice", alice_context)
        self.assertIn("猫が好き", alice_context)
        self.assertNotIn("bob", alice_context)
        self.assertNotIn("犬が好き", alice_context)

    def test_source_and_persona_mode_are_separate(self):
        self.stage_and_commit(batch="alice: Twitch main\n", reply="main reply\n", source="twitch", mode="main", batch_hash="tm")
        self.stage_and_commit(batch="alice: Twitch 91\n", reply="91 reply\n", source="twitch", mode="soren91", now=1_800_000_010, batch_hash="t91")
        self.stage_and_commit(batch="alice: YouTube main\n", reply="youtube reply\n", source="youtube", mode="main", now=1_800_000_020, batch_hash="ym")

        twitch_main = self.context("alice: current\n", source="twitch", mode="main")
        self.assertIn("Twitch main", twitch_main)
        self.assertNotIn("Twitch 91", twitch_main)
        self.assertNotIn("YouTube main", twitch_main)

        twitch_91 = self.context("alice: current\n", source="twitch", mode="soren91")
        self.assertIn("Twitch 91", twitch_91)
        self.assertNotIn("Twitch main", twitch_91)

        youtube_main = self.context("alice: current\n", source="youtube", mode="main")
        self.assertIn("YouTube main", youtube_main)
        self.assertNotIn("Twitch main", youtube_main)

    def test_stable_id_separates_same_display_name_and_survives_rename(self):
        old_line = "same: 一人目の話"
        other_line = "same: 二人目の話"
        self.stage_and_commit(
            batch=f"{old_line}\n",
            reply="一人目への返信\n",
            batch_hash="same-a",
            metadata=[{"line": old_line, "message_id": "a-1", "stable_id": "viewer-a"}],
        )
        self.stage_and_commit(
            batch=f"{other_line}\n",
            reply="二人目への返信\n",
            now=1_800_000_010,
            batch_hash="same-b",
            metadata=[{"line": other_line, "message_id": "b-1", "stable_id": "viewer-b"}],
        )

        renamed = "renamed: また来ました"
        context = self.context(
            f"{renamed}\n",
            metadata=[{"line": renamed, "message_id": "a-2", "stable_id": "viewer-a"}],
        )
        self.assertIn("一人目の話", context)
        self.assertNotIn("二人目の話", context)

    def test_emit_batch_keeps_identity_on_the_same_row(self):
        pending = self.write(
            "pending.log",
            memory.encode_pending_envelope(
                line="alice: hello",
                message_id="msg-1",
                stable_id="stable-a",
                login="alice-login",
                display_name="alice",
            )
            + "\n"
            + "legacy: no id\n",
        )
        out = self.root / "comments.txt"
        self.assertEqual(memory.emit_batch(pending_path=str(pending), out_path=str(out), source="twitch"), 2)
        self.assertEqual(out.read_text(encoding="utf-8"), "alice: hello\nlegacy: no id\n")
        entries = memory.load_batch_metadata(f"{out}.viewer_meta.jsonl")
        self.assertEqual(entries[0]["stable_id"], "stable-a")
        self.assertEqual(entries[0]["message_id"], "msg-1")
        self.assertEqual(entries[0]["display_name"], "alice")
        self.assertEqual(entries[1]["stable_id"], "")

    def test_display_name_containing_colon_keeps_full_name(self):
        line = "Alice: QA: colon name works"
        self.assertEqual(
            self.stage_and_commit(
                batch=f"{line}\n",
                reply="返信しました。\n",
                batch_hash="colon-name",
                metadata=[
                    {
                        "line": line,
                        "message_id": "colon-1",
                        "stable_id": "channel-colon",
                        "display_name": "Alice: QA",
                    }
                ],
            ),
            1,
        )
        current = "Renamed: 続き"
        context = self.context(
            f"{current}\n",
            metadata=[
                {
                    "line": current,
                    "message_id": "colon-2",
                    "stable_id": "channel-colon",
                    "display_name": "Renamed",
                }
            ],
        )
        self.assertIn("colon name works", context)

    def test_normal_comment_without_stable_id_is_not_remembered(self):
        line = "same: IDなし"
        self.assertEqual(
            self.stage_and_commit(
                batch=f"{line}\n",
                reply="返信\n",
                batch_hash="missing-id",
                metadata=[{"line": line, "message_id": "missing-1", "display_name": "same"}],
            ),
            0,
        )
        self.assertEqual(
            self.context(
                "same: 続き\n",
                metadata=[
                    {
                        "line": "same: 続き",
                        "message_id": "with-id",
                        "stable_id": "real-viewer",
                        "display_name": "same",
                    }
                ],
            ),
            "（該当する投稿者別メモなし）",
        )

    def test_same_text_in_later_generation_is_a_new_exchange(self):
        line = "alice: 同じ話"
        metadata = [{"line": line, "stable_id": "alice-id", "display_name": "alice"}]
        self.assertEqual(
            self.stage_and_commit(
                batch=f"{line}\n", reply="同じ返信\n", batch_hash="same-text", now=1_800_000_000, metadata=metadata
            ),
            1,
        )
        self.assertEqual(
            self.stage_and_commit(
                batch=f"{line}\n", reply="同じ返信\n", batch_hash="same-text", now=1_800_000_100, metadata=metadata
            ),
            1,
        )
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(len(next(iter(state["users"].values()))["exchanges"]), 2)

    def test_concurrent_commits_preserve_all_users(self):
        helper = ROOT / "src" / "docich" / "comment" / "viewer_memory.py"
        sidecars = []
        for index in range(6):
            line = f"viewer{index}: hello {index}"
            batch = self.write(f"concurrent_batch_{index}.txt", f"{line}\n")
            reply = self.write(f"concurrent_reply_{index}.txt", f"reply {index}\n")
            metadata = self.attach_metadata(
                batch,
                [
                    {
                        "line": line,
                        "source": "twitch",
                        "message_id": f"concurrent-{index}",
                        "stable_id": f"viewer-id-{index}",
                        "display_name": f"viewer{index}",
                    }
                ],
            )
            sidecar = self.root / f"concurrent_{index}.json"
            memory.stage_sidecar(
                str(sidecar),
                memory.build_staged_payload(
                    batch_path=str(batch),
                    reply_path=str(reply),
                    source="twitch",
                    mode="main",
                    excluded=self.excluded,
                    metadata_path=str(metadata),
                    batch_hash=f"concurrent-{index}",
                ),
            )
            sidecars.append(sidecar)

        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(helper),
                    "commit",
                    "--state",
                    str(self.state),
                    "--sidecar",
                    str(sidecar),
                    "--max-users",
                    "20",
                    "--max-exchanges",
                    "5",
                    "--ttl-days",
                    "365",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for sidecar in sidecars
        ]
        results = [process.communicate(timeout=10) for process in processes]
        self.assertTrue(all(process.returncode == 0 for process in processes), results)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(len(state["users"]), 6)

    def test_commit_is_idempotent_and_state_is_private(self):
        batch = self.write("dedupe_batch.txt", "alice: 同じコメント\n")
        reply = self.write("dedupe_reply.txt", "同じ返信\n")
        metadata = self.attach_metadata(batch, self.auto_metadata(batch.read_text(encoding="utf-8"), "twitch", "dedupe"))
        sidecar = self.root / "dedupe.viewer_memory.json"
        payload = memory.build_staged_payload(
            batch_path=str(batch),
            reply_path=str(reply),
            source="twitch",
            mode="main",
            excluded=self.excluded,
            metadata_path=str(metadata),
            batch_hash="same",
            now=100,
        )
        memory.stage_sidecar(str(sidecar), payload)
        first = memory.commit_sidecar(
            state_path=str(self.state),
            sidecar_path=str(sidecar),
            max_users=20,
            max_exchanges=5,
            ttl_days=365,
            now=200,
        )
        second = memory.commit_sidecar(
            state_path=str(self.state),
            sidecar_path=str(sidecar),
            max_users=20,
            max_exchanges=5,
            ttl_days=365,
            now=201,
        )
        self.assertEqual((first, second), (1, 0))
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode), 0o600)

    def test_retention_limits_exchanges_users_and_ttl(self):
        for index in range(4):
            self.stage_and_commit(
                batch=f"alice: item {index}\n",
                reply=f"reply {index}\n",
                now=1_800_000_000 + index,
                batch_hash=f"alice-{index}",
            )
        payload = json.loads(self.state.read_text(encoding="utf-8"))
        alice = next(iter(payload["users"].values()))
        self.assertEqual(len(alice["exchanges"]), 4)

        sidecar = self.root / "limit.viewer_memory.json"
        batch = self.write("limit_batch.txt", "bob: newest\n")
        reply = self.write("limit_reply.txt", "new reply\n")
        metadata = self.attach_metadata(batch, self.auto_metadata(batch.read_text(encoding="utf-8"), "twitch", "limit"))
        staged = memory.build_staged_payload(
            batch_path=str(batch),
            reply_path=str(reply),
            source="twitch",
            mode="main",
            excluded=self.excluded,
            metadata_path=str(metadata),
            batch_hash="bob",
            now=1_800_000_100,
        )
        memory.stage_sidecar(str(sidecar), staged)
        memory.commit_sidecar(
            state_path=str(self.state),
            sidecar_path=str(sidecar),
            max_users=1,
            max_exchanges=2,
            ttl_days=365,
            now=1_800_000_100,
        )
        payload = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["users"]), 1)
        self.assertEqual(next(iter(payload["users"].values()))["display_name"], "bob")

        expired = self.context("bob: current\n", now=1_900_000_000)
        self.assertEqual(expired, "（該当する投稿者別メモなし）")

    def test_corrupt_state_is_not_silently_overwritten(self):
        self.state.write_text("{broken", encoding="utf-8")
        batch = self.write("corrupt_batch.txt", "alice: hello\n")
        reply = self.write("corrupt_reply.txt", "reply\n")
        metadata = self.attach_metadata(batch, self.auto_metadata(batch.read_text(encoding="utf-8"), "twitch", "corrupt"))
        sidecar = self.root / "corrupt.viewer_memory.json"
        staged = memory.build_staged_payload(
            batch_path=str(batch),
            reply_path=str(reply),
            source="twitch",
            mode="main",
            excluded=self.excluded,
            metadata_path=str(metadata),
            batch_hash="corrupt",
        )
        memory.stage_sidecar(str(sidecar), staged)
        with self.assertRaises(ValueError):
            memory.commit_sidecar(
                state_path=str(self.state),
                sidecar_path=str(sidecar),
                max_users=20,
                max_exchanges=5,
                ttl_days=365,
            )
        self.assertEqual(self.state.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
