"""Late observation must reuse evidence, never reset the live renderer."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[3]


class RuntimeWorkflowTests(unittest.TestCase):
    def test_late_browser_probe_only_has_fixed_read_modes(self):
        text = (ROOT / '.github/workflows/twica-runtime-probe.yml').read_text()
        block = text.split('# Observe the existing page AFTER a reported event;', 1)[1]
        self.assertIn('for mode in transport dom errors; do', block)
        self.assertIn('twica_browser_operator.py %s', block)
        self.assertIn('git diff --quiet HEAD -- || exit 61', block)
        self.assertIn('$(git rev-parse HEAD)', block)
        self.assertNotIn('invoke refresh', block)
        self.assertNotIn('systemctl', block)
        self.assertNotIn('twica_browser_refresh_epoch', text)
        self.assertIn('case "$rc" in', block)
        self.assertIn('browser_rc=$?', block)

    def test_all_run_blocks_are_valid_bash(self):
        # Dependency-free extraction for this fixed literal-run workflow.
        lines = (ROOT / '.github/workflows/twica-runtime-probe.yml').read_text().splitlines()
        for index, line in enumerate(lines):
            if line.strip() not in ('run: |', '- run: |'):
                continue
            base = len(line) - len(line.lstrip())
            script = []
            for following in lines[index + 1:]:
                if following.strip() and len(following) - len(following.lstrip()) <= base:
                    break
                script.append(following[base + 2:])
            result = subprocess.run(['bash', '-n'], input='\n'.join(script), text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
