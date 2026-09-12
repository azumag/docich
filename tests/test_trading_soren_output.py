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
                delivery_key="fill:event-a",
            )

    def test_paper_corner_intro_is_spoken_only_for_opening(self):
        intro = "PAPER・暗号資産の模擬売買コーナーです。"
        self.assertEqual(
            soren_output._paper_corner_speech_text(intro + "開始します。", "paper-corner:2026-09-12:opening"),
            intro + "開始します。",
        )
        script = soren_output._paper_corner_speech_text(
            intro + "今日は損益を見ます。", "paper-corner:2026-09-12:script:1"
        )
        self.assertEqual(script, "今日は損益を見ます。")

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


if __name__ == "__main__":
    unittest.main()
