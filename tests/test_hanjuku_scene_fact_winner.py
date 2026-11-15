"""battle_result facts name the winner/loser so the narrator can't invert them.

g1207 (2026-11-16): バタール won vs ヴィーナス — record had outcome='win' —
but the announced commentary called バタール the loser. A bare win/loss left
the direction ambiguous to the LLM; the fact now names winner and loser.
"""

import unittest

from docich import hanjuku_scene as scene


class BattleResultFactTests(unittest.TestCase):
    def test_win_names_ally_winner_and_enemy_loser(self):
        fact = scene.fact({'decision': 'battle_result', 'ally': 'バタール',
                           'enemy': 'ヴィーナス', 'outcome': 'win',
                           'observed_metric': {'ally_hp': 58, 'enemy_hp': 0}})
        self.assertEqual(fact['outcome'], 'win')
        self.assertEqual(fact['winner'], 'バタール')
        self.assertEqual(fact['loser'], 'ヴィーナス')

    def test_loss_names_enemy_winner_and_ally_loser(self):
        fact = scene.fact({'decision': 'battle_result', 'ally': 'ミモザ',
                           'enemy': 'ブレッツェル', 'outcome': 'loss',
                           'observed_metric': {'ally_hp': 0, 'enemy_hp': 41}})
        self.assertEqual(fact['outcome'], 'loss')
        self.assertEqual(fact['winner'], 'ブレッツェル')
        self.assertEqual(fact['loser'], 'ミモザ')


if __name__ == '__main__':
    unittest.main()
