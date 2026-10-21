import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / 'ops/local/compare_shared_runtime_pids.py'


class SharedPidCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sharedpid-cli-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.receipt = {'schema_version': 2, 'scan_complete': True,
            'boot_id': '12345678-1234-1234-1234-123456789abc',
            'roles': {role: {'count': 1, 'pids': [10+i],
                'entries': [{'pid': 10+i, 'start_ticks': 100}]}
                for i, role in enumerate(('shared_stream', 'encoder', 'audio'))}}

    def run_cli(self, before, after):
        for name, value in (('before', before), ('after', after)):
            if value is not None:
                (self.root/name).write_text(json.dumps(value))
        run = subprocess.run([sys.executable, str(CLI), '--before', str(self.root/'before'),
            '--after', str(self.root/'after')], capture_output=True, text=True, timeout=10)
        return run.returncode, json.loads(run.stdout)

    def test_saved_gateway_envelopes_match(self):
        envelope = {'status': 'diagnosed', 'diagnostics': {'shared_runtime_pids': self.receipt}}
        code, result = self.run_cli(envelope, envelope)
        self.assertEqual(code, 0); self.assertTrue(result['match'])

    def test_missing_malformed_partial_and_changed_are_nonzero(self):
        for case in ('missing', 'partial', 'schema', 'pid', 'ticks', 'boot'):
            with self.subTest(case=case):
                after = json.loads(json.dumps(self.receipt))
                if case == 'missing': after = None
                elif case == 'partial': after['scan_complete'] = False
                elif case == 'schema': after['schema_version'] = 999
                elif case == 'boot': after['boot_id'] = '22345678-1234-1234-1234-123456789abc'
                elif case == 'pid':
                    after['roles']['audio']['entries'][0]['pid'] = 99
                    after['roles']['audio']['pids'] = [99]
                else: after['roles']['audio']['entries'][0]['start_ticks'] += 1
                code, result = self.run_cli(self.receipt, after)
                self.assertEqual(code, 1); self.assertFalse(result['match'])

    def test_invalid_json_has_only_fixed_failure_output(self):
        (self.root/'after').write_text('{"PRIVATE":"secret"')
        code, result = self.run_cli(self.receipt, None)
        self.assertEqual(code, 1)
        self.assertNotIn('PRIVATE', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
