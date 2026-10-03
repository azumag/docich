import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('twica_window', ROOT / 'ops/vm_actions/probe_twica_card_window.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class CardWindowTests(unittest.TestCase):
    def test_idle_is_not_claimed_as_display_success(self):
        evidence = module.WindowEvidence()
        evidence.sample(0, False, 20)
        self.assertEqual(evidence.result(), 26)

    def test_success_requires_visible_dom_and_pixels_together(self):
        evidence = module.WindowEvidence()
        evidence.sample(0, False, 0)
        self.assertEqual(evidence.result(), 26)
        evidence.sample(1, True, 0)
        self.assertEqual(evidence.result(), 0)

    def test_trailing_health_at_hide_is_not_a_long_gap(self):
        evidence = module.WindowEvidence()
        evidence.sample(0, True, 0)
        evidence.sample(1, True, 20)
        evidence.sample(2, False, 20)
        self.assertEqual(evidence.result(), 0)

    def test_long_visible_dom_without_input_is_failure_even_after_pixels(self):
        for bad in (20, 40, 41, 42):
            evidence = module.WindowEvidence()
            evidence.sample(0, True, 0)
            evidence.sample(1, True, bad)
            evidence.sample(3, True, bad)
            self.assertEqual(evidence.result(), 24)

    def test_unknown_samples_do_not_establish_a_gap(self):
        evidence = module.WindowEvidence()
        evidence.sample(0, True, 20)
        evidence.sample(1, None, 20)
        evidence.sample(4, True, 20)
        self.assertEqual(evidence.result(), 25)
        self.assertEqual(module.WindowEvidence().result(), 50)

    def test_clipped_pixels_are_distinct(self):
        evidence = module.WindowEvidence()
        evidence.sample(0, True, 21)
        self.assertEqual(evidence.result(), 21)


if __name__ == '__main__':
    unittest.main()
