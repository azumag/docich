"""Read-only regressions from hash-verified, owner-approved Actions evidence."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import evaluate_hanjuku_vision as vision

FIXTURES = Path(__file__).parent / 'fixtures' / 'hanjuku'


class RealFrameTests(unittest.TestCase):
    def test_live_defender_successor_panel_resumes_the_next_general(self):
        frame = vision._frame((FIXTURES / 'g508-defense-successor.png').read_bytes())
        self.assertEqual(frame.digest(), '39df9a8aa8a43f6aa8cf083fc42f7b312502e5d4a63f53b70fc84564a58c5ac3')
        from docich.hanjuku_screen import parse
        from docich.hanjuku_policy import battle_step
        screen = parse(frame)
        self.assertEqual(screen.kind, 'battle')
        self.assertEqual((screen.battle.enemy, screen.battle.enemy_hp,
                          screen.battle.ally, screen.battle.ally_hp),
                         ('シナモン', 39, 'ヴィーナス', 82))
        memory = {'chapter': 1, 'captured': ['ジョンリギ'],
                  'battle': {'ally': 'クミン', 'enemy': 'シナモン', 'side': 'defense',
                             'castle': 'ジョンリギ', 'ally_hp': 0, 'enemy_hp': 39,
                             'cards_used': []}}
        self.assertEqual(battle_step(screen, memory), [])
        self.assertTrue(battle_step(screen, memory))
        self.assertEqual(memory['battle']['ally'], 'ヴィーナス')
        self.assertEqual(memory['captured'], ['ジョンリギ'])

    def test_bottom_enemy_hp_row_is_not_cropped_in_general_egg_menu(self):
        # Owner-authorized read-only live snapshot; RGB matched observation
        # 1790753591.2442434 from g508, not an exported running-runtime archive.
        frame = vision._frame((FIXTURES / 'g508-egg-general-menu.png').read_bytes())
        self.assertEqual(frame.digest(), '232a183fd4ea57828eb88540f3f1b429d86d6f0e82a2383cdc94e0a50d97b546')
        from docich.hanjuku_screen import parse
        from docich.hanjuku_policy import egg_battle_step, pad
        screen = parse(frame)
        self.assertEqual(screen.kind, 'egg_battle_menu')
        self.assertEqual(screen.menu_cursor, 188)
        self.assertEqual([(r.name, r.hp, r.side) for r in screen.egg_rows],
                         [('ヴィーナス', 82, 'ally'), ('ダークエルフ', 216, 'enemy')])
        memory = {'battle': {'ally': 'ヴィーナス', 'enemy': 'カシュー', 'ally_hp': 30,
                             'enemy_hp': 29, 'side': 'attack', 'egg_retreat_attempts': 3}}
        self.assertEqual(egg_battle_step(screen, memory), [pad('a')])
        self.assertEqual(memory['battle']['ally_hp'], 82)
        self.assertEqual(memory['battle']['enemy_hp'], 29)
        self.assertEqual(memory['battle']['egg_attack_label'], 'こうげき')

    def test_wounded_defender_does_not_open_random_self_damage_choices(self):
        frame = vision._frame((FIXTURES / 'g482-disabled-defense.png').read_bytes())
        from docich.hanjuku_screen import parse
        from docich.hanjuku_policy import battle_menu_step, pad
        screen = parse(frame)
        self.assertEqual(screen.kind, 'battle_menu')
        self.assertTrue(screen.hidden_battle_commands)
        memory = {'battle': {'ally': 'パプリカ', 'enemy': 'ヘラ', 'side': 'defense',
                             'ally_hp': 34, 'enemy_hp': 59, 'start_ally_hp': 34,
                             'start_enemy_hp': 59}}
        self.assertEqual(battle_menu_step(screen, memory), [pad('b')])
        self.assertEqual(memory['_records'][-1]['decision'], 'battle_okunote_risk_declined')

    def test_merchant_sprite_does_not_hide_confirmation_hand(self):
        frame = vision._frame((FIXTURES / 'g478-merchant.png').read_bytes())
        from docich.hanjuku_screen import parse
        from docich.hanjuku_policy import yes_no_step, pad
        screen = parse(frame)
        self.assertEqual(screen.kind, 'yes_no')
        self.assertEqual(screen.hand, (163, 177, 180, 190))
        self.assertEqual(screen.selected, 'うむッ!')
        memory = {}
        self.assertEqual(yes_no_step(screen, memory), [pad('a')])
        self.assertEqual(memory['_records'][-1]['decision'], 'prompt')

    def test_source_bound_visual_labels_without_game_decisions(self):
        samples = json.loads((FIXTURES / 'labels.json').read_text())['samples']
        # Import the normal parser while making accidental policy execution fail.
        vision._frame((FIXTURES / samples[0]['file']).read_bytes())
        with patch('docich.hanjuku_bot.decide', side_effect=AssertionError('no game decisions')):
            for sample in samples:
                with self.subTest(file=sample['file']):
                    raw = (FIXTURES / sample['file']).read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), sample['file_sha256'])
                    self.assertEqual(vision._frame(raw).digest(), sample['rgb_sha256'])
                    observed = vision.observe(raw)
                    for key, expected in sample['expected'].items():
                        self.assertEqual(observed[key], expected, key)

    def test_stalled_building_screen_remains_readable_to_existing_recovery(self):
        frame = vision._frame((FIXTURES / 'g462-frame-100.png').read_bytes())
        from docich.hanjuku_bot import classify
        from docich.hanjuku_screen import parse
        screen = parse(frame, phase=classify(frame))
        self.assertEqual(screen.selected, 'アルマムーン')
        self.assertIn('これいじょうのぞうちく', screen.text)
        self.assertIn('ぞうちくなさいます', screen.text)


if __name__ == '__main__':
    unittest.main()
