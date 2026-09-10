"""P0-3 tests (Issue #198): dashboard rendering and program-view adapter."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.dashboard import (  # noqa: E402
    COLS,
    ROWS,
    load_snapshot,
    render_dashboard,
    sparkline,
)

NOW = 1_800_000_000.0


def _snapshot(**over):
    base = {
        "schema_version": 1,
        "mode": "paper",
        "worker_state": "paper_worker_idle",
        "capital_reference": "10000",
        "deployed_reference": "3000",
        "open_positions": {"POL/JPY": "99.7738", "SAND/JPY": "239.1200"},
        "eligible_symbols": ["POL/JPY", "SAND/JPY"],
        "recent_fills": [
            {"symbol": "POL/JPY", "side": "buy", "amount": "99.7738",
             "price": "15.034", "quote": "JPY"},
        ],
        "skipped_reason_codes": ["below_min_cost"],
        "signal_summary": {"candidate_count": 2, "candidate_reason_codes": ["mean_reversion_discount"]},
        "snapshot_seq": 7,
        "snapshot_generated_at": NOW,
        "market_freshness": {
            "POL/JPY": {"quality": "fresh", "reason_code": "ok"},
            "SAND/JPY": {"quality": "stale", "reason_code": "stale_data"},
        },
    }
    base.update(over)
    return base


class TestSparkline(unittest.TestCase):
    def test_flat_input_stays_flat(self):
        self.assertEqual(sparkline([100.0] * 24), "▅" * 24)

    def test_empty_input_is_missing(self):
        self.assertIn("―", sparkline([]))

    def test_rising_input_rises(self):
        marks = sparkline([float(i) for i in range(24)])
        self.assertEqual(marks[0], "▁")
        self.assertEqual(marks[-1], "█")


class TestRenderDashboard(unittest.TestCase):
    def test_frame_is_80x24(self):
        import unicodedata

        def width(line):
            return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in line)

        frame = render_dashboard(_snapshot(), {"POL/JPY": [float(i) for i in range(1, 25)]},
                                 now=NOW, remaining_s=1500.0)
        lines = frame.split("\n")
        self.assertEqual(len(lines), ROWS)
        for line in lines:
            self.assertEqual(width(line), COLS)

    def test_header_shows_paper_and_remaining(self):
        frame = render_dashboard(_snapshot(), {}, now=NOW, remaining_s=1500.0)
        self.assertIn("PAPER", frame.split("\n")[0])
        self.assertIn("残り25分", frame.split("\n")[0])

    def test_no_candles_and_no_invented_prices(self):
        frame = render_dashboard(_snapshot(), {}, now=NOW, remaining_s=60.0)
        self.assertNotIn("始値", frame)
        self.assertNotIn("高値", frame)
        self.assertNotIn("安値", frame)
        self.assertNotIn("ローソク", frame)
        self.assertIn("終値", frame)

    def test_empty_snapshot_renders_waiting_screen(self):
        frame = render_dashboard({}, {}, now=NOW, remaining_s=None)
        self.assertIn("観測対象がありません", frame)
        self.assertEqual(len(frame.split("\n")), ROWS)

    def test_malformed_input_never_raises(self):
        frame = render_dashboard({"open_positions": "oops", "recent_fills": [None]},
                                 {"X": ["nan"]}, now=float("nan"), remaining_s=None)
        self.assertEqual(len(frame.split("\n")), ROWS)

    def test_zero_trades_is_normal(self):
        snapshot = _snapshot(open_positions={}, recent_fills=[])
        frame = render_dashboard(snapshot, {}, now=NOW, remaining_s=60.0)
        self.assertIn("未取引は正常", frame)


class TestLoadSnapshot(unittest.TestCase):
    def test_malformed_files_yield_empty_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trading = root / "trading"
            trading.mkdir()
            (trading / "status.json").write_text("{broken", encoding="utf-8")
            snapshot, closes = load_snapshot(root / "trading")
            self.assertEqual((snapshot, closes), ({}, {}))

    def test_closes_use_stored_values_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trading = root / "trading"
            trading.mkdir()
            (trading / "status.json").write_text(json.dumps(_snapshot()), encoding="utf-8")
            (trading / "market_cache.json").write_text(json.dumps({
                "schema_version": 1,
                "symbols": {"POL/JPY": {"closes": ["1.0", "2.0", "oops", -3]}},
            }), encoding="utf-8")
            _snapshot_data, closes = load_snapshot(root / "trading")
            self.assertEqual(closes, {"POL/JPY": [1.0, 2.0]})


class TestProgramViewAdapter(unittest.TestCase):
    def _spec(self, tmp_path):
        import uuid
        from docich.game_switch import RuntimeSpec

        return RuntimeSpec(
            game="paper-view", adapter="program", generation=9,
            runtime_id="g9-x", lease_id=str(uuid.uuid4()),
            runtime_dir=Path(tmp_path) / "run" / "runtimes" / "g9-x",
            game_window="game-g9", agent_window="agent-g9",
            adapter_session="docich-game-g9")

    def test_only_reserved_name_resolves(self):
        import dataclasses
        from docich.adapters.base import AdapterError
        from docich.adapters.program import make_program_view_adapter
        with tempfile.TemporaryDirectory() as tmp:
            spec = self._spec(tmp)
            with self.assertRaises(AdapterError):
                make_program_view_adapter(None, dataclasses.replace(spec, game="robots"))

    def test_view_has_no_agent_and_no_boundary(self):
        from docich.adapters.program import make_program_view_adapter
        with tempfile.TemporaryDirectory() as tmp:
            adapter = make_program_view_adapter(None, self._spec(tmp))
            self.assertFalse(adapter.agent_enabled)
            self.assertFalse(adapter.requires_round_boundary)
            self.assertIsNone(adapter.request_round_boundary)
            self.assertIsNone(adapter.cancel_round_boundary)

    def test_text_dashboard_command_when_configured(self):
        from types import SimpleNamespace
        from docich.adapters.program import make_program_view_adapter
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "docich.toml"
            config.write_text('[paper_corner]\ndashboard = "text"\n', encoding="utf-8")
            g = SimpleNamespace(config_path=str(config), state_dir=root / "run")
            adapter = make_program_view_adapter(g, self._spec(tmp))
            command = adapter._game_command()
            self.assertIn("dashboard-watch", command[-1])
            self.assertNotIn("paper-view", " ".join(command[:-1]))
            self.assertIn(str(root / "run" / "trading"), command)

    def test_html_dashboard_is_default(self):
        from types import SimpleNamespace
        from unittest import mock
        from docich.adapters import program
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = SimpleNamespace(config_path=str(root / "missing.toml"), state_dir=root / "run")
            with mock.patch.object(program, "_browser_bin", return_value="/usr/bin/chromium"):
                adapter = program.make_program_view_adapter(g, self._spec(tmp))
                command = adapter._game_command()
            self.assertEqual(command[-5:], ["dashboard-server", "--host", "127.0.0.1", "--port", "8799"])
            self.assertIn(str(root / "run" / "trading"), command)
            self.assertEqual(adapter.dashboard_kind, "html")

    def test_html_viewer_is_browser_contained(self):
        from types import SimpleNamespace
        from unittest import mock
        from docich.adapters import program
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = SimpleNamespace(
                config_path=str(root / "missing.toml"), state_dir=root / "run",
                display=SimpleNamespace(name=":99", viewport_width=960, viewport_height=540,
                                        viewport_x=0, viewport_y=90))
            with mock.patch.object(program, "_browser_bin", return_value="/usr/bin/chromium"):
                adapter = program.make_program_view_adapter(g, self._spec(tmp))
                viewer = adapter._xterm_command()
            joined = " ".join(viewer)
            self.assertIn("presentation.py", joined)
            self.assertIn("--app=http://127.0.0.1:8799/", joined)
            self.assertIn("--window-size=960,540", joined)
            self.assertNotIn("xterm", joined)

    def test_view_config_is_synthetic(self):
        from docich.adapters.program import paper_view_game_config
        game = paper_view_game_config()
        self.assertEqual(game.name, "paper-view")
        self.assertFalse(game.agent.enabled)
        self.assertFalse(game.lifecycle.require_round_boundary)


if __name__ == "__main__":
    unittest.main()


class TestDashboardWindowTarget(unittest.TestCase):
    def test_readiness_uses_session_birth_window(self):
        from types import SimpleNamespace
        from docich.adapters.program import make_program_view_adapter
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = SimpleNamespace(config_path=str(root / "docich.toml"),
                                state_dir=root / "run")
            import uuid
            from docich.game_switch import RuntimeSpec
            spec = RuntimeSpec(
                game="paper-view", adapter="program", generation=9,
                runtime_id="g9-x", lease_id=str(uuid.uuid4()),
                runtime_dir=root / "run" / "runtimes" / "g9-x",
                game_window="game-g9", agent_window="agent-g9",
                adapter_session="docich-game-g9")
            adapter = make_program_view_adapter(g, spec)
            with self.assertRaises(Exception):
                adapter._dashboard_window_target()


class TestBlockChart(unittest.TestCase):
    def test_flat_stays_flat(self):
        from docich.trading.dashboard import block_chart
        rows = block_chart([100.0] * 24)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(set(row) == {"▅"} for row in rows))

    def test_empty_is_missing(self):
        from docich.trading.dashboard import block_chart
        rows = block_chart([])
        self.assertEqual(len(rows), 3)
        self.assertTrue(all("―" in row for row in rows))

    def test_rising_fills_top_last(self):
        from docich.trading.dashboard import block_chart
        rows = block_chart([float(i) for i in range(24)], width=24, height=3)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(len(row), 24)
        # Top row has blocks only at the high (right) end.
        self.assertTrue(rows[0].startswith(" "))
        self.assertTrue(rows[0].endswith("█"))
        # Bottom row is full once values exceed the lowest band.
        self.assertNotIn(" ", rows[2])


class TestDashboardWindowResolution(unittest.TestCase):
    def _adapter(self, tmp_path):
        from types import SimpleNamespace
        from docich.adapters.program import make_program_view_adapter
        import uuid
        from docich.game_switch import RuntimeSpec
        root = Path(tmp_path)
        g = SimpleNamespace(config_path=str(root / "docich.toml"),
                            state_dir=root / "run")
        spec = RuntimeSpec(
            game="paper-view", adapter="program", generation=9,
            runtime_id="g9-x", lease_id=str(uuid.uuid4()),
            runtime_dir=root / "run" / "runtimes" / "g9-x",
            game_window="game-g9", agent_window="agent-g9",
            adapter_session="docich-game-g9")
        return make_program_view_adapter(g, spec)

    def test_picks_non_game_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._adapter(tmp)
            adapter.tmux.list_windows = lambda: ["python3", "game-g9"]
            self.assertEqual(adapter._dashboard_window_target(),
                             "docich-game-g9:python3")

    def test_ambiguous_windows_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._adapter(tmp)
            adapter.tmux.list_windows = lambda: ["python3", "extra", "game-g9"]
            with self.assertRaises(Exception):
                adapter._dashboard_window_target()


class TestWatchLoopFraming(unittest.TestCase):
    def test_frame_has_no_trailing_newline_scroll(self):
        # The watch loop must not emit a trailing newline: the frame fills
        # exactly ROWS rows, and one more newline scrolls the header (with
        # the readiness marker) off the visible pane.
        import inspect
        from docich.trading import dashboard
        source = inspect.getsource(dashboard.watch_loop)
        self.assertIn('end=""', source)
