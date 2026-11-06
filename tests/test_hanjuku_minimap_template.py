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

    def test_news_and_map_use_stable_fixed_slots(self):
        # The procedure row remains reserved even when it has no active
        # children, so the news and map never jump as events arrive/expire.
        self.assertRegex(HTML, r"grid-template-rows:\s*210px 64px minmax\(0, 1fr\)")
        self.assertRegex(HTML, r"\.hanjuku-gap-news\s*\{[^}]*height:\s*210px;")
        self.assertRegex(HTML, r"\.hanjuku-gap-progress:empty\s*\{\s*visibility:\s*hidden;")
        self.assertNotIn(".hanjuku-gap-progress:empty { display: none; }", HTML)
        self.assertRegex(HTML, r"\.hanjuku-gap-minimap\s*\{[^}]*width:\s*min\(100%, 174px\);[^}]*aspect-ratio:\s*1 / 1;")
        self.assertRegex(HTML, r"\.hanjuku-gap-minimap\s*\{[^}]*flex:\s*0 0 auto;")
        self.assertIn("const width = 300, height = 300, margin = 25;", HTML)
        self.assertIn("viewBox:'0 0 300 300'", HTML)
        self.assertNotIn("viewBox:'0 0 300 225'", HTML)
        self.assertRegex(HTML, r"\.hanjuku-gap-empty\s*\{[^}]*aspect-ratio:\s*1 / 1;")

    def test_routes_are_svg_not_general_cards(self):
        self.assertIn("function gapMinimap(value, moves, owned, lost, recallRoute)", HTML)
        self.assertIn("class:'hanjuku-gap-minimap'", HTML)
        self.assertIn("'marker-end':'url(#gap-arrow-'+move.kind+')'", HTML)
        self.assertIn("count.textContent = '進軍数 '+moveCount", HTML)
        self.assertIn("if (!from || !to || move.source === move.target)", HTML)
        self.assertIn("move.target === 'あたしの家'", HTML)
        self.assertIn("return 'recall'", HTML)
        self.assertIn("const observedReturn = recallRoute && table[recallRoute.target]", HTML)
        self.assertIn("all.push(['帰還元', observedReturn.origin])", HTML)
        self.assertIn("const marchCount = number(countMatch?.[1]);", HTML)
        # The old stacked general/name-and-route visual is not used anymore.
        self.assertNotIn("general.textContent = move.general;", HTML)
        self.assertNotIn("flowTitle.textContent = '出撃チャート';", HTML)


if __name__ == "__main__":
    unittest.main()
