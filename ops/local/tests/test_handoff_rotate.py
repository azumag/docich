import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "handoff_rotate.py"
spec = importlib.util.spec_from_file_location("handoff_rotate", SCRIPT)
rotate_tool = importlib.util.module_from_spec(spec)
sys.modules["handoff_rotate"] = rotate_tool
spec.loader.exec_module(rotate_tool)

PREAMBLE = "# docich handoff (private)\n\n> 再開時はここから読む。\n\n"


def section(index, stamp):
    return f"## {stamp} — 作業{index}\n\n- 本文{index}\n"


def document(count=8):
    stamps = [f"2026-10-{14 - (index // 4):02d} {23 - (index % 4)}:0{index % 6}"
              for index in range(count)]
    return (PREAMBLE + "".join(section(index, stamp) for index, stamp in enumerate(stamps))).encode()


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="handoff-rotate-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.handoff = self.root / "handoff.md"
        self.archive = self.root / "handoff-archive"
        # Never inherit a real shared-source configuration into these tests.
        self.env = {key: value for key, value in os.environ.items()
                    if key != rotate_tool.HANDOFF_ENV}

    def run_cli(self, *arguments, env=None, handoff=True, archive=True):
        command = [sys.executable, str(SCRIPT), arguments[0]]
        if handoff:
            command += ["--handoff", str(self.handoff)]
        if archive:
            command += ["--archive-dir", str(self.archive)]
        command += list(arguments[1:])
        return subprocess.run(command, capture_output=True, text=True, cwd=str(self.root),
                              env=self.env if env is None else env)


class DocumentParsingTests(unittest.TestCase):
    def test_preamble_and_sections_split(self):
        text = PREAMBLE + section(0, "2026-10-14 01:00") + section(1, "2026-10-13 02:00")
        preamble, sections = rotate_tool.split_document(text)
        self.assertEqual("".join(preamble), PREAMBLE)
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].instant, rotate_tool.datetime(2026, 10, 14, 1, 0))
        self.assertEqual(sections[1].instant, rotate_tool.datetime(2026, 10, 13, 2, 0))

    def test_headings_inside_fences_are_not_sections(self):
        text = PREAMBLE + "```md\n## not a section\n```\n" + section(0, "2026-10-14")
        preamble, sections = rotate_tool.split_document(text)
        self.assertEqual(len(sections), 1)
        self.assertIn("## not a section", "".join(preamble))

    def test_undated_heading_has_no_instant(self):
        _, sections = rotate_tool.split_document("## 続き: 見出しに日付なし\n\n- 本文\n")
        self.assertEqual(len(sections), 1)
        self.assertIsNone(sections[0].instant)

    def test_window_is_a_bounded_prefix(self):
        _, sections = rotate_tool.split_document(document(6).decode())
        self.assertEqual(rotate_tool.select_prefix(sections, 2, 0), 2)
        self.assertEqual(rotate_tool.select_prefix(sections, 99, 0), 6)
        self.assertEqual(rotate_tool.select_prefix([], 5, 0), 0)

    def test_time_expansion_is_opt_in_and_still_a_prefix(self):
        _, sections = rotate_tool.split_document(document(10).decode())
        self.assertEqual(rotate_tool.select_prefix(sections, 2, 0), 2)
        expanded = rotate_tool.select_prefix(sections, 2, 48)
        self.assertGreaterEqual(expanded, 2)
        self.assertLessEqual(expanded, 10)


class RotationTests(CliTestCase):
    def test_rotate_is_lossless_bounded_and_idempotent(self):
        original = document(8)
        self.handoff.write_bytes(original)
        self.assertEqual(self.run_cli("check", "--keep", "3").returncode, 1)

        result = self.run_cli("rotate", "--keep", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("作業", result.stdout + result.stderr)

        kept = self.handoff.read_bytes()
        self.assertTrue(kept.startswith(PREAMBLE.encode()))
        _, kept_sections = rotate_tool.split_document(kept.decode())
        self.assertEqual(len(kept_sections), 3)
        self.assertEqual(kept_sections[0].text,
                         rotate_tool.split_document(original.decode())[1][0].text)

        archived = []
        for path in sorted(self.archive.glob("*.md")):
            archived.extend(rotate_tool.parse_archive(path.read_bytes()))
        self.assertEqual(sorted(item.digest for item in kept_sections + archived),
                         sorted(item.digest
                                for item in rotate_tool.split_document(original.decode())[1]))

        self.assertEqual(self.run_cli("check", "--keep", "3").returncode, 0)
        again = self.run_cli("rotate", "--keep", "3")
        self.assertEqual(again.returncode, 0)
        self.assertIn("nothing to archive", again.stdout)
        self.assertEqual(self.handoff.read_bytes(), kept)
        self.assertEqual(self.run_cli("verify", "--keep", "3").returncode, 0)

    def test_archive_is_routed_by_section_month(self):
        self.handoff.write_bytes(PREAMBLE.encode() + section(0, "2026-10-14").encode()
                                 + section(1, "2026-10-02").encode() + section(2, "2026-09-30").encode())
        self.assertEqual(self.run_cli("rotate", "--keep", "1").returncode, 0)
        self.assertEqual(sorted(path.name for path in self.archive.glob("*.md")),
                         ["2026-09.md", "2026-10.md"])
        index = [json.loads(line) for line in
                 (self.archive / "INDEX.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(index), 1)
        self.assertEqual(len(index[0]["sections"]), 2)
        self.assertEqual({record["archive"] for record in index[0]["sections"]},
                         {"2026-09.md", "2026-10.md"})

    def test_identical_sections_are_both_archived(self):
        self.handoff.write_bytes(PREAMBLE.encode() + section(0, "2026-10-14").encode()
                                 + section(0, "2026-10-14").encode())
        self.assertEqual(self.run_cli("rotate", "--keep", "1").returncode, 0)
        archived = rotate_tool.parse_archive((self.archive / "2026-10.md").read_bytes())
        self.assertEqual(len(archived), 1)
        self.assertEqual(self.run_cli("verify").returncode, 0)

    def test_concurrent_change_aborts_without_rewriting_handoff(self):
        original = document(8)
        self.handoff.write_bytes(original)
        changed = original + section(99, "2026-10-14 06:00").encode()
        arguments = rotate_tool.build_parser().parse_args(
            ["rotate", "--handoff", str(self.handoff), "--archive-dir", str(self.archive), "--keep", "3"])
        with mock.patch.object(rotate_tool, "read_source", side_effect=[original, changed]):
            with self.assertRaises(rotate_tool.RotationError):
                rotate_tool.rotate(arguments)
        self.assertEqual(self.handoff.read_bytes(), original)
        self.assertEqual(sorted(path.name for path in self.archive.glob("*.md")), [])

    def test_verify_detects_a_missing_archived_section(self):
        self.handoff.write_bytes(document(6))
        self.assertEqual(self.run_cli("rotate", "--keep", "2").returncode, 0)
        self.assertEqual(self.run_cli("verify").returncode, 0)
        archive = sorted(self.archive.glob("*.md"))[0]
        _, sections = rotate_tool.split_document(archive.read_text(encoding="utf-8"))
        archive.write_bytes("".join(item.text for item in sections[:1] + sections[2:]).encode())
        self.assertEqual(self.run_cli("verify").returncode, 1)

    def test_dry_run_writes_nothing(self):
        self.handoff.write_bytes(document(6))
        result = self.run_cli("rotate", "--keep", "2", "--dry-run")
        self.assertEqual(result.returncode, 0)
        self.assertIn("dry-run", result.stdout)
        self.assertFalse(self.archive.exists())


class ArchiveSafetyTests(CliTestCase):
    def test_archive_inside_a_work_tree_requires_an_ignore_rule(self):
        subprocess.run(["git", "init", "-q"], cwd=str(self.root), env=self.env,
                       capture_output=True, check=True)
        original = document(4)
        self.handoff.write_bytes(original)
        result = self.run_cli("rotate", "--keep", "2")
        self.assertEqual(result.returncode, 1)
        self.assertIn("committable", result.stderr)
        self.assertEqual(self.handoff.read_bytes(), original)
        self.assertFalse(self.archive.exists())

    def test_ignored_archive_inside_a_work_tree_is_allowed(self):
        subprocess.run(["git", "init", "-q"], cwd=str(self.root), env=self.env,
                       capture_output=True, check=True)
        (self.root / ".gitignore").write_text("/handoff-archive/\n", encoding="utf-8")
        self.handoff.write_bytes(document(4))
        result = self.run_cli("rotate", "--keep", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_cli("verify").returncode, 0)


class SourceResolutionTests(CliTestCase):
    def test_environment_source_is_used_when_no_flag_is_given(self):
        self.handoff.write_bytes(document(3))
        env = dict(self.env, **{rotate_tool.HANDOFF_ENV: str(self.handoff)})
        result = self.run_cli("check", "--keep", "5", env=env, handoff=False, archive=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unconfigured_source_never_falls_back_to_a_checkout(self):
        self.handoff.write_bytes(document(3))
        result = self.run_cli("check", handoff=False, archive=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not configured", result.stderr)

    def test_relative_or_foreign_paths_are_refused(self):
        for argument in ("handoff.md", str(self.root / "other.md")):
            with self.subTest(argument=argument):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "check", "--handoff", argument],
                    capture_output=True, text=True, cwd=str(self.root), env=self.env)
                self.assertEqual(result.returncode, 1)
                self.assertIn("absolute handoff.md path required", result.stderr)

    def test_symlinked_handoff_is_refused(self):
        self.handoff.write_bytes(document(3))
        alias = self.root / "alias"
        alias.mkdir()
        (alias / "handoff.md").symlink_to(self.handoff)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "check", "--handoff", str(alias / "handoff.md")],
            capture_output=True, text=True, cwd=str(self.root), env=self.env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("symlinked", result.stderr)

    def test_missing_handoff_fails_without_creating_it(self):
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing", result.stderr)
        self.assertFalse(self.handoff.exists())


if __name__ == "__main__":
    unittest.main()
