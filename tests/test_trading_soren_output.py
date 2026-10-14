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

class TestAudioQueuePathAdapter(unittest.TestCase):
    """Pure callbacks: no shell, audio worker, real .env or queue files."""

    def _queue_case(self, environment, dotenv, expected, *, publish=True, fence=None):
        import fnmatch
        import json
        import subprocess
        from types import SimpleNamespace
        from docich import yoso_narration as yn

        root = Path("/synthetic/soren")
        context = "hanjuku_commentary" if fence is not None else "yosou:intro"
        markers = set()
        calls = []

        def read_text(path, *args, **kwargs):
            self.assertEqual(path, root / ".env")
            if dotenv is None:
                raise FileNotFoundError("synthetic missing .env")
            return dotenv

        def glob(path, pattern):
            return iter(marker for marker in markers
                        if marker.parent == path and fnmatch.fnmatchcase(marker.name, pattern))

        def run(command, **kwargs):
            # Model only the shell's literal queue lookup, not shell execution.
            child = kwargs.get("env", os.environ)
            queue = Path(child.get("COMMENT_QUEUE_DIR") or "tmp/.comment_queue")
            if not queue.is_absolute():
                queue = Path(kwargs["cwd"]) / queue
            calls.append((command, kwargs, queue))
            if publish:
                markers.add(queue / f"comment_announce_1_2_3_{command[4]}.txt")
            return SimpleNamespace(returncode=0)

        with mock.patch.dict(os.environ, environment, clear=True), \
             mock.patch.object(soren_output, "resolve_soren_root", return_value=root), \
             mock.patch.object(yn, "resolve_soren_root", return_value=root), \
             mock.patch.object(soren_output, "_retry_pending_hanjuku_terminal") as drain, \
             mock.patch.object(Path, "read_text", read_text), \
             mock.patch.object(Path, "glob", glob), \
             mock.patch.object(subprocess, "run", side_effect=run):
            before = dict(os.environ)
            soren_output.enqueue_audio_text(
                None, "synthetic intro", context=context, speaker="14",
                runtime_fence=fence,
            )
            drain.assert_called_once_with(None)
            command, kwargs, queue = calls[0]
            self.assertEqual(queue, Path(expected))
            self.assertEqual(yn.voice_queue_dir(None), queue)
            self.assertIn("env", kwargs)
            self.assertTrue(Path(kwargs["env"]["COMMENT_QUEUE_DIR"]).is_absolute())
            self.assertEqual(kwargs["env"], {**before, "COMMENT_QUEUE_DIR": str(queue)})
            self.assertEqual(dict(os.environ), before)
            self.assertIsNot(kwargs["env"], os.environ)
            self.assertEqual({key: value for key, value in kwargs.items() if key != "env"},
                             {"cwd": str(root), "text": True, "capture_output": True,
                              "timeout": 30.0, "check": False})
            self.assertEqual(command[:2], ["bash", "-c"])
            self.assertEqual(command[3:6], ["synthetic intro", context, "14"])
            self.assertNotIn(".env", command[2])
            if fence is None:
                self.assertEqual(len(command), 6)
                self.assertNotIn('"$3"', command[2])
            else:
                self.assertEqual(json.loads(command[6]), fence)
                self.assertIn('"$3"', command[2])
            pending = int(publish and fence is None)
            self.assertEqual(yn.voice_items_in_flight(None), pending)
            if publish:
                queued = next(iter(markers))
                markers.remove(queued)
                markers.add(queued.with_suffix(".playing"))
                self.assertEqual(yn.voice_items_in_flight(None), pending)
                markers.clear()
            self.assertEqual(yn.voice_items_in_flight(None), 0)

    def test_audio_queue_matches_reader_for_literal_overrides(self):
        inherited = {"PATH": "/synthetic/bin", "COMMENT_AUDIO_DEDUP_TTL_SEC": "120"}
        cases = (
            ({}, "", "/synthetic/soren/tmp/.comment_queue"),
            ({}, None, "/synthetic/soren/tmp/.comment_queue"),
            ({"COMMENT_QUEUE_DIR": ""}, "", "/synthetic/soren/tmp/.comment_queue"),
            ({}, "COMMENT_QUEUE_DIR=\n", "/synthetic/soren/tmp/.comment_queue"),
            ({}, "COMMENT_QUEUE_DIR=tmp/env_queue\n", "/synthetic/soren/tmp/env_queue"),
            ({}, 'COMMENT_QUEUE_DIR="tmp/env_queue" # literal\n', "/synthetic/soren/tmp/env_queue"),
            ({}, "COMMENT_QUEUE_DIR=/synthetic/absolute\n", "/synthetic/absolute"),
            ({"COMMENT_QUEUE_DIR": ""}, "COMMENT_QUEUE_DIR=tmp/env_queue\n", "/synthetic/soren/tmp/env_queue"),
            ({"COMMENT_QUEUE_DIR": "tmp/custom"}, "", "/synthetic/soren/tmp/custom"),
            ({"COMMENT_QUEUE_DIR": "/synthetic/custom"}, "", "/synthetic/custom"),
            ({"COMMENT_QUEUE_DIR": "tmp/winner"}, "COMMENT_QUEUE_DIR=tmp/ignored\n", "/synthetic/soren/tmp/winner"),
            ({}, "UNRELATED_DOTENV=synthetic\nCOMMENT_QUEUE_DIR=tmp/env_queue\n", "/synthetic/soren/tmp/env_queue"),
        )
        for environment, dotenv, expected in cases:
            with self.subTest(environment=environment, dotenv=dotenv):
                self._queue_case({**inherited, **environment}, dotenv, expected)

    def test_audio_queue_environment_does_not_change_fence_or_sink_noops(self):
        fence = {"game": "hanjuku-hero", "runtime_id": "g1-abcdef", "generation": 1,
                 "lease_id": "synthetic-lease", "expires_at": 1234}
        self._queue_case({}, "COMMENT_QUEUE_DIR=tmp/env_queue\n",
                         "/synthetic/soren/tmp/env_queue", fence=fence)
        # Paused/content-deduplicated sinks still return success without a marker.
        self._queue_case({}, "COMMENT_QUEUE_DIR=tmp/env_queue\n",
                         "/synthetic/soren/tmp/env_queue", publish=False)

    def test_audio_rejection_and_timeout_keep_existing_errors(self):
        import subprocess
        from types import SimpleNamespace

        with mock.patch.object(soren_output, "resolve_soren_root", return_value=Path("/synthetic/soren")), \
             mock.patch.object(soren_output, "comment_queue_dir", return_value=Path("/synthetic/queue")), \
             mock.patch.object(soren_output, "_retry_pending_hanjuku_terminal"), \
             mock.patch.object(subprocess, "run") as run:
            run.return_value = SimpleNamespace(returncode=1, stderr="rejected")
            with self.assertRaisesRegex(soren_output.SorenOutputError, "Soren audio queue rejected notification: rejected"):
                soren_output.enqueue_audio_text(None, "synthetic")
            run.side_effect = subprocess.TimeoutExpired("synthetic", 30.0)
            with self.assertRaisesRegex(soren_output.SorenOutputError, "Soren audio queue delivery failed"):
                soren_output.enqueue_audio_text(None, "synthetic")

    def test_empty_or_pending_audio_never_resolves_or_starts_a_queue(self):
        import subprocess

        with mock.patch.object(soren_output, "resolve_soren_root") as root, \
             mock.patch.object(soren_output, "comment_queue_dir") as queue, \
             mock.patch.object(soren_output, "_retry_pending_hanjuku_terminal") as drain, \
             mock.patch.object(subprocess, "run") as run:
            for text in ("", "   "):
                with self.assertRaisesRegex(soren_output.SorenOutputError, "empty audio text"):
                    soren_output.enqueue_audio_text(None, text)
            drain.assert_not_called()
            drain.side_effect = soren_output.HanjukuTerminalPendingError("synthetic pending")
            with self.assertRaises(soren_output.HanjukuTerminalPendingError):
                soren_output.enqueue_audio_text(None, "synthetic")
            root.assert_not_called()
            queue.assert_not_called()
            run.assert_not_called()

    def test_chat_keeps_its_existing_implicit_environment(self):
        import subprocess
        from types import SimpleNamespace

        with mock.patch.object(soren_output, "resolve_soren_root", return_value=Path("/synthetic/soren")), \
             mock.patch.object(soren_output, "comment_queue_dir") as queue, \
             mock.patch.object(subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            soren_output.enqueue_chat(None, "synthetic chat", delivery_key="synthetic-key")
            self.assertNotIn("env", run.call_args.kwargs)
            queue.assert_not_called()
