import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

class BoundedProcessTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('bounded',Path(__file__).resolve().parents[1]/'bounded_process.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
    def test_large_child_file_is_not_limited(self):
        with tempfile.TemporaryDirectory() as d:
            rc,out=self.module().run_bounded([sys.executable,'-c',"open('db','wb').write(b'x'*2097152);print('ok')"],cwd=d,limit=1024)
            self.assertEqual((rc,out),(0,'ok\n'))
            self.assertEqual((Path(d)/'db').stat().st_size,2097152)
    def test_output_flood_is_bounded(self):
        with self.assertRaises(ValueError):
            self.module().run_bounded([sys.executable,'-c',"print('x'*4096)"],limit=1024)
    def test_timeout_kills_child_group(self):
        with self.assertRaises(TimeoutError):
            self.module().run_bounded([sys.executable,'-c','import time;time.sleep(5)'],timeout=.1)
    def test_parent_exit_with_inherited_pipe_does_not_hang(self):
        rc,out=self.module().run_bounded([sys.executable,'-c',"import subprocess;subprocess.Popen(['sleep','10']);print('done')"],timeout=1)
        self.assertEqual((rc,out),(0,'done\n'))
