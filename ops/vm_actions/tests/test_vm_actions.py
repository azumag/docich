import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
AUTH=ROOT/'ops/vm_actions/authorize.py'
BUILD=ROOT/'ops/vm_actions/build_archive.py'
GATEWAY=ROOT/'ops/vm_actions/gateway.py'
WF=ROOT/'.github/workflows/vm-operations.yml'

class AuthorizeTests(unittest.TestCase):
    def run_auth(self, **overrides):
        env={
            'GITHUB_REPOSITORY':'azumag/soviet_now',
            'GITHUB_REPOSITORY_ID':'1155505884',
            'GITHUB_REPOSITORY_OWNER':'azumag',
            'GITHUB_REPOSITORY_OWNER_ID':'9018513',
            'GITHUB_ACTOR':'azumag',
            'GITHUB_ACTOR_ID':'9018513',
            'GITHUB_TRIGGERING_ACTOR':'azumag',
            'GITHUB_REF':'refs/heads/main',
            'GITHUB_WORKFLOW_REF':'azumag/soviet_now/.github/workflows/vm-operations.yml@refs/heads/main',
            'GITHUB_EVENT_NAME':'workflow_dispatch',
            'GITHUB_SHA':'a'*40,
            'INPUT_OPERATION':'deploy',
            'INPUT_TARGET':'preview',
            'INPUT_REF':'feature/test',
            'INPUT_CONFIRM':'',
        }
        env.update(overrides)
        return subprocess.run(['python3', str(AUTH)], text=True, capture_output=True, env=env)

    def test_owner_manual_preview_allowed(self):
        p=self.run_auth(); self.assertEqual(p.returncode,0,p.stderr)
        data=json.loads(p.stdout); self.assertEqual(data['ref'],'feature/test')

    def test_docich_owner_context_allowed(self):
        p=self.run_auth(
            GITHUB_REPOSITORY='azumag/docich',
            GITHUB_REPOSITORY_ID='1327276249',
            GITHUB_WORKFLOW_REF='azumag/docich/.github/workflows/vm-operations.yml@refs/heads/main',
        )
        self.assertEqual(p.returncode,0,p.stderr)

    def test_non_owner_actor_denied(self):
        p=self.run_auth(GITHUB_ACTOR='collab',GITHUB_ACTOR_ID='42')
        self.assertNotEqual(p.returncode,0)

    def test_non_owner_rerun_denied(self):
        p=self.run_auth(GITHUB_TRIGGERING_ACTOR='collab')
        self.assertNotEqual(p.returncode,0)

    def test_workflow_must_be_main_copy(self):
        p=self.run_auth(GITHUB_WORKFLOW_REF='azumag/soviet_now/.github/workflows/vm-operations.yml@refs/heads/feature')
        self.assertNotEqual(p.returncode,0)

    def test_production_exec_needs_confirmation(self):
        p=self.run_auth(INPUT_OPERATION='exec',INPUT_TARGET='production',INPUT_REF='main',INPUT_CONFIRM='')
        self.assertNotEqual(p.returncode,0)
        p=self.run_auth(INPUT_OPERATION='exec',INPUT_TARGET='production',INPUT_REF='main',INPUT_CONFIRM='production')
        self.assertEqual(p.returncode,0,p.stderr)

    def test_push_is_fixed_to_production_deploy(self):
        p=self.run_auth(GITHUB_EVENT_NAME='push',INPUT_OPERATION='',INPUT_TARGET='',INPUT_REF='')
        self.assertEqual(p.returncode,0,p.stderr)
        data=json.loads(p.stdout); self.assertEqual((data['operation'],data['target'],data['ref']),('deploy','production','a'*40))

class BuildArchiveTests(unittest.TestCase):
    def make_repo(self):
        d=Path(tempfile.mkdtemp(prefix='vmops-git-'))
        subprocess.run(['git','init','-q',d],check=True)
        subprocess.run(['git','-C',d,'config','user.email','t@example.com'],check=True)
        subprocess.run(['git','-C',d,'config','user.name','T'],check=True)
        for p,c in {
            'app.py':'print(1)\n', '.env':'SECRET=x\n', 'logs/live.log':'x\n', 'tmp/state':'x\n',
            'data/runtime.json':'x\n', '.github/workflows/x.yml':'x\n', 'ops/vm_actions/x':'x\n'
        }.items():
            q=d/p; q.parent.mkdir(parents=True,exist_ok=True); q.write_text(c)
        (d/'link').symlink_to('/etc/passwd')
        subprocess.run(['git','-C',d,'add','-A'],check=True)
        subprocess.run(['git','-C',d,'commit','-qm','init'],check=True)
        sha=subprocess.check_output(['git','-C',d,'rev-parse','HEAD'],text=True).strip()
        return d,sha

    def test_runtime_and_control_paths_are_excluded(self):
        repo,sha=self.make_repo(); out=repo/'out.tar'
        p=subprocess.run([str(BUILD),str(repo),sha,str(out),'soviet_now'],text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr)
        with tarfile.open(out) as tf: names=set(tf.getnames())
        self.assertIn('app.py',names)
        for blocked in ['.env','logs/live.log','tmp/state','data/runtime.json','.github/workflows/x.yml','ops/vm_actions/x','link']:
            self.assertNotIn(blocked,names)

    def test_sha_must_equal_checked_out_head(self):
        repo,sha=self.make_repo(); out=repo/'out.tar'
        p=subprocess.run([str(BUILD),str(repo),'a'*40,str(out),'soviet_now'],text=True,capture_output=True)
        self.assertNotEqual(p.returncode,0)

def make_tar(files, symlink=None):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w') as tf:
        for name,data in files.items():
            b=data.encode(); ti=tarfile.TarInfo(name); ti.size=len(b); ti.mode=0o644; tf.addfile(ti,io.BytesIO(b))
        if symlink:
            ti=tarfile.TarInfo(symlink[0]); ti.type=tarfile.SYMTYPE; ti.linkname=symlink[1]; tf.addfile(ti)
    return out.getvalue()

class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.base=Path(tempfile.mkdtemp(prefix='vmops-gw-'))
        self.state=self.base/'state'; self.state.mkdir()
        self.prod=self.base/'soren'; self.prod.mkdir()
        self.doc=self.base/'docich'; self.doc.mkdir()
        subprocess.run(['git','init','-q',self.doc],check=True)
        subprocess.run(['git','-C',self.doc,'config','user.email','t@example.com'],check=True)
        subprocess.run(['git','-C',self.doc,'config','user.name','T'],check=True)
        (self.doc/'app.py').write_text('v1\n')
        subprocess.run(['git','-C',self.doc,'add','app.py'],check=True)
        subprocess.run(['git','-C',self.doc,'commit','-qm','v1'],check=True)
        self.config=self.base/'config.json'
        self.config.write_text(json.dumps({'state':str(self.state),'repos':{
            'soviet_now':{'production':str(self.prod),'mode':'overlay'}, 'docich':{'production':str(self.doc),'mode':'git'}
        }}))
    def call(self, cmd, payload=b''):
        env=os.environ.copy(); env['SSH_ORIGINAL_COMMAND']=cmd; env['VMOPS_TESTING']='1'
        return subprocess.run(['python3',str(GATEWAY),str(self.config)],input=payload,capture_output=True,env=env)
    def upload(self,sha='a'*40,files=None):
        files=files or {'app.py':'v1\n'}
        return self.call(f'upload soviet_now preview {sha}',make_tar(files))

    def make_docich_bundle(self, text='v2\n'):
        candidate=self.base/'candidate'
        subprocess.run(['git','clone','-q',self.doc,candidate],check=True)
        subprocess.run(['git','-C',candidate,'config','user.email','t@example.com'],check=True)
        subprocess.run(['git','-C',candidate,'config','user.name','T'],check=True)
        (candidate/'app.py').write_text(text)
        subprocess.run(['git','-C',candidate,'add','app.py'],check=True)
        subprocess.run(['git','-C',candidate,'commit','-qm','candidate'],check=True)
        sha=subprocess.check_output(['git','-C',candidate,'rev-parse','HEAD'],text=True).strip()
        bundle=self.base/'candidate.bundle'
        subprocess.run(['git','-C',candidate,'bundle','create',bundle,'HEAD'],check=True)
        return sha,bundle.read_bytes()

    def test_docich_production_uses_git_bundle_and_preserves_git_head(self):
        sha,bundle=self.make_docich_bundle()
        p=self.call(f'upload docich production {sha}',bundle)
        self.assertEqual(p.returncode,0,p.stderr.decode())
        p=self.call(f'bootstrap docich production {sha}')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        p=self.call(f'deploy docich production {sha}')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        head=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        self.assertEqual(head,sha)
        self.assertEqual((self.doc/'app.py').read_text(),'v2\n')
        status=subprocess.check_output(['git','-C',self.doc,'status','--porcelain','--untracked-files=no','--ignore-submodules=all'],text=True)
        self.assertEqual(status,'')

    def test_docich_git_deploy_refuses_tracked_drift(self):
        sha,bundle=self.make_docich_bundle()
        self.assertEqual(self.call(f'upload docich production {sha}',bundle).returncode,0)
        (self.doc/'app.py').write_text('manual-hotfix\n')
        p=self.call(f'deploy docich production {sha}')
        self.assertNotEqual(p.returncode,0)
        self.assertEqual((self.doc/'app.py').read_text(),'manual-hotfix\n')

    def test_upload_rejects_symlink(self):
        p=self.call('upload soviet_now preview '+'a'*40,make_tar({'app.py':'x'},('bad','/etc/passwd')))
        self.assertNotEqual(p.returncode,0)

    def test_preview_deploy_and_exec(self):
        sha='a'*40
        self.assertEqual(self.upload(sha).returncode,0)
        p=self.call(f'deploy soviet_now preview {sha}')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        p=self.call(f'exec soviet_now preview {sha}',b'printf hello')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        self.assertIn(b'hello',p.stdout)

    def test_production_deploy_requires_bootstrap_and_detects_drift(self):
        sha1='a'*40; sha2='b'*40
        (self.prod/'app.py').write_text('old\n')
        self.assertEqual(self.upload(sha1,{'app.py':'v1\n'}).returncode,0)
        p=self.call(f'deploy soviet_now production {sha1}')
        self.assertNotEqual(p.returncode,0)
        self.assertEqual(self.call(f'bootstrap soviet_now production {sha1}').returncode,0)
        self.assertEqual(self.call(f'deploy soviet_now production {sha1}').returncode,0)
        self.assertEqual((self.prod/'app.py').read_text(),'v1\n')
        self.assertEqual(self.upload(sha2,{'app.py':'v2\n'}).returncode,0)
        (self.prod/'app.py').write_text('manual-hotfix\n')
        p=self.call(f'deploy soviet_now production {sha2}')
        self.assertNotEqual(p.returncode,0)
        self.assertEqual((self.prod/'app.py').read_text(),'manual-hotfix\n')

    def test_production_exec_hides_command_output(self):
        sha='a'*40
        (self.prod/'app.py').write_text('old\n')
        self.assertEqual(self.upload(sha).returncode,0)
        self.assertEqual(self.call(f'bootstrap soviet_now production {sha}').returncode,0)
        p=self.call(f'exec soviet_now production {sha}',b'printf SUPERSECRET')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        self.assertNotIn(b'SUPERSECRET',p.stdout)
        logs=list((self.state/'logs').glob('*.log'))
        self.assertTrue(logs)
        self.assertIn('SUPERSECRET',logs[-1].read_text())

class WorkflowPolicyTests(unittest.TestCase):
    def test_owner_and_environment_gates_present(self):
        text=WF.read_text()
        self.assertIn("environment: vm-operations",text)
        self.assertIn("github.actor_id == 9018513",text)
        self.assertIn("github.triggering_actor == 'azumag'",text)
        self.assertIn("github.ref_protected == true",text)
        self.assertIn("persist-credentials: false",text)
        self.assertIn("submodules: false",text)
        self.assertNotIn('pull_request_target:',text)
    def test_ssh_is_pinned_and_no_interactive_shell(self):
        text=WF.read_text()
        self.assertIn('StrictHostKeyChecking=yes',text)
        self.assertIn('ForwardAgent=no',text)
        self.assertIn('ClearAllForwardings=yes',text)
        self.assertIn('VM_SSH_KNOWN_HOSTS',text)
        self.assertNotIn('ssh-keyscan',text)
        self.assertIn('bundle create',text)

if __name__=='__main__': unittest.main()
