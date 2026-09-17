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

    def test_persona_is_fixed_for_every_segment_of_one_corner(self):
        """One persona hosts the whole corner; it must not alternate segment

        to segment (a listener hearing both voices swap mid-corner reported
        this as a bug, not a feature).
        """
        keys = [
            "paper-corner:2026-09-17:opening",
            "paper-corner:2026-09-17:script:1",
            "paper-corner:2026-09-17:script:2",
            "paper-corner:2026-09-17:script:8",
            "paper-corner:2026-09-17:chatter:9",
            "paper-corner:2026-09-17:switch-notice",
        ]
        personas = {soren_output.pick_paper_persona(key) for key in keys}
        self.assertEqual(len(personas), 1, personas)

    def test_persona_pick_covers_both_voices_across_different_corners(self):
        # Persona varies by corner identity (scope+date), so different dates
        # (production) or different manual-run uuids still cover both voices.
        seen = {
            soren_output.pick_paper_persona(f"paper-corner:2026-09-{day:02d}:script:1")
            for day in range(1, 29)
        }
        self.assertEqual(seen, {"chuka", "meriken"})

    def test_speech_text_has_no_fixed_persona_preamble(self):
        """Both personas used to open with one of a 3-line canned quip pool,

        which read as "the same fixed preamble" to a listener regardless of
        which persona was speaking (reported 2026-09-18). The quip existed
        only to inject entropy against the player-side content-hash dedupe;
        that dedupe now exempts this channel entirely upstream (soviet_now
        #431), so the quip has no remaining purpose and is removed. Speech
        text must depend only on the body/segment, not on which persona a
        given corner happens to be hosted by.
        """
        body = "損益を見ます。"
        for key in (
            "paper-corner:2026-09-17:script:3",
            "paper-corner-manual-abc123def456:2026-09-18:script:2",
        ):
            spoken = soren_output._paper_corner_speech_text(body, key)
            self.assertEqual(spoken, body)

    def test_speech_text_is_identical_across_corners_with_different_personas(self):
        # Two corners that land on different personas must still speak the
        # same body text identically -- no per-persona prefix.
        body = "損益を見ます。"
        meriken_key = None
        chuka_key = None
        for day in range(1, 29):
            key = f"paper-corner:2026-09-{day:02d}:script:3"
            persona = soren_output.pick_paper_persona(key)
            if persona == "meriken" and meriken_key is None:
                meriken_key = key
            elif persona == "chuka" and chuka_key is None:
                chuka_key = key
            if meriken_key and chuka_key:
                break
        self.assertIsNotNone(meriken_key)
        self.assertIsNotNone(chuka_key)
        self.assertEqual(
            soren_output._paper_corner_speech_text(body, meriken_key),
            soren_output._paper_corner_speech_text(body, chuka_key),
        )

    def test_meriken_delivery_uses_soren91_voice_and_chuka_uses_default(self):
        def key_for(persona):
            # Persona is now fixed per corner (scope+date), so vary the date
            # to find one corner hosted by each voice.
            for day in range(1, 29):
                key = f"paper-corner:2026-09-{day:02d}:probe:0"
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
                self.assertEqual(enqueue.call_args.args[1], "本文")
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
