import os, re, subprocess, tempfile, time, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / 'ops' / 'vm_actions' / 'storage_reclaim_staged.sh'
STAGES = ['journal_vacuum','apt_clean','snap_disabled','snapd_cache','docker_prune','logrotate_install','logrotate_run']
GRAMMAR = re.compile(r'^stage=(?:' + '|'.join(STAGES) + r') result=(?:ok|failed) reason=(?:none|sudo_denied|command_failed|not_present|skipped)$')

class StagedTests(unittest.TestCase):
    def run_helper(self, apply=False, stage=None, extra=None):
        env = dict(os.environ)
        env['APPLY'] = '1' if apply else '0'
        argv = ['bash', str(HELPER)]
        if stage:
            argv += ['--stage', stage]
        if extra:
            argv += extra
        return subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60, check=False)

    def test_all_stage_lines_match_grammar(self):
        r = self.run_helper()
        self.assertEqual(r.returncode, 0, r.stderr)
        stage_lines = [ln for ln in r.stdout.splitlines() if ln.startswith('stage=')]
        self.assertEqual(len(stage_lines), len(STAGES))
        for ln in stage_lines:
            self.assertRegex(ln, GRAMMAR)

    def test_stage_filter_runs_only_requested_stage(self):
        r = self.run_helper(stage='journal_vacuum')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('stage=journal_vacuum', r.stdout)
        for s in STAGES:
            if s != 'journal_vacuum':
                self.assertNotIn('stage=' + s, r.stdout)

    def test_snap_cache_age_gate(self):
        d = tempfile.mkdtemp()
        p = Path(d)
        old = p / 'old.blob'
        new = p / 'new.blob'
        old.write_text('x')
        new.write_text('x')
        old_mtime = time.time() - 10 * 86400
        os.utime(old, (old_mtime, old_mtime))
        r = self.run_helper(apply=True, extra=['--snap-cache-root', str(p)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())


if __name__ == '__main__':
    unittest.main()
