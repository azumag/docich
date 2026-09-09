"""Deploy must not recurse into submodules when fetching the parent bundle.

Regression for the 2026-09-10 production outage: every `deploy docich production`
failed at the very first step. git's default `fetch.recurseSubmodules=on-demand`
made the bundle fetch try to resolve every gitlink reachable in the incoming
history, including one whose submodule commit had been garbage collected upstream
(a merged-and-deleted PR branch). The remote answered
`upload-pack: not our ref <sha>`, the fetch exited non-zero, and `deploy_git`
aborted before it could write `deployment_intent` or move HEAD -- so the VM stayed
pinned at an old baseline and every later reconcile failed too.

The bundle only ever carries the parent repository; owned submodules are synced
explicitly afterwards by `sync_owned_submodules`. So the fetch must pass
`--no-recurse-submodules`.
"""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = ROOT / 'ops/vm_actions/gateway.py'

spec = importlib.util.spec_from_file_location('gateway_dangling_gitlink', GATEWAY)
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)


def git(repo, *args, check=True):
    return subprocess.run(['git', '-C', str(repo), *args], check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    git(path, 'config', 'user.email', 't@example.com')
    git(path, 'config', 'user.name', 'T')
    git(path, 'config', 'protocol.file.allow', 'always')
    return path


class DeployDanglingGitlinkTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o022))
        self.base = Path(tempfile.mkdtemp(prefix='vmops-dangling-gitlink-'))
        self.state = self.base / 'state'
        self.state.mkdir()
        self.docich = self.base / 'docich'
        self.cfg = {
            'state': str(self.state),
            'repos': {'docich': {'production': str(self.docich), 'mode': 'git'}},
        }

    def _commit(self, repo, name, text):
        (repo / name).write_text(text)
        git(repo, 'add', name)
        git(repo, 'commit', '-qm', text)
        return git(repo, 'rev-parse', 'HEAD').stdout.strip()

    def test_deploy_survives_gitlink_missing_from_submodule_remote(self):
        # A submodule remote that will never contain the gitlink we point at.
        subremote = init_repo(self.base / 'subremote')
        self._commit(subremote, 'lib.txt', 'sub-v1')

        # A commit that exists only in an unrelated repository: standing in for a
        # gitlink whose branch was merged and deleted, then garbage collected.
        orphan = init_repo(self.base / 'orphan')
        missing_sha = self._commit(orphan, 'lib.txt', 'sub-orphan')

        # Production checkout with the submodule populated (so git would recurse).
        init_repo(self.docich)
        self._commit(self.docich, 'app.py', 'v1')
        git(self.docich, '-c', 'protocol.file.allow=always',
            'submodule', 'add', '-q', str(subremote), 'games/other')
        git(self.docich, 'commit', '-qm', 'add submodule')
        old_sha = git(self.docich, 'rev-parse', 'HEAD').stdout.strip()

        gw.write_json(gw.current_file(self.cfg, 'docich'),
                      {'mode': 'git', 'sha': old_sha, 'previous_head': None})

        # Candidate advances the gitlink to the unavailable commit.
        candidate = self.base / 'candidate'
        subprocess.run(['git', 'clone', '-q', '--no-recurse-submodules',
                        str(self.docich), str(candidate)], check=True)
        git(candidate, 'config', 'user.email', 't@example.com')
        git(candidate, 'config', 'user.name', 'T')
        git(candidate, 'config', 'protocol.file.allow', 'always')
        subprocess.run(['git', '-C', str(candidate), 'update-index', '--add',
                        '--cacheinfo', f'160000,{missing_sha},games/other'], check=True)
        (candidate / 'app.py').write_text('v2')
        git(candidate, 'add', 'app.py')
        git(candidate, 'commit', '-qm', 'v2 with unavailable gitlink')
        new_sha = git(candidate, 'rev-parse', 'HEAD').stdout.strip()

        bundle = gw.bundle_file(self.cfg, 'docich', new_sha)
        bundle.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', '-C', str(candidate), 'bundle', 'create',
                        str(bundle), 'HEAD'], check=True)

        # Precondition: the gitlink really is unreachable from the submodule remote.
        probe = subprocess.run(['git', '-C', str(self.docich / 'games/other'),
                                'cat-file', '-t', missing_sha],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(probe.returncode, 0,
                            'gitlink must be absent for this regression to be meaningful')

        # The deploy must not be blocked by the unavailable submodule commit.
        gw.deploy_git(self.cfg, 'docich', new_sha)

        head = git(self.docich, 'rev-parse', 'HEAD').stdout.strip()
        self.assertEqual(head, new_sha)
        self.assertEqual(gw.read_json(gw.current_file(self.cfg, 'docich'))['sha'], new_sha)

    def test_authoritative_bundle_fetch_does_not_recurse_and_is_strict(self):
        """The parent fetch is strict + non-recursive; any recursive fetch is best effort."""
        lines = [line for line in GATEWAY.read_text().splitlines()
                 if "'fetch'" in line and "'--force'" in line]
        self.assertTrue(lines, 'bundle fetch invocation not found')

        strict = [l for l in lines if '--no-recurse-submodules' in l]
        recursive = [l for l in lines if '--recurse-submodules=on-demand' in l]
        self.assertEqual(len(strict), 1, 'exactly one non-recursive parent fetch expected')
        self.assertNotIn('check=False', strict[0], 'parent fetch must stay strict')

        # A recursive prefetch may exist, but only as a non-fatal optimisation.
        source = GATEWAY.read_text()
        for line in recursive:
            index = source.index(line)
            following = source[index:index + 400]
            self.assertIn('check=False', following,
                          'recursive submodule fetch must not be able to fail the deploy')


if __name__ == '__main__':
    unittest.main()
