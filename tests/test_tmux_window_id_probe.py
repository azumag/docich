import subprocess
import unittest
from unittest import mock

from docich import tmux as tmux_mod


def _ok(stdout: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


class TmuxWindowIdProbeTest(unittest.TestCase):
    @mock.patch("docich.tmux.procs.run")
    def test_window_exists_preserves_stable_window_id_probe(self, mock_run):
        mock_run.return_value = _ok("@7\n")
        tmux = tmux_mod.Tmux()

        self.assertTrue(tmux.window_target_exists("@7"))
        self.assertEqual(
            mock_run.call_args.args[0],
            ["tmux", "display-message", "-p", "-t", "@7", "#{window_id}"],
        )


if __name__ == "__main__":
    unittest.main()
