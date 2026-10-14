"""P2 tests: CLI (robots) round-boundary detection on CliCoordinatorAdapter."""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.adapters import AdapterError  # noqa: E402
from docich.adapters import cli_game  # noqa: E402
from docich.game_switch import (  # noqa: E402
    DeadlineExceededError,
    ReadinessTimeoutError,
    RuntimeSpec,
)
from docich.naming import runtime_names  # noqa: E402
from docich.tmux import OwnershipMismatchError, PaneState, TmuxOwnership  # noqa: E402


def _spec(generation: int = 1, game: str = "robots") -> RuntimeSpec:
    names = runtime_names(generation)
    runtime_id = f"g{generation}-abcdef"
    return RuntimeSpec(
        game=game,
        adapter="cli",
        generation=generation,
        runtime_id=runtime_id,
        lease_id=str(uuid.uuid4()),
        runtime_dir=Path("/run") / "runtimes" / runtime_id,
        game_window=names.game_window,
        agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


class FakeTmux:
    """Checked tmux subset (test_coordinator_adapter と同じ形状) に、境界待機が
    入力や停止を行わないことを検証するため send_keys 記録を追加したもの。"""

    def __init__(self):
        self.sessions = {}
        self.windows = {}
        self.calls = []
        self.capture = "game screen"
        self.pane_states = [PaneState(dead=False, pid=1234)]

    def _expected(self, ownership):
        return (ownership.runtime_id, ownership.generation, ownership.role)

    @staticmethod
    def _ownership(value):
        return TmuxOwnership(runtime_id=value[0], generation=value[1], role=value[2])

    def session_target_exists(self, session):
        self.calls.append(("session_target_exists", session))
        return session in self.sessions

    def window_target_exists(self, target):
        self.calls.append(("window_target_exists", target))
        return target in self.windows

    def read_session_ownership(self, session):
        self.calls.append(("read_session_ownership", session))
        if session not in self.sessions:
            raise OwnershipMismatchError("missing session")
        return self._ownership(self.sessions[session])

    def read_window_ownership(self, target):
        self.calls.append(("read_window_ownership", target))
        if target not in self.windows:
            raise OwnershipMismatchError("missing window")
        return self._ownership(self.windows[target])

    def create_window_owned(self, name, cmd, ownership, env=None):
        self.calls.append(("create_window_owned", name, list(cmd), self._expected(ownership)))
        self.windows[f"docich:{name}"] = self._expected(ownership)

    def kill_session_owned(self, session, expected):
        self.calls.append(("kill_session_owned", session, self._expected(expected)))
        if session not in self.sessions:
            return False
        del self.sessions[session]
        return True

    def kill_window_owned(self, target, expected):
        self.calls.append(("kill_window_owned", target, self._expected(expected)))
        if target not in self.windows:
            return False
        del self.windows[target]
        return True

    def send_keys(self, session, keys, literal=False):
        self.calls.append(("send_keys", session, list(keys), literal))

    def pane_states_checked(self, target):
        self.calls.append(("pane_states_checked", target))
        return list(self.pane_states)

    def capture_pane_checked(self, target):
        self.calls.append(("capture_pane_checked", target))
        return self.capture


class RoundBoundaryTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        self.g = config.load_global(self.repo_root)
        game_dir = self.repo_root / "config" / "games"
        game_dir.mkdir(parents=True)
        self.game_path = game_dir / "robots.toml"
        self.game_path.write_text(
            '[game]\nname = "robots"\nadapter = "cli"\ntitle = "Robots"\n\n'
            '[lifecycle]\nrequire_round_boundary = true\n\n'
            '[cli]\ncommand = "robots"\n',
            encoding="utf-8",
        )
        self.tmux = FakeTmux()
        self.deadline = time.monotonic() + 60.0
        self.request_id = "12345678-1234-5678-1234-567812345678"
        spec = _spec()
        self.spec = RuntimeSpec(
            game=spec.game,
            adapter=spec.adapter,
            generation=spec.generation,
            runtime_id=spec.runtime_id,
            lease_id=spec.lease_id,
            runtime_dir=Path(self._tmpdir.name) / "runtimes" / spec.runtime_id,
            game_window=spec.game_window,
            agent_window=spec.agent_window,
            adapter_session=spec.adapter_session,
        )
        adapter = cli_game.CliCoordinatorAdapter(
            self.g, config.load_game(self.g, "robots"), self.spec
        )
        adapter.tmux = self.tmux
        self.adapter = adapter

    def tearDown(self):
        self._tmpdir.cleanup()

    def _ready(self):
        self.tmux.sessions[self.spec.adapter_session] = (
            self.spec.runtime_id,
            self.spec.generation,
            "adapter",
        )

    def _result_path(self) -> Path:
        return self.spec.runtime_dir / cli_game.ROUND_BOUNDARY_RESULT_FILENAME

    @staticmethod
    def _input_or_kill_calls(tmux):
        return [
            call for call in tmux.calls
            if call[0] in {"send_keys", "kill_session_owned", "kill_window_owned"}
        ]

    def _read_result(self):
        return json.loads(self._result_path().read_text(encoding="utf-8"))


class TestRequestRoundBoundary(RoundBoundaryTestBase):
    def test_another_game_prompt_acks_and_saves_score(self):
        self._ready()
        self.tmux.capture = (
            "  Robots                    [frame 12]\n"
            "Score: 123\n"
            "Another game? (y/n)"
        )
        self.adapter.request_round_boundary(self.request_id, self.deadline, None)

        path = self._result_path()
        self.assertTrue(path.is_file())
        payload = self._read_result()
        self.assertEqual(payload["schema"], 1)
        self.assertEqual(payload["request_id"], self.request_id)
        self.assertEqual(payload["game"], "robots")
        self.assertEqual(payload["generation"], 1)
        self.assertEqual(payload["prompt"], "Another game? (y/n)")
        self.assertEqual(payload["score"], 123)
        self.assertEqual(payload["captured_pane_tail"], self.tmux.capture.splitlines())
        self.assertTrue(payload["detected_at"].endswith("Z"))
        dt.datetime.fromisoformat(payload["detected_at"].replace("Z", "+00:00"))
        # 非公開ファイル (0600) として原子的に書かれていること。
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self._input_or_kill_calls(self.tmux), [])

    def test_prompt_match_is_case_insensitive(self):
        self._ready()
        self.tmux.capture = "score: 9\nANOTHER GAME? (Y/N)"
        self.adapter.request_round_boundary(self.request_id, self.deadline, None)
        payload = self._read_result()
        self.assertEqual(payload["prompt"], "ANOTHER GAME? (Y/N)")
        self.assertEqual(payload["score"], 9)

    def test_score_takes_max_of_running_and_final_scores(self):
        self._ready()
        self.tmux.capture = "Score: 40\nwave 5\nScore: 123\nAnother game? (y/n)"
        self.adapter.request_round_boundary(self.request_id, self.deadline, None)
        self.assertEqual(self._read_result()["score"], 123)

    def test_score_strips_thousands_commas(self):
        self._ready()
        self.tmux.capture = "Score: 1,234\nAnother game? (y/n)"
        self.adapter.request_round_boundary(self.request_id, self.deadline, None)
        self.assertEqual(self._read_result()["score"], 1234)

    def test_missing_score_line_acks_with_null_score(self):
        self._ready()
        self.tmux.capture = "Another game? (y/n)"
        # prompt が境界の根拠なので score は best-effort: 無くてもackする。
        self.adapter.request_round_boundary(self.request_id, self.deadline, None)
        payload = self._read_result()
        self.assertIsNone(payload["score"])
        self.assertEqual(payload["prompt"], "Another game? (y/n)")

    def test_captured_pane_tail_keeps_last_15_lines(self):
        self._ready()
        lines = [f"wave {index}" for index in range(20)]
        lines.append("Another game? (y/n)")
        self.tmux.capture = "\n".join(lines)
        self.adapter.request_round_boundary(self.request_id, self.deadline, None)
        payload = self._read_result()
        self.assertEqual(len(payload["captured_pane_tail"]), 15)
        self.assertEqual(payload["captured_pane_tail"], self.tmux.capture.splitlines()[-15:])
        self.assertEqual(payload["captured_pane_tail"][-1], "Another game? (y/n)")

    def test_really_quit_is_not_a_boundary(self):
        self._ready()
        self.tmux.capture = "wave 12\nReally quit? (y/n)"
        with mock.patch.object(cli_game, "ROUND_BOUNDARY_POLL_INTERVAL_S", 0.05):
            with self.assertRaises(ReadinessTimeoutError):
                self.adapter.request_round_boundary(
                    self.request_id, time.monotonic() + 0.3, None
                )
        self.assertFalse(self._result_path().exists())
        self.assertEqual(self._input_or_kill_calls(self.tmux), [])

    def test_no_prompt_times_out_without_result_file(self):
        self._ready()
        self.tmux.capture = "wave 5\nScore: 40"
        with mock.patch.object(cli_game, "ROUND_BOUNDARY_POLL_INTERVAL_S", 0.05):
            with self.assertRaises(ReadinessTimeoutError):
                self.adapter.request_round_boundary(
                    self.request_id, time.monotonic() + 0.3, None
                )
        self.assertFalse(self._result_path().exists())
        self.assertEqual(self._input_or_kill_calls(self.tmux), [])

    def test_cancel_during_wait_raises_deadline_exceeded(self):
        self._ready()
        self.tmux.capture = "wave 7"
        cancel = threading.Event()
        errors = []

        def runner():
            try:
                self.adapter.request_round_boundary(
                    self.request_id, time.monotonic() + 5.0, cancel
                )
            except BaseException as exc:  # noqa: BLE001 - assert in main thread
                errors.append(exc)

        with mock.patch.object(cli_game, "ROUND_BOUNDARY_POLL_INTERVAL_S", 0.05):
            worker = threading.Thread(target=runner)
            worker.start()
            try:
                limit = time.monotonic() + 1.0
                while time.monotonic() < limit:
                    if any(call[0] == "capture_pane_checked" for call in self.tmux.calls):
                        break
                    time.sleep(0.01)
                cancel.set()
                worker.join(2.0)
                self.assertFalse(worker.is_alive())
            finally:
                cancel.set()
                worker.join(2.0)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], DeadlineExceededError)
        self.assertFalse(self._result_path().exists())
        self.assertEqual(self._input_or_kill_calls(self.tmux), [])

    def test_missing_session_raises_readiness_timeout(self):
        with self.assertRaises(ReadinessTimeoutError):
            self.adapter.request_round_boundary(self.request_id, self.deadline, None)
        self.assertFalse(self._result_path().exists())

    def test_dead_pane_raises_readiness_timeout(self):
        self._ready()
        self.tmux.pane_states = [PaneState(dead=True, pid=0)]
        with self.assertRaises(ReadinessTimeoutError):
            self.adapter.request_round_boundary(self.request_id, self.deadline, None)
        self.assertFalse(self._result_path().exists())
        self.assertEqual(self._input_or_kill_calls(self.tmux), [])

    def test_ownership_mismatch_raises(self):
        self._ready()
        self.tmux.sessions[self.spec.adapter_session] = ("g1-zzzzzz", 1, "adapter")
        with self.assertRaises(OwnershipMismatchError):
            self.adapter.request_round_boundary(self.request_id, self.deadline, None)
        self.assertFalse(self._result_path().exists())
        self.assertEqual(self._input_or_kill_calls(self.tmux), [])

    def test_unwritable_result_path_does_not_ack(self):
        # 境界は検出できても結果を保存できない場合はackしない (fail closed:
        # coordinatorがruntimeを維持する)。
        self._ready()
        self.tmux.capture = "Score: 5\nAnother game? (y/n)"
        self.spec.runtime_dir.parent.mkdir(parents=True, exist_ok=True)
        self.spec.runtime_dir.write_text("not a directory", encoding="utf-8")
        with self.assertRaises(AdapterError):
            self.adapter.request_round_boundary(self.request_id, self.deadline, None)
        self.assertFalse((self.spec.runtime_dir / cli_game.ROUND_BOUNDARY_RESULT_FILENAME).exists())
        self.assertEqual(self._input_or_kill_calls(self.tmux), [])


class TestRoundBoundaryCapability(RoundBoundaryTestBase):
    def test_capability_is_callable_when_policy_flag_is_true(self):
        # robots.toml 相当 (require_round_boundary = true) では capability を
        # 公開し、coordinatorがdrainingで境界待ちを行う。
        self.assertTrue(self.adapter.requires_round_boundary)
        self.assertTrue(callable(self.adapter.request_round_boundary))
        self.assertTrue(callable(self.adapter.cancel_round_boundary))

    def test_capability_is_hidden_when_policy_flag_is_false(self):
        # policy flagがfalseのゲームは capability を公開しない。coordinatorは
        # drainingへ入らず、従来どおり即時quiesceで切替わる。
        game_path = self.repo_root / "config" / "games" / "nethack.toml"
        game_path.write_text(
            '[game]\nname = "nethack"\nadapter = "cli"\ntitle = "NetHack"\n\n'
            '[cli]\ncommand = "nethack"\n',
            encoding="utf-8",
        )
        adapter = cli_game.CliCoordinatorAdapter(
            self.g, config.load_game(self.g, "nethack"), self.spec
        )
        self.assertFalse(adapter.requires_round_boundary)
        self.assertIsNone(adapter.request_round_boundary)
        self.assertIsNone(adapter.cancel_round_boundary)


class TestCancelRoundBoundary(RoundBoundaryTestBase):
    def test_cancel_returns_true_quickly_without_side_effects(self):
        self._ready()
        started = time.monotonic()
        result = self.adapter.cancel_round_boundary(
            self.request_id, time.monotonic() + 1.0, None
        )
        self.assertTrue(result)
        self.assertLess(time.monotonic() - started, 0.5)
        # 観測 (存在確認とownership照合) 以外の副作用は無いこと。
        self.assertTrue(
            {call[0] for call in self.tmux.calls}
            <= {"session_target_exists", "read_session_ownership"}
        )
        self.assertEqual(self._input_or_kill_calls(self.tmux), [])

    def test_cancel_without_session_is_success(self):
        result = self.adapter.cancel_round_boundary(
            self.request_id, time.monotonic() + 1.0, None
        )
        self.assertTrue(result)
        self.assertFalse(any(call[0] == "read_session_ownership" for call in self.tmux.calls))

    def test_cancel_with_foreign_session_raises(self):
        self._ready()
        self.tmux.sessions[self.spec.adapter_session] = ("g1-zzzzzz", 1, "adapter")
        with self.assertRaises(OwnershipMismatchError):
            self.adapter.cancel_round_boundary(
                self.request_id, time.monotonic() + 1.0, None
            )

    def test_cancel_honors_cancel_event(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(DeadlineExceededError):
            self.adapter.cancel_round_boundary(
                self.request_id, time.monotonic() + 1.0, cancel
            )


if __name__ == "__main__":
    unittest.main()
