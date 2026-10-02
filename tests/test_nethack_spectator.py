from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docich.nethack_spectator import (
    blank_frame,
    classify_char,
    main,
    parse_tty,
    render_live_shell,
    render_html,
)
from docich.nethack_tiles import TILESET_NAME, tile_key


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

    def test_tile_key_distinguishes_common_nethack_items(self) -> None:
        self.assertEqual(tile_key("player", "@"), "player")
        self.assertEqual(tile_key("floor", "#"), "corridor")
        self.assertEqual(tile_key("wall", "-"), "wall-horizontal")
        self.assertEqual(tile_key("wall", "|"), "wall-vertical")
        self.assertEqual(tile_key("stairs", "<"), "stairs-up")
        self.assertEqual(tile_key("stairs", ">"), "stairs-down")
        expected = {
            ")": "weapon",
            "[": "armor",
            "(": "tool",
            "=": "ring",
            "/": "wand",
            "%": "food",
            "!": "potion",
            "?": "scroll",
            "*": "gem",
            "$": "gold",
            '"': "amulet",
        }
        for glyph, key in expected.items():
            with self.subTest(glyph=glyph):
                self.assertEqual(tile_key("item", glyph), key)

    def test_parse_classic_tty_layout(self) -> None:
        text = "hello\n.@..\n.|>.\nHP:10\nDlvl:2\n"
        frame = parse_tty(text, cols=5, rows=5)
        self.assertEqual(frame.message, "hello")
        self.assertEqual(frame.status, ("HP:10", "Dlvl:"))
        self.assertEqual(frame.rows, 2)
        players = [cell for cell in frame.cells if cell.kind == "player"]
        self.assertEqual([(cell.x, cell.y) for cell in players], [(1, 0)])

    def test_default_render_is_original_svg_tiles(self) -> None:
        frame = parse_tty("msg\n.@!>\n.|d?\nHP:9\nDlvl:1\n", cols=5, rows=5)
        rendered = render_html(frame)
        self.assertIn('class="sprite-atlas"', rendered)
        self.assertIn(f'content="{TILESET_NAME}"', rendered)
        self.assertIn('href="#tile-player"', rendered)
        self.assertIn('href="#tile-potion"', rendered)
        self.assertIn('href="#tile-stairs-down"', rendered)
        self.assertIn('href="#tile-creature"', rendered)
        self.assertIn('class="tile-glyph"', rendered)
        self.assertIn('class="cell player"', rendered)
        self.assertIn('class="mode-tiles"', rendered)

    def test_ascii_mode_remains_immediate_fallback(self) -> None:
        frame = parse_tty("msg\n.@..\n.|>.\nHP:9\nDlvl:1\n", cols=5, rows=5)
        rendered = render_html(frame, visual_mode="ascii")
        self.assertNotIn("sprite-atlas", rendered)
        self.assertNotIn('href="#tile-player"', rendered)
        self.assertIn('class="mode-ascii"', rendered)
        self.assertIn('data-glyph="@">@</span>', rendered)
        with self.assertRaises(ValueError):
            render_html(frame, visual_mode="unknown")

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

    def test_cli_writes_tiles_by_default_and_accepts_ascii(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "frame.txt"
            output = root / "out" / "nethack.html"
            source.write_text("msg\n.@\n..\nHP:9\nDlvl:1\n", encoding="utf-8")
            rc = main(["--input", str(source), "--output", str(output), "--cols", "8", "--rows", "5"])
            self.assertEqual(rc, 0)
            self.assertTrue(output.is_file())
            self.assertIn("mode-tiles", output.read_text(encoding="utf-8"))
            rc = main([
                "--input", str(source), "--output", str(output),
                "--cols", "8", "--rows", "5", "--visual-mode", "ascii",
            ])
            self.assertEqual(rc, 0)
            self.assertIn("mode-ascii", output.read_text(encoding="utf-8"))

    def test_invalid_dimensions_fail(self) -> None:
        with self.assertRaises(ValueError):
            parse_tty("", cols=0)
        with self.assertRaises(ValueError):
            parse_tty("", rows=2)

    def test_non_map_screens_keep_the_entire_tty(self) -> None:
        cases = {
            "inventory": "Inventory:\n a - a +0 short sword\n b - a food ration\n",
            "menu": "What do you want to use? @\n a - a +0 short sword\n b - a food ration\n",
            "tombstone": "You were killed by a grid bug.\n\n\n\nRIP docich\n",
            "startup": "Shall I pick a character for you? [ynq]\n\n\n\n\n",
        }
        for name, terminal in cases.items():
            with self.subTest(screen=name):
                frame = parse_tty(terminal, cols=40, rows=5)
                self.assertEqual(frame.frame_kind, "text")
                self.assertEqual(len(frame.tty_lines), 5)
                rendered = render_html(frame)
                self.assertIn('class="tty-screen"', rendered)
                self.assertIn(terminal.splitlines()[0], rendered)
                self.assertNotIn('class="board"', rendered)

    def test_uncertain_map_and_unknown_glyphs_fail_safe(self) -> None:
        menu_with_map_cursor = "What do you want? @\n.|+..\n.....\nHP:10\nDlvl:1\n"
        frame = parse_tty(menu_with_map_cursor, cols=24, rows=5)
        self.assertEqual(frame.frame_kind, "text")
        self.assertIn("@", render_html(frame))

        unknown = parse_tty("msg\n.@}#\n.|+>\nHP:10\nDlvl:1\n", cols=8, rows=5)
        self.assertEqual(unknown.frame_kind, "map")
        self.assertIn('data-glyph="}"', render_html(unknown))
        self.assertIn('href="#tile-other"', render_html(unknown))

    def test_live_shell_is_fixed_incremental_and_epoch_pinned(self) -> None:
        shell = render_live_shell(
            runtime_id="g3-deadbeef",
            generation=3,
            presentation_epoch="p-0123456789abcdef",
            poll_interval_ms=500,
            stale_after_ms=3000,
        )
        self.assertIn('fetch(\'/frame\'', shell)
        self.assertIn('payload.runtime_id !== expectedRuntimeId', shell)
        self.assertIn('payload.generation !== expectedGeneration', shell)
        self.assertIn('payload.presentation_epoch !== expectedEpoch', shell)
        self.assertIn("textContent", shell)
        self.assertNotIn("innerHTML", shell)
        self.assertNotIn("location.reload", shell)
        self.assertNotIn("https://", shell)
        with self.assertRaises(ValueError):
            render_live_shell(
                runtime_id="g3-deadbeef",
                generation=3,
                presentation_epoch="bad/epoch",
            )
        with self.assertRaises(ValueError):
            render_live_shell(
                runtime_id="g3-deadbeef",
                generation=True,
                presentation_epoch="p-valid",
            )


if __name__ == "__main__":
    unittest.main()
