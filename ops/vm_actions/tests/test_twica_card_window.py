import importlib.util
from pathlib import Path
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

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


class ClockRegressionTests(unittest.TestCase):
    def test_heartbeat_published_during_read_is_not_a_future_heartbeat(self):
        # Reading a producer file takes time. The producer can replace it after
        # an observer began the sample; the new timestamp is not a host-clock fault.
        ticks = [10_000_000_000]
        policy = {'schema': 1, 'owner':'common', 'generation':'a' * 32}
        def read(path, **kwargs):
            ticks[0] += 1_000_000
            if path.name == 'control.json': return dict(policy)
            return {'schema':1, 'identity':'current', 'pid':123, 'encoder_pid':456,
                    'monotonic_ns':ticks[0], 'generation':policy['generation'],
                    'dom_state':'observed', 'card_visible':False, 'cards_observed':0,
                    'attempts':0, 'width':6, 'height':2}
        fake = SimpleNamespace(ROOT=Path('/fixture'), SOREN=Path('/soren'),
            read_record=read, gate=lambda *args: None, native_gate=lambda *args:None,
            fresh=lambda row, now: 0 <= now - row['monotonic_ns'] < 3_000_000_000,
            HEADER=SimpleNamespace(size=24), MAX_PIXELS=12,
            read_file=lambda *args:b'', classify_frame=lambda *args:20)
        with patch.dict(sys.modules, {'probe_twica_runtime':fake}), \
                patch.object(module.time, 'monotonic_ns', side_effect=lambda:ticks[0]), \
                patch.object(module.time, 'monotonic', side_effect=[0., 0., 0., 91.]), \
                patch.object(module.time, 'sleep'):
            self.assertEqual(module.observe(), 26)  # Valid idle sample, not transition.


if __name__ == '__main__':
    unittest.main()
