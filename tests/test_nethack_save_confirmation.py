import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.adapters import nethack as nethack_adapter  # noqa: E402
from docich.game_switch import DeadlineExceededError  # noqa: E402


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


class _PromptTmux(_ConfirmingTmux):
    """Game window sitting on a pane, with NetHack's reaction to a lone ``n``."""

    def __init__(self, *, pane, honors_n=True, exits_on_n=False):
        super().__init__()
        self.pane_text = pane
        self.honors_n = honors_n
        self.exits_on_n = exits_on_n

    def send_keys(self, target, keys, literal=False):
        self.calls.append(("send_keys", target, list(keys), literal))
        if keys == ["n"]:
            if self.exits_on_n:
                self.process_alive = False
            elif self.honors_n:
                self.pane_text = "Dlvl:1 HP:16(16)"
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

    # -- cancel: only an *unanswered* prompt may be withdrawn -------------------

    def _cancel_adapter(self, tmux, *, target="adapter:nethack", order=None):
        adapter = object.__new__(nethack_adapter.NethackCoordinatorAdapter)
        adapter.spec = SimpleNamespace(adapter_session="adapter")
        adapter.tmux = tmux

        def check_active(deadline, cancel):
            if cancel is not None and cancel.is_set():
                raise DeadlineExceededError("cancelled")
            if time.monotonic() >= deadline:
                raise DeadlineExceededError("deadline")

        adapter._check_active = check_active
        adapter._verify_session_ownership = mock.Mock(
            side_effect=lambda: order.append("ownership") if order is not None else None
        )
        adapter._runtime_process_window_target = mock.Mock(return_value=target)
        return adapter

    @staticmethod
    def _sent_keys(tmux):
        return [call for call in tmux.calls if call[0] == "send_keys"]

    def test_cancel_answers_only_n_at_the_unanswered_prompt(self):
        tmux = _PromptTmux(pane="Really save? [yn] (n)\n------------\n|......@...|")
        order = []
        adapter = self._cancel_adapter(tmux, order=order)
        real_send = tmux.send_keys
        tmux.send_keys = lambda *a, **k: (order.append("send"), real_send(*a, **k))[1]

        result = adapter.cancel_round_boundary("req-1", time.monotonic() + 1.0, None)

        self.assertTrue(result)
        self.assertEqual(self._sent_keys(tmux), [("send_keys", "adapter:nethack", ["n"], True)])
        self.assertEqual(order, ["ownership", "send"])  # ownership verified before any key
        self.assertTrue(tmux.process_alive)
        self.assertFalse(tmux.saved)

    def test_cancel_refuses_without_the_prompt_and_sends_nothing(self):
        for pane in ("Dlvl:1 HP:16(16)", "Really quit? [yn] (n)", ""):
            with self.subTest(pane=pane):
                tmux = _PromptTmux(pane=pane)
                adapter = self._cancel_adapter(tmux)
                self.assertFalse(adapter.cancel_round_boundary("req-1", time.monotonic() + 1.0, None))
                self.assertEqual(self._sent_keys(tmux), [])

    def test_cancel_refuses_once_the_answer_was_typed(self):
        # ``y`` already given: the game is writing its save; there is no way back.
        for pane in ("Really save? [yn] (n) y", "Really save? [yn] (n) n"):
            with self.subTest(pane=pane):
                tmux = _PromptTmux(pane=pane)
                adapter = self._cancel_adapter(tmux)
                self.assertFalse(adapter.cancel_round_boundary("req-1", time.monotonic() + 1.0, None))
                self.assertEqual(self._sent_keys(tmux), [])

    def test_cancel_refuses_when_the_process_window_or_session_is_gone(self):
        tmux = _PromptTmux(pane="Really save? [yn] (n)")
        adapter = self._cancel_adapter(tmux)
        adapter._runtime_process_window_target = mock.Mock(return_value=None)
        self.assertFalse(adapter.cancel_round_boundary("req-1", time.monotonic() + 1.0, None))
        self.assertEqual(self._sent_keys(tmux), [])

        tmux = _PromptTmux(pane="Really save? [yn] (n)")
        tmux.session_target_exists = lambda session: False
        adapter = self._cancel_adapter(tmux)
        self.assertFalse(adapter.cancel_round_boundary("req-1", time.monotonic() + 1.0, None))
        self.assertEqual(self._sent_keys(tmux), [])
        adapter._verify_session_ownership.assert_not_called()

    def test_cancel_refuses_when_the_screen_cannot_be_read(self):
        tmux = _PromptTmux(pane="Really save? [yn] (n)")

        def broken(target):
            raise RuntimeError("tmux gone")

        tmux.capture_pane = broken
        adapter = self._cancel_adapter(tmux)
        self.assertFalse(adapter.cancel_round_boundary("req-1", time.monotonic() + 1.0, None))
        self.assertEqual(self._sent_keys(tmux), [])

    def test_cancel_never_sends_a_key_to_a_foreign_or_cancelled_request(self):
        tmux = _PromptTmux(pane="Really save? [yn] (n)")
        adapter = self._cancel_adapter(tmux)
        adapter._verify_session_ownership = mock.Mock(side_effect=RuntimeError("ownership mismatch"))
        with self.assertRaises(RuntimeError):
            adapter.cancel_round_boundary("req-1", time.monotonic() + 1.0, None)
        self.assertEqual(self._sent_keys(tmux), [])

        tmux = _PromptTmux(pane="Really save? [yn] (n)")
        adapter = self._cancel_adapter(tmux)
        cancelled = mock.Mock()
        cancelled.is_set.return_value = True
        with self.assertRaises(DeadlineExceededError):
            adapter.cancel_round_boundary("req-1", time.monotonic() + 1.0, cancelled)
        self.assertEqual(self._sent_keys(tmux), [])

    def test_cancel_is_not_acknowledged_while_the_prompt_stays_up(self):
        tmux = _PromptTmux(pane="Really save? [yn] (n)", honors_n=False)
        adapter = self._cancel_adapter(tmux)
        with self.assertRaises(DeadlineExceededError):
            adapter.cancel_round_boundary("req-1", time.monotonic() + 0.3, None)
        # one ``n`` only: never a retry storm into a game we do not understand
        self.assertEqual(self._sent_keys(tmux), [("send_keys", "adapter:nethack", ["n"], True)])

    def test_cancel_is_not_acknowledged_if_the_process_exits(self):
        tmux = _PromptTmux(pane="Really save? [yn] (n)", exits_on_n=True)
        adapter = self._cancel_adapter(tmux)
        self.assertFalse(adapter.cancel_round_boundary("req-1", time.monotonic() + 1.0, None))
        self.assertEqual(len(self._sent_keys(tmux)), 1)

    def test_pending_prompt_match_is_specific(self):
        self.assertTrue(nethack_adapter._is_save_prompt_pending("Really save? [yn] (n)"))
        self.assertTrue(nethack_adapter._is_save_prompt_pending("  Really save? [yn] (n)  \n|@|"))
        self.assertFalse(nethack_adapter._is_save_prompt_pending("Really save? [yn] (n) y"))
        self.assertFalse(nethack_adapter._is_save_prompt_pending("Really quit? [yn] (n)"))
        self.assertFalse(nethack_adapter._is_save_prompt_pending("you say: Really save? [yn] (n)"))


if __name__ == "__main__":
    unittest.main()
