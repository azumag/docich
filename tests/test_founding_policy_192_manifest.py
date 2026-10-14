import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'ops/hotfixes/founding_policy_20260907.json'

class Policy192ManifestTests(unittest.TestCase):
    def test_reviewed_correction_source_is_pinned(self):
        data = json.loads(MANIFEST.read_text())
        self.assertEqual(data['source_sha'], '54de54d993fe4190e72476c14b981c9d32193247')
        self.assertEqual(data['files']['prompts/game_theory.md']['old'],
                         '2ca5618f593206e0218a2aa3777f3fff2dbd4ffa4a85e5e0a514f5bc84f31edf')

    def test_projection_excludes_strategy_and_runtime(self):
        files = json.loads(MANIFEST.read_text())['files']
        self.assertEqual(set(files), {'prompts/game_theory.md', 'prompts/analyze_strategy.md',
            'prompts/implement_strategy.md', 'prompts/improve_strategy.md',
            'prompts/review_strategy.md', 'data/user_review.md', 'tests/test_founding_policy_contract.py'})
        for name, info in files.items():
            with self.subTest(path=name):
                self.assertRegex(info['old'], r'^[0-9a-f]{64}$')
                self.assertRegex(info['new'], r'^[0-9a-f]{64}$')
                self.assertIn(info['mode'], (0o644, 0o664, 0o755))

if __name__ == '__main__': unittest.main()
