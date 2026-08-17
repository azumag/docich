import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import cli, speech  # noqa: E402


def _wav_bytes(duration_frames: int = 160) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * duration_frames)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


class SpeechBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo_root = self.root
        self.cfg = speech.SpeechConfig(urls=("http://127.0.0.1:50021",))

    def tearDown(self):
        self._tmpdir.cleanup()


class TestChunking(SpeechBase):
    def test_split_by_full_stop_and_newline(self):
        chunks = speech.split_chunks("こんにちは。世界。\n次です。", 200)
        self.assertEqual(chunks, ["こんにちは。世界。次です。"])

    def test_split_merges_short_sentences(self):
        chunks = speech.split_chunks("あ。い。", 10)
        self.assertEqual(len(chunks), 1)
        self.assertLessEqual(len(chunks[0]), 10)

    def test_split_long_sentence_by_comma(self):
        chunks = speech.split_chunks("あ、い、う、え、お。", 3)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 3)


class TestSanitize(SpeechBase):
    def test_removes_hash_chars(self):
        self.assertEqual(speech.sanitize_text("a#b＃c", []), "abc")

    def test_word_replace(self):
        pairs = [("地政学的", "ちせいがくてき")]
        self.assertEqual(speech.sanitize_text("地政学的な話", pairs), "ちせいがくてきな話")

    def test_word_replace_file_loading(self):
        path = self.root / "config" / "voicevox_word_replace.txt"
        path.parent.mkdir(parents=True)
        path.write_text("# comment\n安定性\tあんていせい\n", encoding="utf-8")
        pairs = speech.load_word_replace(path)
        self.assertEqual(pairs, [("安定性", "あんていせい")])


class TestNYPause(SpeechBase):
    def _query(self):
        return {
            "accent_phrases": [
                {
                    "moras": [
                        {"text": "に", "vowel": "i", "consonant": "n"},
                        {"text": "ゅ", "vowel": "u", "consonant": "ny"},
                    ],
                    "accent": 2,
                    "pause_mora": None,
                    "is_interrogative": False,
                }
            ]
        }

    def test_inserts_pau_before_ny_after_i(self):
        fixed = speech.apply_ny_pause_fix(self._query(), 0.2)
        phrases = fixed["accent_phrases"]
        self.assertEqual(len(phrases), 2)
        self.assertEqual(phrases[0]["pause_mora"]["vowel"], "pau")
        self.assertEqual(phrases[1]["moras"][0]["consonant"], "ny")

    def test_rejects_invalid_pause_len(self):
        with self.assertRaises(speech.SpeechError):
            speech.apply_ny_pause_fix(self._query(), 2.0)


class TestSynthesis(SpeechBase):
    def _patch_http(self, responses: list):
        counter = {"n": 0}

        def fake_get(url, timeout):
            if url.endswith("/speakers"):
                return b"[]"
            raise AssertionError(f"unexpected GET {url}")

        def fake_post(url, data, timeout):
            req = json.loads(data) if isinstance(data, bytes) and data else {}
            counter["n"] += 1
            if "/audio_query" in url:
                return json.dumps(
                    {"accent_phrases": [], "pitchScale": 0.0,
                     "speedScale": 1.0, "intonationScale": 1.0}
                ).encode()
            if "/synthesis" in url:
                return responses.pop(0) if responses else _wav_bytes()
            raise AssertionError(f"unexpected POST {url}")

        post_patch = mock.patch.object(speech, "_http_post_bytes", side_effect=fake_post)
        get_patch = mock.patch.object(speech, "_http_get_bytes", side_effect=fake_get)
        return post_patch, get_patch, counter

    def test_synthesize_short_text(self):
        post_patch, get_patch, _ = self._patch_http([])
        with post_patch, get_patch:
            out = self.root / "out.wav"
            speech.synthesize("こんにちは。", out, self.cfg)
        self.assertTrue(out.is_file())
        self.assertGreater(out.stat().st_size, 40)

    def test_synthesize_multichunk_concats(self):
        wavs = [_wav_bytes(160), _wav_bytes(320)]
        post_patch, get_patch, _ = self._patch_http(wavs)
        with post_patch, get_patch:
            out = self.root / "out.wav"
            speech.synthesize("長い。テキスト。", out, speech.SpeechConfig(
                urls=self.cfg.urls, max_chars=2
            ))
        self.assertTrue(out.is_file())

    def test_url_failover(self):
        cfg = speech.SpeechConfig(
            urls=("http://primary.invalid", "http://fallback.invalid")
        )
        calls: list[str] = []

        def fake_post(url, data, timeout):
            calls.append(url)
            if "/audio_query" in url:
                return json.dumps(
                    {"accent_phrases": [], "speedScale": 1.0}
                ).encode()
            if "primary" in url:
                raise speech.SpeechError("fail")
            return _wav_bytes()

        with mock.patch.object(speech, "_http_post_bytes", side_effect=fake_post), mock.patch.object(
            speech, "_http_get_bytes", return_value=b"[]"
        ):
            out = self.root / "out.wav"
            speech.synthesize("こんにちは。", out, cfg)
        self.assertTrue(out.is_file())
        self.assertTrue(any("fallback" in c for c in calls))

    def test_check_server_prefers_first_reachable(self):
        def fake_get(url, timeout):
            if "primary" in url:
                raise speech.SpeechError("down")
            return b"[]"

        with mock.patch.object(speech, "_http_get_bytes", side_effect=fake_get):
            active = speech.check_server(
                speech.SpeechConfig(urls=("http://primary.invalid", "http://ok.invalid"))
            )
        self.assertEqual(active, "http://ok.invalid")

    def test_audio_query_applies_tempo_pitch(self):
        captured = {}

        def fake_post(url, data, timeout):
            if "/audio_query" in url:
                return json.dumps(
                    {"accent_phrases": [], "pitchScale": 0.0,
                     "speedScale": 1.0, "intonationScale": 1.0}
                ).encode()
            captured["payload"] = json.loads(data)
            return _wav_bytes()

        cfg = speech.SpeechConfig(
            urls=self.cfg.urls, pitch=0.2, tempo=1.5, intonation=0.8
        )
        with mock.patch.object(speech, "_http_post_bytes", side_effect=fake_post), mock.patch.object(
            speech, "_http_get_bytes", return_value=b"[]"
        ):
            out = self.root / "out.wav"
            speech.synthesize("こんにちは。", out, cfg)
        self.assertAlmostEqual(captured["payload"]["speedScale"], 1.5)
        self.assertAlmostEqual(captured["payload"]["pitchScale"], 0.2)
        self.assertAlmostEqual(captured["payload"]["intonationScale"], 0.8)


class TestCliVoicevox(SpeechBase):
    def _tmp_config(self):
        games = self.root / "config" / "games"
        games.mkdir(parents=True)
        toml = self.root / "docich.toml"
        toml.write_text(
            "[paths]\n"
            f'state_dir = "{self.root / "run"}"\n'
            f'games_dir = "{games}"\n'
            f'roms_dir = "{self.root / "roms"}"\n',
            encoding="utf-8",
        )
        return toml

    def test_voicevox_synth_dry_run(self):
        toml = self._tmp_config()
        text = self.root / "in.txt"
        text.write_text("こんにちは。", encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main([
                "--config", str(toml), "voicevox", "synth",
                "-f", str(text), "-o", str(self.root / "out.wav"), "--dry-run",
            ])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("voicevox dry-run:", out.getvalue())

    def test_voicevox_requires_subcommand(self):
        toml = self._tmp_config()
        with self.assertRaises(SystemExit):
            cli.main(["--config", str(toml), "voicevox"])

    def test_voicevox_parse_speakers(self):
        args = cli.build_parser().parse_args(["voicevox", "speakers"])
        self.assertEqual(args.voicevox_command, "speakers")


if __name__ == "__main__":
    unittest.main()
