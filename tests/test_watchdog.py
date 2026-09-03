import hashlib
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import cli, config, watchdog  # noqa: E402
from docich.state import State  # noqa: E402


class TestFreezeDetector(unittest.TestCase):
    def test_returns_true_once_when_threshold_reached(self):
        d = watchdog.FreezeDetector(3)
        self.assertFalse(d.feed("a"))
        self.assertFalse(d.feed("a"))
        self.assertTrue(d.feed("a"))  # 3回連続に達した瞬間だけ True

    def test_does_not_repeat_true_immediately_after_reporting(self):
        d = watchdog.FreezeDetector(3)
        d.feed("a")
        d.feed("a")
        self.assertTrue(d.feed("a"))
        self.assertFalse(d.feed("a"))  # 同じフリーズ状態のままでは連発しない
        self.assertFalse(d.feed("a"))

    def test_digest_change_resets_counter(self):
        d = watchdog.FreezeDetector(3)
        self.assertFalse(d.feed("a"))
        self.assertFalse(d.feed("a"))
        self.assertFalse(d.feed("b"))  # 変化でリセットされ、まだ True にならない
        self.assertFalse(d.feed("b"))
        self.assertTrue(d.feed("b"))  # b で改めて3回連続

    def test_none_resets_counter(self):
        d = watchdog.FreezeDetector(3)
        self.assertFalse(d.feed("a"))
        self.assertFalse(d.feed("a"))
        self.assertFalse(d.feed(None))  # 撮影失敗はリセット扱い
        self.assertFalse(d.feed("a"))
        self.assertFalse(d.feed("a"))
        self.assertTrue(d.feed("a"))  # 改めて3回連続必要

    def test_requires_threshold_again_after_report_even_if_digest_unchanged(self):
        # remedy が効かず同じ digest が続くケースでも、再度 threshold 回必要
        # (True を返した直後にカウンタが 0 から数え直しになる)。
        d = watchdog.FreezeDetector(3)
        d.feed("a")
        d.feed("a")
        self.assertTrue(d.feed("a"))
        self.assertFalse(d.feed("a"))
        self.assertFalse(d.feed("a"))
        self.assertTrue(d.feed("a"))

    def test_requires_threshold_again_after_report_when_digest_changes(self):
        # remedy が効いて画面が変わったケース: 新しい digest でも threshold 回必要。
        d = watchdog.FreezeDetector(2)
        d.feed("a")
        self.assertTrue(d.feed("a"))
        self.assertFalse(d.feed("b"))
        self.assertTrue(d.feed("b"))


class TestNextRotationGame(unittest.TestCase):
    def test_advances_to_next(self):
        self.assertEqual(watchdog.next_rotation_game(["a", "b", "c"], "a"), "b")
        self.assertEqual(watchdog.next_rotation_game(["a", "b", "c"], "b"), "c")

    def test_wraps_from_last_to_first(self):
        self.assertEqual(watchdog.next_rotation_game(["a", "b", "c"], "c"), "a")

    def test_current_none_returns_first(self):
        self.assertEqual(watchdog.next_rotation_game(["a", "b", "c"], None), "a")

    def test_current_not_in_list_returns_first(self):
        self.assertEqual(watchdog.next_rotation_game(["a", "b", "c"], "zzz"), "a")

    def test_empty_games_raises_value_error(self):
        with self.assertRaises(ValueError):
            watchdog.next_rotation_game([], "a")
        with self.assertRaises(ValueError):
            watchdog.next_rotation_game([], None)


class TestDocichBin(unittest.TestCase):
    def test_points_at_repo_bin_docich(self):
        # cli._docich_bin() と同じ結果になること (循環import回避のための独自導出)。
        self.assertEqual(watchdog._docich_bin(), cli._docich_bin())
        self.assertTrue(watchdog._docich_bin().endswith("bin/docich"))


class WatchdogConfigTestBase(unittest.TestCase):
    """State/Tmux/XKit を絡めた単体テスト用の最小 GlobalConfig を作る。
    実際の tmux/X には一切触れない (Tmux/XKit は MagicMock で差し替える)。"""

    def _make_config(self, tmp: Path, extra: str = "") -> config.GlobalConfig:
        toml_path = Path(tmp) / "docich.toml"
        toml_path.write_text(
            "[paths]\n"
            f'state_dir = "{Path(tmp) / "run"}"\n\n'
            "[watchdog]\n"
            "freeze_cycles = 2\n"
            f"{extra}\n",
            encoding="utf-8",
        )
        return config.load_global(Path(tmp), config_path=toml_path)


class TestCheckWindows(WatchdogConfigTestBase):
    def test_missing_display_window_triggers_up_remedy(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._make_config(tmp)
            tmux = mock.MagicMock()
            tmux.has_window.side_effect = lambda name: name != "display"
            with mock.patch("docich.watchdog._run_remedy", return_value=0) as remedy:
                watchdog._check_windows(g, tmux)
            remedy.assert_called_once_with(g, "up")

    def test_all_windows_present_does_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._make_config(tmp)  # audio.enabled=既定true, stream.mode=既定null
            tmux = mock.MagicMock()
            tmux.has_window.return_value = True
            with mock.patch("docich.watchdog._run_remedy") as remedy:
                watchdog._check_windows(g, tmux)
            remedy.assert_not_called()

    def test_stream_window_missing_is_ignored_when_mode_is_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._make_config(tmp)  # stream.mode 既定 "null"
            tmux = mock.MagicMock()
            tmux.has_window.side_effect = lambda name: name != "stream"
            with mock.patch("docich.watchdog._run_remedy") as remedy:
                watchdog._check_windows(g, tmux)
            remedy.assert_not_called()


class TestCheckFreeze(WatchdogConfigTestBase):
    def test_skips_when_agent_window_not_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._make_config(tmp)
            state = State(g)
            state.ensure()
            state.set_current_game("nethack")
            tmux = mock.MagicMock()
            tmux.has_window.side_effect = lambda name: name == "game"  # agent 無し
            xkit = mock.MagicMock()
            detector = watchdog.FreezeDetector(g.watchdog.freeze_cycles)
            watchdog._check_freeze(g, state, tmux, xkit, detector, Path(tmp) / "frame.png")
            xkit.screenshot.assert_not_called()

    def test_skips_when_no_current_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._make_config(tmp)
            state = State(g)
            state.ensure()
            tmux = mock.MagicMock()
            tmux.has_window.return_value = True
            xkit = mock.MagicMock()
            detector = watchdog.FreezeDetector(g.watchdog.freeze_cycles)
            watchdog._check_freeze(g, state, tmux, xkit, detector, Path(tmp) / "frame.png")
            xkit.screenshot.assert_not_called()

    def test_fires_remedy_after_threshold_identical_screenshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._make_config(tmp)  # freeze_cycles = 2
            state = State(g)
            state.ensure()
            state.set_current_game("nethack")
            tmux = mock.MagicMock()
            tmux.has_window.return_value = True  # game/agent どちらも起動中
            xkit = mock.MagicMock()

            frame_path = Path(tmp) / "watchdog" / "frame.png"
            frame_path.parent.mkdir(parents=True, exist_ok=True)

            def fake_screenshot(path, _width, _height):
                Path(path).write_bytes(b"same-frame")
                return Path(path)

            xkit.screenshot.side_effect = fake_screenshot
            detector = watchdog.FreezeDetector(g.watchdog.freeze_cycles)

            with mock.patch("docich.watchdog._run_remedy", return_value=0) as remedy, \
                    mock.patch(
                        "docich.watchdog._active_tuple",
                        return_value=("nethack", "g1-a", 1, None),
                    ):
                watchdog._check_freeze(g, state, tmux, xkit, detector, frame_path)
                remedy.assert_not_called()  # tuple 確立で判定保留
                watchdog._check_freeze(g, state, tmux, xkit, detector, frame_path)
                remedy.assert_not_called()  # 1回目: まだ閾値未到達
                watchdog._check_freeze(g, state, tmux, xkit, detector, frame_path)
            remedy.assert_called_once_with(g, "restart")

    def test_tuple_change_resets_and_holds_judgment(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._make_config(tmp)
            state = State(g)
            state.ensure()
            state.set_current_game("nethack")
            tmux = mock.MagicMock()
            tmux.has_window.return_value = True
            xkit = mock.MagicMock()
            frame_path = Path(tmp) / "frame.png"
            frame_path.write_bytes(b"same-frame")
            xkit.screenshot.side_effect = lambda *a: frame_path
            detector = watchdog.FreezeDetector(g.watchdog.freeze_cycles)

            with mock.patch("docich.watchdog._run_remedy") as remedy, \
                    mock.patch("docich.watchdog._active_tuple") as tuple_mock:
                tuple_mock.return_value = ("nethack", "g1-a", 1, None)
                watchdog._check_freeze(g, state, tmux, xkit, detector, frame_path)
                tuple_mock.return_value = ("nethack", "g2-b", 2, None)  # 切替で tuple 変化
                watchdog._check_freeze(g, state, tmux, xkit, detector, frame_path)
                watchdog._check_freeze(g, state, tmux, xkit, detector, frame_path)
            remedy.assert_not_called()  # tuple 変化で判定保留

    def test_absent_tuple_resets_and_holds_judgment(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._make_config(tmp)
            state = State(g)
            state.ensure()
            tmux = mock.MagicMock()
            tmux.has_window.return_value = True
            xkit = mock.MagicMock()
            detector = watchdog.FreezeDetector(g.watchdog.freeze_cycles)
            with mock.patch("docich.watchdog._run_remedy") as remedy, \
                    mock.patch("docich.watchdog._active_tuple", return_value=None):
                watchdog._check_freeze(g, state, tmux, xkit, detector, Path(tmp) / "f.png")
            remedy.assert_not_called()
            xkit.screenshot.assert_not_called()

    def test_screenshot_failure_feeds_none_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._make_config(tmp)
            state = State(g)
            state.ensure()
            state.set_current_game("nethack")
            tmux = mock.MagicMock()
            tmux.has_window.return_value = True
            xkit = mock.MagicMock()
            xkit.screenshot.side_effect = subprocess.CalledProcessError(1, ["ffmpeg"])
            detector = watchdog.FreezeDetector(g.watchdog.freeze_cycles)
            # 例外を外へ漏らさず完了することを確認する (digest=None として feed する)。
            watchdog._check_freeze(g, state, tmux, xkit, detector, Path(tmp) / "frame.png")


class TestFreezeTargets(unittest.TestCase):
    """watchdog._freeze_targets: canonical active があれば世代別 window、
    無ければ legacy (固定 window + mirror) にフォールバックする。"""

    def _write_config(self, tmp: Path) -> Path:
        toml_path = tmp / "docich.toml"
        toml_path.write_text(
            "[paths]\n"
            f'state_dir = "{tmp / "run"}"\n'
            f'games_dir = "{tmp / "games"}"\n'
            f'roms_dir = "{tmp / "roms"}"\n',
            encoding="utf-8",
        )
        return toml_path

    def test_legacy_fallback_without_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            toml_path = self._write_config(tmp_path)
            g = config.load_global(tmp_path, config_path=toml_path)
            state = State(g)
            state.set_current_game("nethack")
            current, game_window, agent_window = watchdog._freeze_targets(g, state)
            self.assertEqual((current, game_window, agent_window), ("nethack", "game", "agent"))

    def test_generation_windows_when_canonical_active(self):
        import uuid

        from docich.game_switch import GameSwitchStore
        from docich.naming import runtime_names

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            toml_path = self._write_config(tmp_path)
            g = config.load_global(tmp_path, config_path=toml_path)
            state = State(g)
            names = runtime_names(2)
            store = GameSwitchStore(g.state_dir)
            canonical, _ = store.canonical.load()
            canonical.update(
                {
                    "phase": "ready",
                    "active": {
                        "game": "robots",
                        "adapter": "cli",
                        "generation": 2,
                        "runtime_id": "g2-abcdef",
                        "lease_id": str(uuid.uuid4()),
                        "game_window": names.game_window,
                        "agent_window": names.agent_window,
                        "adapter_session": names.adapter_session,
                        "started_at": "2026-09-03T00:00:00Z",
                    },
                    "next_generation": 3,
                }
            )
            store.canonical.save(canonical)
            current, game_window, agent_window = watchdog._freeze_targets(g, state)
            self.assertEqual((current, game_window, agent_window), ("robots", "game-g2", "agent-g2"))

    def test_freeze_detection_triggers_on_generation_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            toml_path = self._write_config(tmp_path)
            g = config.load_global(tmp_path, config_path=toml_path)
            state = State(g)
            tmux = mock.MagicMock()
            # legacy "game"/"agent" は存在せず、世代別 window だけがある
            tmux.has_window.side_effect = lambda name: name in ("game-g2", "agent-g2")
            xkit = mock.MagicMock()
            digest = hashlib.sha256(b"frame").hexdigest()
            frame_path = tmp_path / "frame.png"
            frame_path.write_bytes(b"frame")
            xkit.screenshot.side_effect = lambda *a: frame_path
            detector = mock.MagicMock()
            detector.feed.return_value = False
            from unittest.mock import patch as _patch

            with _patch("docich.watchdog._freeze_targets", return_value=("robots", "game-g2", "agent-g2")):
                watchdog._check_freeze(g, state, tmux, xkit, detector, frame_path)
            self.assertTrue(detector.feed.called)


class TestCmdRotate(unittest.TestCase):
    """cli.cmd_rotate: 切替先決定・dry-run・空 games のエラー処理。
    dry-run は tmux に触れない。非 dry-run は coordinator の rotate 経由で
    lock 内で target を決定する。実際の tmux/X には一切触れない。"""

    def _write_config(self, tmp: Path, games: list[str]) -> Path:
        games_literal = ", ".join(f'"{name}"' for name in games)
        toml_path = tmp / "docich.toml"
        toml_path.write_text(
            "[paths]\n"
            f'state_dir = "{tmp / "run"}"\n'
            f'games_dir = "{tmp / "games"}"\n'
            f'roms_dir = "{tmp / "roms"}"\n\n'
            "[rotation]\n"
            f"games = [{games_literal}]\n",
            encoding="utf-8",
        )
        return toml_path

    def test_rotate_without_games_raises_cli_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            toml_path = self._write_config(tmp_path, [])
            g = config.load_global(tmp_path, config_path=toml_path)
            with self.assertRaises(cli.CliError):
                cli.cmd_rotate(g, dry_run=True)

    def test_rotate_dry_run_prints_target_and_does_not_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            toml_path = self._write_config(tmp_path, ["nethack", "hanjuku-hero"])
            g = config.load_global(tmp_path, config_path=toml_path)
            with mock.patch("docich.cli._coordinator") as coordinator_mock:
                out = io.StringIO()
                with redirect_stdout(out):
                    rc = cli.cmd_rotate(g, dry_run=True)
            self.assertEqual(rc, 0)
            coordinator_mock.assert_not_called()
            self.assertIn("nethack", out.getvalue())  # current が無いので games[0]

    def test_rotate_switches_to_the_game_after_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            toml_path = self._write_config(tmp_path, ["nethack", "hanjuku-hero"])
            g = config.load_global(tmp_path, config_path=toml_path)
            State(g).set_current_game("nethack")
            result = cli.SwitchResult(
                request_id="r", operation="rotate", status="succeeded",
                target=None, from_game="nethack", to_game="hanjuku-hero",
                generation=2, error_code=None, detail=None,
                warnings=(), cleanup_pending=False, receipt=None,
            )
            with mock.patch("docich.cli._coordinator") as coordinator_mock, \
                    mock.patch("docich.cli._require_no_legacy_runtime"):
                coordinator_mock.return_value.rotate.return_value = result
                rc = cli.cmd_rotate(g, dry_run=False)
            self.assertEqual(rc, 0)
            coordinator_mock.return_value.rotate.assert_called_once()
            self.assertEqual(
                coordinator_mock.return_value.rotate.call_args.args[0],
                ["nethack", "hanjuku-hero"],
            )

    def test_rotate_wraps_from_last_game_to_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            toml_path = self._write_config(tmp_path, ["nethack", "hanjuku-hero"])
            g = config.load_global(tmp_path, config_path=toml_path)
            State(g).set_current_game("hanjuku-hero")
            result = cli.SwitchResult(
                request_id="r", operation="rotate", status="succeeded",
                target=None, from_game="hanjuku-hero", to_game="nethack",
                generation=2, error_code=None, detail=None,
                warnings=(), cleanup_pending=False, receipt=None,
            )
            with mock.patch("docich.cli._coordinator") as coordinator_mock, \
                    mock.patch("docich.cli._require_no_legacy_runtime"):
                coordinator_mock.return_value.rotate.return_value = result
                cli.cmd_rotate(g, dry_run=False)
            coordinator_mock.return_value.rotate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
