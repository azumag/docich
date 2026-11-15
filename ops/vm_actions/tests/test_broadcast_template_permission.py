"""Regression coverage for inaccessible production broadcast template paths."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

COLLECTOR = Path(__file__).resolve().parents[1] / "collect_diagnostics.py"
spec = importlib.util.spec_from_file_location("collect_diagnostics_permission_guard", COLLECTOR)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class BroadcastTemplatePermissionTests(unittest.TestCase):
    def test_probe_metadata_permission_error_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "template.html"
            template.write_text("test", encoding="utf-8")
            with mock.patch.object(Path, "is_file", side_effect=PermissionError(13, "denied")):
                result = collector._broadcast_template_probe(template, 100)
            self.assertEqual(result, {"present": False})

    def test_source_selection_permission_error_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            soren = root / "soren"
            soren.mkdir()
            original = Path.is_file
            def guarded_is_file(path):
                if path == root / collector.BROADCAST_TEMPLATE_REL:
                    raise PermissionError(13, "denied")
                return original(path)
            with mock.patch.object(Path, "is_file", guarded_is_file):
                result = collector._collect_broadcast_template(soren, root, now=100)
            self.assertEqual(result["resolved_source"], "soren-fallback")
            self.assertEqual(result["docich_template"], {"present": False})


if __name__ == "__main__":
    unittest.main()
