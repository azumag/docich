"""RuntimeBackend interface / DocichBackend / SorenBackend の単体テスト (issue #43)。

いずれのテストも実プロセス・実VMに触れない。SorenBackend は tmpdir 上の
pid ファイル・game_state.json のみを扱う (実 soviet_now チェックアウトは不要)。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import runtime_backend as rb  # noqa: E402


class TestCapabilityConstants(unittest.TestCase):
    def test_all_capabilities_are_the_two_read_only_surfaces(self):
        self.assertEqual(set(rb.ALL_CAPABILITIES), {rb.CAPABILITY_LIST_WORKERS, rb.CAPABILITY_GET_STATUS})


class TestRuntimeBackendIsPureInterface(unittest.TestCase):
    def test_cannot_instantiate_abstract_backend(self):
        with self.assertRaises(TypeError):
            rb.RuntimeBackend()  # type: ignore[abstract]


class TestDocichBackend(unittest.TestCase):
    """soviet_now に一切依存しない mock 実装。実プロセス/実ファイルなしで完結する。"""

    def test_default_backend_reports_both_capabilities_supported(self):
        backend = rb.DocichBackend()
        self.assertEqual(backend.capabilities(), {rb.CAPABILITY_LIST_WORKERS: True, rb.CAPABILITY_GET_STATUS: True})

    def test_list_workers_returns_injected_data_without_touching_filesystem(self):
        workers = [{"worker": "fake_worker", "pid": 123, "alive": True, "paused": False, "status": "ok"}]
        backend = rb.DocichBackend(workers=workers)
        self.assertEqual(backend.list_workers(), workers)
        # 呼び出し側が返り値を変更しても内部状態は独立している (防御的コピー)
        backend.list_workers()[0]["pid"] = 999
        self.assertEqual(backend.list_workers()[0]["pid"], 123)

    def test_get_status_returns_injected_data(self):
        status = {"exists": True, "path": "/dev/null", "mtime": 1, "data": {"state": "playing"}, "state": "playing", "score": 7}
        backend = rb.DocichBackend(status=status)
        self.assertEqual(backend.get_status(), status)

    def test_get_status_default_is_explicit_absent_snapshot(self):
        backend = rb.DocichBackend()
        self.assertEqual(
            backend.get_status(),
            {"exists": False, "path": None, "mtime": 0, "data": None, "state": "", "score": None},
        )


class TestSorenBackendUnsupported(unittest.TestCase):
    """soviet_now 未配置 (soren_root が無い / eloop_lib.sh が無い) の扱い。"""

    def test_missing_soren_root_directory_reports_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist"
            backend = rb.SorenBackend(missing)
            self.assertEqual(
                backend.capabilities(),
                {rb.CAPABILITY_LIST_WORKERS: False, rb.CAPABILITY_GET_STATUS: False},
            )

    def test_soren_root_without_eloop_lib_reports_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)  # eloop_lib.sh を置かない = 未配置
            backend = rb.SorenBackend(root)
            self.assertFalse(backend.capabilities()[rb.CAPABILITY_LIST_WORKERS])
            self.assertFalse(backend.capabilities()[rb.CAPABILITY_GET_STATUS])

    def test_calling_unsupported_capability_raises_explicit_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = rb.SorenBackend(Path(tmp))
            with self.assertRaises(rb.CapabilityUnsupportedError):
                backend.list_workers()
            with self.assertRaises(rb.CapabilityUnsupportedError):
                backend.get_status()

    def test_soren_deployed_helper_never_raises_on_missing_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope"
            self.assertFalse(rb.soren_deployed(missing))


class TestSorenBackendSupported(unittest.TestCase):
    """soren_root に eloop_lib.sh がある (=配置済) 場合の read-only 取得。

    実プロセスは一切起動しない: pid ファイルには自プロセスの pid を書いて
    「生存している pid」を作り、存在しない pid で「死んでいる」ケースを作る。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.soren_root = Path(self.tmp.name)
        (self.soren_root / "eloop_lib.sh").write_text("# stub\n", encoding="utf-8")
        (self.soren_root / "tmp/state").mkdir(parents=True)
        self.backend = rb.SorenBackend(self.soren_root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_capabilities_both_true_when_deployed(self):
        self.assertEqual(
            self.backend.capabilities(),
            {rb.CAPABILITY_LIST_WORKERS: True, rb.CAPABILITY_GET_STATUS: True},
        )

    def test_list_workers_reports_not_running_when_no_pid_files(self):
        workers = self.backend.list_workers()
        names = {w["worker"] for w in workers}
        self.assertEqual(names, set(rb._KNOWN_WORKERS))
        for w in workers:
            self.assertIsNone(w["pid"])
            self.assertFalse(w["alive"])
            self.assertEqual(w["status"], "not_running")

    def test_list_workers_detects_alive_pid_via_self_pid(self):
        pid_file = self.soren_root / "tmp/state/radio_worker.pid"
        pid_file.write_text(str(os.getpid()) + "\n", encoding="utf-8")
        workers = self.backend.list_workers()
        row = next(w for w in workers if w["worker"] == "radio_worker")
        self.assertEqual(row["pid"], os.getpid())
        self.assertTrue(row["alive"])
        self.assertEqual(row["status"], "ok")

    def test_list_workers_reports_paused_toggleable_worker(self):
        (self.soren_root / "tmp/state/chat_worker.paused").write_text("{}\n", encoding="utf-8")
        workers = self.backend.list_workers()
        row = next(w for w in workers if w["worker"] == "chat_worker")
        self.assertTrue(row["paused"])
        self.assertEqual(row["status"], "paused")

    def test_get_status_reports_absent_game_state(self):
        status = self.backend.get_status()
        self.assertFalse(status["exists"])
        self.assertIsNone(status["data"])
        self.assertEqual(status["state"], "")
        self.assertIsNone(status["score"])
        self.assertEqual(status["path"], str(self.soren_root / "game_state.json"))

    def test_get_status_reads_existing_game_state_json(self):
        (self.soren_root / "game_state.json").write_text(
            json.dumps({"state": "revolution", "score": 42}), encoding="utf-8"
        )
        status = self.backend.get_status()
        self.assertTrue(status["exists"])
        self.assertEqual(status["state"], "revolution")
        self.assertEqual(status["score"], 42)
        self.assertEqual(status["data"], {"state": "revolution", "score": 42})


if __name__ == "__main__":
    unittest.main()
