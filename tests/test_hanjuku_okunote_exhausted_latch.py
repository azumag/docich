"""A spent summon-menu attempt budget must not reset every cycle.

g1163 2026-11-10 18:5x-19:0x: one battle produced 887
``battle_okunote_summon_unconfirmed`` plus 887 ``_exhausted`` records in twenty
minutes. The exhausted verdict returned to the ordinary menu, the ordinary
menu re-entered the summon path, and the counter started again from zero, so
the battle never advanced.
"""
from __future__ import annotations

import unittest
from unittest import mock

from docich.hanjuku.flows import battle_menus
from docich.hanjuku.flows import okunote


class ExhaustedLatchTest(unittest.TestCase):
    def test_the_exhausted_battle_skips_the_summon_path(self):
        cur = {"ally_hp": 50, "enemy_hp": 50, "enemy": "ヒュドラ",
               "okunote_summon_exhausted": True, "okunote_flow": {"ticks": 0}}
        mem = {"battle": cur, "_records": []}
        screen = type("Screen", (), {"kind": "battle_menu", "egg_rows": [(176, "たまご")],
                                     "menu_cursor": None, "hidden_battle_commands": False,
                                     "lines": [], "text": "", "hand": None})()
        with mock.patch.object(okunote, "menu_pending", return_value=None), \
                mock.patch.object(okunote, "summon_reading", return_value=None):
            # One confirm hands the stuck menu back instead of pressing B forever.
            first = battle_menus.okunote_step(screen, mem)
            self.assertEqual(first, [battle_menus.pad("a")])
            self.assertIsNone(battle_menus.okunote_step(screen, mem))
        self.assertFalse([r for r in mem["_records"]
                          if r.get("decision", "").startswith("battle_okunote_summon_unconfirmed")])
