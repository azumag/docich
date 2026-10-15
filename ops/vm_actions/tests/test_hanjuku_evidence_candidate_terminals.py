"""Execute the actual Actions list validator, not a duplicate implementation."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[3]


class CandidateTerminalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = (ROOT / '.github/workflows/hanjuku-evidence-query.yml').read_text()
        block = workflow.split('python3 - "$work/output/candidates.json" <<\'PY\'\n', 1)[1]
        cls.validator = textwrap.dedent(block.split('          PY\n', 1)[0])

    def validate(self, reason, **changes):
        candidate = dict(runtime_id='g7-12345678', generation=7,
                         terminal_reason=reason, observations=150, actions_sent=12)
        candidate.update(changes)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidates.json'
            path.write_text(json.dumps(dict(schema=1, status='ok', truncated=False,
                                            runtimes=[candidate])))
            return subprocess.run([sys.executable, '-', str(path)], input=self.validator,
                                  text=True, capture_output=True)

    def test_every_native_terminal_is_accepted(self):
        source = ast.parse((ROOT / 'src/docich/hanjuku_run.py').read_text())
        assignment = next(n for n in source.body if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'TERMINAL_REASONS'
                                  for t in n.targets))
        reasons = ast.literal_eval(assignment.value.args[0])
        self.assertEqual(set(reasons), {'game_over', 'screen_stalled', 'input_stalled'})
        for reason in reasons:
            with self.subTest(reason=reason):
                result = self.validate(reason)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_terminal_is_still_rejected(self):
        for reason in ('active', 'manual_stop', 'unknown', None):
            with self.subTest(reason=reason):
                self.assertNotEqual(self.validate(reason).returncode, 0)

    def test_identity_and_counter_guards_remain(self):
        for changes in ({'generation': 8}, {'observations': -1},
                        {'actions_sent': True}, {'runtime_id': '../g7-12345678'}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.validate('input_stalled', **changes).returncode, 0)


if __name__ == '__main__':
    unittest.main()
