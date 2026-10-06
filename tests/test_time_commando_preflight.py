"""Synthetic archives only: no commercial content or emulator execution."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich import time_commando_preflight as preflight  # noqa: E402

CUE = ('FILE "GAME.GOG" BINARY\n'
       ' TRACK 01 MODE1/2352\n INDEX 01 00:00:00\n'
       ' TRACK 02 AUDIO\n INDEX 01 00:00:01\n'
       ' TRACK 03 AUDIO\n INDEX 01 00:00:02\n')


class TestTimeCommandoPreflight(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private-content.zip"

    def make(self, *, changes=None, descriptor="GAME.CUE", prefix="timco/"):
        files = {prefix + name: b"synthetic only" for name in (
            "TIMECO.EXE", *preflight.COMPANIONS,
        )}
        files[prefix + descriptor] = CUE.encode()
        files[prefix + "GAME.GOG"] = b"X" * (2352 * 3)
        files.update(changes or {})
        with zipfile.ZipFile(self.path, "w") as archive:
            for name, content in files.items():
                if content is not None:
                    archive.writestr(name, content)
        return self.path

    def inspect(self, **kwargs):
        return preflight.inspect_archive(self.make(**kwargs))

    def blocked(self, code, **kwargs):
        result = self.inspect(**kwargs)
        self.assertEqual(result["archive_structure"], "blocked")
        self.assertEqual(result["blockers"], [code])
        self.assertEqual(result["runtime_acceptance"], "not_run")
        self.assertEqual(result["rights_acceptance"], "not_assessed")
        return result

    def test_complete_metadata_never_claims_runtime_or_rights(self):
        result = self.inspect()
        self.assertEqual(result["archive_structure"], "metadata_checks_passed")
        self.assertEqual(result["entry_location"], "nested_directory")
        self.assertEqual(result["runtime_acceptance"], "not_run")
        self.assertEqual(result["rights_acceptance"], "not_assessed")
        self.assertEqual(result["cd"]["track_modes"], ["MODE1/2352", "AUDIO", "AUDIO"])

    def test_root_layout_and_dosz_supported(self):
        self.path = self.path.with_suffix(".dosz")
        result = self.inspect(prefix="")
        self.assertEqual(result["archive_structure"], "metadata_checks_passed")
        self.assertEqual(result["entry_location"], "archive_root")

    def test_limited_descriptor_materials_report_missing_image(self):
        self.blocked("cd_reference_missing_or_case_mismatch", descriptor="GAME.DAT",
                     changes={"timco/GAME.GOG": None})

    def test_dat_with_image_still_needs_explicit_cue(self):
        result = self.blocked("explicit_cue_content_required_before_runtime_test",
                              descriptor="GAME.DAT")
        self.assertIn("dat_extension_is_not_verified_as_a_pure_cd_image", result["warnings"])

    def test_case_collision_rejected(self):
        self.blocked("duplicate_or_case_collision", changes={"timco/timeco.exe": b"x"})

    def test_implicit_directories_cannot_conflict_with_files_or_case(self):
        self.blocked("file_directory_collision", changes={"timco": b"x"})
        self.blocked("file_directory_collision", changes={"timco/TIMECO.EXE/child": b"x"})
        self.blocked("duplicate_or_case_collision", changes={"TIMCO/": b""})
        self.blocked("duplicate_or_case_collision", changes={"timco/DRIVERS/a": b"x", "timco/drivers/b": b"x"})

    def test_matching_explicit_directory_is_allowed(self):
        result = self.inspect(changes={"timco/": b""})
        self.assertEqual(result["archive_structure"], "metadata_checks_passed")

    def test_original_and_effective_zip_names_must_match_and_both_be_safe(self):
        # Python versions supporting Unicode Path extra fields can replace
        # filename while retaining orig_filename. Check both namespaces.
        self.make()
        real_infolist = zipfile.ZipFile.infolist
        for effective, code in (("../TIMECO.EXE", "unsafe_member_name"),
                                ("other/TIMECO.EXE", "member_name_metadata_mismatch")):
            with self.subTest(effective=effective):
                def renamed(archive):
                    members = real_infolist(archive)
                    members[0].filename = effective
                    return members
                with mock.patch.object(zipfile.ZipFile, "infolist", renamed), \
                        mock.patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("no read")):
                    result = preflight.inspect_archive(self.path)
                self.assertEqual(result["blockers"], [code])

    def test_unsafe_archive_names_rejected(self):
        for name in ("../secret", "/secret", "C:/secret", "timco\\secret",
                     "timco//secret", "./secret", "timco/secret\x00ignored"):
            with self.subTest(name=name):
                # ZipFile removes NUL suffixes when writing, so use a parsed
                # ZipInfo to verify the original untrusted filename directly.
                if "\x00" in name:
                    with self.assertRaises(preflight.InspectionError):
                        preflight._safe_name(name)
                else:
                    self.blocked("unsafe_member_name", changes={name: b"x"})

    def test_symlinks_are_metadata_blockers(self):
        self.make()
        info = zipfile.ZipInfo("timco/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.path, "a") as archive:
            archive.writestr(info, "../private")
        self.assertEqual(preflight.inspect_archive(self.path)["blockers"], ["non_regular_member"])

    def test_missing_and_ambiguous_entry(self):
        self.blocked("missing_or_ambiguous_timeco_exe", changes={"timco/TIMECO.EXE": None})
        self.blocked("missing_or_ambiguous_timeco_exe", changes={"other/TIMECO.EXE": b"x"})

    def test_empty_entry_and_missing_companion(self):
        self.blocked("empty_timeco_exe", changes={"timco/TIMECO.EXE": b""})
        for name in preflight.COMPANIONS:
            with self.subTest(name=name):
                self.blocked("missing_or_empty_companion", changes={"timco/" + name: None})

    def test_cd_reference_case_must_match_exactly(self):
        self.blocked("cd_reference_missing_or_case_mismatch", changes={
            "timco/GAME.GOG": None, "timco/game.gog": b"X" * 7056,
        })

    def test_cd_reference_cannot_escape_archive(self):
        self.blocked("unsafe_member_name", changes={
            "timco/GAME.CUE": CUE.replace('"GAME.GOG"', '"../GAME.GOG"').encode(),
        })

    def test_truncated_or_empty_cd_image(self):
        self.blocked("cd_image_truncated_before_index", changes={"timco/GAME.GOG": b"X" * 4704})
        self.blocked("empty_cd_image", changes={"timco/GAME.GOG": b""})

    def test_ambiguous_descriptor_and_autorun_require_review(self):
        self.blocked("ambiguous_cd_descriptor", changes={"timco/SECOND.cue": CUE.encode()})
        self.blocked("autoexec_batch_requires_separate_review", changes={"DOSBOX.BAT": b"bad"})

    def test_invalid_track_and_index_metadata(self):
        for old, new, code in (
            ("TRACK 02", "TRACK 04", "track_sequence"),
            ("00:00:01", "00:60:01", "invalid_index"),
            ("00:00:01", "00:00:75", "invalid_index"),
            ("00:00:02", "00:00:00", "decreasing_index"),
            ("INDEX 01 00:00:02", "", "missing_track_index01"),
            ("TRACK 01 MODE1/2352", "TRACK 01 AUDIO", "unexpected_track_layout"),
            ("TRACK 03 AUDIO\n INDEX 01 00:00:02", "", "unexpected_track_count"),
        ):
            with self.subTest(code=code):
                self.blocked(code, changes={"timco/GAME.CUE": CUE.replace(old, new).encode()})

    def test_descriptor_size_is_bounded(self):
        self.blocked("descriptor_size_limit", changes={
            "timco/GAME.CUE": b"X" * (preflight.MAX_DESCRIPTOR_BYTES + 1),
        })

    def test_descriptor_grammar_encoding_and_indexes_fail_closed(self):
        for content, code in (
            (CUE.replace('FILE "GAME.GOG" BINARY', 'FILE "GAME.GOG" WAVE'), "unsupported_descriptor_line"),
            (CUE.replace('FILE "GAME.GOG" BINARY\n', ''), "track_without_file"),
            (CUE.replace(' TRACK 01 MODE1/2352\n', ''), "index_without_track"),
            (CUE.replace('INDEX 01 00:00:01', 'INDEX 01 00:00:01\n INDEX 01 00:00:01'), "duplicate_or_reversed_index"),
            (CUE.replace('INDEX 01 00:00:01', 'INDEX 01 00:00:01\n INDEX 00 00:00:01'), "duplicate_or_reversed_index"),
            (CUE.replace('INDEX 01 00:00:01', 'INDEX 02 00:00:01'), "invalid_index"),
        ):
            with self.subTest(code=code):
                self.blocked(code, changes={"timco/GAME.CUE": content.encode()})
        self.blocked("descriptor_encoding", changes={"timco/GAME.CUE": b"\xff"})

    def test_orphan_file_and_zero_length_tracks_rejected(self):
        for content in (
            'FILE "UNUSED.GOG" BINARY\n' + CUE,
            CUE + 'FILE "UNUSED.GOG" BINARY\n',
        ):
            with self.subTest(content=content):
                self.blocked("file_without_track", changes={
                    "timco/GAME.CUE": content.encode(), "timco/UNUSED.GOG": b"x" * 7056,
                })
        self.blocked("non_increasing_track_index01", changes={
            "timco/GAME.CUE": CUE.replace("00:00:01", "00:00:00").replace("00:00:02", "00:00:00").encode(),
        })
        self.blocked("non_increasing_track_start", changes={
            "timco/GAME.CUE": CUE.replace("INDEX 01 00:00:01",
                                          "INDEX 00 00:00:00\n INDEX 01 00:00:01").encode(),
        })
        self.blocked("duplicate_cd_reference", changes={
            "timco/GAME.CUE": CUE.replace(' TRACK 02 AUDIO', ' FILE "GAME.GOG" BINARY\n TRACK 02 AUDIO').encode(),
        })

    def test_distinct_image_files_can_each_start_at_index_zero(self):
        cue = ('FILE "GAME.GOG" BINARY\n TRACK 01 MODE1/2352\n INDEX 01 00:00:00\n'
               'FILE "AUDIO1.BIN" BINARY\n TRACK 02 AUDIO\n INDEX 01 00:00:00\n'
               'FILE "AUDIO2.BIN" BINARY\n TRACK 03 AUDIO\n INDEX 01 00:00:00\n')
        result = self.inspect(changes={
            "timco/GAME.CUE": cue.encode(), "timco/AUDIO1.BIN": b"x" * 2352,
            "timco/AUDIO2.BIN": b"x" * 2352,
        })
        self.assertEqual(result["archive_structure"], "metadata_checks_passed")
        self.assertEqual(result["cd"]["referenced_images"], 3)

    def test_zero_pregap_within_same_track_is_valid(self):
        result = self.inspect(changes={
            "timco/GAME.CUE": CUE.replace('INDEX 01 00:00:01',
                                          'INDEX 00 00:00:01\n INDEX 01 00:00:01').encode(),
        })
        self.assertEqual(result["archive_structure"], "metadata_checks_passed")

    def test_missing_descriptor_and_unsupported_extension(self):
        self.blocked("missing_cd_descriptor", changes={"timco/GAME.CUE": None})
        self.make()
        other = self.path.with_suffix(".exe")
        self.path.rename(other)
        self.assertEqual(preflight.inspect_archive(other)["blockers"], ["unsupported_archive_extension"])

    def test_read_errors_are_sanitized(self):
        self.make()
        with mock.patch.object(zipfile.ZipFile, "open", side_effect=ValueError("/private/user/path")):
            result = preflight.inspect_archive(self.path)
        self.assertEqual(result["blockers"], ["archive_read_failed"])
        self.assertNotIn("/private/user/path", str(result))

    def test_encrypted_member_rejected_without_decompression(self):
        self.make()
        real_infolist = zipfile.ZipFile.infolist
        def encrypted(archive):
            members = real_infolist(archive)
            members[0].flag_bits |= 1
            return members
        with mock.patch.object(zipfile.ZipFile, "infolist", encrypted), \
                mock.patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("no read")):
            result = preflight.inspect_archive(self.path)
        self.assertEqual(result["blockers"], ["encrypted_member"])

    def test_acceptance_template_does_not_claim_any_runtime_success(self):
        path = ROOT / "config/examples/time-commando/acceptance.json"
        data = json.loads(path.read_text())
        self.assertEqual(data["overall_status"], "not_run")
        self.assertEqual(data["production_corner_acceptance"], "not_run")
        self.assertTrue(all(check["status"] == "not_run" and check["evidence"] == []
                            for check in data["checks"]))
        self.assertEqual(len({check["id"] for check in data["checks"]}), len(data["checks"]))
        self.assertTrue(all(value == "not_assessed" for value in data["rights"].values()))
        self.assertTrue(all(value is False for key, value in data["authorization"].items()
                            if key != "target_environment"))

    def test_metadata_limits_fail_before_descriptor_read(self):
        self.make()
        with mock.patch.object(preflight, "MAX_MEMBERS", 1), \
                mock.patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("must not read")):
            self.assertEqual(preflight.inspect_archive(self.path)["blockers"], ["member_limit"])
        with mock.patch.object(preflight, "MAX_TOTAL_BYTES", 1):
            self.assertEqual(preflight.inspect_archive(self.path)["blockers"], ["uncompressed_size_limit"])
        with mock.patch.object(preflight, "MAX_ARCHIVE_BYTES", 1):
            self.assertEqual(preflight.inspect_archive(self.path)["blockers"], ["archive_size_limit"])

    def test_only_small_descriptor_is_read_and_no_extraction_occurs(self):
        self.make()
        real_open = zipfile.ZipFile.open
        read_names = []
        def checked_open(archive, member, *args, **kwargs):
            read_names.append(member.filename)
            return real_open(archive, member, *args, **kwargs)
        with mock.patch.object(zipfile.ZipFile, "open", checked_open), \
                mock.patch.object(zipfile.ZipFile, "extract", side_effect=AssertionError("no extraction")), \
                mock.patch.object(zipfile.ZipFile, "extractall", side_effect=AssertionError("no extraction")):
            result = preflight.inspect_archive(self.path)
        self.assertEqual(result["archive_structure"], "metadata_checks_passed")
        self.assertEqual(read_names, ["timco/GAME.CUE"])
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_archive_errors_do_not_expose_private_path(self):
        self.path.write_bytes(b"not a zip")
        result = preflight.inspect_archive(self.path)
        self.assertEqual(result["blockers"], ["archive_read_failed"])
        self.assertNotIn(str(self.path), str(result))

    def test_cli_exit_code_and_json_do_not_claim_readiness(self):
        self.make()
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(preflight.main([str(self.path)]), 0)
        self.assertIn('"runtime_acceptance": "not_run"', output.getvalue())
        self.assertNotIn(str(self.path), output.getvalue())
        self.make(changes={"timco/GAME.GOG": None})
        with redirect_stdout(StringIO()):
            self.assertEqual(preflight.main([str(self.path)]), 1)


if __name__ == "__main__":
    unittest.main()
