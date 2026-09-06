import importlib.util
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "ops/hotfixes/apply_founding_policy_20260907.py"

def load():
    if not SCRIPT.is_file():
        raise AssertionError("reviewed policy installer is missing")
    spec = importlib.util.spec_from_file_location("policy_hotfix", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def h(data):
    return hashlib.sha256(data).hexdigest()

class PolicyHotfixTests(unittest.TestCase):
    def fixture(self, root):
        (root / "prompts").mkdir()
        (root / "prompts/a.md").write_bytes(b"old a")
        (root / "prompts/a.md").chmod(0o640)
        return {"prompts/a.md":{"old":h(b"old a"),"new":h(b"new a"),"mode":0o640},
                "prompts/b.md":{"old":None,"new":h(b"new b"),"mode":0o644}}, {"prompts/a.md":b"new a","prompts/b.md":b"new b"}

    def test_apply_and_idempotent_preserve_modes_and_backups(self):
        m=load()
        with tempfile.TemporaryDirectory() as d:
            r=Path(d); specs,data=self.fixture(r)
            self.assertEqual(m.apply(r,specs,data),"applied")
            self.assertEqual((r/"prompts/a.md").read_bytes(),b"new a")
            self.assertEqual((r/"prompts/a.md").stat().st_mode & 0o777,0o640)
            self.assertEqual(m.apply(r,specs,data),"already_applied")
            self.assertTrue(any(p.read_bytes()==b"old a" for p in (r/"tmp/policy-backups").rglob("a.md")))

    def test_any_drift_refuses_whole_transaction(self):
        m=load()
        with tempfile.TemporaryDirectory() as d:
            r=Path(d); specs,data=self.fixture(r); (r/"prompts/b.md").write_bytes(b"concurrent")
            with self.assertRaises(ValueError): m.apply(r,specs,data)
            self.assertEqual((r/"prompts/a.md").read_bytes(),b"old a")
            self.assertEqual((r/"prompts/b.md").read_bytes(),b"concurrent")

    def test_wrong_source_hash_refuses(self):
        m=load()
        with tempfile.TemporaryDirectory() as d:
            r=Path(d); specs,data=self.fixture(r); data["prompts/b.md"]=b"bad"
            with self.assertRaises(ValueError): m.apply(r,specs,data)
            self.assertEqual((r/"prompts/a.md").read_bytes(),b"old a")

    def test_symlink_parent_refuses(self):
        m=load()
        with tempfile.TemporaryDirectory() as d:
            r=Path(d); (r/"other").mkdir(); (r/"prompts").symlink_to(r/"other",target_is_directory=True)
            with self.assertRaises(ValueError):
                m.apply(r,{"prompts/a.md":{"old":None,"new":h(b"a"),"mode":0o644}},{"prompts/a.md":b"a"})

    def test_partial_replace_failure_rolls_back(self):
        m=load()
        with tempfile.TemporaryDirectory() as d:
            r=Path(d); specs,data=self.fixture(r); original=m.atomic_write
            def fail(path,raw,mode):
                if path==r/"prompts/b.md": raise OSError("injected failure")
                original(path,raw,mode)
            with patch.object(m,"atomic_write",side_effect=fail):
                with self.assertRaises(OSError): m.apply(r,specs,data)
            self.assertEqual((r/"prompts/a.md").read_bytes(),b"old a")
            self.assertFalse((r/"prompts/b.md").exists())

    def test_quiescence_holds_runtime_spawn_guard_and_rechecks_idle(self):
        m=load()
        with tempfile.TemporaryDirectory() as d:
            r=Path(d); (r/"tmp/state").mkdir(parents=True)
            (r/"tmp/state/improve_state.json").write_text('{"status":"idle"}')
            with m.improvement_quiescence(r):
                self.assertTrue((r/"tmp/state/.improve_spawn.lock").is_dir())
                with self.assertRaisesRegex(ValueError, "spawn is in progress"):
                    with m.improvement_quiescence(r): pass
            self.assertFalse((r/"tmp/state/.improve_spawn.lock").exists())
            (r/"tmp/improve.lock").touch()
            with self.assertRaisesRegex(ValueError, "not idle"):
                with m.improvement_quiescence(r): pass
            self.assertFalse((r/"tmp/state/.improve_spawn.lock").exists())
        self.assertIn("with improvement_quiescence(ROOT):", SCRIPT.read_text())


if __name__ == "__main__": unittest.main()
