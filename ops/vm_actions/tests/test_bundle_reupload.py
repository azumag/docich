import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = ROOT / 'ops/vm_actions/gateway.py'


class BundleReuploadTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='vmops-reupload-'))
        self.state = self.base / 'state'
        self.state.mkdir()
        self.doc = self.base / 'docich'
        self.doc.mkdir()
        subprocess.run(['git', 'init', '-q', self.doc], check=True)
        subprocess.run(['git', '-C', self.doc, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', self.doc, 'config', 'user.name', 'T'], check=True)
        (self.doc / 'app.py').write_text('v1\n')
        subprocess.run(['git', '-C', self.doc, 'add', 'app.py'], check=True)
        subprocess.run(['git', '-C', self.doc, 'commit', '-qm', 'v1'], check=True)
        self.config = self.base / 'config.json'
        self.config.write_text(json.dumps({'state': str(self.state), 'repos': {'docich': {'production': str(self.doc), 'mode': 'git'}}}))

    def call(self, command, payload):
        env = os.environ.copy()
        env['SSH_ORIGINAL_COMMAND'] = command
        env['VMOPS_TESTING'] = '1'
        return subprocess.run(['python3', str(GATEWAY), str(self.config)], input=payload, capture_output=True, env=env)

    def make_bundle(self):
        candidate = self.base / 'candidate'
        subprocess.run(['git', 'clone', '-q', self.doc, candidate], check=True)
        subprocess.run(['git', '-C', candidate, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', candidate, 'config', 'user.name', 'T'], check=True)
        (candidate / 'app.py').write_text('v2\n')
        subprocess.run(['git', '-C', candidate, 'add', 'app.py'], check=True)
        subprocess.run(['git', '-C', candidate, 'commit', '-qm', 'candidate'], check=True)
        sha = subprocess.check_output(['git', '-C', candidate, 'rev-parse', 'HEAD'], text=True).strip()
        path = self.base / 'candidate.bundle'
        subprocess.run(['git', '-C', candidate, 'bundle', 'create', path, 'HEAD'], check=True)
        return sha, path.read_bytes()

    def test_valid_reupload_replaces_existing_bytes_for_same_sha(self):
        sha, bundle = self.make_bundle()
        first = self.call(f'upload docich production {sha}', bundle)
        self.assertEqual(first.returncode, 0, first.stderr.decode())
        stored = self.state / 'bundles' / 'docich' / f'{sha}.bundle'
        stored.write_bytes(b'corrupt-old-bundle')
        second = self.call(f'upload docich preview {sha}', bundle)
        self.assertEqual(second.returncode, 0, second.stderr.decode())
        self.assertEqual(stored.read_bytes(), bundle)


if __name__ == '__main__':
    unittest.main()
