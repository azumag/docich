"""Pinned real Soren runtime vs real policy installer (CI supplies source)."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = os.environ.get('SOREN_SPAWN_SOURCE')
SCRIPT = ROOT / 'ops/hotfixes/apply_founding_policy_20260907.py'

@unittest.skipUnless(SOURCE, 'set SOREN_SPAWN_SOURCE to the reviewed runtime checkout')
class PolicySpawnInteropTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('installer',SCRIPT)
        self.m=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.m)
        self.src=Path(SOURCE).resolve()
        self.m.require_runtime_protocol(self.src)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();(self.root/'tmp/state').mkdir(parents=True)
        (self.root/'tmp/state/improve_state.json').write_text('{"status":"idle"}')
        self.guard=self.root/self.m.SPAWN_GUARD

    def spawn(self):
        return subprocess.run(['bash','-c','source "$1/strategy/improve.sh"; _acquire_spawn_lock','test',str(self.src)],
            env={**os.environ,'ELOOP_LIB_DIR':str(self.src),'IMPROVE_SPAWN_LOCK_DIR':str(self.guard)},capture_output=True,timeout=10)

    def test_real_runtime_cannot_steal_at_any_guard_age(self):
        for age in (0,100,86400):
            with self.subTest(age=age), self.m.improvement_quiescence(self.root):
                ts=time.time()-age;os.utime(self.guard,(ts,ts))
                self.assertEqual(self.spawn().returncode,1)
                self.assertEqual((self.guard/'owner').read_text().strip(),str(os.getpid()))

    def test_runtime_recovers_after_installer_is_killed(self):
        code=('import importlib.util,pathlib,sys,time; '
              's=importlib.util.spec_from_file_location("m",sys.argv[1]); '
              'm=importlib.util.module_from_spec(s);s.loader.exec_module(m); '
              'c=m.improvement_quiescence(pathlib.Path(sys.argv[2]));c.__enter__(); '
              'print("locked",flush=True);time.sleep(30)')
        proc=subprocess.Popen([sys.executable,'-c',code,str(SCRIPT),str(self.root)],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(),'locked')
            self.assertEqual(self.spawn().returncode,1)
            proc.kill();proc.wait(timeout=5)
            self.assertEqual(self.spawn().returncode,0)
        finally:
            if proc.poll() is None:proc.kill();proc.wait(timeout=5)
            proc.stdout.close()

    def test_installer_does_not_steal_an_existing_spawn_guard(self):
        self.guard.mkdir();(self.guard/'owner').write_text(str(os.getpid()))
        with self.assertRaises(ValueError):
            with self.m.improvement_quiescence(self.root):pass
        self.assertEqual((self.guard/'owner').read_text(),str(os.getpid()))

if __name__=='__main__':unittest.main()
