"""Fixed authorization, source integrity and readonly protocol; no VM calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from ops.vm_actions.tests import test_corner_rotation_operator as operator_tests

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('admin_check_collector', ROOT / 'ops/vm_actions/collect_diagnostics.py')
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)
PATHS = ('nethack_admin_release', 'nethack_admin_resources', 'nethack_admin_preflight', 'nethack_admin_result', 'nethack_resource_fence',
         'nethack_return', 'retro_corner', 'game_switch', 'hanjuku_manual_cancel', 'naming', 'tmux')


class AdminProtocolTests(unittest.TestCase):
    def auth(self, **kw):
        return operator_tests.CornerRotationAuthorizeTests().run_auth(**kw)

    def test_owner_protected_main_canonical_exact_inputs_and_private_output(self):
        release = dict(INPUT_OPERATION='admin-release-nethack', INPUT_EXPECTED_RESERVATION='a' * 64,
                       INPUT_APPROVAL_EXPIRES_AT='1800000000')
        result = self.auth(**release)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('a' * 64, result.stdout + result.stderr)
        self.assertNotIn('1800000000', result.stdout + result.stderr)
        for changes in ({'INPUT_EXPECTED_RESERVATION': ''}, {'INPUT_EXPECTED_RESERVATION': 'a'*64+';id'},
            {'INPUT_APPROVAL_EXPIRES_AT': ''}, {'INPUT_APPROVAL_EXPIRES_AT': '1800000000;id'},
            {'GITHUB_ACTOR_ID': '42'}, {'GITHUB_REF_PROTECTED': 'false'},
            {'GITHUB_WORKFLOW_REF': operator_tests.LEGACY_REF}, {'INPUT_CONFIRM': ''},
            {'GITHUB_REF': 'refs/heads/test'}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.auth(**{**release, **changes}).returncode, 0)
        self.assertEqual(self.auth(INPUT_OPERATION='check-admin-release-nethack').returncode, 0)
        self.assertNotEqual(self.auth(INPUT_OPERATION='check-admin-release-nethack',
                                     INPUT_EXPECTED_RESERVATION='a'*64).returncode, 0)
        self.assertNotEqual(self.auth(INPUT_OPERATION='check-admin-release-nethack',
                                     INPUT_APPROVAL_EXPIRES_AT='1800000000').returncode, 0)

    def test_event_duplicate_oversize_and_bad_types_refuse_without_disclosure(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / 'event.json'
            for raw in ('{"inputs":{"approval_expires_at":"1800000000","approval_expires_at":"1800000600"}}',
                        ' '*1048577, '{"inputs":{"approval_expires_at":true}}',
                        '{"inputs":{"approval_expires_at":"PRIVATE_SENTINEL"}}'):
                event.write_text(raw)
                result = self.auth(INPUT_OPERATION='admin-release-nethack',
                                   INPUT_EXPECTED_RESERVATION='a'*64, GITHUB_EVENT_PATH=str(event))
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('PRIVATE', result.stdout + result.stderr)

    def test_new_authority_modules_are_verified_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name in PATHS:
                target = root / f'src/docich/{name}.py'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / f'src/docich/{name}.py').read_bytes())
            def git(argv, **kw):
                if 'show' in argv:
                    path = argv[-1].removeprefix('HEAD:')
                    return subprocess.CompletedProcess(argv, 0, (ROOT/path).read_bytes(), b'')
                return subprocess.CompletedProcess(argv, 0, b'a'*40 if 'rev-parse' in argv else b'', b'')
            with patch.object(diag, 'PROD_ROOT', root), patch.object(diag.subprocess, 'run', git), \
                    patch('docich.nethack_admin_release.check', Mock(return_value={'status':'verified'})) as checker, \
                    patch('docich.nethack_admin_preflight.preflight', Mock(return_value={'status':'classification'})) as classifier:
                self.assertEqual(diag._collect_nethack_admin_check(root, root, 1, player='fixture')['status'], 'verified')
                self.assertEqual(diag._collect_nethack_admin_preflight(root, root, 1, player='fixture')['status'], 'classification')
                self.assertEqual(checker.call_count, 1)
                for name in PATHS:
                    target = root / f'src/docich/{name}.py'
                    original = target.read_bytes(); target.write_bytes(original+b'\n# drift\n')
                    self.assertEqual(diag._collect_nethack_admin_check(root, root, 1, player='fixture')['reason'], 'code_unverified')
                    projection = diag._collect_nethack_admin_preflight(root, root, 1, player='fixture')
                    self.assertEqual(projection['reason'], 'code_unverified')
                    self.assertIs(projection['release_authority'], False)
                    target.write_bytes(original)
                self.assertEqual(checker.call_count, 1)
                self.assertEqual(classifier.call_count, 1)

    def test_initial_check_ignores_poisoned_bytecode_and_creates_no_cache(self):
        # An equal-size/equal-mtime .pyc would normally mask the verified source.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = root / 'src/docich'; package.mkdir(parents=True)
            (package/'__init__.py').touch()
            for name in PATHS: (package/f'{name}.py').write_text('')
            trusted = "def check(*a,**k): return {'status':'trusted'}\n"
            spoofed = trusted.replace('trusted', 'spoofed')
            target = package/'nethack_admin_release.py'; target.write_text(spoofed)
            import py_compile
            stamp = target.stat().st_mtime
            cache = package / '__pycache__' / f'nethack_admin_release.cpython-{sys.version_info.major}{sys.version_info.minor}.pyc'
            py_compile.compile(str(target), cfile=str(cache), doraise=True)
            target.write_text(trusted); os.utime(target, (stamp, stamp))
            initial = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            program = '''
import importlib.util,json,pathlib,subprocess,sys,types
root=pathlib.Path(sys.argv[1]); collector=pathlib.Path(sys.argv[2])
pkg=types.ModuleType('docich');pkg.__path__=[str(root/'src/docich')];sys.modules['docich']=pkg
for name,attrs in [('docich.runtime_backend', {'_pid_is_active':lambda *a:False,'_process_is_zombie':lambda *a:False}),
                   ('docich.semantic_decision.diagnostics', {'describe':lambda *a:{}})]:
    m=types.ModuleType(name);m.__dict__.update(attrs);sys.modules[name]=m
s=importlib.util.spec_from_file_location('collector',collector);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
d.PROD_ROOT=root;sys.path.insert(0,str(root/'src'))
def git(argv,**kw):
    out=(root/argv[-1].removeprefix('HEAD:')).read_bytes() if 'show' in argv else (b'a'*40 if 'rev-parse' in argv else b'')
    return subprocess.CompletedProcess(argv,0,out,b'')
d.subprocess.run=git
print(json.dumps(d._collect_nethack_admin_check(root,root,1,player='fixture')))
'''
            result = subprocess.run([sys.executable, '-B', '-c', program, str(root),
                str(ROOT/'ops/vm_actions/collect_diagnostics.py')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'trusted', result.stdout+result.stderr)
            self.assertEqual(initial, {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_fixed_release_script_rejects_injection_and_code_drift_without_execution(self):
        script = ROOT / 'ops/vm_actions/admin_release_nethack.sh'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'src/docich').mkdir(parents=True); (root/'config').mkdir()
            (root/'src/docich/nethack_admin_release.py').touch()
            (root/'config/docich.soren-live.toml').touch()
            for binary, body in {
                'git': 'case "$*" in *rev-parse*) printf "%s" "${VM_HEAD:-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb}" ;; *status*) printf "%s" "${VM_DIRTY:-}" ;; esac',
                'python3': '[[ "$1" == -I ]] && exit 0\nexec "'+sys.executable+'" -c \'import json,sys;print(json.dumps(sys.argv[1:]))\' "$@"',
                'timeout': 'shift\nexec "$@"',
            }.items():
                path=root/binary;path.write_text('#!/bin/bash\n'+body+'\n');path.chmod(0o755)
            env={**os.environ, 'PATH':str(root)+os.pathsep+os.environ['PATH'], 'DOCICH_PROD_ROOT':str(root),
                 'NETHACK_ADMIN_SHA':'b'*40, 'NETHACK_ADMIN_EXPECTED':'a'*64, 'NETHACK_ADMIN_EXPIRES':'1800000000'}
            result=subprocess.run(['bash',str(script)],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout), ['-B','-P','-X','pycache_prefix=/dev/null/docich-disabled-cache',
                '-m','docich.nethack_admin_release','--expected','a'*64,'--expires','1800000000','--sha','b'*40])
            for changed, rc in (({'NETHACK_ADMIN_EXPECTED':'a'*64+';id'},64),
                ({'NETHACK_ADMIN_EXPIRES':'1800000000;id'},64), ({'VM_HEAD':'c'*40},25),
                ({'VM_DIRTY':' M tracked.py'},25)):
                result=subprocess.run(['bash',str(script)],env={**env,**changed},capture_output=True,text=True)
                self.assertEqual(result.returncode,rc);self.assertEqual(result.stdout,'')


if __name__ == '__main__':
    unittest.main()
