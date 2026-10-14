from __future__ import annotations

from pathlib import Path
import datetime as dt
import os
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.trading import soren_output  # noqa: E402
from docich.meriken_voice import MERIKEN_SPEAKER_DEFAULT, resolve_meriken_speaker  # noqa: E402


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

    def test_speech_drops_leading_conclusion_lead_in(self):
        """Generation already strips it; a durable/replayed or disobedient

        segment must still not open with the lead-in on air.
        """
        body = "結論からお伝えしますと、本日は損益が拮抗しています。"
        for key in (
            "paper-corner:2026-09-23:ai:1",
            "paper-corner:2026-09-23:script:2",
            "paper-corner-manual-abc123def456:2026-09-23:ai:3",
        ):
            self.assertEqual(
                soren_output._paper_corner_speech_text(body, key),
                "本日は損益が拮抗しています。",
            )
        # Non-paper deliveries keep their text verbatim.
        self.assertEqual(
            soren_output._paper_corner_speech_text(body, "fill:event-a"), body
        )

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
        self.assertEqual(personas, {"meriken"})

    def test_every_date_and_all_eight_slots_use_meriken_in_every_paper_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = config.load_global(Path(tmp))
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch("docich.trading.soren_output._retry_pending_hanjuku_terminal"), \
                 mock.patch("docich.webui._enqueue_audio_text", return_value={"ok": True}) as enqueue:
                # Include a leap year's every date, both edges of the year,
                # fresh manual identities, all slots and exact retry/replay.
                for day in range(366):
                    date = dt.date(2028, 1, 1) + dt.timedelta(days=day)
                    for scope, owner in (("paper-corner", ""),
                                         (f"paper-corner-manual-{day:012x}", ""),
                                         (f"paper-corner-operator-{day:012x}", ""),
                                         (f"corner-rotation:{day:012x}", "paper")):
                        for slot in range(1, 9):
                            key = f"{scope}:{date}:script:{slot}"
                            self.assertEqual(soren_output.pick_paper_persona(key, corner_owner=owner), "meriken")
                            for _ in range(2):
                                soren_output.enqueue_speech(g, "本文", event_id=key, corner_owner=owner)
                                self.assertEqual(enqueue.call_args.kwargs["speaker"], "14")
                                self.assertEqual(enqueue.call_args.kwargs["delivery_key"], key)
                                self.assertEqual(enqueue.call_args.args[1], "本文")

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

    def test_speech_text_is_identical_across_dates_and_scopes(self):
        body = "損益を見ます。"
        for key in ("paper-corner:2026-09-01:script:3",
                    "paper-corner:2026-10-05:script:3",
                    "paper-corner-manual-abcdef123456:2026-10-05:script:3",
                    "paper-corner-operator-abcdef123456:2026-10-05:script:3"):
            self.assertEqual(soren_output._paper_corner_speech_text(body, key), body)

    def test_paper_delivery_uses_soren91_override_for_opening_and_notices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("SOREN91_VOICEVOX_SPEAKER=24\n", encoding="utf-8")
            g = config.load_global(root)
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(soren_output, "resolve_soren_root", return_value=root), \
                 mock.patch("docich.webui._enqueue_audio_text", return_value={"ok": True}) as enqueue:
                for scope in ("paper-corner", "paper-corner-manual-abcd", "paper-corner-operator-abcd"):
                    for suffix in ("opening", "switch-notice", "closing", "ai:1"):
                        key = f"{scope}:2026-10-05:{suffix}"
                        soren_output.enqueue_speech(g, "本文", event_id=key)
                        self.assertEqual(enqueue.call_args.kwargs["speaker"], "24")
                        self.assertEqual(enqueue.call_args.kwargs["delivery_key"], key)
                        self.assertEqual(enqueue.call_args.args[1], "本文")

    def test_non_paper_deliveries_keep_their_text_and_default_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = config.load_global(Path(tmp))
            with mock.patch("docich.trading.soren_output.resolve_meriken_speaker") as resolve, \
                 mock.patch("docich.webui._enqueue_audio_text", return_value={"ok": True}) as enqueue:
                for key in ("", "fill:event-a", "arbitrage:event-a", "stocks:report", "fx:report",
                            "soren91:announce", "weather:opening", "paper-worker:report", "paper-cornerish:x"):
                    soren_output.enqueue_speech(g, "結論からお伝えしますと、本文です。", event_id=key)
                    self.assertEqual(enqueue.call_args.kwargs["speaker"], "")
                    self.assertEqual(enqueue.call_args.kwargs["delivery_key"], key)
                    self.assertEqual(enqueue.call_args.args[1], "結論からお伝えしますと、本文です。")
            resolve.assert_not_called()

    def test_meriken_voice_falls_back_when_env_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(resolve_meriken_speaker(config.load_global(root), root), "14")

    def test_meriken_voice_override_precedence_and_invalid_values(self):
        cases = (
            (" 24 ", "46", "voicevox_speaker = 14", "24"),
            ("bad value", "'46'", "voicevox_speaker = 14", "46"),
            ("", "46 # runtime override", "voicevox_speaker = 14", "46"),
            ("", "", "voicevox_speaker = 24", "24"),
            ("$(secret)", "bad value", "voicevox_speaker = 24", "24"),
            ("a" * 65, "bad;value", "voicevox_speaker = true", "14"),
            ("\ninvalid value", "", 'voicevox_speaker = "bad value"', "14"),
            ("", "", "voicevox_speaker = []", "14"),
            ("not-a-style", "14.0", "voicevox_speaker = -1", "14"),
            ("-1", "None", "voicevox_speaker = 24", "24"),
            ("", "", "voicevox_speaker = 0", "0"),
        )
        for process, dotenv, game, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                games = root / "config/games"
                games.mkdir(parents=True)
                (games / "soren91.toml").write_text(
                    '[game]\nname = "soren91"\ntitle = "Soren91"\nadapter = "soren91"\n'
                    '[soren91]\n' + game + '\n', encoding="utf-8",
                )
                (root / ".env").write_text("SOREN91_VOICEVOX_SPEAKER=" + dotenv + "\n", encoding="utf-8")
                with mock.patch.dict(os.environ, {"SOREN91_VOICEVOX_SPEAKER": process}, clear=True):
                    self.assertEqual(resolve_meriken_speaker(config.load_global(root), root), expected)

    def test_meriken_voice_handles_unreadable_or_malformed_sources(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            g = config.load_global(root)
            with mock.patch("docich.webui._read_dotenv_dict", side_effect=OSError("private value")), \
                 mock.patch("docich.meriken_voice.load_game", side_effect=ValueError("private value")):
                self.assertEqual(resolve_meriken_speaker(g, root), "14")

    def test_public_meriken_default_matches_soren91_game_config(self):
        path = Path(__file__).resolve().parents[1] / "config/games/soren91.toml"
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(str(raw["soren91"]["voicevox_speaker"]), MERIKEN_SPEAKER_DEFAULT)


def test_invalid_meriken_overrides_and_errors_are_not_logged(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SOREN91_VOICEVOX_SPEAKER", "synthetic-private-value")
    (tmp_path / ".env").write_text("SOREN91_VOICEVOX_SPEAKER=synthetic-private-value\n", encoding="utf-8")
    g = config.load_global(tmp_path)
    with mock.patch("docich.meriken_voice.load_game", side_effect=ValueError("synthetic-private-value")):
        assert resolve_meriken_speaker(g, tmp_path) == "14"
    output = capsys.readouterr()
    assert output.out == output.err == ""


if __name__ == "__main__":
    unittest.main()


class TestRuntimeFencedAudio(unittest.TestCase):
    def test_optional_fence_is_a_literal_fourth_argument_and_legacy_stays_three(self):
        import json
        import subprocess
        from types import SimpleNamespace
        identity = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcdef', 'generation': 1,
                    'lease_id': 'lease', 'expires_at': 1234}
        with mock.patch.object(soren_output, 'resolve_soren_root', return_value=Path('/soren')), \
             mock.patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            soren_output.enqueue_audio_text(None, 'text', context='hanjuku_commentary', runtime_fence=identity)
            cmd = run.call_args.args[0]
            self.assertEqual(json.loads(cmd[-1]), identity)
            self.assertIn('"$3"', cmd[2])
            soren_output.enqueue_audio_text(None, 'other', context='soren91')
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[-3:], ['other', 'soren91', ''])
            self.assertNotIn('"$3"', cmd[2])

    def test_later_audio_does_not_overtake_a_pending_terminal_recap(self):
        import subprocess
        from types import SimpleNamespace

        events = []
        g = SimpleNamespace(state_dir=Path("/state"), repo_root=Path("/repo"))
        with mock.patch(
            "docich.hanjuku_narration.retry_pending_terminal_deliveries",
            side_effect=lambda _g, **_kwargs: events.append("terminal-drain") or False,
        ), mock.patch.object(soren_output, "resolve_soren_root", return_value=Path("/soren")), \
             mock.patch.object(subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            with self.assertRaises(soren_output.SorenOutputError):
                soren_output.enqueue_audio_text(g, "次の音声", context="crypto_paper")

        self.assertEqual(events, ["terminal-drain"])
        run.assert_not_called()
