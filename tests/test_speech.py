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
            if url.endswith("/version"):
                return b'"0.25.2"'
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


class TestEndpointChain(SpeechBase):
    """VOICEVOX_URLS chain + persisted multiplicative backoff (docich, 2026-08-27)."""

    def _cfg(self, urls, **kw):
        kw.setdefault("state_file", self.root / "tmp/state/voicevox_endpoints.json")
        kw.setdefault("backoff_base_sec", 30.0)
        kw.setdefault("backoff_mult", 2.0)
        kw.setdefault("backoff_max_sec", 900.0)
        return speech.SpeechConfig(urls=tuple(urls), **kw)

    def test_parse_url_chain(self):
        self.assertEqual(
            speech.parse_url_chain("http://a:50021, http://b:50021/ http://a:50021\nhttp://c:1"),
            ["http://a:50021", "http://b:50021", "http://c:1"],
        )
        self.assertEqual(speech.parse_url_chain(""), [])

    def test_from_env_urls_chain_and_state_file(self):
        env = {
            "VOICEVOX_URLS": "http://mac:50021,http://desktop:50021,http://127.0.0.1:50021",
            "VOICEVOX_BACKOFF_BASE_SEC": "10",
            "VOICEVOX_BACKOFF_MULT": "3",
            "VOICEVOX_BACKOFF_MAX_SEC": "100",
        }
        cfg = speech.SpeechConfig.from_env(env=env, soren_root=self.root / "soren")
        self.assertEqual(cfg.urls, ("http://mac:50021", "http://desktop:50021", "http://127.0.0.1:50021"))
        self.assertEqual(cfg.state_file, self.root / "soren/tmp/state" / speech.STATE_FILE_NAME)
        self.assertEqual((cfg.backoff_base_sec, cfg.backoff_mult, cfg.backoff_max_sec), (10.0, 3.0, 100.0))

    def test_from_env_legacy_keys_still_build_chain(self):
        env = {"VOICEVOX_URL_PRIMARY": "http://remote:50021", "VOICEVOX_URL_FALLBACK": "http://127.0.0.1:50021"}
        cfg = speech.SpeechConfig.from_env(env=env)
        self.assertEqual(cfg.urls[0], "http://remote:50021")
        self.assertIn("http://127.0.0.1:50021", cfg.urls)

    def test_backoff_is_multiplicative_and_capped(self):
        cfg = self._cfg(["http://a"], backoff_base_sec=30, backoff_mult=2, backoff_max_sec=900)
        self.assertEqual([speech.backoff_delay(n, cfg) for n in (1, 2, 3, 4, 5, 6, 7)],
                         [30.0, 60.0, 120.0, 240.0, 480.0, 900.0, 900.0])
        self.assertEqual(speech.backoff_delay(0, cfg), 0.0)

    def test_failure_persists_backoff_and_reorders_plan(self):
        cfg = self._cfg(["http://a", "http://b"])
        self.assertEqual([i["url"] for i in speech.plan_endpoints(cfg)], ["http://a", "http://b"])
        d1 = speech.record_failure(cfg, "http://a", "boom")
        d2 = speech.record_failure(cfg, "http://a", "boom again")
        self.assertEqual((d1, d2), (30.0, 60.0))
        state = speech.load_endpoint_state(cfg.state_file)
        self.assertEqual(state["endpoints"]["http://a"]["failures"], 2)
        self.assertEqual(state["endpoints"]["http://a"]["fail_count"], 2)
        plan = speech.plan_endpoints(cfg)
        self.assertEqual([(i["url"], i["status"]) for i in plan], [("http://b", "ready"), ("http://a", "backoff")])
        self.assertGreater(plan[1]["retry_in_sec"], 50)
        # backoff expiry restores chain order
        later = speech.load_endpoint_state(cfg.state_file)
        plan2 = speech.plan_endpoints(cfg, later, now=later["endpoints"]["http://a"]["next_retry_at"] + 1)
        self.assertEqual([i["url"] for i in plan2], ["http://a", "http://b"])
        # success resets counters and records the active endpoint
        speech.record_success(cfg, "http://a", 1234.0)
        state = speech.load_endpoint_state(cfg.state_file)
        self.assertEqual(state["endpoints"]["http://a"]["failures"], 0)
        self.assertNotIn("next_retry_at", state["endpoints"]["http://a"])
        self.assertEqual(state["active_url"], "http://a")
        self.assertEqual(state["events"][-1]["event"], "recover")

    def test_all_in_backoff_still_tries_in_chain_order(self):
        cfg = self._cfg(["http://a", "http://b"])
        speech.record_failure(cfg, "http://a", "x")
        speech.record_failure(cfg, "http://b", "y")
        plan = speech.plan_endpoints(cfg)
        self.assertEqual([i["url"] for i in plan], ["http://a", "http://b"])
        self.assertTrue(all(i["status"] == "backoff" for i in plan))

    def test_disabled_endpoint_is_skipped_unless_nothing_else(self):
        cfg = self._cfg(["http://a", "http://b"])
        speech.set_endpoint_disabled(cfg, "http://a", True)
        self.assertEqual([i["url"] for i in speech.plan_endpoints(cfg)], ["http://b"])
        speech.set_endpoint_disabled(cfg, "http://b", True)
        self.assertEqual([i["status"] for i in speech.plan_endpoints(cfg)], ["disabled", "disabled"])
        speech.set_endpoint_disabled(cfg, "http://a", False)
        self.assertEqual([i["url"] for i in speech.plan_endpoints(cfg)], ["http://a"])

    def _chain_http(self, down: set, synth_fail: set = frozenset()):
        gets: list[str] = []
        posts: list[str] = []

        def fake_get(url, timeout):
            gets.append(url)
            if any(url.startswith(d) for d in down):
                raise speech.SpeechError(f"VOICEVOX 接続失敗: {url}")
            return b'"0.25.2"' if url.endswith("/version") else b"[]"

        def fake_post(url, data, timeout):
            posts.append(url)
            if "/audio_query" in url:
                return json.dumps({"accent_phrases": [], "speedScale": 1.0}).encode()
            if any(url.startswith(d) for d in synth_fail):
                raise speech.SpeechError("HTTP 500")
            return _wav_bytes()

        return (
            mock.patch.object(speech, "_http_get_bytes", side_effect=fake_get),
            mock.patch.object(speech, "_http_post_bytes", side_effect=fake_post),
            gets,
            posts,
        )

    def test_active_ready_endpoint_is_tried_first(self):
        cfg = self._cfg(["http://a:50021", "http://b:50021"])
        plan = [
            {"url": "http://a:50021", "status": "ready", "position": 1},
            {"url": "http://b:50021", "status": "ready", "position": 2},
        ]
        calls = []
        with mock.patch.dict(os.environ, {"VOICEVOX_ACTIVE_URL": "http://b:50021"}), \
             mock.patch.object(speech, "plan_endpoints", return_value=plan), \
             mock.patch.object(speech, "probe_endpoint", side_effect=lambda url, _cfg: calls.append(url)), \
             mock.patch.object(speech, "_synthesize_chunks_at_url"), \
             mock.patch.object(speech, "record_success"), \
             mock.patch.object(speech, "_chain_log"):
            served = speech.synthesize_chunks(["hello"], self.root / "active.wav", cfg)
        self.assertEqual(served, "http://b:50021")
        self.assertEqual(calls, ["http://b:50021"])

    def test_active_backoff_endpoint_does_not_jump_a_ready_endpoint(self):
        cfg = self._cfg(["http://a:50021", "http://b:50021"])
        plan = [
            {"url": "http://a:50021", "status": "ready", "position": 1},
            {"url": "http://b:50021", "status": "backoff", "position": 2},
        ]
        calls = []
        with mock.patch.dict(os.environ, {"VOICEVOX_ACTIVE_URL": "http://b:50021"}), \
             mock.patch.object(speech, "plan_endpoints", return_value=plan), \
             mock.patch.object(speech, "probe_endpoint", side_effect=lambda url, _cfg: calls.append(url)), \
             mock.patch.object(speech, "_synthesize_chunks_at_url"), \
             mock.patch.object(speech, "record_success"), \
             mock.patch.object(speech, "_chain_log"):
            served = speech.synthesize_chunks(["hello"], self.root / "active-backoff.wav", cfg)
        self.assertEqual(served, "http://a:50021")
        self.assertEqual(calls, ["http://a:50021"])

    def test_synthesize_falls_through_and_records_state(self):
        cfg = self._cfg(["http://down:50021", "http://ok:50021"])
        get_p, post_p, gets, posts = self._chain_http(down={"http://down"})
        out = self.root / "out.wav"
        with get_p, post_p:
            speech.synthesize("こんにちは。", out, cfg)
        self.assertTrue(out.is_file())
        # the unreachable endpoint is only probed, never posted to
        self.assertFalse(any(p.startswith("http://down") for p in posts))
        self.assertTrue(any(g == "http://down:50021/version" for g in gets))
        state = speech.load_endpoint_state(cfg.state_file)
        self.assertEqual(state["endpoints"]["http://down:50021"]["failures"], 1)
        self.assertEqual(state["active_url"], "http://ok:50021")
        # second call: the failed endpoint is in backoff -> not even probed
        gets.clear(); posts.clear()
        with get_p, post_p:
            speech.synthesize("また。", out, cfg)
        self.assertEqual(gets, ["http://ok:50021/version"])
        self.assertTrue(all(p.startswith("http://ok") for p in posts))
        self.assertEqual(state["endpoints"]["http://down:50021"]["fail_count"], 1)

    def test_synthesis_error_mid_chunk_moves_to_next_endpoint(self):
        cfg = self._cfg(["http://flaky:50021", "http://ok:50021"], max_chars=2)
        get_p, post_p, gets, posts = self._chain_http(down=set(), synth_fail={"http://flaky"})
        out = self.root / "out.wav"
        with get_p, post_p:
            speech.synthesize("あ。い。", out, cfg)
        self.assertTrue(out.is_file())
        state = speech.load_endpoint_state(cfg.state_file)
        self.assertEqual(state["endpoints"]["http://flaky:50021"]["failures"], 1)
        self.assertEqual(state["endpoints"]["http://ok:50021"]["ok_count"], 1)
        self.assertFalse(list(out.parent.glob(".voicevox_chunk_*")))

    def test_all_endpoints_down_raises_with_details(self):
        cfg = self._cfg(["http://a:1", "http://b:1"])
        get_p, post_p, _, _ = self._chain_http(down={"http://a", "http://b"})
        with get_p, post_p, self.assertRaises(speech.SpeechError) as ctx:
            speech.synthesize("x。", self.root / "o.wav", cfg)
        self.assertIn("http://a:1", str(ctx.exception))
        self.assertIn("http://b:1", str(ctx.exception))

    def test_endpoint_report_probe_records_and_heals(self):
        cfg = self._cfg(["http://a:1", "http://b:1"])
        speech.record_failure(cfg, "http://b:1", "earlier")
        get_p, post_p, _, _ = self._chain_http(down={"http://a"})
        with get_p, post_p:
            report = speech.endpoint_report(cfg, probe=True)
        rows = {r["url"]: r for r in report["endpoints"]}
        self.assertFalse(rows["http://a:1"]["probe"]["ok"])
        self.assertEqual(rows["http://a:1"]["status"], "backoff")
        self.assertTrue(rows["http://b:1"]["probe"]["ok"])
        self.assertEqual(rows["http://b:1"]["status"], "ready")
        self.assertEqual(rows["http://b:1"]["failures"], 0)
        self.assertIn("base_sec", report["backoff"])

    def test_reset_clears_backoff(self):
        cfg = self._cfg(["http://a", "http://b"])
        speech.record_failure(cfg, "http://a", "x")
        speech.record_failure(cfg, "http://b", "y")
        speech.reset_endpoint(cfg, "http://a")
        self.assertEqual([i["status"] for i in speech.plan_endpoints(cfg)], ["ready", "backoff"])
        speech.reset_endpoint(cfg)
        self.assertEqual([i["status"] for i in speech.plan_endpoints(cfg)], ["ready", "ready"])
        self.assertTrue((self.root / "tmp/state" / speech.CHAIN_LOG_NAME).is_file())

    def test_cli_endpoints_json(self):
        state = self.root / "state.json"
        env = {"VOICEVOX_URLS": "http://x:1,http://y:1", "VOICEVOX_STATE_FILE": str(state)}
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), redirect_stdout(buf):
            rc = cli.main(["voicevox", "endpoints", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual([r["url"] for r in data["endpoints"]], ["http://x:1", "http://y:1"])
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), redirect_stdout(buf):
            rc = cli.main(["voicevox", "endpoints", "--disable", "--url", "http://x:1"])
        self.assertEqual(rc, 0)
        self.assertIn("disabled", buf.getvalue())
        self.assertTrue(json.loads(state.read_text())["endpoints"]["http://x:1"]["disabled"])
