import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.resolver import scorelog  # noqa: E402


class StrategyKeyTest(unittest.TestCase):
    def test_key_is_stable_across_mapping_order(self):
        left = {"food_urgency": 400.0, "move_budget": 12.0}
        right = {"move_budget": 12.0, "food_urgency": 400.0}
        self.assertEqual(scorelog.strategy_key(left), scorelog.strategy_key(right))

    def test_key_changes_with_strategy_content(self):
        base = {"food_urgency": 400.0, "move_budget": 12.0}
        changed = {"food_urgency": 250.0, "move_budget": 12.0}
        self.assertNotEqual(scorelog.strategy_key(base), scorelog.strategy_key(changed))


if __name__ == "__main__":
    unittest.main()
