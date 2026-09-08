import os
import re
import unittest
from pathlib import Path

from ops.vm_actions.runtime_registry import (
    DIAG_WINDOW_SEC,
    KNOWN_LANES,
    MAX_JSON_BYTES,
    QUEUE_STALE_SEC,
    WORKERS,
    default_pid_relpath,
    required_workers,
    worker_entry,
    worker_names,
)

ROOT = Path(__file__).resolve().parents[3]


def _soviet_start_all():
    override = os.environ.get("SOVIET_START_ALL")
    if override:
        return Path(override)
    return ROOT / "games" / "soviet_now" / "start_all.sh"


class RegistrySchemaTests(unittest.TestCase):
    def test_worker_entries_have_valid_shape(self):
        self.assertGreater(len(WORKERS), 10)
        seen = set()
        for entry in WORKERS:
            self.assertEqual(len(entry), 5)
            name, required, category, pid_rel, kind = entry
            self.assertIsInstance(name, str)
            self.assertNotIn(name, seen)
            seen.add(name)
            self.assertIsInstance(required, bool)
            self.assertIsInstance(category, str)
            self.assertTrue(category)
            self.assertIn(kind, ("pid", "owner"))
            if pid_rel is not None:
                self.assertFalse(pid_rel.startswith("/"))
                self.assertNotIn("..", pid_rel)

    def test_required_workers_are_a_conservative_core(self):
        required = set(required_workers())
        self.assertIn("radio_worker", required)
        self.assertIn("chat_worker", required)
        self.assertNotIn("youtube_worker", required)
        self.assertNotIn("kick_worker", required)
        self.assertNotIn("direct_stream", required)
        self.assertNotIn("soviet_watchdog", required)

    def test_helpers_are_consistent(self):
        self.assertEqual(set(worker_names()), {e[0] for e in WORKERS})
        self.assertEqual(worker_entry("radio_worker")[0], "radio_worker")
        self.assertIsNone(worker_entry("no_such_worker"))
        self.assertEqual(default_pid_relpath("radio_worker"), "tmp/state/radio_worker.pid")

    def test_bounds_are_sane(self):
        self.assertEqual(DIAG_WINDOW_SEC, 900)
        self.assertEqual(QUEUE_STALE_SEC, 900)
        self.assertLessEqual(MAX_JSON_BYTES, 65536)
        self.assertIn("radio", KNOWN_LANES)
        self.assertIn("comment", KNOWN_LANES)


def _parse_start_all_workers(text):
    """Extract supervised worker names from start_all.sh without executing it."""
    names = []
    block = re.search(r"declare -a WORKER_NAMES=\((.*?)\)", text, re.S)
    if block:
        names += re.findall(r'"([^"]+)"', block.group(1))
    for extra in re.findall(r"WORKER_NAMES\+=\((.*?)\)", text, re.S):
        names += re.findall(r'"([^"]+)"', extra)
    prepend = re.search(r'WORKER_NAMES=\("([^"]+)" "\$\{WORKER_NAMES\[@\]\}"\)', text)
    if prepend:
        names.append(prepend.group(1))
    return names


def _parse_start_all_table(text, func):
    found = {}
    block = re.search(rf"{func}\(\) \{{(.*?)\n\}}", text, re.S)
    if not block:
        return found
    for name, value in re.findall(r"(\S+)\) echo [\"']([^\"']*)[\"']", block.group(1)):
        if name != "*":
            found[name] = value
    return found


class RegistryDriftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = _soviet_start_all()
        if not path.is_file():
            raise unittest.SkipTest(f"soviet_now start_all.sh not available at {path}")
        cls.text = path.read_text(encoding="utf-8")

    def test_every_supervised_worker_is_registered(self):
        missing = [n for n in _parse_start_all_workers(self.text) if n not in set(worker_names())]
        self.assertEqual(missing, [])

    def test_every_pidfile_mapping_is_registered(self):
        table = _parse_start_all_table(self.text, "_pidfile_for_worker")
        self.assertGreater(len(table), 10)
        registry = {e[0]: (e[3] or default_pid_relpath(e[0])) for e in WORKERS}
        for name, pidfile in table.items():
            if not pidfile or "${" in pidfile:
                continue
            self.assertIn(name, registry, f"worker {name} missing from registry")
            self.assertEqual(registry[name], pidfile, f"pid file drift for {name}")

    def test_every_process_pattern_is_registered(self):
        table = _parse_start_all_table(self.text, "_pattern_for_worker")
        self.assertGreater(len(table), 10)
        missing = [n for n in table if n not in set(worker_names())]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
