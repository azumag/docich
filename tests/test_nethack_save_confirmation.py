import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.adapters import nethack as nethack_adapter  # noqa: E402


class _ConfirmingTmux:
    def __init__(self) -> None:
        self.process_alive = True
        self.saved = False
        self.pane_text = "Dlvl:1 HP:16(16)"
        self.calls: list[tuple] = []

    def session_target_exists(self, session):
        self.calls.append(("session_target_exists", session))
        return True

    def window_target_exists(self, target, *, strict=False):
        self.calls.append(("window_target_exists", target, strict))
        return self.process_alive

    def capture_pane(self, target):
        self.calls.append(("capture_pane", target))
        return self.pane_text

    def send_keys(self, target, keys, literal=False):
        self.calls.append(("send_keys", target, list(keys), literal))
        if keys == ["S"]:
            # Real NetHack asks for confirmation before writing the save.
            self.pane_text = "Really save? [yn] (n)"
        elif keys == ["y"]:
            self.saved = True
            self.process_alive = False


class TestNethackSaveConfirmation(unittest.TestCase):
    def test_real_save_prompt_is_confirmed_once_before_durable_success(self):
        adapter = object.__new__(nethack_adapter.NethackCoordinatorAdapter)
        adapter.spec = SimpleNamespace(adapter_session="adapter")
        tmux = _ConfirmingTmux()
        adapter.tmux = tmux

        adapter._check_active = mock.Mock()
        adapter._verify_session_ownership = mock.Mock()
        adapter._runtime_process_window_target = mock.Mock(
            return_value="adapter:nethack"
        )
        adapter._at_character_creation = mock.Mock(return_value=False)
        adapter._save_signatures = mock.Mock(return_value={})
        save_file = Path("/tmp/1000docich")
        adapter._new_or_changed_save = mock.Mock(
            side_effect=lambda before: save_file if tmux.saved else None
        )
        adapter._write_boundary_result = mock.Mock()
        adapter._boundary_wait_check = mock.Mock()

        adapter.request_round_boundary("req-1", time.monotonic() + 1.0, None)

        sends = [call for call in tmux.calls if call[0] == "send_keys"]
        self.assertEqual(
            sends,
            [
                ("send_keys", "adapter:nethack", ["Escape"], False),
                ("send_keys", "adapter:nethack", ["S"], True),
                ("send_keys", "adapter:nethack", ["y"], True),
            ],
        )
        adapter._write_boundary_result.assert_called_once_with(
            "req-1", outcome="suspended", save_file=save_file
        )

    def test_confirmation_match_is_specific(self):
        self.assertTrue(
            nethack_adapter._is_save_confirmation_screen("Really save? [yn] (n)")
        )
        self.assertFalse(
            nethack_adapter._is_save_confirmation_screen("Really quit? [yn] (n)")
        )

    def test_cancel_boundary_fails_closed_without_sending_input(self):
        adapter = object.__new__(nethack_adapter.NethackCoordinatorAdapter)
        tmux = _ConfirmingTmux()
        adapter.tmux = tmux

        self.assertFalse(
            adapter.cancel_round_boundary("req-1", time.monotonic() + 1.0, None)
        )
        self.assertEqual(tmux.calls, [])


if __name__ == "__main__":
    unittest.main()
