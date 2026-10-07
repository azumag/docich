"""Regression tests for the game-switch orphan sweep (Issue #1105).

A tmux teardown that only walks pane descendants misses a process whose
parent (the pane leader) already exited: tmux reparents it, it leaves the
descendant tree and keeps burning CPU after the switch reports success.
These tests pin down what the teardown may and may not reclaim.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import tmux as tmux_mod  # noqa: E402
from docich.process_tree import TerminationResult  # noqa: E402


def _ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _stopped(*pids: int) -> TerminationResult:
    return TerminationResult(
        roots=tuple(pids), term_sent=tuple(pids), kill_sent=(), remaining=()
    )


def _survived(pids: tuple[int, ...]) -> TerminationResult:
    return TerminationResult(roots=pids, term_sent=pids, kill_sent=(), remaining=pids)


OWNER = tmux_mod.TmuxOwnership("g1-abcdef", 1, "game")


class TestOwnershipEnvironmentExport(unittest.TestCase):
    def test_window_creation_exports_runtime_tags(self):
        tmux = tmux_mod.Tmux()
        with mock.patch("docich.tmux.procs.run") as mock_run:
            mock_run.side_effect = [
                _ok("@7\n"),
                _ok(), _ok(), _ok(),
                _ok("g1-abcdef\n"), _ok("1\n"), _ok("game\n"),
            ]
            window_id = tmux.create_window_owned(
                "game-g1", ["moon-buggy"], OWNER, env={"DISPLAY": ":99"}
            )

        self.assertEqual(window_id, "@7")
        args = mock_run.call_args_list[0].args[0]
        self.assertIn("DISPLAY=:99", args)
        self.assertIn("DOCICH_TMUX_RUNTIME_ID=g1-abcdef", args)
        self.assertIn("DOCICH_TMUX_GENERATION=1", args)
        self.assertIn("DOCICH_TMUX_ROLE=game", args)
        self.assertEqual(args[-1], "moon-buggy")

    def test_session_creation_exports_runtime_tags(self):
        tmux = tmux_mod.Tmux()
        adapter = tmux_mod.TmuxOwnership("g1-abcdef", 1, "adapter")
        with mock.patch("docich.tmux.procs.run") as mock_run:
            mock_run.side_effect = [
                _ok("$4\n"), _ok(),
                _ok(), _ok(), _ok(),
                _ok("g1-abcdef\n"), _ok("1\n"), _ok("adapter\n"),
            ]
            session_id = tmux.create_game_session_owned(
                "docich-game-g1", ["moon-buggy"], 80, 24, adapter
            )

        self.assertEqual(session_id, "$4")
        args = mock_run.call_args_list[0].args[0]
        self.assertIn("DOCICH_TMUX_RUNTIME_ID=g1-abcdef", args)
        self.assertIn("DOCICH_TMUX_ROLE=adapter", args)
        self.assertEqual(args[-1], "moon-buggy")


class TestOrphanSweepOnWindowTeardown(unittest.TestCase):
    def setUp(self):
        self.tmux = tmux_mod.Tmux()

    @staticmethod
    def _window_kill_calls() -> list[subprocess.CompletedProcess]:
        return [
            _ok("game-g1\n"),
            _ok("g1-abcdef\n"), _ok("1\n"), _ok("game\n"),
            _ok("123\n"),
            _ok(),
        ]

    @mock.patch("docich.tmux.process_pgid", return_value=None)
    @mock.patch("docich.tmux.processes_with_env")
    @mock.patch("docich.tmux.terminate_process_tree")
    @mock.patch("docich.tmux.procs.run")
    def test_tagged_orphan_outside_the_pane_is_reclaimed(
        self, mock_run, mock_terminate, mock_env, _mock_pgid
    ):
        mock_run.side_effect = self._window_kill_calls()
        mock_env.return_value = [4242]
        mock_terminate.side_effect = [_stopped(123), _stopped(4242)]

        self.assertTrue(self.tmux.kill_window_owned("docich:game-g1", OWNER))

        self.assertEqual(
            [call.args[0] for call in mock_terminate.call_args_list],
            [[123], [4242]],
        )
        self.assertEqual(
            mock_env.call_args.args[0],
            {
                "DOCICH_TMUX_RUNTIME_ID": "g1-abcdef",
                "DOCICH_TMUX_GENERATION": "1",
                "DOCICH_TMUX_ROLE": "game",
            },
        )

    @mock.patch("docich.tmux.process_pgid", return_value=None)
    @mock.patch("docich.tmux.processes_with_env", return_value=[])
    @mock.patch("docich.tmux.terminate_process_tree")
    @mock.patch("docich.tmux.procs.run")
    def test_unattributable_processes_are_never_signalled(
        self, mock_run, mock_terminate, _mock_env, _mock_pgid
    ):
        mock_run.side_effect = self._window_kill_calls()
        mock_terminate.side_effect = [_stopped(123)]

        self.assertTrue(self.tmux.kill_window_owned("docich:game-g1", OWNER))

        mock_terminate.assert_called_once_with([123])

    @mock.patch("docich.tmux.process_pgid", return_value=None)
    @mock.patch("docich.tmux.processes_with_env", return_value=[4242])
    @mock.patch("docich.tmux.terminate_process_tree")
    @mock.patch("docich.tmux.procs.run")
    def test_surviving_orphan_fails_the_teardown_closed(
        self, mock_run, mock_terminate, _mock_env, _mock_pgid
    ):
        mock_run.side_effect = self._window_kill_calls()
        mock_terminate.side_effect = [_stopped(123), _survived((4242,))]

        with self.assertRaises(tmux_mod.TmuxError) as ctx:
            self.tmux.kill_window_owned("docich:game-g1", OWNER)

        self.assertIn("4242", str(ctx.exception))

    @mock.patch("docich.tmux.ancestor_pids", return_value=[])
    @mock.patch("docich.tmux.processes_in_pane_scopes", return_value=[777])
    @mock.patch("docich.tmux.is_running", return_value=False)
    @mock.patch("docich.tmux.process_pgid", return_value=123)
    @mock.patch("docich.tmux.processes_with_env", return_value=[])
    @mock.patch("docich.tmux.terminate_process_tree")
    @mock.patch("docich.tmux.procs.run")
    def test_reparented_child_is_found_by_pane_process_group(
        self, mock_run, mock_terminate, _mock_env, _mock_pgid, _mock_running,
        mock_scopes, _mock_ancestors,
    ):
        mock_run.side_effect = self._window_kill_calls()
        mock_terminate.side_effect = [_stopped(123), _stopped(777)]

        self.assertTrue(self.tmux.kill_window_owned("docich:game-g1", OWNER))

        mock_scopes.assert_called_once_with({123}, cgroup_marker="tmux-spawn-")
        self.assertEqual(
            [call.args[0] for call in mock_terminate.call_args_list],
            [[123], [777]],
        )

    def test_pane_group_evidence_is_skipped_while_a_leader_lives(self):
        with (
            mock.patch("docich.tmux.is_running", return_value=True),
            mock.patch("docich.tmux.processes_with_env", return_value=[]),
            mock.patch("docich.tmux.processes_in_pane_scopes") as mock_scopes,
        ):
            victims = self.tmux._escaped_process_ids(None, None, {123: 123})

        self.assertEqual(victims, ())
        mock_scopes.assert_not_called()

    def test_sweep_refuses_more_than_the_cap(self):
        over_cap = [5000 + index for index in range(tmux_mod.MAX_ORPHAN_SWEEP_PROCESSES + 1)]
        with (
            mock.patch("docich.tmux.processes_with_env", return_value=over_cap),
            mock.patch("docich.tmux.terminate_process_tree", return_value=_stopped(123)) as mock_terminate,
        ):
            with self.assertRaises(tmux_mod.TmuxError) as ctx:
                self.tmux._reap_escaped_processes(
                    OWNER, ("game",), {}, operation="window停止"
                )

        self.assertIn("上限", str(ctx.exception))
        mock_terminate.assert_not_called()

    def test_caller_ancestors_are_never_signalled(self):
        with (
            mock.patch(
                "docich.tmux.processes_with_env",
                return_value=[os.getpid(), 4242],
            ),
            mock.patch(
                "docich.tmux.ancestor_pids", return_value=[os.getpid(), 1]
            ),
        ):
            victims = self.tmux._escaped_process_ids(OWNER, ("game",), {})

        self.assertEqual(victims, (4242,))

    def test_reclaim_is_logged_for_the_switch_timeline(self):
        with (
            mock.patch("docich.tmux.processes_with_env", return_value=[4242]),
            mock.patch("docich.tmux.terminate_process_tree", return_value=_stopped(4242)),
            self.assertLogs("docich.tmux", level="WARNING") as logs,
        ):
            remaining = self.tmux._reap_escaped_processes(
                OWNER, ("game",), {}, operation="window停止"
            )

        self.assertEqual(remaining, ())
        self.assertTrue(any("4242" in line for line in logs.output))


class TestOrphanSweepOnSessionTeardown(unittest.TestCase):
    def setUp(self):
        self.tmux = tmux_mod.Tmux()

    @staticmethod
    def _session_kill_calls() -> list[subprocess.CompletedProcess]:
        return [
            _ok(),
            _ok("g1-abcdef\n"), _ok("1\n"), _ok("adapter\n"),
            _ok("123\n"),
            _ok(),
        ]

    @mock.patch("docich.tmux.process_pgid", return_value=None)
    @mock.patch("docich.tmux.processes_with_env")
    @mock.patch("docich.tmux.terminate_process_tree")
    @mock.patch("docich.tmux.procs.run")
    def test_session_kill_reclaims_every_role_of_this_runtime(
        self, mock_run, mock_terminate, mock_env, _mock_pgid
    ):
        mock_run.side_effect = self._session_kill_calls()
        mock_env.return_value = [901]
        mock_terminate.side_effect = [_stopped(123), _stopped(901)]
        adapter = tmux_mod.TmuxOwnership("g1-abcdef", 1, "adapter")

        self.assertTrue(self.tmux.kill_session_owned("docich-game-g1", adapter))

        # A session teardown owns every role, so the sweep must not narrow the
        # query to one role (a role filter would leave the game orphan behind).
        self.assertEqual(
            mock_env.call_args.args[0],
            {
                "DOCICH_TMUX_RUNTIME_ID": "g1-abcdef",
                "DOCICH_TMUX_GENERATION": "1",
            },
        )
        self.assertEqual(
            [call.args[0] for call in mock_terminate.call_args_list],
            [[123], [901]],
        )

    @mock.patch("docich.tmux.ancestor_pids", return_value=[])
    @mock.patch("docich.tmux.processes_in_pane_scopes", return_value=[777])
    @mock.patch("docich.tmux.is_running", return_value=False)
    @mock.patch("docich.tmux.process_pgid", return_value=123)
    @mock.patch("docich.tmux.terminate_process_tree")
    @mock.patch("docich.tmux.procs.run")
    def test_legacy_session_cleanup_uses_pane_group_evidence(
        self, mock_run, mock_terminate, _mock_pgid, _mock_running,
        mock_scopes, _mock_ancestors,
    ):
        mock_run.side_effect = [_ok("123\n"), _ok()]
        mock_terminate.side_effect = [_stopped(123), _stopped(777)]

        self.tmux.stop_game_session_named("docich-game-g7")

        mock_scopes.assert_called_once_with({123}, cgroup_marker="tmux-spawn-")
        self.assertEqual(
            [call.args[0] for call in mock_terminate.call_args_list],
            [[123], [777]],
        )


if __name__ == "__main__":
    unittest.main()
