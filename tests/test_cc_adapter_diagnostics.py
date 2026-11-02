"""Regression tests for the CC translation adapter diagnostics section (#2894)."""

import importlib.util
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_SPEC = importlib.util.spec_from_file_location(
    "collect_diagnostics_cc_adapter", ROOT / "ops/vm_actions/collect_diagnostics.py"
)
collector = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(collector)


def _closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class CcAdapterDiagnosticsTest(unittest.TestCase):
    def test_section_reports_unreachable_with_port_only(self):
        with mock.patch.dict("os.environ", {"DOCICH_CC_ADAPTER_PORT": str(_closed_port())}):
            section = collector._collect_cc_translation_adapter()
        self.assertEqual(set(section), {"port", "state", "upstream"})
        self.assertEqual(section["state"], "unreachable")
        self.assertIsNone(section["upstream"])

    def test_section_falls_back_to_default_port_on_bad_env(self):
        with mock.patch.dict("os.environ", {"DOCICH_CC_ADAPTER_PORT": "99999"}):
            section = collector._collect_cc_translation_adapter()
        self.assertEqual(section["port"], 4100)

    def test_section_never_contains_prompt_or_key_fields(self):
        with mock.patch.dict("os.environ", {"DOCICH_CC_ADAPTER_PORT": str(_closed_port())}):
            section = collector._collect_cc_translation_adapter()
        text = repr(section).lower()
        for forbidden in ("prompt", "messages", "api_key", "authorization", "gateway"):
            self.assertNotIn(forbidden, text)

    def test_payload_wires_section_under_its_key(self):
        source = (ROOT / "ops/vm_actions/collect_diagnostics.py").read_text(encoding="utf-8")
        self.assertIn('"cc_translation_adapter": _collect_cc_translation_adapter(),', source)


if __name__ == "__main__":
    unittest.main()
