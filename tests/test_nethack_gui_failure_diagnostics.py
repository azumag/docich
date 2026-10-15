import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import nethack_gui_failure_diagnostics as diagnostics


class ReadinessFailure(RuntimeError):
    pass


class NethackGuiFailureDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / "nethack_tiles.json"
        self.presentation = self.root / "presentation.json"
        self.error = ReadinessFailure("original deadline")
        self.adapter = SimpleNamespace(
            readiness=mock.Mock(side_effect=self.error),
            _presentation_path=lambda: self.presentation,
        )
        self.probe = mock.Mock(return_value={"available": True, "visible_count": 2})
        self.deadline = object()
        self.cancel = object()

    def write_states(self, **changes):
        manifest = {"status": "starting", "mode": "tiles", "reason": "browser_window_missing",
                    "browser_pid": 123, "frame_port": 4321}
        manifest.update(changes)
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        self.presentation.write_text(json.dumps({"status": "starting", "display": ":77", "window": None}), encoding="utf-8")

    def call_failure(self):
        with self.assertRaises(ReadinessFailure) as caught:
            diagnostics.readiness_with_diagnostics(
                self.adapter, self.deadline, self.cancel, self.root, ":78", self.probe,
            )
        self.assertIs(caught.exception, self.error)
        self.adapter.readiness.assert_called_once_with(self.deadline, self.cancel)
        return self.error.__notes__[-1]

    def test_success_does_not_probe_or_change_result(self):
        result = object()
        self.adapter.readiness.side_effect = None
        self.adapter.readiness.return_value = result
        self.assertIs(diagnostics.readiness_with_diagnostics(
            self.adapter, self.deadline, self.cancel, self.root, ":78", self.probe,
        ), result)
        self.probe.assert_not_called()
        self.adapter.readiness.assert_called_once_with(self.deadline, self.cancel)

    def test_original_failure_keeps_bounded_state_before_cleanup(self):
        self.write_states()
        note = self.call_failure()
        self.assertIn('"reason":"browser_window_missing"', note)
        self.assertIn('"browser_present":true', note)
        self.assertIn('"visible_count":2', note)
        self.assertLessEqual(len(note), diagnostics.MAX_NOTE_CHARS)
        self.assertNotIn(str(self.root), note)
        self.assertNotIn('123', note)
        self.assertNotIn('4321', note)

    def test_missing_states_do_not_mask_original_exception(self):
        note = self.call_failure()
        self.assertIn('"read":"unavailable"', note)

    def test_oversize_states_are_not_reported(self):
        self.manifest.write_bytes(b' ' * (diagnostics.MAX_SOURCE_BYTES + 1))
        note = self.call_failure()
        self.assertIn('"read":"oversize"', note)

    def test_nonobject_and_malformed_states_keep_original(self):
        for content in ('[]', '{not json'):
            with self.subTest(content=content):
                self.adapter.readiness.reset_mock()
                self.manifest.write_text(content, encoding="utf-8")
                note = self.call_failure()
                self.assertNotIn(content, note)

    def test_unknown_strings_extra_fields_and_x11_titles_are_excluded(self):
        private = "https://invalid.example/private-value"
        self.write_states(status=private, reason=private, secret=private)
        self.probe.return_value = {"available": True, "visible_count": 999999,
                                   "windows": [{"title": private}], "environment": private}
        note = self.call_failure()
        self.assertNotIn(private, note)
        self.assertNotIn('windows', note)
        self.assertNotIn('environment', note)
        self.assertIn('"visible_count":16', note)
        self.assertIn('"status":"unknown"', note)

    def test_bad_display_does_not_reach_window_probe(self):
        self.write_states()
        self.presentation.write_text(json.dumps({"display": "https://invalid.example"}), encoding="utf-8")
        note = self.call_failure()
        self.probe.assert_called_once_with(":78")
        self.assertNotIn('invalid.example', note)

    def test_boolean_window_count_is_not_a_number(self):
        self.write_states()
        self.probe.return_value = {"available": True, "visible_count": True}
        self.assertIn('"visible_count":null', self.call_failure())

    def test_probe_failure_cannot_replace_original_exception(self):
        self.write_states()
        self.probe.side_effect = RuntimeError("private detail")
        self.assertEqual(self.call_failure(), "NetHack GUI readiness: diagnostic unavailable")

    def test_state_read_failure_cannot_replace_original_exception(self):
        with mock.patch.object(diagnostics.os, "open", side_effect=PermissionError("private detail")):
            note = self.call_failure()
        self.assertNotIn('private detail', note)

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "nofollow is unavailable")
    def test_symlink_state_is_not_followed(self):
        target = self.root / "other.json"
        target.write_text('{"status":"tiles_active"}', encoding="utf-8")
        self.manifest.symlink_to(target)
        self.assertIn('"read":"unavailable"', self.call_failure())
        self.assertEqual(target.read_text(encoding="utf-8"), '{"status":"tiles_active"}')

    def test_no_nofollow_support_does_not_open_state(self):
        with mock.patch.object(diagnostics.os, "O_NOFOLLOW", 0, create=True), mock.patch.object(diagnostics.os, "open") as opening:
            self.call_failure()
        opening.assert_not_called()

    def test_exact_source_limit_is_accepted(self):
        self.write_states()
        raw = self.manifest.read_bytes()
        self.manifest.write_bytes(raw + b" " * (diagnostics.MAX_SOURCE_BYTES - len(raw)))
        self.assertIn('"read":"ok"', self.call_failure())

    def test_directory_state_is_unavailable(self):
        self.manifest.mkdir()
        self.assertIn('"read":"unavailable"', self.call_failure())

    @unittest.skipUnless(hasattr(os, "mkfifo") and hasattr(os, "O_NONBLOCK"), "nonblocking FIFO is unavailable")
    def test_fifo_state_is_rejected_without_reading(self):
        os.mkfifo(self.manifest)
        self.assertIn('"read":"unavailable"', self.call_failure())

    def test_descriptor_is_closed_if_fstat_fails(self):
        with mock.patch.object(diagnostics.os, "open", return_value=12345), \
             mock.patch.object(diagnostics.os, "fstat", side_effect=OSError("private detail")), \
             mock.patch.object(diagnostics.os, "close") as close:
            self.call_failure()
        self.assertEqual(close.call_args_list, [mock.call(12345), mock.call(12345)])

    def test_add_note_failure_cannot_replace_original_exception(self):
        class NoteFailure(ReadinessFailure):
            def add_note(self, value):
                raise RuntimeError("note failed")
        self.error = NoteFailure("original")
        self.adapter.readiness.side_effect = self.error
        with self.assertRaises(NoteFailure) as caught:
            diagnostics.readiness_with_diagnostics(self.adapter, self.deadline, self.cancel, self.root, ":78", self.probe)
        self.assertIs(caught.exception, self.error)

    def test_baseexception_is_preserved_without_diagnostics(self):
        error = KeyboardInterrupt()
        self.adapter.readiness.side_effect = error
        with self.assertRaises(KeyboardInterrupt) as caught:
            diagnostics.readiness_with_diagnostics(self.adapter, self.deadline, self.cancel, self.root, ":78", self.probe)
        self.assertIs(caught.exception, error)
        self.probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
