import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import process_tree  # noqa: E402
from docich.process_tree import process_table, terminate_process_tree  # noqa: E402


class TestProcessTree(unittest.TestCase):
    def test_terminates_root_and_descendants(self):
        if not process_table():
            self.skipTest("process table is unavailable in this sandbox")
        child_code = "import time; time.sleep(30)"
        parent_code = (
            "import subprocess,sys,time; "
            "subprocess.Popen([sys.executable, '-c', sys.argv[1]]); "
            "time.sleep(30)"
        )
        parent = subprocess.Popen(
            [sys.executable, "-c", parent_code, child_code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            # Give the child enough time to appear in ps/proc on both Linux and
            # macOS before taking the ownership snapshot.
            time.sleep(0.1)
            result = terminate_process_tree(
                [parent.pid], term_timeout_s=0.5, kill_timeout_s=0.5
            )
            parent.wait(timeout=2)
            self.assertEqual(result.remaining, ())
            self.assertFalse(_pid_is_running(parent.pid))
        finally:
            if _pid_is_running(parent.pid):
                os.kill(parent.pid, 9)
                parent.wait(timeout=2)


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class TestOwnershipIntrospection(unittest.TestCase):
    """Issue #1105: prove ownership of processes outside the pane tree."""

    def test_processes_with_env_requires_every_exact_pair(self):
        envs = {
            10: {
                "DOCICH_TMUX_RUNTIME_ID": "g1-abcdef",
                "DOCICH_TMUX_GENERATION": "1",
                "DOCICH_TMUX_ROLE": "game",
            },
            11: {
                "DOCICH_TMUX_RUNTIME_ID": "g1-abcdef",
                "DOCICH_TMUX_GENERATION": "2",
                "DOCICH_TMUX_ROLE": "game",
            },
            12: {
                "DOCICH_TMUX_RUNTIME_ID": "g1-fedcba",
                "DOCICH_TMUX_GENERATION": "1",
                "DOCICH_TMUX_ROLE": "game",
            },
            13: {},
        }
        tags = {"DOCICH_TMUX_RUNTIME_ID": "g1-abcdef", "DOCICH_TMUX_GENERATION": "1"}

        self.assertEqual(
            process_tree.processes_with_env(tags, pids=[10, 11, 12, 13], environ_reader=envs.get),
            [10],
        )
        # A role filter distinguishes the runtime's own game from its agent.
        self.assertEqual(
            process_tree.processes_with_env(
                {**tags, "DOCICH_TMUX_ROLE": "agent"},
                pids=[10, 11, 12, 13],
                environ_reader=envs.get,
            ),
            [],
        )
        self.assertEqual(process_tree.processes_with_env({}, pids=[10]), [])

    def test_processes_in_pane_scopes_requires_group_and_scope(self):
        pids = [20, 21, 22]
        pgid_lookup = {20: 123, 21: 123, 22: 999}
        cgroups = {
            20: "0::/user.slice/user-1000.slice/tmux-spawn-abc.scope",
            21: "0::/user.slice/user-1000.slice/app.slice",
            22: "0::/user.slice/user-1000.slice/tmux-spawn-abc.scope",
        }

        self.assertEqual(
            process_tree.processes_in_pane_scopes(
                {123}, pids=pids, pgid_lookup=pgid_lookup, cgroup_reader=cgroups.get
            ),
            [20],
        )
        self.assertEqual(
            process_tree.processes_in_pane_scopes(
                set(), pids=pids, pgid_lookup=pgid_lookup, cgroup_reader=cgroups.get
            ),
            [],
        )

    def test_ancestor_pids_walks_to_the_root(self):
        table = {
            30: process_tree.ProcessInfo(pid=30, ppid=20, state="S"),
            20: process_tree.ProcessInfo(pid=20, ppid=1, state="S"),
            1: process_tree.ProcessInfo(pid=1, ppid=0, state="S"),
        }
        with mock.patch("docich.process_tree.process_table", return_value=table):
            self.assertEqual(process_tree.ancestor_pids(30), [30, 20, 1])

    def test_unreadable_process_identities_degrade_to_none(self):
        missing = 2**22 - 1
        self.assertEqual(process_tree.process_environ(missing), {})
        self.assertIsNone(process_tree.process_pgid(missing))

    def test_pgid_and_start_ticks_read_the_real_proc_identity(self):
        if not Path("/proc").is_dir():
            self.skipTest("/proc is unavailable outside Linux")
        pid = os.getpid()
        self.assertEqual(process_tree.process_pgid(pid), os.getpgrp())
        self.assertIsInstance(process_tree.process_start_ticks(pid), int)


if __name__ == "__main__":
    unittest.main()
