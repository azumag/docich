from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docich.nethack_spectator import (
    blank_frame,
    classify_char,
    main,
    parse_tty,
    render_html,
)


class TestNethackSpectator(unittest.TestCase):
    def test_classify_core_glyphs(self) -> None:
        self.assertEqual(classify_char("@"), "player")
        self.assertEqual(classify_char("."), "floor")
        self.assertEqual(classify_char("|"), "wall")
        self.assertEqual(classify_char("+"), "door")
        self.assertEqual(classify_char(">"), "stairs")
        self.assertEqual(classify_char("d"), "creature")
        self.assertEqual(classify_char("!"), "item")
        self.assertEqual(classify_char("^"), "trap")

    def test_parse_classic_tty_layout(self) -> None:
        text = "hello\n.@..\n.|>.\nHP:10\nDlvl:2\n"
        frame = parse_tty(text, cols=5, rows=5)
        self.assertEqual(frame.message, "hello")
        self.assertEqual(frame.status, ("HP:10", "Dlvl:"))
        self.assertEqual(frame.rows, 2)
        players = [cell for cell in frame.cells if cell.kind == "player"]
        self.assertEqual([(cell.x, cell.y) for cell in players], [(1, 0)])

    def test_render_escapes_viewer_text(self) -> None:
        frame = parse_tty("<script>\n.@\n..\nHP<1\nD>1\n", cols=8, rows=5)
        rendered = render_html(frame)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn('class="cell player"', rendered)

    def test_blank_frame_and_auto_refresh_are_presentation_only(self) -> None:
        frame = blank_frame("standby <safe>", cols=8, rows=5)
        self.assertEqual(frame.rows, 2)
        self.assertTrue(all(cell.kind == "void" for cell in frame.cells))
        rendered = render_html(frame, auto_refresh_ms=750)
        self.assertIn("standby &lt;safe&gt;", rendered)
        self.assertIn("window.location.reload()", rendered)
        self.assertIn("750", rendered)
        with self.assertRaises(ValueError):
            render_html(frame, auto_refresh_ms=99)

    def test_cli_writes_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "frame.txt"
            output = root / "out" / "nethack.html"
            source.write_text("msg\n.@\n..\nHP:9\nDlvl:1\n", encoding="utf-8")
            rc = main(["--input", str(source), "--output", str(output), "--cols", "8", "--rows", "5"])
            self.assertEqual(rc, 0)
            self.assertTrue(output.is_file())
            self.assertIn("NetHack", output.read_text(encoding="utf-8"))

    def test_invalid_dimensions_fail(self) -> None:
        with self.assertRaises(ValueError):
            parse_tty("", cols=0)
        with self.assertRaises(ValueError):
            parse_tty("", rows=2)


if __name__ == "__main__":
    unittest.main()
