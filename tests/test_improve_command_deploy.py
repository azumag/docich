import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'ops/hotfixes/apply_improve_command_20260907.py'

def load():
    if not SCRIPT.is_file():raise AssertionError('reviewed installer is missing')
    s=importlib.util.spec_from_file_location('deploy_command',SCRIPT)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

class CommandDeployTests(unittest.TestCase):
    def root(self,p):
        (p/'tmp/state').mkdir(parents=True)
        (p/'tmp/state/improve_state.json').write_text('{"status":"idle","pid":0}')
        (p/'tmp/state/improve_daemon.paused').write_text('tmp/state/step5-founding-20260906T201809Z')

    def test_fixed_allowlist_dependency_order(self):
        m=load();doc=m.manifest()
        self.assertEqual(doc['source_sha'],'fea026ddfb285eee603acd5f330f286624754c8d')
        self.assertEqual(list(doc['files']),['strategy/improve_command.py','strategy/ai.sh','eloop_improve.sh'])
        self.assertIsNone(doc['files']['strategy/improve_command.py']['old'])
        self.assertEqual(doc['files']['strategy/ai.sh']['old'],'d4f7f508791c80410118e15d278f33299783d6f5f5a77179cb10f164cd883f58')

    def test_missing_or_foreign_pause_refuses(self):
        m=load()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d).resolve();self.root(p)
            m.preflight(p)
            (p/'tmp/state/improve_daemon.paused').write_text('another-job')
            with self.assertRaises(ValueError):m.preflight(p)
            (p/'tmp/state/improve_daemon.paused').unlink()
            with self.assertRaises(ValueError):m.preflight(p)

    def test_live_pid_and_bad_state_refuse(self):
        m=load()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d).resolve();self.root(p)
            for state in ({'status':'running','pid':123},{'status':'idle','pid':123},{'status':'unknown','pid':0}):
                (p/'tmp/state/improve_state.json').write_text(json.dumps(state))
                with self.assertRaises(ValueError):m.preflight(p)

    def test_source_and_content_are_validated_before_write(self):
        m=load();doc=m.manifest()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d).resolve();self.root(p)
            with self.assertRaises(ValueError):m.validate_payload(doc,{k:b'wrong' for k in doc['files']})
            doc['files']['strategy.py']={'old':None,'new':hashlib.sha256(b'a').hexdigest(),'mode':420}
            with self.assertRaises(ValueError):m.validate_payload(doc,{k:b'a' for k in doc['files']})

    def test_pause_symlink_refuses(self):
        m=load()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d).resolve();self.root(p)
            f=p/'tmp/state/improve_daemon.paused';value=f.read_text();f.unlink()
            target=p/'other';target.write_text(value);f.symlink_to(target)
            with self.assertRaises(ValueError):m.preflight(p)

if __name__=='__main__':unittest.main()
