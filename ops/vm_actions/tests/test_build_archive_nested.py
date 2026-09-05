import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BUILD = ROOT / 'ops/vm_actions/build_archive.py'


class BuildArchiveNestedTests(unittest.TestCase):
    def test_nested_tracked_files_are_included(self):
        repo = Path(tempfile.mkdtemp(prefix='vmops-nested-'))
        subprocess.run(['git', 'init', '-q', repo], check=True)
        subprocess.run(['git', '-C', repo, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', repo, 'config', 'user.name', 'T'], check=True)
        (repo / 'app.py').write_text('print(1)\n')
        nested = repo / 'src' / 'nested.py'
        nested.parent.mkdir(parents=True)
        nested.write_text('print(2)\n')
        subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)
        subprocess.run(['git', '-C', repo, 'commit', '-qm', 'init'], check=True)
        sha = subprocess.check_output(['git', '-C', repo, 'rev-parse', 'HEAD'], text=True).strip()
        out = repo / 'out.tar'

        proc = subprocess.run(
            [sys.executable, str(BUILD), str(repo), sha, str(out), 'soviet_now'],
            text=True,
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with tarfile.open(out) as archive:
            self.assertIn('src/nested.py', archive.getnames())


if __name__ == '__main__':
    unittest.main()
