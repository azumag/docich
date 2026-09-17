from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.trading import soren_output  # noqa: E402


class TestSorenOutputAdapter(unittest.TestCase):
    def test_relative_webui_soren_root_resolves_inside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "docich.toml"
            cfg.write_text('[webui]\nsoren_root = "runtime/soren"\n', encoding="utf-8")
            g = config.load_global(root, config_path=cfg)
            self.assertEqual(soren_output.resolve_soren_root(g), (root / "runtime/soren").resolve())

    def test_existing_sorengame_runtime_root_wins_over_reference_submodule(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime-soren"
            runtime.mkdir()
            games = root / "config" / "games"
            games.mkdir(parents=True)
            (games / "sorengame.toml").write_text(
                "[game]\nname = \"sorengame\"\ntitle = \"Soren\"\nadapter = \"soren\"\n"
                f"[soren]\nroot = \"{runtime}\"\n",
                encoding="utf-8",
            )
            reference = root / "games" / "soviet_now"
            reference.mkdir(parents=True)
            (reference / "eloop_lib.sh").write_text("# ref\n", encoding="utf-8")
            g = config.load_global(root)
            self.assertEqual(soren_output.resolve_soren_root(g), runtime.resolve())

    def test_overlay_uses_shared_strict_queue_and_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = config.load_global(Path(tmp))
            payload = {"ts": 100, "category": "worker", "title": "PAPER", "body": "x", "level": "info"}
            with mock.patch("docich.trading.soren_output.append_event", return_value=False) as append, \
                 mock.patch("docich.trading.soren_output.regenerate_overlay", return_value=True, create=True) as regenerate:
                soren_output.send_overlay(g, payload)
            root = soren_output.resolve_soren_root(g)
            append.assert_called_once_with(root, payload, strict=True, regenerate=False)
            regenerate.assert_called_once_with(root)

    def test_overlay_regeneration_failure_is_not_acknowledged(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = config.load_global(Path(tmp))
            payload = {"ts": 100, "category": "worker", "title": "PAPER", "body": "x", "level": "info"}
            with mock.patch("docich.trading.soren_output.append_event", return_value=True), \
                 mock.patch("docich.trading.soren_output.regenerate_overlay", return_value=False, create=True):
                with self.assertRaises(soren_output.SorenOutputError):
                    soren_output.send_overlay(g, payload)

    def test_speech_reuses_existing_soren_audio_queue_with_event_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = config.load_global(Path(tmp))
            with mock.patch("docich.webui._enqueue_audio_text", return_value={"ok": True, "dedup": True}) as enqueue:
                soren_output.enqueue_speech(g, "ペーパー速報", event_id="fill:event-a")
            enqueue.assert_called_once_with(
                soren_output.resolve_soren_root(g), "ペーパー速報", "crypto_paper",
                speaker="", delivery_key="fill:event-a",
            )

    def test_paper_corner_intro_is_spoken_only_for_opening(self):
        intro = "PAPER・暗号資産の模擬売買コーナーです。"
        opening = soren_output._paper_corner_speech_text(
            intro + "開始します。", "paper-corner:2026-09-12:opening"
        )
        self.assertTrue(opening.endswith(intro + "開始します。"))
        script = soren_output._paper_corner_speech_text(
            intro + "今日は損益を見ます。", "paper-corner:2026-09-12:script:1"
        )
        self.assertTrue(script.endswith("今日は損益を見ます。"))
        self.assertNotIn(intro, script)

    def test_periodic_paper_corner_report_adds_varied_chatter_without_intro(self):
        intro = "PAPER・暗号資産の模擬売買コーナーです。"
        first = soren_output._paper_corner_speech_text(
            intro + "模擬資金1万円です。", "paper-corner:2026-09-12:0"
        )
        later = soren_output._paper_corner_speech_text(
            intro + "模擬資金1万円です。", "paper-corner:2026-09-12:2"
        )
        self.assertNotIn(intro, first)
        self.assertNotIn(intro, later)
        self.assertIn("模擬資金1万円です。", first)
        self.assertIn("模擬資金1万円です。", later)
        self.assertNotEqual(first, later)
        self.assertIn("BTC/JPY", later)

    def test_persona_pick_is_stable_and_covers_both_voices(self):
        seen = {soren_output.pick_paper_persona(f"paper-corner:2026-09-17:script:{i}") for i in range(40)}
        self.assertEqual(seen, {"chuka", "meriken"})
        for key in ("paper-corner:2026-09-17:opening", "paper-corner:2026-09-17:script:2"):
            self.assertEqual(
                soren_output.pick_paper_persona(key), soren_output.pick_paper_persona(key)
            )

    def test_persona_quips_match_house_speech_rules(self):
        for persona, quips in (
            ("meriken", soren_output._MERIKEN_QUIPS),
            ("chuka", soren_output._CHUKA_QUIPS),
        ):
            self.assertTrue(2 <= len(quips) <= 5)
            for quip in quips:
                self.assertTrue(quip.endswith(("です。", "ます。", "ました。", "でしょう。", "ですけど。")), quip)
                self.assertFalse(quip.endswith("ね。"))
                self.assertNotIn("だ。", quip)
                self.assertNotIn("である", quip)
        self.assertTrue(any("僕" in q for q in soren_output._MERIKEN_QUIPS))
        self.assertTrue(any("私" in q for q in soren_output._CHUKA_QUIPS))
        self.assertTrue(all("ね。" not in q for q in soren_output._MERIKEN_QUIPS))
        # Non-corner events stay plain with the default voice.
        self.assertEqual(soren_output.paper_persona_quip("fill:event-a"), ("chuka", ""))

    def test_speech_text_opens_with_the_delivery_persona_quip(self):
        key = "paper-corner:2026-09-17:script:3"
        persona, quip = soren_output.paper_persona_quip(key)
        spoken = soren_output._paper_corner_speech_text("損益を見ます。", key)
        self.assertTrue(spoken.startswith(quip))
        self.assertTrue(spoken.endswith("損益を見ます。"))
        self.assertIn(persona, ("chuka", "meriken"))

    def test_meriken_delivery_uses_soren91_voice_and_chuka_uses_default(self):
        def key_for(persona):
            for i in range(500):
                key = f"paper-corner:2026-09-17:probe:{i}"
                if soren_output.pick_paper_persona(key) == persona:
                    return key
            raise AssertionError(f"no {persona} key found")

        meriken_key = key_for("meriken")
        chuka_key = key_for("chuka")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("SOREN91_VOICEVOX_SPEAKER=14\n", encoding="utf-8")
            g = config.load_global(root)
            with mock.patch.object(soren_output, "resolve_soren_root", return_value=root), \
                 mock.patch("docich.webui._enqueue_audio_text", return_value={"ok": True}) as enqueue:
                soren_output.enqueue_speech(g, "本文", event_id=meriken_key)
                _, kwargs = enqueue.call_args
                self.assertEqual(kwargs.get("speaker"), "14")
                quip = soren_output.paper_persona_quip(meriken_key)[1]
                self.assertTrue(kwargs and quip in str(enqueue.call_args.args[1]))
            with mock.patch.object(soren_output, "resolve_soren_root", return_value=root), \
                 mock.patch("docich.webui._enqueue_audio_text", return_value={"ok": True}) as enqueue:
                soren_output.enqueue_speech(g, "本文", event_id=chuka_key)
                _, kwargs = enqueue.call_args
                self.assertEqual(kwargs.get("speaker"), "")

    def test_meriken_voice_falls_back_when_env_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(soren_output._meriken_speaker_id(root), "46")


if __name__ == "__main__":
    unittest.main()
