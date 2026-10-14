"""Bounded boundary diagnostics for a refused cancel / timed-out save (#1015).

The production wedge this pins is: the save boundary times out, the follow-up
cancel is refused, and canonical stays ``draining``. Before #1015 the refusal
returned a bare ``False`` with no reason, so the owner could not tell *which*
precondition failed. These tests fix two things:

1. every refusal path records a bounded, enum-only observation; and
2. a recorded observation never contains pane text, paths, keys or argv.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.adapters.nethack import (  # noqa: E402
    BOUNDARY_DIAG_FILENAME,
    CANCEL_REFUSAL_REASONS,
    PROMPT_CLASSES,
    NethackCoordinatorAdapter,
    ReadinessTimeoutError,
)

_DIAG_PATH = (
    Path(__file__).resolve().parents[1] / "ops" / "vm_actions" / "collect_diagnostics.py"
)
_spec = importlib.util.spec_from_file_location("collect_diagnostics_under_test", _DIAG_PATH)
collect_diagnostics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collect_diagnostics)

PANE_WITHOUT_PROMPT = "Dlvl:1 $:0 HP:16(16) Xp:1 Weak"
SAVE_PROMPT_PANE = "Really save? [yn] (n)"


class _FakeTmux:
    def __init__(self, pane: str, *, session: bool = True, capture_raises: bool = False):
        self.pane = pane
        self.session = session
        self.capture_raises = capture_raises
        self.calls: list[tuple[list[str], bool]] = []

    def session_target_exists(self, _target):
        return self.session

    def capture_pane(self, _target):
        if self.capture_raises:
            raise RuntimeError("tmux gone")
        return self.pane

    def window_target_exists(self, _target, strict=False):
        return True

    def send_keys(self, _target, keys, literal=False):
        self.calls.append((list(keys), literal))


def _adapter(runtime_dir: Path, tmux: _FakeTmux, *, generation: int = 301):
    adapter = object.__new__(NethackCoordinatorAdapter)
    adapter.spec = SimpleNamespace(
        adapter_session="adapter",
        runtime_dir=runtime_dir,
        generation=generation,
        runtime_id=f"g{generation}-test",
        game="nethack",
    )
    adapter.tmux = tmux
    adapter.player_name = "docich"
    adapter.save_dir = Path("/nonexistent/save")
    adapter._check_active = lambda deadline, cancel: None
    adapter._verify_session_ownership = lambda: None
    adapter._runtime_process_window_target = lambda: "adapter:nethack"
    return adapter


def _read_diag(runtime_dir: Path) -> dict:
    path = runtime_dir / BOUNDARY_DIAG_FILENAME
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class BoundaryDiagVocabularyTests(unittest.TestCase):
    """The read-only collector must not drift from the writer's enums."""

    def test_collector_reasons_match_the_adapter(self) -> None:
        self.assertEqual(
            set(collect_diagnostics._BOUNDARY_DIAG_REASONS), set(CANCEL_REFUSAL_REASONS)
        )

    def test_collector_prompt_classes_match_the_adapter(self) -> None:
        self.assertEqual(
            set(collect_diagnostics._BOUNDARY_DIAG_PROMPT_CLASSES), set(PROMPT_CLASSES)
        )

    def test_prompt_classification_returns_only_the_fixed_enum(self) -> None:
        classify = NethackCoordinatorAdapter._classify_prompt
        cases = {
            SAVE_PROMPT_PANE: "save_prompt_pending",
            "Really save? [yn]": "save_confirmation",
            "Shall I pick a character for you": "character_creation",
            PANE_WITHOUT_PROMPT: "unknown",
            "": "unknown",
        }
        for text, expected in cases.items():
            self.assertEqual(classify(text), expected, text)
        self.assertTrue(set(cases.values()) <= PROMPT_CLASSES)


class RefusedCancelIsObservedTests(unittest.TestCase):
    def test_refusal_without_a_pending_prompt_records_the_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            tmux = _FakeTmux(PANE_WITHOUT_PROMPT)
            adapter = _adapter(runtime_dir, tmux)
            refused = adapter.cancel_round_boundary("req", time.monotonic() + 5.0, None)
            self.assertFalse(refused)
            self.assertEqual(tmux.calls, [])  # no key is sent on refusal
            diag = _read_diag(runtime_dir)
            self.assertEqual(diag["operation"], "cancel")
            self.assertEqual(diag["reason"], "prompt_not_pending")
            self.assertEqual(diag["prompt_class"], "unknown")
            self.assertTrue(diag["process_target_present"])
            self.assertIs(diag["save_signature_changed"], None)
            self.assertEqual(diag["generation"], 301)

    def test_refusal_records_the_observed_prompt_class(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapter = _adapter(runtime_dir, _FakeTmux(SAVE_PROMPT_PANE))
            # A save *confirmation* (already answered) is not the unanswered
            # prompt, so the cancel refuses -- but the class is still useful.
            adapter.tmux.pane = "Really save? [yn] y"
            self.assertFalse(
                adapter.cancel_round_boundary("req", time.monotonic() + 5.0, None)
            )
            diag = _read_diag(runtime_dir)
            self.assertEqual(diag["reason"], "prompt_not_pending")
            self.assertEqual(diag["prompt_class"], "save_confirmation")

    def test_missing_session_is_recorded_without_touching_the_pane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            tmux = _FakeTmux(SAVE_PROMPT_PANE, session=False)
            adapter = _adapter(runtime_dir, tmux)
            self.assertFalse(
                adapter.cancel_round_boundary("req", time.monotonic() + 5.0, None)
            )
            diag = _read_diag(runtime_dir)
            self.assertEqual(diag["reason"], "session_missing")
            self.assertIsNone(diag["process_target_present"])
            self.assertEqual(tmux.calls, [])

    def test_capture_failure_is_recorded_as_capture_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapter = _adapter(runtime_dir, _FakeTmux("", capture_raises=True))
            self.assertFalse(
                adapter.cancel_round_boundary("req", time.monotonic() + 5.0, None)
            )
            self.assertEqual(_read_diag(runtime_dir)["reason"], "capture_failed")
            self.assertEqual(_read_diag(runtime_dir)["prompt_class"], "capture_failed")

    def test_recorded_observation_never_carries_pane_text(self) -> None:
        secretish = "Really save? [yn] (n) SECRET_PANE_MARKER"
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapter = _adapter(runtime_dir, _FakeTmux(secretish))
            adapter.cancel_round_boundary("req", time.monotonic() + 5.0, None)
            raw = (runtime_dir / BOUNDARY_DIAG_FILENAME).read_text(encoding="utf-8")
            self.assertNotIn("SECRET_PANE_MARKER", raw)
            self.assertNotIn("Really save", raw)
            self.assertNotIn(str(runtime_dir), raw)
            # Only fixed enum / boolean / int / timestamp values are allowed.
            payload = json.loads(raw)
            self.assertEqual(
                set(payload),
                {
                    "schema_version",
                    "operation",
                    "reason",
                    "process_target_present",
                    "process_alive",
                    "prompt_class",
                    "save_signature_changed",
                    "boundary_outcome",
                    "generation",
                    "runtime_id",
                    "recorded_at",
                },
            )
            self.assertIn(payload["reason"], CANCEL_REFUSAL_REASONS)
            self.assertIn(payload["prompt_class"], PROMPT_CLASSES)
            # No boundary result exists here, so the outcome must read as
            # ``unknown`` -- never as a success claim.
            self.assertEqual(payload["boundary_outcome"], "unknown")

    def test_diagnostic_failure_never_turns_a_refusal_into_success(self) -> None:
        # ``runtime_dir`` does not exist here: the write fails, the refusal
        # must still be reported as ``False``.
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "missing" / "nested"
            adapter = _adapter(runtime_dir, _FakeTmux(PANE_WITHOUT_PROMPT))
            self.assertFalse(
                adapter.cancel_round_boundary("req", time.monotonic() + 5.0, None)
            )

    def test_unwritable_or_unknown_enum_is_dropped_by_the_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapter = _adapter(runtime_dir, _FakeTmux(PANE_WITHOUT_PROMPT))
            adapter._record_boundary_diag(
                "cancel",
                reason="not-a-reviewed-reason",
                process_target_present=True,
                process_alive=True,
                prompt_class="unknown",
                save_signature_changed=False,
            )
            self.assertFalse((runtime_dir / BOUNDARY_DIAG_FILENAME).exists())


class SaveTimeoutObservationTests(unittest.TestCase):
    """The #1015 wedge: the save boundary times out and nothing is visible."""

    def test_save_timeout_records_wait_timeout_and_keeps_failing_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapter = _adapter(runtime_dir, _FakeTmux(PANE_WITHOUT_PROMPT))
            adapter._boundary_save_before = {}
            with self.assertRaises(ReadinessTimeoutError):
                adapter._boundary_wait_check(time.monotonic() - 1.0, None)
            diag = _read_diag(runtime_dir)
            self.assertEqual(diag["operation"], "wait")
            self.assertEqual(diag["reason"], "wait_timeout")

    def test_save_signature_changed_is_unknown_without_a_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapter = _adapter(runtime_dir, _FakeTmux(PANE_WITHOUT_PROMPT))
            # No baseline: a different driver sent ``S``. Unknown must not be
            # reported as ``False`` (that would read as "no save happened").
            self.assertIsNone(adapter._save_signature_changed())
            with self.assertRaises(ReadinessTimeoutError):
                adapter._boundary_wait_check(time.monotonic() - 1.0, None)
            self.assertIsNone(_read_diag(runtime_dir)["save_signature_changed"])


class SaveTimeoutThenRefusedCancelKeepsDrainingTests(unittest.TestCase):
    """Regression for the production sequence: timeout -> refused cancel.

    Canonical must never fall back to a false-success ``ready``.
    """

    def test_the_sequence_is_observable_and_still_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            runtime_dir = state_dir / "runtimes" / "g301-test"
            runtime_dir.mkdir(parents=True)
            tmux = _FakeTmux(PANE_WITHOUT_PROMPT)
            adapter = _adapter(runtime_dir, tmux)

            # 1. the save boundary times out (no fresh save, process stays up)
            with self.assertRaises(ReadinessTimeoutError):
                adapter._boundary_wait_check(time.monotonic() - 1.0, None)
            first = _read_diag(runtime_dir)
            self.assertEqual(first["reason"], "wait_timeout")

            # 2. the generic recovery then tries to cancel and is refused
            self.assertFalse(
                adapter.cancel_round_boundary("req", time.monotonic() + 5.0, None)
            )
            second = _read_diag(runtime_dir)
            self.assertEqual(second["operation"], "cancel")
            self.assertEqual(second["reason"], "prompt_not_pending")

            # 3. no key was ever sent, so nothing can claim success
            self.assertEqual(tmux.calls, [])

            # The collector reads exactly this evidence back out.
            (state_dir / "game_switch.json").write_text(
                json.dumps(
                    {
                        "active": {
                            "game": "nethack",
                            "generation": 301,
                            "runtime_id": "g301-test",
                        }
                    }
                ),
                encoding="utf-8",
            )
            collected = collect_diagnostics._collect_nethack_boundary(
                state_dir, time.time()
            )
            self.assertTrue(collected["present"])
            self.assertTrue(collected["readable"])
            self.assertTrue(collected["active_runtime"])
            self.assertFalse(collected["stale_runtime"])
            self.assertEqual(collected["operation"], "cancel")
            self.assertEqual(collected["reason"], "prompt_not_pending")
            self.assertEqual(collected["prompt_class"], "unknown")
            self.assertIn(collected["reason"], collect_diagnostics._BOUNDARY_DIAG_REASONS)

    def test_a_diag_from_an_older_generation_is_marked_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            runtime_id = "g300-old"
            target = state_dir / "runtimes" / runtime_id
            target.mkdir(parents=True)
            (state_dir / "game_switch.json").write_text(
                json.dumps(
                    {"active": {"game": "nethack", "generation": 301, "runtime_id": "g301-new"}}
                ),
                encoding="utf-8",
            )
            # The active runtime has no diag at all.
            collected = collect_diagnostics._collect_nethack_boundary(
                state_dir, time.time()
            )
            self.assertFalse(collected["present"])

    def test_unknown_enum_values_from_disk_are_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            runtime_id = "g301-abc"
            target = state_dir / "runtimes" / runtime_id
            target.mkdir(parents=True)
            (state_dir / "game_switch.json").write_text(
                json.dumps(
                    {"active": {"game": "nethack", "generation": 301, "runtime_id": runtime_id}}
                ),
                encoding="utf-8",
            )
            (target / BOUNDARY_DIAG_FILENAME).write_text(
                json.dumps(
                    {
                        "operation": "cancel",
                        "reason": "raw pane text with SECRET",
                        "prompt_class": "free text",
                        "process_target_present": "not-a-bool",
                        "generation": 301,
                        "recorded_at": "not-a-timestamp",
                    }
                ),
                encoding="utf-8",
            )
            collected = collect_diagnostics._collect_nethack_boundary(
                state_dir, time.time()
            )
            self.assertIsNone(collected["reason"])
            self.assertIsNone(collected["prompt_class"])
            self.assertIsNone(collected["process_target_present"])
            self.assertIsNone(collected["age_sec"])


if __name__ == "__main__":
    unittest.main()
