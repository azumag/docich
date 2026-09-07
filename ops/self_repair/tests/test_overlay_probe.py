import importlib.util
from pathlib import Path
import tempfile
import unittest

class OverlayProbeTests(unittest.TestCase):
    def module(self):
        p=Path(__file__).resolve().parents[1]/'overlay_probe.py'
        s=importlib.util.spec_from_file_location('probe',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
    def test_syntax_fault_is_reproduced(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'generate_event_overlay.py').write_text('def broken(:')
            self.assertEqual(self.module().health(root,wait_seconds=0),1)
    def test_missing_source_or_stale_output_is_not_repair_authorization(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            self.assertEqual(self.module().health(root,wait_seconds=0),2)
            (root/'generate_event_overlay.py').write_text('pass')
            self.assertEqual(self.module().health(root,wait_seconds=0),2)
    def test_fresh_output_and_valid_source_healthy(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'generate_event_overlay.py').write_text('pass')
            out=root/'tmp/state/event_overlay.html';out.parent.mkdir(parents=True)
            out.write_text('<html><section id="work-indicator"></section><div id="toasts"></div></html>')
            self.assertEqual(self.module().health(root,wait_seconds=0),0)

    def test_old_output_newer_than_source_is_still_unhealthy(self):
        import os,time
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'generate_event_overlay.py';source.write_text('pass')
            out=root/'tmp/state/event_overlay.html';out.parent.mkdir(parents=True)
            out.write_text('<html><section id="work-indicator"></section><div id="toasts"></div></html>')
            now=time.time();os.utime(source,(now-200,now-200));os.utime(out,(now-100,now-100))
            self.assertEqual(self.module().health(root,wait_seconds=0),2)
