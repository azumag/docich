import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "profile_cpu.py"
SPEC = importlib.util.spec_from_file_location("profile_cpu", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class OtherCommLabelTests(unittest.TestCase):
    def test_known_mappings(self):
        cases = {
            "opencode": "opencode",
            "radio_worker": "worker:radio_worker",
            "chrome_crashpad_handler": "browser",
            "crashpad_handler": "browser",
        }
        for comm, expected in cases.items():
            with self.subTest(comm=comm):
                # These are executable/comm classification mappings.  Exercise
                # the public classifier rather than the residual `other`
                # sanitizer, which intentionally preserves safe unknown comms.
                self.assertEqual(mod.classify([comm], comm), expected)
                self.assertEqual(mod.classify(None, comm), expected)


if __name__ == "__main__":
    unittest.main()
