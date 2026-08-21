import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich import webui  # noqa: E402


def _make_global(repo_root: Path) -> config.GlobalConfig:
    return config.load_global(repo_root)


def _write_env(soren_root: Path, content: str) -> None:
    soren_root.mkdir(parents=True, exist_ok=True)
    (soren_root / ".env").write_text(content, encoding="utf-8")


class TestResolveSorenRoot(unittest.TestCase):
    def test_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _make_global(Path(tmp))
            p = Path(tmp) / "override"
            p.mkdir()
            self.assertEqual(webui._resolve_soren_root(g, str(p)), p.resolve())

    def test_config_relative_to_repo_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            g = _make_global(repo_root)
            g.webui.soren_root = "games/soviet_now"
            self.assertEqual(
                webui._resolve_soren_root(g, None),
                (repo_root / "games/soviet_now").resolve(),
            )

    def test_auto_discover(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "games/soviet_now").mkdir(parents=True)
            (repo_root / "games/soviet_now" / "eloop_lib.sh").write_text("# x\n")
            g = _make_global(repo_root)
            self.assertEqual(
                webui._resolve_soren_root(g, None),
                (repo_root / "games/soviet_now").resolve(),
            )


class TestReadDotenv(unittest.TestCase):
    def test_parses_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_env(root, 'A=1\nB="quoted"\nC=with # comment\n# comment\n\n')
            d = webui._read_dotenv_dict(root)
            self.assertEqual(d["A"], "1")
            self.assertEqual(d["B"], "quoted")
            self.assertEqual(d["C"], "with")

    def test_invalid_keys_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_env(root, "1BAD=1\n=noval\nBAD_KEY\nGOOD=1\n")
            d = webui._read_dotenv_dict(root)
            self.assertEqual(d, {"GOOD": "1"})

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(webui._read_dotenv_dict(Path(tmp)), {})


class TestEffectiveValue(unittest.TestCase):
    def test_inheritance(self):
        d = {"AI_COMMON_AGENTS": "codex:a,codex:b"}
        self.assertEqual(webui._effective_value("RADIO_AGENTS", d), "codex:a,codex:b")
        self.assertEqual(webui._effective_value("RADIO_PREPASS_AGENTS", d), "codex:a,codex:b")
        self.assertEqual(webui._effective_value("COMMENT_AGENTS", d), "codex:a,codex:b")
        # COMMENT_TRANSLATION_AGENTS inherits COMMENT_AGENTS
        self.assertEqual(webui._effective_value("COMMENT_TRANSLATION_AGENTS", d), "codex:a,codex:b")

    def test_explicit_empty_falls_back(self):
        d = {"AI_COMMON_AGENTS": "codex:a", "RADIO_AGENTS": ""}
        self.assertEqual(webui._effective_value("RADIO_AGENTS", d), "codex:a")

    def test_defaults(self):
        self.assertIn("deepseek-v4-flash-free", webui._effective_value("AI_COMMON_AGENTS", {}))
        self.assertEqual(webui._effective_value("AI_AGENT_BACKOFF_SEC", {}), "600")


class TestValidateValue(unittest.TestCase):
    def test_valid_chain(self):
        webui._validate_value("AI_COMMON_AGENTS", "codex:a,codex:b,local")
        webui._validate_value("RADIO_AGENTS", "")  # empty = inherit

    def test_invalid_chain(self):
        with self.assertRaises(ValueError):
            webui._validate_value("AI_COMMON_AGENTS", "")
        with self.assertRaises(ValueError):
            webui._validate_value("AI_COMMON_AGENTS", "codex:a,,local")
        with self.assertRaises(ValueError):
            webui._validate_value("AI_COMMON_AGENTS", "codex:a;local")

    def test_backoff_items(self):
        webui._validate_value("AI_BACKOFF_SEC_ITEMS", "deepseek-v4-flash-free:86400 local:1800")
        with self.assertRaises(ValueError):
            webui._validate_value("AI_BACKOFF_SEC_ITEMS", "deepseek-v4-flash-free")
        with self.assertRaises(ValueError):
            webui._validate_value("AI_BACKOFF_SEC_ITEMS", "model:30")  # < 60

    def test_agent_backoff_sec(self):
        webui._validate_value("AI_AGENT_BACKOFF_SEC", "600")
        with self.assertRaises(ValueError):
            webui._validate_value("AI_AGENT_BACKOFF_SEC", "abc")

    def test_failure_backoff_sec(self):
        # PR #125: 一過性障害用の短バックオフ (既定 300)
        webui._validate_value("AI_BACKOFF_FAILURE_SEC", "300")
        self.assertEqual(webui.DEFAULTS["AI_BACKOFF_FAILURE_SEC"], "300")
        self.assertIn("AI_BACKOFF_FAILURE_SEC", webui.WEBUI_ALLOWLIST)
        with self.assertRaises(ValueError):
            webui._validate_value("AI_BACKOFF_FAILURE_SEC", "abc")
        with self.assertRaises(ValueError):
            webui._validate_value("AI_BACKOFF_FAILURE_SEC", "5")  # 30未満は拒否
        with self.assertRaises(ValueError):
            webui._validate_value("AI_BACKOFF_FAILURE_SEC", "-10")

    def test_peak_windows(self):
        webui._validate_value("PEAK_HOURS_WINDOWS", "10-13,15-19")
        webui._validate_value("PEAK_HOURS_WINDOWS", "22-02")
        webui._validate_value("PEAK_HOURS_WINDOWS", "")  # empty = disabled
        with self.assertRaises(ValueError):
            webui._validate_value("PEAK_HOURS_WINDOWS", "10-25")

    def test_peak_bools(self):
        webui._validate_value("PEAK_HOURS_AGENT_SWAP_ENABLED", "0")
        webui._validate_value("PEAK_HOURS_AGENT_SWAP_ENABLED", "1")
        # ランタイム (core/helpers.sh) は "1" のみ有効と判定するため 0/1 限定
        with self.assertRaises(ValueError):
            webui._validate_value("PEAK_HOURS_AGENT_SWAP_ENABLED", "true")
        with self.assertRaises(ValueError):
            webui._validate_value("PEAK_HOURS_AGENT_SWAP_ENABLED", "2")

    def test_tz_rejects_shell_metachars(self):
        # .env は worker が bash で source するため、シェル構文は一切許さない
        webui._validate_value("PEAK_HOURS_TZ", "Asia/Tokyo")
        webui._validate_value("PEAK_HOURS_TZ", "UTC")
        with self.assertRaises(ValueError):
            webui._validate_value("PEAK_HOURS_TZ", "$(touch /tmp/pwned)")
        with self.assertRaises(ValueError):
            webui._validate_value("PEAK_HOURS_TZ", "Asia/Tokyo;rm -rf /")
        with self.assertRaises(ValueError):
            webui._validate_value("PEAK_HOURS_TZ", "")
        with self.assertRaises(ValueError):
            webui._validate_value("PEAK_HOURS_TZ", "`id`")

    def test_non_string_rejected(self):
        with self.assertRaises(ValueError):
            webui._validate_value("AI_AGENT_BACKOFF_SEC", {"x": 1})

    def test_unknown_key(self):
        with self.assertRaises(ValueError):
            webui._validate_value("STREAM_KEY", "x" * 30)

    def test_stream_settings_validators(self):
        # allowlist/defaults 登録
        for k in (
            "SOREN_DIRECT_STREAM_SIZE",
            "SOREN_DIRECT_STREAM_FPS",
            "SOREN_DIRECT_STREAM_VIDEO_KBPS",
            "SOREN_DIRECT_STREAM_AUDIO_KBPS",
            "SOREN_DIRECT_STREAM_AUDIO_DELAY_MS",
            "DOCICH_CC_ENABLED",
        ):
            self.assertIn(k, webui.WEBUI_ALLOWLIST)
            self.assertIn(k, webui.DEFAULTS)
        # size
        webui._validate_value("SOREN_DIRECT_STREAM_SIZE", "1280x720")
        with self.assertRaises(ValueError):
            webui._validate_value("SOREN_DIRECT_STREAM_SIZE", "1280")  # 形式不正
        with self.assertRaises(ValueError):
            webui._validate_value("SOREN_DIRECT_STREAM_SIZE", "100x720")  # 幅 < 320
        with self.assertRaises(ValueError):
            webui._validate_value("SOREN_DIRECT_STREAM_SIZE", "4096x2160")  # 幅 > 3840
        with self.assertRaises(ValueError):
            webui._validate_value("SOREN_DIRECT_STREAM_SIZE", "1281x720")  # 奇数
        # int range (lib/direct_stream.py load_config と同じ範囲)
        webui._validate_value("SOREN_DIRECT_STREAM_FPS", "30")
        with self.assertRaises(ValueError):
            webui._validate_value("SOREN_DIRECT_STREAM_FPS", "0")
        with self.assertRaises(ValueError):
            webui._validate_value("SOREN_DIRECT_STREAM_FPS", "61")
        webui._validate_value("SOREN_DIRECT_STREAM_VIDEO_KBPS", "4500")
        with self.assertRaises(ValueError):
            webui._validate_value("SOREN_DIRECT_STREAM_VIDEO_KBPS", "499")
        with self.assertRaises(ValueError):
            webui._validate_value("SOREN_DIRECT_STREAM_VIDEO_KBPS", "6001")
        webui._validate_value("SOREN_DIRECT_STREAM_AUDIO_KBPS", "160")
        with self.assertRaises(ValueError):
            webui._validate_value("SOREN_DIRECT_STREAM_AUDIO_KBPS", "63")
        webui._validate_value("SOREN_DIRECT_STREAM_AUDIO_DELAY_MS", "150")
        with self.assertRaises(ValueError):
            webui._validate_value("SOREN_DIRECT_STREAM_AUDIO_DELAY_MS", "-1")
        with self.assertRaises(ValueError):
            webui._validate_value("SOREN_DIRECT_STREAM_FPS", "abc")
        # cc bool
        webui._validate_value("DOCICH_CC_ENABLED", "0")
        webui._validate_value("DOCICH_CC_ENABLED", "1")
        for bad in ("true", "2", "", "yes"):
            with self.assertRaises(ValueError):
                webui._validate_value("DOCICH_CC_ENABLED", bad)


class TestStreamControl(unittest.TestCase):
    def _soren(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        soren = Path(tmp.name) / "soren"
        (soren / "tmp/state").mkdir(parents=True)
        return soren

    def _write_status(self, soren: Path, payload: dict):
        d = soren / "tmp/state/direct_stream"
        d.mkdir(parents=True, exist_ok=True)
        (d / "status.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_state_off_without_status_file(self):
        soren = self._soren()
        st = webui._get_stream_status(soren)
        self.assertEqual(st["state"], "off")
        self.assertFalse(st["paused"])
        self.assertFalse(st["running"])

    def test_state_live_with_alive_pid(self):
        soren = self._soren()
        self._write_status(
            soren,
            {"running": True, "pid": os.getpid(), "ffmpeg_pid": os.getpid(), "backend": "ffmpeg", "fps": 30.0},
        )
        st = webui._get_stream_status(soren)
        self.assertEqual(st["state"], "live")
        self.assertTrue(st["runner_alive"])
        self.assertTrue(st["ffmpeg_alive"])

    def test_state_off_with_dead_pid(self):
        soren = self._soren()
        self._write_status(soren, {"running": True, "pid": 3999999, "ffmpeg_pid": 3999998})
        st = webui._get_stream_status(soren)
        self.assertEqual(st["state"], "off")

    def test_state_paused_marker_wins_over_stale_status(self):
        soren = self._soren()
        self._write_status(soren, {"running": False, "pid": None, "ffmpeg_pid": None})
        webui._set_worker_paused(soren, "direct_stream", True)
        st = webui._get_stream_status(soren)
        self.assertEqual(st["state"], "paused")
        self.assertTrue(st["paused"])
        webui._set_worker_paused(soren, "direct_stream", False)
        self.assertFalse(webui._is_worker_paused(soren, "direct_stream"))

    def test_pause_marker_content_and_unsupported_worker(self):
        soren = self._soren()
        webui._set_worker_paused(soren, "chat_worker", True)
        marker = soren / "tmp/state/chat_worker.paused"
        self.assertTrue(marker.is_file())
        data = json.loads(marker.read_text(encoding="utf-8"))
        self.assertTrue(data["paused"])
        self.assertEqual(data["source"], "webui")
        with self.assertRaises(ValueError):
            webui._set_worker_paused(soren, "radio_worker", True)

    def test_chat_flag_in_stream_status(self):
        soren = self._soren()
        self.assertFalse(webui._get_stream_status(soren)["chat_paused"])
        webui._set_worker_paused(soren, "chat_worker", True)
        self.assertTrue(webui._get_stream_status(soren)["chat_paused"])

    def test_stop_is_noop_when_no_processes(self):
        soren = self._soren()
        result = webui._stop_stream_runner(soren)
        self.assertTrue(result["stopped"])
        self.assertFalse(result["escalated_kill"])
        self.assertEqual(result["remaining_pids"], [])
        self.assertTrue(webui._is_worker_paused(soren, "direct_stream"))


class TestDotenvQuote(unittest.TestCase):
    def test_plain_value_unquoted(self):
        self.assertEqual(webui._dotenv_quote("codex:a,codex:b"), "codex:a,codex:b")
        self.assertEqual(webui._dotenv_quote("600"), "600")

    def test_space_value_quoted(self):
        self.assertEqual(
            webui._dotenv_quote("deepseek-v4-flash-free:86400 local:1800"),
            '"deepseek-v4-flash-free:86400 local:1800"',
        )

    def test_shell_metachars_escaped(self):
        self.assertEqual(webui._dotenv_quote('a"b$c`d'), r'"a\"b\$c\`d"')


class TestSanitize(unittest.TestCase):
    def test_sanitize_agent(self):
        self.assertEqual(webui._sanitize_agent("codex:deepseek-v4-flash-free"), "codex_deepseek-v4-flash-free")
        self.assertEqual(webui._sanitize_agent("codex:amd-token-factory/deepseek"), "codex_amd-token-factory_deepseek")
        self.assertEqual(webui._sanitize_agent(""), "")

    def test_fmt_remaining(self):
        self.assertEqual(webui._fmt_remaining(3600), "1h")
        self.assertEqual(webui._fmt_remaining(3660), "1h01m")
        self.assertEqual(webui._fmt_remaining(120), "2m")
        self.assertEqual(webui._fmt_remaining(45), "45s")

    def test_peak_time_validity(self):
        self.assertTrue(webui._is_valid_peak_time("10"))
        self.assertTrue(webui._is_valid_peak_time("1330"))
        self.assertTrue(webui._is_valid_peak_time("13:30"))
        self.assertFalse(webui._is_valid_peak_time("24"))
        self.assertFalse(webui._is_valid_peak_time("13:99"))


class TestAtomicEnvUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tmp/state").mkdir(parents=True)
        _write_env(self.root, "AI_AGENT_BACKOFF_SEC=600\nMODEL_IMPROVE_LIST=codex:a\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_updates_and_preserves(self):
        mtime = webui._dotenv_mtime(self.root)
        new_mtime = webui._atomic_env_update(self.root, {"AI_AGENT_BACKOFF_SEC": "900", "RADIO_AGENTS": "codex:b"}, mtime)
        self.assertIsInstance(new_mtime, int)
        d = webui._read_dotenv_dict(self.root)
        self.assertEqual(d["AI_AGENT_BACKOFF_SEC"], "900")
        self.assertEqual(d["RADIO_AGENTS"], "codex:b")
        self.assertEqual(d["MODEL_IMPROVE_LIST"], "codex:a")

    def test_empty_value_removes_override(self):
        _write_env(self.root, "RADIO_AGENTS=codex:x\n")
        webui._atomic_env_update(self.root, {"RADIO_AGENTS": ""}, None)
        d = webui._read_dotenv_dict(self.root)
        self.assertNotIn("RADIO_AGENTS", d)

    def test_mtime_mismatch_raises(self):
        with self.assertRaises(ValueError):
            webui._atomic_env_update(self.root, {"AI_AGENT_BACKOFF_SEC": "900"}, 12345)

    def test_creates_file_when_missing(self):
        root2 = Path(self.tmp.name) / "sub"
        root2.mkdir()
        (root2 / "tmp/state").mkdir(parents=True)
        webui._atomic_env_update(root2, {"AI_COMMON_AGENTS": "codex:a"}, None)
        env = root2 / ".env"
        self.assertTrue(env.is_file())
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)
        self.assertEqual(webui._read_dotenv_dict(root2)["AI_COMMON_AGENTS"], "codex:a")

    def test_backup_created(self):
        webui._atomic_env_update(self.root, {"AI_AGENT_BACKOFF_SEC": "900"}, None)
        backups = list(self.root.glob(".env.bak.*"))
        self.assertEqual(len(backups), 1)

    def test_space_value_quoted_in_file(self):
        webui._atomic_env_update(self.root, {"AI_BACKOFF_SEC_ITEMS": "deepseek-v4-flash-free:86400 local:1800"}, None)
        lines = (self.root / ".env").read_text(encoding="utf-8").splitlines()
        self.assertIn('AI_BACKOFF_SEC_ITEMS="deepseek-v4-flash-free:86400 local:1800"', lines)

    def test_peak_windows_empty_line_preserved(self):
        webui._atomic_env_update(self.root, {"PEAK_HOURS_WINDOWS": ""}, None)
        env = self.root / ".env"
        self.assertIn("PEAK_HOURS_WINDOWS=", env.read_text(encoding="utf-8").splitlines())
        # 空=無効として有効値は空になる
        self.assertEqual(webui._effective_value("PEAK_HOURS_WINDOWS", webui._read_dotenv_dict(self.root)), "")

    def test_key_not_in_allowlist_rejected(self):
        with self.assertRaises(ValueError):
            webui._atomic_env_update(self.root, {"STREAM_KEY": "x"}, None)


class TestBackoffDir(unittest.TestCase):
    def test_default_under_soren_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(webui._backoff_dir(root), root / "tmp/state/ai_backoff")
            self.assertEqual(webui._stats_dir(root), root / "tmp/state/ai_stats")


class TestAudioQueue(unittest.TestCase):
    def test_comment_queue_dir_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(webui._comment_queue_dir(root), root / "tmp/.comment_queue")

    def test_comment_queue_dir_from_dotenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_env(root, 'COMMENT_QUEUE_DIR="tmp/custom_queue"\n')
            self.assertEqual(webui._comment_queue_dir(root), root / "tmp/custom_queue")

    def test_enqueue_audio_text_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tmp/state").mkdir(parents=True)
            res = webui._enqueue_audio_text(root, "お待たせしております。", "webui_test")
            self.assertTrue(res["ok"])
            self.assertFalse(res["dedup"])
            self.assertIsNotNone(res["filename"])
            f = root / "tmp/.comment_queue" / res["filename"]
            self.assertTrue(f.is_file())
            self.assertEqual(f.read_text(encoding="utf-8").strip(), "お待たせしております。")

    def test_enqueue_audio_text_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tmp/state").mkdir(parents=True)
            r1 = webui._enqueue_audio_text(root, "同じテキストです", "webui_test")
            self.assertFalse(r1["dedup"])
            r2 = webui._enqueue_audio_text(root, "同じテキストです", "webui_test")
            self.assertTrue(r2["dedup"])
            self.assertIsNone(r2["filename"])

    def test_enqueue_speaker_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tmp/state").mkdir(parents=True)
            res = webui._enqueue_audio_text(root, "話者テスト", "webui_test", speaker="46")
            side = Path(str(root / "tmp/.comment_queue" / res["filename"]) + ".speaker")
            self.assertTrue(side.is_file())
            self.assertEqual(side.read_text(encoding="utf-8"), "46")

    def test_enqueue_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                webui._enqueue_audio_text(root, "", "webui_test")
            with self.assertRaises(ValueError):
                webui._enqueue_audio_text(root, "a" * 1001, "webui_test")
            with self.assertRaises(ValueError):
                webui._enqueue_audio_text(root, "ok", "bad source!")
            with self.assertRaises(ValueError):
                webui._enqueue_audio_text(root, "ok", "webui_test", speaker="bad speaker!")

    def test_list_audio_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tmp/state").mkdir(parents=True)
            webui._enqueue_audio_text(root, "一件目", "webui_test")
            webui._enqueue_audio_text(root, "二件目", "webui_test", speaker="109")
            items = webui._list_audio_queue(root)
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["preview"], "一件目")
            self.assertEqual(items[1]["preview"], "二件目")
            self.assertEqual(items[1]["speaker"], "109")

    def test_audio_hash(self):
        self.assertEqual(len(webui._comment_audio_hash("abc")), 32)


class TestPredictions(unittest.TestCase):
    def test_clean_remote_prediction_drops_unknown_fields(self):
        item = webui._prediction_clean_remote_item(
            {
                "id": "prediction-1",
                "title": "12ゲーム中に建国できる？",
                "status": "ACTIVE",
                "outcomes": [{"id": "outcome-1", "title": "建国なし", "users": 2, "secret": "drop"}],
                "secret_token": "must-not-escape",
            }
        )
        self.assertEqual(item["id"], "prediction-1")
        self.assertNotIn("secret_token", item)
        self.assertNotIn("secret", item["outcomes"][0])

    def test_status_snapshot_is_safe_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tmp/state").mkdir(parents=True)
            (root / "eloop_lib.sh").write_text("# x\n")
            status = webui._prediction_status_snapshot(root)
            self.assertIn("enabled", status)
            self.assertNotIn("TWITCH_PREDICTIONS_TOKEN", json.dumps(status))

    def test_action_argument_validation(self):
        # The allowlisted action vocabulary is intentionally small; this also
        # guards against accidentally turning the shell wrapper into a command
        # injection surface.
        self.assertEqual(webui.PREDICTION_ACTIONS, {"create", "resolve", "cancel", "sync"})
        self.assertEqual(len(webui.PREDICTION_OUTCOME_LABELS), 4)


class TestRunWebuiDryRun(unittest.TestCase):
    def test_dry_run_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            g = _make_global(repo_root)
            rc = webui.run_webui(g, soren_root=str(repo_root), dry_run=True)
            self.assertEqual(rc, 0)


class TestHttpHandlers(unittest.TestCase):
    """実サーバー (ThreadingHTTPServer) を起動して API を叩く。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.soren = self.repo_root / "soren"
        (self.soren / "tmp/state").mkdir(parents=True)
        (self.soren / "eloop_lib.sh").write_text("# x\n")
        _write_env(self.soren, "AI_AGENT_BACKOFF_SEC=600\n")
        self.g = _make_global(self.repo_root)
        self.server = webui.ThreadingHTTPServer(("127.0.0.1", 0), webui._Handler)
        webui._Handler.g = self.g
        webui._Handler.soren_root = self.soren
        webui._Handler.read_only = False
        webui._Handler.start_time = time.time()
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        import http.client

        self.client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.tmp.cleanup()

    def _request(self, method, path, body=None, headers=None):
        import json as _json

        hdrs = dict(headers or {})
        if body is not None and not isinstance(body, bytes):
            body = _json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        self.client.request(method, path, body=body, headers=hdrs)
        res = self.client.getresponse()
        return res.status, json.loads(res.read().decode("utf-8"))

    def test_get_health(self):
        status, data = self._request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_get_index(self):
        self.client.request("GET", "/")
        res = self.client.getresponse()
        body = res.read().decode("utf-8")
        self.assertEqual(res.status, 200)
        self.assertIn("docich webui", body)

    def test_put_config_roundtrip(self):
        status, data = self._request("GET", "/api/config")
        mtime = data["env_mtime"]
        status, data = self._request("PUT", "/api/config", {"values": {"AI_AGENT_BACKOFF_SEC": "900"}, "expected_mtime": mtime})
        self.assertEqual(status, 200, data)
        status, data = self._request("GET", "/api/config")
        entry = next(e for e in data["entries"] if e["key"] == "AI_AGENT_BACKOFF_SEC")
        self.assertEqual(entry["value"], "900")
        self.assertTrue(entry["in_env"])

    def test_put_config_conflict(self):
        status, data = self._request("PUT", "/api/config", {"values": {"AI_AGENT_BACKOFF_SEC": "900"}, "expected_mtime": 12345})
        self.assertEqual(status, 409)
        self.assertEqual(data["error"], "conflict")

    def test_put_config_tz_injection_rejected(self):
        status, data = self._request("PUT", "/api/config", {"values": {"PEAK_HOURS_TZ": "$(touch /tmp/pwned)"}, "expected_mtime": 12345})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "validation_error")

    def test_put_config_unknown_key_rejected(self):
        status, data = self._request("PUT", "/api/config", {"values": {"STREAM_KEY": "x"}})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "not_allowed")

    def test_put_config_invalid_json(self):
        self.client.request("PUT", "/api/config", body=b"{invalid", headers={"Content-Type": "application/json"})
        res = self.client.getresponse()
        self.assertEqual(res.status, 400)

    def test_put_config_bad_content_length(self):
        self.client.request("PUT", "/api/config", body=b"{}", headers={"Content-Length": "abc"})
        res = self.client.getresponse()
        self.assertEqual(res.status, 400)
        res.read()

    def test_put_config_body_too_large(self):
        status, data = self._request("PUT", "/api/config", body="x" * (webui.MAX_BODY_BYTES + 1))
        self.assertEqual(status, 413)

    def test_read_only_enforced(self):
        webui._Handler.read_only = True
        try:
            status, data = self._request("PUT", "/api/config", {"values": {"AI_AGENT_BACKOFF_SEC": "900"}})
            self.assertEqual(status, 403)
        finally:
            webui._Handler.read_only = False

    def test_backoffs_clear(self):
        status, data = self._request("POST", "/api/backoffs/clear")
        self.assertEqual(status, 200)
        self.assertIn("cleared", data)

    def test_delete_backoff_traversal_rejected(self):
        status, data = self._request("DELETE", "/api/backoffs/..%2F..%2Fetc")
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "invalid_agent")

    def test_stats_empty(self):
        status, data = self._request("GET", "/api/stats?days=3")
        self.assertEqual(status, 200)
        self.assertIn("days", data)

    def test_predictions_status_is_secret_safe(self):
        status, data = self._request("GET", "/api/predictions")
        self.assertEqual(status, 200)
        self.assertIn("remote", data)
        self.assertIn("worker", data)
        self.assertNotIn("TWITCH_PREDICTIONS_TOKEN", json.dumps(data))

    def test_predictions_action_rejects_unknown_action(self):
        status, data = self._request("POST", "/api/predictions/action", {"action": "shell"})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "invalid_action")

    def test_stream_get_off_state(self):
        status, data = self._request("GET", "/api/stream")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["state"], "off")
        self.assertFalse(data["paused"])
        self.assertFalse(data["chat_paused"])

    def test_stream_stop_creates_marker_and_start_clears_it(self):
        webui.STREAM_START_WAIT_SEC = 1
        try:
            # stop: プロセスなし → 即完了、マーカー作成
            status, data = self._request("POST", "/api/stream", {"action": "stop"})
            self.assertEqual(status, 200)
            self.assertTrue(data["ok"])
            self.assertEqual(data["state"], "paused")
            self.assertTrue((self.soren / "tmp/state/direct_stream.paused").is_file())
            # start: supervisor がいないので off のまま (マーカーは消える)
            status, data = self._request("POST", "/api/stream", {"action": "start"})
            self.assertEqual(status, 200)
            self.assertFalse((self.soren / "tmp/state/direct_stream.paused").exists())
            self.assertEqual(data["state"], "off")
            self.assertFalse(data["ok"])
            self.assertTrue(data.get("hint"))
        finally:
            webui.STREAM_START_WAIT_SEC = 20

    def test_stream_invalid_action_rejected(self):
        status, data = self._request("POST", "/api/stream", {"action": "restart"})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "invalid_action")
        status, data = self._request("POST", "/api/stream", {})
        self.assertEqual(status, 400)

    def test_chat_toggle_roundtrip(self):
        status, data = self._request("POST", "/api/chat", {"action": "stop"})
        self.assertEqual(status, 200)
        self.assertTrue(data["chat_paused"])
        self.assertTrue((self.soren / "tmp/state/chat_worker.paused").is_file())
        status, data = self._request("GET", "/api/stream")
        self.assertTrue(data["chat_paused"])
        status, data = self._request("POST", "/api/chat", {"action": "start"})
        self.assertEqual(status, 200)
        self.assertFalse(data["chat_paused"])
        self.assertFalse((self.soren / "tmp/state/chat_worker.paused").exists())

    def test_chat_invalid_action_rejected(self):
        status, data = self._request("POST", "/api/chat", {"action": "pause"})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "invalid_action")

    def test_stream_and_chat_read_only_enforced(self):
        webui._Handler.read_only = True
        try:
            status, _ = self._request("POST", "/api/stream", {"action": "stop"})
            self.assertEqual(status, 403)
            status, _ = self._request("POST", "/api/chat", {"action": "stop"})
            self.assertEqual(status, 403)
            self.assertFalse((self.soren / "tmp/state/direct_stream.paused").exists())
            self.assertFalse((self.soren / "tmp/state/chat_worker.paused").exists())
        finally:
            webui._Handler.read_only = False

    def test_index_includes_stream_tab(self):
        self.client.request("GET", "/")
        res = self.client.getresponse()
        body = res.read().decode("utf-8")
        self.assertIn('data-tab="stream"', body)
        self.assertIn("/api/stream", body)

    def test_audio_enqueue_and_list(self):
        status, data = self._request("POST", "/api/audio/enqueue", {"text": "読み上げテストです", "source": "webui_test"})
        self.assertEqual(status, 200, data)
        self.assertFalse(data["dedup"])
        status, data = self._request("GET", "/api/audio/queue")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(data["count"], 1)
        self.assertIn("queue_dir", data)
        fname = data["items"][0]["filename"]
        self.assertTrue(fname.startswith("comment_announce_"))

    def test_audio_enqueue_dedup_http(self):
        status, data = self._request("POST", "/api/audio/enqueue", {"text": "重複テスト", "source": "webui_test"})
        self.assertEqual(status, 200, data)
        self.assertFalse(data["dedup"])
        status, data = self._request("POST", "/api/audio/enqueue", {"text": "重複テスト", "source": "webui_test"})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["dedup"])

    def test_audio_enqueue_validation_http(self):
        status, data = self._request("POST", "/api/audio/enqueue", {"text": ""})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "validation_error")
        status, data = self._request("POST", "/api/audio/enqueue", {"text": "x", "source": "../etc"})
        self.assertEqual(status, 400)

    def test_audio_delete_item(self):
        status, data = self._request("POST", "/api/audio/enqueue", {"text": "削除対象", "source": "webui_test"})
        self.assertEqual(status, 200, data)
        fname = data["filename"]
        status, data = self._request("DELETE", f"/api/audio/queue/{fname}")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["deleted"], fname)
        status, data = self._request("DELETE", f"/api/audio/queue/{fname}")
        self.assertEqual(status, 404)

    def test_audio_delete_traversal_rejected(self):
        status, data = self._request("DELETE", "/api/audio/queue/..%2F..%2Fetc%2Fpasswd.txt")
        self.assertEqual(status, 400)

    def test_audio_clear(self):
        self._request("POST", "/api/audio/enqueue", {"text": "クリア対象", "source": "webui_test"})
        status, data = self._request("DELETE", "/api/audio/queue")
        self.assertEqual(status, 200, data)
        self.assertIn("cleared", data)
        status, data = self._request("GET", "/api/audio/queue")
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 0)

    def test_token_auth_required(self):
        self.g.webui.token = "supersecret123"
        try:
            status, data = self._request("GET", "/api/health")
            self.assertEqual(status, 401)
            status, data = self._request("GET", "/api/health", headers={"Authorization": "Bearer supersecret123"})
            self.assertEqual(status, 200)
            # 早期 return (401) でもログ記録の parsed 参照で例外にならないこと
            status, data = self._request("PUT", "/api/config", {"values": {"AI_AGENT_BACKOFF_SEC": "900"}})
            self.assertEqual(status, 401)
            status, data = self._request("DELETE", "/api/backoffs/x")
            self.assertEqual(status, 401)
            status, data = self._request("POST", "/api/backoffs/clear")
            self.assertEqual(status, 401)
            status, data = self._request("POST", "/api/backoffs/clear", headers={"Authorization": "Bearer supersecret123"})
            self.assertEqual(status, 200)
        finally:
            self.g.webui.token = ""


class TestWebUIConfig(unittest.TestCase):
    def test_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _make_global(Path(tmp))
            self.assertEqual(g.webui.bind, "127.0.0.1")
            self.assertEqual(g.webui.port, 8787)
            self.assertEqual(g.webui.token_env, "DOCICH_WEBUI_TOKEN")
            self.assertFalse(g.webui.read_only)

    def test_load_from_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            toml = """[webui]
bind = "0.0.0.0"
port = 9000
read_only = true
"""
            (repo_root / "docich.toml").write_text(toml, encoding="utf-8")
            g = config.load_global(repo_root, config_path=repo_root / "docich.toml")
            self.assertEqual(g.webui.bind, "0.0.0.0")
            self.assertEqual(g.webui.port, 9000)
            self.assertTrue(g.webui.read_only)

    def test_invalid_port_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            toml = '[webui]\nport = 80\n'
            (repo_root / "docich.toml").write_text(toml, encoding="utf-8")
            with self.assertRaises(config.ConfigError):
                config.load_global(repo_root, config_path=repo_root / "docich.toml")


if __name__ == "__main__":
    unittest.main()
