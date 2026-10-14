import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import tmux as tmux_mod  # noqa: E402


class TestPaneCleanupScope(unittest.TestCase):
    @mock.patch("docich.tmux.procs.run")
    def test_pane_pid_lookup_stays_scoped_to_target(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="123\n", stderr=""
        )

        pids = tmux_mod.Tmux()._pane_pids("docich:game-g1")

        self.assertEqual(pids, [123])
        self.assertEqual(
            mock_run.call_args.args[0],
            ["tmux", "list-panes", "-t", "docich:game-g1", "-F", "#{pane_pid}"],
        )
        self.assertNotIn("-a", mock_run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
