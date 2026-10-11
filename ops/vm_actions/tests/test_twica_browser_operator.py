import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('browser_ops_test', ROOT / 'ops/vm_actions/twica_browser_operator.py')
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)


class BrowserOperatorTests(unittest.TestCase):
    def test_passive_categories_never_claim_blank_as_card(self):
        self.assertEqual(ops.classify({'dom_state':'observed', 'card_present':False}, 'dom'), 33)
        self.assertEqual(ops.classify({'dom_state':'observed', 'card_present':True}, 'dom'), 31)
        self.assertEqual(ops.classify({'dom_state':'observed', 'card_visible':True}, 'dom'), 0)
        self.assertEqual(ops.classify({'dom_state':'observed', 'cards_observed':1}, 'dom'), 32)
        self.assertEqual(ops.classify({}, 'dom'), 30)

    def test_http_socket_and_configuration_are_distinct(self):
        row = {'page_state':'overlay', 'events_state':'ok'}
        self.assertEqual(ops.classify(row, 'transport'), 20)
        self.assertEqual(ops.classify(dict(row, socket_state='receiving'), 'transport'), 0)
        self.assertEqual(ops.classify(dict(row, events_state='unobserved', socket_state='receiving'), 'transport'), 21)
        self.assertEqual(ops.classify(dict(row, events_state='unobserved', config_state='ok'), 'transport'), 22)
        self.assertEqual(ops.classify({}, 'transport'), 10)
        for label, code in [('denied',11),('rate_limited',12),('server_error',13),('http_error',14),('network_error',15)]:
            self.assertEqual(ops.classify(dict(row, events_state=label), 'transport'), code)

    def test_errors_have_no_arbitrary_message_projection(self):
        self.assertEqual(ops.classify({'script_errors':1, 'text':'secret'}, 'errors'), 40)
        self.assertEqual(ops.classify({'script_errors':0}, 'errors'), 0)
        self.assertEqual(ops.classify({}, 'errors'), 59)

    def test_refresh_is_renderer_only(self):
        source = (ROOT / 'ops/vm_actions/twica_browser_operator.py').read_text()
        self.assertIn("ops.checked(['systemctl', '--user', 'restart', ops.UNIT])", source)
        self.assertNotIn('arm_stream(', source)
        self.assertNotIn('transfer(', source)
        self.assertNotIn('--confirm-idle', source)
        self.assertIn("after.get('encoder_pid') != before.get('encoder_pid')", source)
        self.assertIn("record.get('pid') != renderer.get('pid')", source)

    def test_invalid_command_is_inert(self):
        for args in [[],['enable'],['refresh','--force'],['../../env']]:
            self.assertEqual(ops.main(args), 59)


if __name__ == '__main__':
    unittest.main()
