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


def _fail(stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


def _stopped(*pids: int) -> TerminationResult:
    return TerminationResult(
        roots=tuple(pids), term_sent=tuple(pids), kill_sent=(), remaining=()
    )


def _survived(pids: tuple[int, ...]) -> TerminationResult:
    return TerminationResult(roots=pids, term_sent=pids, kill_sent=(), remaining=pids)


OWNER = tmux_mod.TmuxOwnership("g1-abcdef", 1, "game")
PANE_SCOPE = tmux_mod.PaneProcessScope(123, "0::/tmux-spawn-original.scope")


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
        pane_stop = mock.patch("docich.tmux.terminate_process_tree", return_value=_stopped(123))
        self.pane_stop = pane_stop.start()
        self.addCleanup(pane_stop.stop)
        cgroup = mock.patch("docich.tmux.process_cgroup", return_value=PANE_SCOPE.cgroup)
        cgroup.start()
        self.addCleanup(cgroup.stop)

    @staticmethod
    def _window_kill_calls() -> list[subprocess.CompletedProcess]:
        return [
            _ok("game-g1\n"),
            _ok("g1-abcdef\n"), _ok("1\n"), _ok("game\n"),
            _ok("123\n"),
            _ok("4321\n"),
            _ok("123\n"),
            _ok(),
        ]

    @mock.patch("docich.tmux.process_pgid", return_value=None)
    @mock.patch("docich.tmux.processes_with_env")
    @mock.patch("docich.tmux.terminate_owned_processes")
    @mock.patch("docich.tmux.procs.run")
    def test_tagged_orphan_outside_the_pane_is_reclaimed(
        self, mock_run, mock_terminate, mock_env, _mock_pgid
    ):
        mock_run.side_effect = self._window_kill_calls()
        mock_env.return_value = [4242]
        mock_terminate.side_effect = [_stopped(4242)]

        self.assertTrue(self.tmux.kill_window_owned("docich:game-g1", OWNER))

        self.assertEqual(
            [call.args[0] for call in mock_terminate.call_args_list],
            [(4242,)],
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
    @mock.patch("docich.tmux.terminate_owned_processes")
    @mock.patch("docich.tmux.procs.run")
    def test_unattributable_processes_are_never_signalled(
        self, mock_run, mock_terminate, _mock_env, _mock_pgid
    ):
        mock_run.side_effect = self._window_kill_calls()
        mock_terminate.return_value = _stopped()

        self.assertTrue(self.tmux.kill_window_owned("docich:game-g1", OWNER))

        mock_terminate.assert_not_called()
        self.pane_stop.assert_called_once_with([123])

    @mock.patch("docich.tmux.process_pgid", return_value=None)
    @mock.patch("docich.tmux.processes_with_env", return_value=[4242])
    @mock.patch("docich.tmux.terminate_owned_processes")
    @mock.patch("docich.tmux.procs.run")
    def test_surviving_orphan_fails_the_teardown_closed(
        self, mock_run, mock_terminate, _mock_env, _mock_pgid
    ):
        mock_run.side_effect = self._window_kill_calls()
        mock_terminate.side_effect = [_survived((4242,))]

        with self.assertRaises(tmux_mod.TmuxError) as ctx:
            self.tmux.kill_window_owned("docich:game-g1", OWNER)

        self.assertIn("4242", str(ctx.exception))

    @mock.patch("docich.tmux.ancestor_pids", return_value=[])
    @mock.patch("docich.tmux.processes_in_pane_scopes", return_value=[777])
    @mock.patch("docich.tmux.is_running", return_value=False)
    @mock.patch("docich.tmux.process_pgid", return_value=123)
    @mock.patch("docich.tmux.processes_with_env", return_value=[])
    @mock.patch("docich.tmux.terminate_owned_processes")
    @mock.patch("docich.tmux.procs.run")
    def test_reparented_child_is_found_by_pane_process_group(
        self, mock_run, mock_terminate, _mock_env, _mock_pgid, _mock_running,
        mock_scopes, _mock_ancestors,
    ):
        mock_run.side_effect = self._window_kill_calls()
        mock_terminate.side_effect = [_stopped(777)]

        self.assertTrue(self.tmux.kill_window_owned("docich:game-g1", OWNER))

        mock_scopes.assert_called_once_with({PANE_SCOPE})
        self.assertEqual(
            [call.args[0] for call in mock_terminate.call_args_list],
            [(777,)],
        )

    def test_pane_group_evidence_is_skipped_while_a_leader_lives(self):
        with (
            mock.patch("docich.tmux.is_running", return_value=True),
            mock.patch("docich.tmux.processes_with_env", return_value=[]),
            mock.patch("docich.tmux.processes_in_pane_scopes") as mock_scopes,
        ):
            victims = self.tmux._escaped_process_ids(None, None, {123: PANE_SCOPE})

        self.assertEqual(victims, ())
        mock_scopes.assert_not_called()

    def test_sweep_refuses_more_than_the_cap(self):
        over_cap = [5000 + index for index in range(tmux_mod.MAX_ORPHAN_SWEEP_PROCESSES + 1)]
        with (
            mock.patch("docich.tmux.processes_with_env", return_value=over_cap),
            mock.patch("docich.tmux.terminate_owned_processes") as mock_terminate,
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

    def test_explicitly_protected_pids_are_never_signalled(self):
        # ``protected`` carries the caller's proven-untouchable set (the tmux
        # server) into the sweep; a tagged PID in it must never be signalled.
        with mock.patch("docich.tmux.processes_with_env", return_value=[4321, 4242]):
            victims = self.tmux._escaped_process_ids(
                OWNER, ("game",), {}, protected=frozenset({4321})
            )

        self.assertEqual(victims, (4242,))

    @mock.patch("docich.tmux.ancestor_pids", return_value=[])
    @mock.patch("docich.tmux.process_pgid", return_value=None)
    @mock.patch("docich.tmux.processes_with_env")
    @mock.patch("docich.tmux.terminate_owned_processes")
    @mock.patch("docich.tmux.procs.run")
    def test_tagged_tmux_server_is_never_swept(
        self, mock_run, mock_terminate, mock_env, _mock_pgid, _mock_ancestors
    ):
        # A tmux client launched from a tagged pane starts a server that keeps
        # the tags, so the sweep's environment query reports the server PID.
        # Signalling it would take every pane on the shared server down with
        # it (display / audio / watchdog / ffmpeg), so the server must be
        # excluded even though it looks owned.
        server_pid = 4321
        mock_run.side_effect = [
            _ok("game-g1\n"),                               # window_target_exists
            _ok("g1-abcdef\n"), _ok("1\n"), _ok("game\n"),   # window ownership
            _ok("123\n"),                                    # pane leader
            _ok(f"{server_pid}\n"),                          # display-message server
            _ok("123\n"),                                    # server pane listing
            _ok(),                                           # kill-window
        ]
        mock_env.return_value = [server_pid, 4242]
        mock_terminate.side_effect = [_stopped(4242)]

        self.assertTrue(self.tmux.kill_window_owned("docich:game-g1", OWNER))

        # The escaped orphan is reclaimed; the tagged server is not in any
        # signal batch.
        self.assertEqual(
            [call.args[0] for call in mock_terminate.call_args_list],
            [(4242,)],
        )

    @mock.patch("docich.tmux.process_pgid", return_value=None)
    @mock.patch("docich.tmux.processes_with_env")
    @mock.patch("docich.tmux.terminate_owned_processes")
    @mock.patch("docich.tmux.procs.run")
    def test_shared_infrastructure_panes_on_the_server_are_never_swept(
        self, mock_run, mock_terminate, mock_env, _mock_pgid
    ):
        # Production layout (cli.py cmd_up): the runtime's agent window is torn
        # down while the untagged display/audio panes sit in the same session
        # of a server that inherited the runtime's tags. Those panes look
        # owned to processes_with_env but must never be signalled — stopping
        # them removes the session and takes the whole server (and every other
        # pane) down with it.
        server_pid, display_pane, audio_pane, orphan = 4321, 9001, 9002, 4242
        mock_run.side_effect = [
            _ok("game-g1\n"),                                # window_target_exists
            _ok("g1-abcdef\n"), _ok("1\n"), _ok("game\n"),    # window ownership
            _ok("123\n"),                                     # target pane leader
            _ok(f"{server_pid}\n"),                           # display-message server
            _ok(f"123\n{display_pane}\n{audio_pane}\n"),       # server pane listing
            _ok(),                                            # kill-window
        ]
        mock_env.return_value = [server_pid, display_pane, audio_pane, orphan]
        mock_terminate.side_effect = [_stopped(orphan)]

        self.assertTrue(self.tmux.kill_window_owned("docich:game-g1", OWNER))

        self.assertEqual(
            [call.args[0] for call in mock_terminate.call_args_list],
            [(orphan,)],
        )

    def test_protected_set_covers_other_panes_and_their_children(self):
        with (
            mock.patch.object(self.tmux, "_server_pid", return_value=4321),
            mock.patch.object(
                self.tmux, "_server_pane_leaders", return_value=[123, 9001, 9002]
            ),
            mock.patch("docich.tmux.ancestor_pids", return_value=[555, 1]),
            mock.patch("docich.tmux.descendant_pids", return_value=[9003]) as mock_desc,
        ):
            protected = self.tmux._protected_teardown_pids([123])

        self.assertIn(4321, protected)      # the server itself
        self.assertIn(9001, protected)      # another live pane (display)
        self.assertIn(9002, protected)      # another live pane (audio)
        self.assertIn(9003, protected)      # a child of another pane
        self.assertNotIn(123, protected)    # the target pane stays a victim
        mock_desc.assert_called_once_with([9001, 9002])

    @mock.patch("docich.tmux.process_pgid", return_value=None)
    @mock.patch("docich.tmux.terminate_process_tree")
    @mock.patch("docich.tmux.procs.run")
    def test_unreadable_server_pane_listing_fails_closed(
        self, mock_run, mock_terminate, _mock_pgid
    ):
        # Without the server-wide pane listing the sweep cannot prove which
        # panes are outside its target, so it must not stop anything.
        mock_run.side_effect = [
            _ok("game-g1\n"),
            _ok("g1-abcdef\n"), _ok("1\n"), _ok("game\n"),
            _ok("123\n"),
            _ok("4321\n"),
            _fail("failed to connect to server: Connection refused"),
        ]

        with self.assertRaises(tmux_mod.TmuxError):
            self.tmux.kill_window_owned("docich:game-g1", OWNER)

        mock_terminate.assert_not_called()

    def test_reclaim_is_logged_for_the_switch_timeline(self):
        with (
            mock.patch("docich.tmux.processes_with_env", return_value=[4242]),
            mock.patch("docich.tmux.terminate_owned_processes", return_value=_stopped(4242)),
            self.assertLogs("docich.tmux", level="WARNING") as logs,
        ):
            remaining = self.tmux._reap_escaped_processes(
                OWNER, ("game",), {}, operation="window停止"
            )

        self.assertEqual(remaining, ())
        self.assertTrue(any("4242" in line for line in logs.output))


class TestDynamicChildProtection(unittest.TestCase):
    """The protection snapshot is re-evaluated, not frozen (Issue #1105).

    A process spawned *after* the protection set was snapshotted — a child
    forked by a pane that is still running, or the leader of a pane created
    concurrently by the switch — inherits the runtime's ownership tags from
    the tagged tmux server.  To the environment query it is indistinguishable
    from a target orphan, so a frozen snapshot would reclaim a process that
    never belonged to the target.
    """

    def setUp(self):
        self.tmux = tmux_mod.Tmux()

    def _patch_descendants(self, root_children, target_children):
        """Mock ``descendant_pids`` per root set (both use the same table)."""

        def fake_descendant_pids(roots):
            roots = frozenset(roots)
            if roots == frozenset([123]):
                return list(target_children)
            return list(root_children)

        return mock.patch("docich.tmux.descendant_pids", fake_descendant_pids)

    def test_child_spawned_after_the_snapshot_is_protected(self):
        protected = tmux_mod.ProtectedTeardownPids(
            [4321, 9001], roots=[4321, 9001], targets=[123]
        )
        # The protected pane 9001 forks a child after the snapshot was taken.
        # (target exclusion is probed first, then the protected roots)
        with self._patch_descendants([7777], []):
            self.assertIn(7777, protected)

    def test_concurrently_created_pane_leader_is_protected(self):
        # tmux spawns new panes as children of the server, so a pane created
        # while the teardown runs is a fresh descendant of the server root.
        protected = tmux_mod.ProtectedTeardownPids(
            [4321, 9001], roots=[4321, 9001], targets=[123]
        )
        with self._patch_descendants([9500], []):
            self.assertIn(9500, protected)

    def test_target_subtree_stays_reclaimable_after_the_snapshot(self):
        # A spawn racing with the teardown inside the target pane must remain
        # a legitimate victim, otherwise the post-condition can no longer fail
        # closed on a real orphan of the target.
        protected = tmux_mod.ProtectedTeardownPids(
            [4321, 9001], roots=[4321, 9001], targets=[123]
        )
        with self._patch_descendants([7777], [8888]):
            self.assertNotIn(8888, protected)
            self.assertIn(7777, protected)

    def test_frozen_protection_never_broadens_by_itself(self):
        # A plain frozenset (no roots) keeps the pre-existing semantics: only
        # the snapshotted members are protected.
        protected = tmux_mod.ProtectedTeardownPids([4321, 9001])
        with self._patch_descendants([7777], []):
            self.assertNotIn(7777, protected)
            self.assertIn(9001, protected)

    def test_expanded_returns_snapshot_plus_fresh_descendants(self):
        protected = tmux_mod.ProtectedTeardownPids(
            [4321, 9001], roots=[4321, 9001], targets=[123]
        )
        with self._patch_descendants([7777], [8888]):
            self.assertEqual(protected.expanded(), frozenset({4321, 9001, 7777}))

    @mock.patch("docich.tmux.process_pgid", return_value=None)
    @mock.patch("docich.tmux.processes_with_env")
    @mock.patch("docich.tmux.terminate_owned_processes")
    @mock.patch("docich.tmux.procs.run")
    def test_sweep_never_signals_a_child_spawned_after_the_snapshot(
        self, mock_run, mock_terminate, mock_env, _mock_pgid
    ):
        server_pid, display_pane, late_child, orphan = 4321, 9001, 7777, 4242
        mock_run.side_effect = [
            _ok("game-g1\n"),                               # window_target_exists
            _ok("g1-abcdef\n"), _ok("1\n"), _ok("game\n"),  # window ownership
            _ok("123\n"),                                   # target pane leader
            _ok(f"{server_pid}\n"),                         # display-message server
            _ok(f"123\n{display_pane}\n"),                  # server pane listing
            _ok(),                                          # kill-window
        ]
        # The tagged server, the still-running display pane and its late child
        # all look owned; only the orphan actually escaped the target pane.
        mock_env.return_value = [server_pid, display_pane, late_child, orphan]
        mock_terminate.side_effect = [_stopped(orphan)]

        with mock.patch(
            "docich.tmux.descendant_pids", return_value=[late_child]
        ):
            self.assertTrue(self.tmux.kill_window_owned("docich:game-g1", OWNER))

        self.assertEqual(
            [call.args[0] for call in mock_terminate.call_args_list],
            [(orphan,)],
        )


class TestOrphanSweepOnSessionTeardown(unittest.TestCase):
    def setUp(self):
        self.tmux = tmux_mod.Tmux()
        pane_stop = mock.patch("docich.tmux.terminate_process_tree", return_value=_stopped(123))
        self.pane_stop = pane_stop.start()
        self.addCleanup(pane_stop.stop)
        cgroup = mock.patch("docich.tmux.process_cgroup", return_value=PANE_SCOPE.cgroup)
        cgroup.start()
        self.addCleanup(cgroup.stop)

    @staticmethod
    def _session_kill_calls() -> list[subprocess.CompletedProcess]:
        return [
            _ok(),
            _ok("g1-abcdef\n"), _ok("1\n"), _ok("adapter\n"),
            _ok("123\n"),
            _ok("4321\n"),
            _ok("123\n"),
            _ok(),
        ]

    @mock.patch("docich.tmux.process_pgid", return_value=None)
    @mock.patch("docich.tmux.processes_with_env")
    @mock.patch("docich.tmux.terminate_owned_processes")
    @mock.patch("docich.tmux.procs.run")
    def test_session_kill_reclaims_every_role_of_this_runtime(
        self, mock_run, mock_terminate, mock_env, _mock_pgid
    ):
        mock_run.side_effect = self._session_kill_calls()
        mock_env.return_value = [901]
        mock_terminate.side_effect = [_stopped(901)]
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
            [(901,)],
        )

    @mock.patch("docich.tmux.ancestor_pids", return_value=[])
    @mock.patch("docich.tmux.processes_in_pane_scopes", return_value=[777])
    @mock.patch("docich.tmux.is_running", return_value=False)
    @mock.patch("docich.tmux.process_pgid", return_value=123)
    @mock.patch("docich.tmux.terminate_owned_processes")
    @mock.patch("docich.tmux.procs.run")
    def test_legacy_session_cleanup_uses_pane_group_evidence(
        self, mock_run, mock_terminate, _mock_pgid, _mock_running,
        mock_scopes, _mock_ancestors,
    ):
        mock_run.side_effect = [_ok("123\n"), _ok("4321\n"), _ok("123\n"), _ok()]
        mock_terminate.side_effect = [_stopped(777)]

        self.tmux.stop_game_session_named("docich-game-g7")

        mock_scopes.assert_called_once_with({PANE_SCOPE})
        self.assertEqual(
            [call.args[0] for call in mock_terminate.call_args_list],
            [(777,)],
        )


if __name__ == "__main__":
    unittest.main()
