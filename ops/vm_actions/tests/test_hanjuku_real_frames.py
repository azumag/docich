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
