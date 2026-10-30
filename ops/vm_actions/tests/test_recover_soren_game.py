import importlib.util
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "recover_soren_game.py"
spec = importlib.util.spec_from_file_location("recover_soren_game", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(tmp_path):
    soren = tmp_path / "soren"
    docich = tmp_path / "docich"
    proc = tmp_path / "proc"
    (soren / "tmp/state").mkdir(parents=True)
    (soren / "tmp/markers").mkdir()
    (docich / "run-soren-live").mkdir(parents=True)
    (proc / "1234").mkdir(parents=True)
    (docich / "run-soren-live/game_switch.json").write_text(json.dumps({
        "active": {"game": "sorengame"}, "phase": "ready"}))
    (soren / "game_state.json").write_text(json.dumps({
        "state": "STOP", "makeSorenCount": 0}))
    os.utime(soren / "game_state.json", (1000, 1000))
    (soren / "tmp/state/main_strategy_runner_active.json").write_text(json.dumps({
        "pid": 1234, "started_at": 900}))
    (proc / "1234/cmdline").write_bytes(b"python3\0-u\0strategy_runner.py\0")
    (proc / "1234/cwd").symlink_to(soren)
    return soren, docich, proc


class RecoverSorenGameTests(unittest.TestCase):
    def test_only_stale_non_founding_stop_matches_exact_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            soren, docich, proc = fixture(Path(directory))
            with mock.patch.object(module, "_birth_time", return_value=899):
                self.assertEqual(module.eligible(soren, docich, proc, now=1100), (1234, 899))
                (soren / "tmp/markers/.soviet_created").touch()
                with self.assertRaisesRegex(ValueError, "founding"):
                    module.eligible(soren, docich, proc, now=1100)
                (soren / "tmp/markers/.soviet_created").unlink()
                os.utime(soren / "game_state.json", (1050, 1050))
                with self.assertRaisesRegex(ValueError, "stale"):
                    module.eligible(soren, docich, proc, now=1100)
                os.utime(soren / "game_state.json", (1000, 1000))
                (proc / "1234/cmdline").write_bytes(b"python3\0-u\0other.py\0")
                with self.assertRaisesRegex(ValueError, "not the match runner"):
                    module.eligible(soren, docich, proc, now=1100)

    def test_stale_founding_marker_does_not_refuse_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            soren, docich, proc = fixture(Path(directory))
            marker = soren / "tmp/markers/.soviet_created"
            observation = soren / "tmp/state/game_observation.json"

            def live_observation(game_id):
                # Only a schema-valid, fresh observation describing this same
                # board counts as live evidence (soviet_now#714).
                observation.write_text(json.dumps({
                    "schema": 1, "game_id": game_id, "stop_id": None,
                    "board": {"state": "STOP", "score": None,
                              "makeSorenCount": 0, "pieces": None},
                    "observed_epoch": 1100}))

            with mock.patch.object(module, "_birth_time", return_value=899):
                # Marker left by an earlier match while the bridge already
                # observes another one: it must not block the only recovery
                # (docich#2345).
                marker.write_text("189|11111111-1111-4111-8111-111111111111\n")
                live_observation("22222222-2222-4222-8222-222222222222")
                self.assertEqual(module.eligible(soren, docich, proc, now=1100), (1234, 899))

                # The same match still holds its founding board: refuse.
                live_observation("11111111-1111-4111-8111-111111111111")
                with self.assertRaisesRegex(ValueError, "founding"):
                    module.eligible(soren, docich, proc, now=1100)

                # A stale observation cannot authorise recovery on a live
                # id mismatch: the marker stays unproven, so refuse.
                live_observation("22222222-2222-4222-8222-222222222222")
                observation.write_text(json.dumps({
                    **json.loads(observation.read_text()),
                    "observed_epoch": 1000}))
                with self.assertRaisesRegex(ValueError, "founding"):
                    module.eligible(soren, docich, proc, now=1100)

                # A live observation of a *different* board cannot decide
                # either: contradictory evidence keeps refusing.
                live_observation("22222222-2222-4222-8222-222222222222")
                observation.write_text(json.dumps({
                    **json.loads(observation.read_text()),
                    "board": {"state": "MOVE", "score": 0,
                              "makeSorenCount": 0, "pieces": []}}))
                with self.assertRaisesRegex(ValueError, "founding"):
                    module.eligible(soren, docich, proc, now=1100)

                # Legacy marker older than the board it is read next to:
                # the mtime gap alone does not prove a new match (unknown),
                # so the marker still refuses (soviet_now#714).
                marker.write_text("189\n")
                observation.unlink()
                os.utime(marker, (900, 900))
                with self.assertRaisesRegex(ValueError, "founding"):
                    module.eligible(soren, docich, proc, now=1100)

                # Legacy marker written together with the board: refuse.
                os.utime(marker, (1000, 1000))
                with self.assertRaisesRegex(ValueError, "founding"):
                    module.eligible(soren, docich, proc, now=1100)

                # A missing marker is not a founding marker.
                marker.unlink()
                self.assertEqual(module.eligible(soren, docich, proc, now=1100), (1234, 899))

    def test_draining_requires_cancelled_ack_and_unpaused_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            soren, docich, proc = fixture(Path(directory))
            (docich / "run-soren-live/game_switch.json").write_text(json.dumps({
                "active": {"game": "sorengame"}, "phase": "draining", "request_id": "request-1"}))
            with mock.patch.object(module, "_birth_time", return_value=899):
                with self.assertRaisesRegex(ValueError, "cancelled"):
                    module.eligible(soren, docich, proc, now=1100)
                (soren / "tmp/state/game_lifecycle").mkdir()
                (soren / "tmp/state/game_lifecycle/ack.json").write_text(json.dumps({
                    "status": "cancelled", "request_id": "request-1"}))
                self.assertEqual(module.eligible(soren, docich, proc, now=1100), (1234, 899))
                (soren / "tmp/state/soren_loop.paused").touch()
                with self.assertRaisesRegex(ValueError, "paused"):
                    module.eligible(soren, docich, proc, now=1100)

    def test_main_signals_only_identical_pid_once(self):
        with mock.patch.object(module, "eligible", return_value=(1234, 900)), \
             mock.patch.object(module, "_birth_time", return_value=900), \
             mock.patch.object(module.os, "pidfd_open", return_value=os.open(os.devnull, os.O_RDONLY), create=True), \
             mock.patch.object(module.signal, "pidfd_send_signal", create=True) as send_signal, \
             mock.patch.object(module.time, "sleep"), \
             mock.patch.object(module.Path, "exists", return_value=False):
            self.assertEqual(module.main(), 0)
            send_signal.assert_called_once()
            self.assertEqual(send_signal.call_args.args[1], signal.SIGTERM)
        with mock.patch.object(module, "eligible", return_value=(1234, 900)), \
             mock.patch.object(module, "_birth_time", return_value=901), \
             mock.patch.object(module.os, "pidfd_open", return_value=os.open(os.devnull, os.O_RDONLY), create=True), \
             mock.patch.object(module.signal, "pidfd_send_signal", create=True) as send_signal:
            with self.assertRaisesRegex(ValueError, "changed"):
                module.main()
            send_signal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
