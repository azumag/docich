from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
MODULE_FILE = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"

NEW_TEMPLATE = (
    '<section id="next-corner"></section>'
    "<style>\n.next-corner-label { margin-top: 1px; font-size: 14px; line-height: 18px; }\n"
    ".next-corner-when { color: #fde68a; font-size: 17px; }\n</style>"
)
OLD_TEMPLATE = (
    '<section id="next-corner"></section>'
    "<style>\n.next-corner-label { margin-top: 1px; font-size: 11px; line-height: 14px; }\n"
    ".next-corner-when { color: #fde68a; font-size: 13px; }\n</style>"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "collect_diagnostics_broadcast_template", MODULE_FILE
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BroadcastTemplateDiagnosticsTest(unittest.TestCase):
    def _roots(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        soren = base / "soren"
        docich = base / "docich"
        for root in (soren, docich):
            (root / "overlays").mkdir(parents=True)
        return soren, docich

    def test_legacy_relative_override_reports_served_soren_template(self):
        module = _load_module()
        soren, docich = self._roots()
        (soren / "overlays" / "direct_broadcast_overlay.html").write_text(
            OLD_TEMPLATE, encoding="utf-8"
        )
        (docich / "overlays" / "direct_broadcast_overlay.html").write_text(
            NEW_TEMPLATE, encoding="utf-8"
        )
        (soren / ".env").write_text(
            "SOREN_STREAM_BACKEND=ffmpeg\n"
            "SOREN_DIRECT_STAGE_LAYOUT=dashboard\n"
            "SOREN_DIRECT_BROADCAST_OVERLAY_HTML_FILE=overlays/direct_broadcast_overlay.html\n",
            encoding="utf-8",
        )
        probe = module._collect_broadcast_template(soren, docich_root=docich, now=0)
        self.assertEqual(probe["resolved_source"], "override-soren-relative")
        self.assertTrue(probe["override_present"])
        self.assertEqual(probe["soren_template"]["next_corner_when_font_px"], 13.0)
        self.assertEqual(probe["soren_template"]["next_corner_label_font_px"], 11.0)
        self.assertEqual(probe["docich_template"]["next_corner_when_font_px"], 17.0)
        self.assertEqual(probe["docich_template"]["next_corner_label_font_px"], 14.0)
        # The override value itself must never reach the diagnostics payload.
        self.assertNotIn(
            "SOREN_DIRECT_BROADCAST_OVERLAY_HTML_FILE",
            json.dumps(probe, sort_keys=True),
        )

    def test_absent_override_prefers_the_docich_template(self):
        module = _load_module()
        soren, docich = self._roots()
        (soren / "overlays" / "direct_broadcast_overlay.html").write_text(
            OLD_TEMPLATE, encoding="utf-8"
        )
        (docich / "overlays" / "direct_broadcast_overlay.html").write_text(
            NEW_TEMPLATE, encoding="utf-8"
        )
        (soren / ".env").write_text("SOREN_STREAM_BACKEND=ffmpeg\n", encoding="utf-8")
        probe = module._collect_broadcast_template(soren, docich_root=docich, now=0)
        self.assertEqual(probe["resolved_source"], "docich")
        self.assertFalse(probe["override_present"])

    def test_absolute_docich_override_is_classified(self):
        module = _load_module()
        soren, docich = self._roots()
        (docich / "overlays" / "direct_broadcast_overlay.html").write_text(
            NEW_TEMPLATE, encoding="utf-8"
        )
        (soren / ".env").write_text(
            "SOREN_DIRECT_BROADCAST_OVERLAY_HTML_FILE="
            + str(docich / "overlays" / "direct_broadcast_overlay.html")
            + "\n",
            encoding="utf-8",
        )
        probe = module._collect_broadcast_template(soren, docich_root=docich, now=0)
        self.assertEqual(probe["resolved_source"], "override-docich-absolute")

    def test_missing_docich_template_falls_back_to_soren(self):
        module = _load_module()
        soren, docich = self._roots()
        (soren / "overlays" / "direct_broadcast_overlay.html").write_text(
            OLD_TEMPLATE, encoding="utf-8"
        )
        probe = module._collect_broadcast_template(soren, docich_root=docich, now=0)
        self.assertEqual(probe["resolved_source"], "soren-fallback")
        self.assertFalse(probe["docich_template"]["present"])
        self.assertTrue(probe["soren_template"]["present"])


if __name__ == "__main__":
    unittest.main()
