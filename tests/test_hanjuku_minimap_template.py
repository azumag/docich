"""Regression checks for the chapter-coordinate Hanjuku broadcast minimap."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from docich import hanjuku_chart as chart

HTML = (Path(__file__).resolve().parents[1] / "overlays" /
        "direct_broadcast_overlay.html").read_text(encoding="utf-8")


def _minimap_cells():
    match = re.search(r"const HANJUKU_MINIMAP_COORDS = (\{[^\n]+\});", HTML)
    assert match, "the minimap's static chapter coordinate table is missing"
    return json.loads(match.group(1))


class HanjukuMinimapTemplateTest(unittest.TestCase):
    def test_each_chapter_uses_chart_castle_coordinates(self):
        cells = _minimap_cells()
        self.assertEqual(set(cells), set(map(str, range(1, 13))))
        for chapter, points in cells.items():
            with self.subTest(chapter=chapter):
                expected = chart.CASTLES[int(chapter)]
                for castle, cell in expected.items():
                    self.assertEqual(points[castle], list(cell),
                                     f"chapter {chapter} castle {castle} drifted")

    def test_house_is_distinct_and_uses_chapter_heart_coordinate(self):
        cells = _minimap_cells()
        for chapter, (vx, vy) in chart.HOUSE_VIEW.items():
            ox, oy = chart.WORLD_MAP_OFFSET[chapter]
            expected = [round(8 * (vx-ox)), round(8 * (vy-oy))]
            self.assertEqual(cells[str(chapter)]["あたしの家"], expected)

    def test_routes_are_svg_not_general_cards(self):
        self.assertIn("function gapMinimap(value, moves, owned, lost)", HTML)
        self.assertIn("class:'hanjuku-gap-minimap'", HTML)
        self.assertIn("'marker-end':'url(#gap-arrow-'+move.kind+')'", HTML)
        self.assertIn("count.textContent = '進軍数 '+moveCount", HTML)
        self.assertIn("if (!from || !to || move.source === move.target)", HTML)
        self.assertIn("move.target === 'あたしの家'", HTML)
        self.assertIn("return 'recall'", HTML)
        # The old stacked general/name-and-route visual is not used anymore.
        self.assertNotIn("general.textContent = move.general;", HTML)
        self.assertNotIn("flowTitle.textContent = '出撃チャート';", HTML)


if __name__ == "__main__":
    unittest.main()
