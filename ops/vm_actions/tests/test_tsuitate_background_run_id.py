import datetime as dt
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load_background():
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    sys.modules.pop("docich.tsuitate_background_start", None)
    return importlib.import_module("docich.tsuitate_background_start")


class TsuitateBackgroundRunIdTests(unittest.TestCase):
    def test_separate_invocation_times_do_not_alias_within_same_hour(self):
        module = load_background()
        first = dt.datetime(2026, 11, 2, 15, 1, 2, 3, tzinfo=dt.timezone.utc)
        second = first.replace(microsecond=4)
        self.assertNotEqual(module.background_run_id(first), module.background_run_id(second))
        self.assertEqual(module.background_run_id(first), "background-20261102150102000003")


if __name__ == "__main__":
    unittest.main()
