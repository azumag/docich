import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"
WORKFLOW = ROOT / ".github" / "workflows" / "vm-operations.yml"
HELPER = ROOT / "ops" / "vm_actions" / "run_audio_gap_measure_once.sh"
AUTHORIZE = ROOT / "ops" / "vm_actions" / "authorize.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


collector = load_module("audio_priority_diagnostics_tested", COLLECTOR)


def audio_gap_fixture(generated_at=1_800_000_000):
    return {
        "schema": "docich.audio_gap.v1",
        "meta": {
            "generated_at": generated_at,
            "monitor": "soren_null.monitor",
            "rate": 48000,
            "channels": 2,
            "duration_sec": 92.0,
            "nonzero_frame_ratio": 0.97,
            "should_not_leak": "/home/ubuntu/secret",
        },
        "gaps": {
            "count": 7,
            "total_ms": 855,
            "max_ms": 171,
            "long_silences": 2,
            "items": [
                {"start_ms": 1200, "ms": 40},
                {"start_ms": 9800, "ms": 171, "note": "secret"},
                {"start_ms": -1, "ms": 30},
                {"start_ms": 20000, "ms": 9999},
            ],
        },
    }


class AudioGapReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "audio-gap.json"
        self.now = 1_800_000_000

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_projects_only_fixed_bounded_fields(self):
        self.write(audio_gap_fixture())
        out = collector._collect_audio_gaps(self.now + 5, path=self.path)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["age_sec"], 5)
        self.assertEqual(out["meta"]["monitor"], "soren_null.monitor")
        self.assertEqual(out["meta"]["duration_sec"], 92)
        self.assertEqual(out["meta"]["nonzero_frame_ratio"], 0.97)
        self.assertEqual(out["gaps"]["count"], 7)
        self.assertEqual(out["gaps"]["total_ms"], 855)
        self.assertEqual(out["gaps"]["max_ms"], 171)
        self.assertEqual(out["gaps"]["long_silences"], 2)
        # Malformed items are dropped whole; valid ones project only the
        # two fixed fields.
        self.assertEqual(out["gaps"]["items"], [
            {"start_ms": 1200, "ms": 40},
            {"start_ms": 9800, "ms": 171},
        ])
        encoded = json.dumps(out, sort_keys=True)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("should_not_leak", encoded)
        self.assertNotIn("note", encoded)
        self.assertNotIn("/home/ubuntu", encoded)

    def test_items_are_truncated_to_the_fixed_bound(self):
        data = audio_gap_fixture()
        data["gaps"]["items"] = [
            {"start_ms": index, "ms": 10} for index in range(120)
        ]
        self.write(data)
        out = collector._collect_audio_gaps(self.now, path=self.path)
        self.assertEqual(len(out["gaps"]["items"]), collector.AUDIO_GAP_MAX_ITEMS)

    def test_stale_report_is_explicit(self):
        self.write(audio_gap_fixture(
            generated_at=self.now - collector.AUDIO_GAP_FRESH_SEC - 1))
        out = collector._collect_audio_gaps(self.now, path=self.path)
        self.assertEqual(out["status"], "stale")
        self.assertTrue(out["readable"])

    def test_missing_report_is_missing(self):
        out = collector._collect_audio_gaps(self.now, path=self.path)
        self.assertEqual(out["status"], "missing")
        self.assertFalse(out["present"])

    def test_symlink_is_not_followed(self):
        real = Path(self.tmp.name) / "real.json"
        real.write_text(json.dumps(audio_gap_fixture()), encoding="utf-8")
        os.symlink(real, self.path)
        out = collector._collect_audio_gaps(self.now, path=self.path)
        self.assertNotEqual(out["status"], "ok")
        self.assertFalse(out["readable"])

    def assert_invalid(self, mutate):
        data = audio_gap_fixture()
        mutate(data)
        self.write(data)
        out = collector._collect_audio_gaps(self.now, path=self.path)
        self.assertEqual(out["status"], "invalid")

    def test_wrong_schema_monitor_rate_channels_or_duration_is_invalid(self):
        self.assert_invalid(lambda d: d.update(schema="other"))
        self.assert_invalid(lambda d: d["meta"].update(monitor="other.monitor"))
        self.assert_invalid(lambda d: d["meta"].update(rate=44100))
        self.assert_invalid(lambda d: d["meta"].update(channels=1))
        self.assert_invalid(lambda d: d["meta"].update(duration_sec=10))
        self.assert_invalid(lambda d: d["meta"].update(duration_sec=999))
        self.assert_invalid(lambda d: d["meta"].update(nonzero_frame_ratio=1.5))
        self.assert_invalid(lambda d: d["meta"].update(generated_at="now"))
        self.assert_invalid(lambda d: d["gaps"].update(count=None))
        self.assert_invalid(lambda d: d["gaps"].update(total_ms=-1))
        self.assert_invalid(lambda d: d["gaps"].update(max_ms="big"))
        self.assert_invalid(lambda d: d["gaps"].update(long_silences=-2))
        self.assert_invalid(lambda d: d.update(meta=None))
        self.assert_invalid(lambda d: d.update(gaps=None))

    def test_oversized_report_is_invalid(self):
        data = audio_gap_fixture()
        data["padding"] = "x" * (collector.AUDIO_GAP_MAX_BYTES + 1024)
        self.write(data)
        out = collector._collect_audio_gaps(self.now, path=self.path)
        self.assertEqual(out["status"], "invalid")

    def test_future_generated_at_is_invalid(self):
        self.write(audio_gap_fixture(generated_at=self.now + 60))
        out = collector._collect_audio_gaps(self.now, path=self.path)
        self.assertEqual(out["status"], "invalid")


class FakeProcBuilder:
    """Minimal /proc-shaped tree for the keeper summary scan."""

    def __init__(self, root):
        self.root = Path(root)

    def process(self, pid, comm, argv, thread_nices=(0,)):
        base = self.root / str(pid)
        (base / "task" / str(pid)).mkdir(parents=True, exist_ok=True)
        (base / "comm").write_text(comm + "\n", encoding="utf-8")
        (base / "cmdline").write_bytes(
            b"\0".join(part.encode() for part in argv) + b"\0")
        for index, nice in enumerate(thread_nices):
            tid = pid if index == 0 else pid * 100 + index
            task = base / "task" / str(tid)
            task.mkdir(parents=True, exist_ok=True)
            (task / "stat").write_text(
                f"{tid} ({comm}) S 0 0 0 0 -1 0 0 0 0 0 0 0 0 0 15 {nice} 1 0 1\n",
                encoding="utf-8")
        return base


class AudioPriorityKeeperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proc = FakeProcBuilder(Path(self.tmp.name) / "proc")
        self.proc.root.mkdir(parents=True)
        self.uid = os.getuid()
        self.files = Path(self.tmp.name) / "files"
        self.files.mkdir()
        self.script = self.files / "priority.py"
        self.unit = self.files / "docich-audio-priority.service"
        self.script.write_text("# keeper script\n", encoding="utf-8")
        self.unit.write_text("[Unit]\nDescription=keeper\n", encoding="utf-8")
        self.systemctl_calls = []

    def systemctl(self, is_enabled="enabled", is_active="active",
                  show="ExecMainStartTimestamp=Sun 2026-10-19 14:00:00 JST\nSubState=running\nNRestarts=0"):
        def fake(args, timeout=5):
            self.systemctl_calls.append(list(args))
            if args[0] == "is-enabled":
                return (0, is_enabled) if is_enabled is not None else None
            if args[0] == "is-active":
                return (0, is_active) if is_active is not None else None
            if args[0] == "show":
                return (0, show) if show is not None else None
            return (1, "")
        return fake

    def collect(self, journal_text=None, deployed_script=None, deployed_unit=None,
                is_enabled="enabled", is_active="active",
                show="ExecMainStartTimestamp=Sun 2026-10-19 14:00:00 JST\nSubState=running\nNRestarts=0"):
        if deployed_script is None:
            deployed_script = self.script
        if deployed_unit is None:
            deployed_unit = self.unit
        with mock.patch.object(collector, "_systemctl_system",
                               side_effect=self.systemctl(is_enabled, is_active, show)), \
             mock.patch.object(collector, "_audio_priority_journal_tail",
                               return_value=journal_text):
            return collector._collect_audio_priority(
                proc_root=self.proc.root, uid=self.uid,
                script_path=self.script, unit_file=self.unit,
                deployed_script=deployed_script, deployed_unit=deployed_unit)

    def test_mirror_framerate_reads_the_single_dash_ffplay_option(self):
        # presentation.py launches the mirror ffplay with the ffmpeg input
        # option "-framerate" (single dash, docich#1811); the collector must
        # read it as well as the double-dash spelling, otherwise the effective
        # hanjuku mirror framerate projects as null (observed in production).
        self.proc.process(201, "ffplay",
                          ["ffplay", "-f", "x11grab", "-framerate", "15",
                           "-draw_mouse", "0", "-video_size", "960x540", ":99"],
                          thread_nices=(0,))
        out = collector._collect_audio_priority(
            proc_root=self.proc.root, uid=self.uid,
            script_path=self.script, unit_file=self.unit,
            deployed_script=self.script, deployed_unit=self.unit)
        self.assertEqual(out["mirror"], {"present": True, "framerate": 15})

    def test_mirror_framerate_keeps_the_double_dash_spelling(self):
        self.proc.process(202, "ffplay",
                          ["ffplay", "-f", "x11grab", "--framerate", "15", ":99"],
                          thread_nices=(0,))
        out = collector._collect_audio_priority(
            proc_root=self.proc.root, uid=self.uid,
            script_path=self.script, unit_file=self.unit,
            deployed_script=self.script, deployed_unit=self.unit)
        self.assertEqual(out["mirror"], {"present": True, "framerate": 15})

    def test_mirror_framerate_non_integer_argument_is_null_not_crash(self):
        self.proc.process(203, "ffplay",
                          ["ffplay", "-f", "x11grab", "-framerate", "auto", ":99"],
                          thread_nices=(0,))
        out = collector._collect_audio_priority(
            proc_root=self.proc.root, uid=self.uid,
            script_path=self.script, unit_file=self.unit,
            deployed_script=self.script, deployed_unit=self.unit)
        self.assertEqual(out["mirror"], {"present": True, "framerate": None})

    def test_target_summary_uses_priority_py_classification(self):
        # The keeper's own rules (loaded from the deployed tree) decide the
        # kinds; thread nices are summarized per kind.
        self.proc.process(101, "retroarch", ["/usr/bin/retroarch"],
                          thread_nices=(0,))
        self.proc.process(102, "pulseaudio", ["/usr/bin/pulseaudio", "--daemon"],
                          thread_nices=(-10, -10, 0))
        self.proc.process(103, "ffmpeg",
                          ["ffmpeg", "-f", "x11grab", "-i", ":99", "-f", "flv"],
                          thread_nices=(-10,))
        # PNG capture ffmpeg must not match the encoder kind.
        self.proc.process(104, "ffmpeg",
                          ["ffmpeg", "-i", "screen.png", "-f", "image2pipe"],
                          thread_nices=(0,))
        self.proc.process(105, "python3",
                          ["python3", "-u", "-m", "docich.twica_encoder"],
                          thread_nices=(0, 0))
        # A script argument that merely looks like the module must not match.
        self.proc.process(106, "python3",
                          ["python3", "-c", "docich.twica_encoder"],
                          thread_nices=(0,))
        self.proc.process(107, "python3",
                          ["/usr/bin/python3",
                           "/usr/local/libexec/azumag-vm-ops/audio_priority/priority.py",
                           "--nice", "-10", "--interval", "5"],
                          thread_nices=(0,))
        self.proc.process(108, "ffplay",
                          ["ffplay", "-f", "x11grab", "--framerate", "15",
                           "-vf", "scale=960:540", ":99"],
                          thread_nices=(0,))

        out = self.collect(journal_text=(
            "audio-priority: started uid=1001 nice=-10 interval=5 dry_run=False\n"
            "audio-priority: reniced pid=101 kind=retroarch threads=1 nice=-10\n"
            "audio-priority: reniced pid=999 kind=mystery threads=1 nice=-10\n"
            "audio-priority: warning operation=apply pid=1 tid=None errno=EPERM "
            "(same warning limited to once per 60s)\n"
            "audio-priority: scan failed: OSError\n"
        ))
        self.assertEqual(out["status"], "ok")
        # The classification only spends bytes when it is broken.
        self.assertNotIn("classification", out)
        targets = out["targets"]
        self.assertEqual(targets["retroarch"],
                         {"present": True, "processes": 1, "threads": 1,
                          "max_nice": 0, "at_target": 0})
        self.assertEqual(targets["pulseaudio"],
                         {"present": True, "processes": 1, "threads": 3,
                          "max_nice": 0, "at_target": 2})
        self.assertEqual(targets["encoder-ffmpeg"],
                         {"present": True, "processes": 1, "threads": 1,
                          "max_nice": -10, "at_target": 1})
        self.assertEqual(targets["twica-feeder"],
                         {"present": True, "processes": 1, "threads": 2,
                          "max_nice": 0, "at_target": 0})
        self.assertEqual(out["keeper"], {"present": True, "pid": 107, "threads": 1})
        self.assertEqual(out["mirror"], {"present": True, "framerate": 15})
        journal = out["journal"]
        self.assertTrue(journal["available"])
        self.assertEqual(journal["reniced_24h"], 2)
        self.assertEqual(journal["reniced_by_kind"],
                         {"retroarch": 1, "other": 1})
        self.assertEqual(journal["warnings_24h"], 1)
        self.assertEqual(journal["scan_failures_24h"], 1)
        self.assertEqual(journal["restarts_24h"], 1)
        service = out["service"]
        self.assertEqual(service["enabled"], "enabled")
        self.assertTrue(service["active"])
        self.assertEqual(service["sub_state"], "running")
        self.assertEqual(service["n_restarts"], 0)
        self.assertEqual(service["started_at"], "Sun 2026-10-19 14:00:00 JST")
        files = out["files"]
        self.assertTrue(files["script"]["matches_deployed"])
        self.assertTrue(files["unit"]["matches_deployed"])

    def test_mismatched_root_owned_copy_is_degraded(self):
        self.proc.process(107, "python3",
                          ["/usr/bin/python3",
                           "/usr/local/libexec/azumag-vm-ops/audio_priority/priority.py"],
                          thread_nices=(0,))
        stale = self.files / "stale-priority.py"
        stale.write_text("# old pre-merge keeper\n", encoding="utf-8")
        out = self.collect(deployed_script=stale)
        self.assertEqual(out["status"], "degraded")
        self.assertFalse(out["files"]["script"]["matches_deployed"])
        self.assertTrue(out["files"]["script"]["present"])
        self.assertIsNotNone(out["files"]["script"]["sha256_16"])
        self.assertEqual(len(out["files"]["script"]["sha256_16"]), 16)

    def test_missing_unit_is_missing_and_queries_are_read_only(self):
        # Dead unit: ExecMainStartTimestamp is empty and must be omitted,
        # SubState still projects. systemd prints Key=Value lines in its own
        # property order, so the parse must never rely on positions.
        out = self.collect(is_enabled="not-found", is_active="inactive",
                           show="NRestarts=0\nSubState=dead\nExecMainStartTimestamp=")
        self.assertEqual(out["status"], "missing")
        self.assertEqual(out["service"]["enabled"], "not-found")
        self.assertFalse(out["service"]["active"])
        self.assertEqual(out["service"]["sub_state"], "dead")
        self.assertEqual(out["service"]["n_restarts"], 0)
        self.assertNotIn("started_at", out["service"])
        for args in self.systemctl_calls:
            self.assertTrue(args[0] in ("is-enabled", "is-active", "show"),
                            args)
            self.assertNotIn("enable", args)
            self.assertNotIn("start", args)
            self.assertNotIn("daemon-reload", args)

    def test_keeper_absent_or_systemd_blind_is_not_ok(self):
        empty = self.collect()
        self.assertEqual(empty["status"], "degraded")
        # Absent keeper/mirror are omitted entirely (absence = not present),
        # keeping the idle section byte-cheap for the huge-telemetry envelope.
        self.assertNotIn("keeper", empty)
        self.assertNotIn("mirror", empty)
        self.assertEqual(empty["targets"], {})
        self.assertEqual(empty["journal"], {"available": False})
        blind = self.collect(is_enabled=None, is_active=None, show=None)
        self.assertEqual(blind["status"], "unknown")
        self.assertEqual(blind["service"], {})

    def test_journal_unavailable_fails_open(self):
        out = self.collect(journal_text=None)
        self.assertEqual(out["journal"], {"available": False})

    def test_idle_journal_omits_zero_counters(self):
        out = self.collect(journal_text="audio-priority: started uid=1001 nice=-10 interval=5 dry_run=False\n")
        self.assertEqual(out["journal"], {"available": True, "restarts_24h": 1})
        idle = self.collect(journal_text="")
        self.assertEqual(idle["journal"], {"available": True})

    def test_absent_installed_files_report_present_false(self):
        out = self.collect()
        self.assertTrue(out["files"]["script"]["present"])
        gone = self.files / "gone.py"
        out = collector._collect_audio_priority(
            proc_root=self.proc.root, uid=self.uid,
            script_path=gone, unit_file=self.unit,
            deployed_script=self.script, deployed_unit=self.unit)
        # The absent copy carries its whole fact; no null padding is emitted.
        self.assertEqual(out["files"]["script"], {"present": False})
        self.assertTrue(out["files"]["unit"]["matches_deployed"])

    def test_unloadable_classification_is_reported_not_guessed(self):
        with mock.patch.object(collector, "_audio_priority_module",
                               return_value=None), \
             mock.patch.object(collector, "_systemctl_system",
                               side_effect=self.systemctl()), \
             mock.patch.object(collector, "_audio_priority_journal_tail",
                               return_value=None):
            out = collector._collect_audio_priority(
                proc_root=self.proc.root, uid=self.uid,
                script_path=self.script, unit_file=self.unit,
                deployed_script=self.script, deployed_unit=self.unit)
        self.assertEqual(out["classification"], "unavailable")
        self.assertEqual(out["targets"], {})

    def test_sections_survive_the_soft_budget_under_pressure(self):
        # The production envelope routinely sits above MAX_JSON_BYTES, so a
        # soft-level stub would blank the keeper summary from every run and
        # break audio_gap_measure's read-back. Both sections must come through
        # the soft cascade untouched (docich#1811).
        audio_priority = {"status": "ok", "uid": 1001, "targets": {}}
        audio_gaps = {"status": "ok", "gaps": {"count": 0}}
        payload = {
            "cpu_profile": {"status": "stale", "meta": {}, "host": {}},
            "corners": {"retro_corner": {}},
            "hanjuku_tactical": {"status": "ok"},
            "ai": {"recent_events": [], "anomalous_components": {}},
            "workers": {"details": {}},
            "nethack_history": {"daily": {"records": []}, "completed_runs": {"records": []}},
            "soren91_drop_profile": {"profileStatus": "missing"},
            "pulse_sink_inputs": {"streams": []},
            "audio_priority": audio_priority,
            "audio_gaps": audio_gaps,
        }
        with mock.patch.object(collector, "MAX_JSON_BYTES", 100):
            collector._diagnostics_budget(payload)
        self.assertEqual(payload["audio_priority"], audio_priority)
        self.assertEqual(payload["audio_gaps"], audio_gaps)

    def test_sections_yield_to_marked_stubs_at_the_hard_ceiling(self):
        # Only the hard ceiling may drop them, via the reviewed largest-first
        # fallback, and the drop is an explicit marked stub.
        payload = {
            "cpu_profile": {"status": "stale", "meta": {}, "host": {}},
            "corners": {"retro_corner": {}},
            "hanjuku_tactical": {"status": "ok"},
            "ai": {"recent_events": [], "anomalous_components": {}},
            "workers": {"details": {}},
            "nethack_history": {"daily": {"records": []}, "completed_runs": {"records": []}},
            "soren91_drop_profile": {"profileStatus": "missing"},
            "pulse_sink_inputs": {"streams": []},
            "audio_priority": {"status": "ok", "uid": 1001},
            "audio_gaps": {"status": "ok", "gaps": {"count": 0}},
        }
        with mock.patch.object(collector, "MAX_JSON_BYTES", 100), \
             mock.patch.object(collector, "HARD_JSON_BYTES", 100):
            collector._diagnostics_budget(payload)
        self.assertEqual(payload["audio_priority"],
                         {"status": "output_omitted", "reason": "hard_ceiling"})
        self.assertEqual(payload["audio_gaps"],
                         {"status": "output_omitted", "reason": "hard_ceiling"})


class AudioPriorityOperationContractTests(unittest.TestCase):
    def test_helper_is_fixed_readonly_measurement(self):
        subprocess.run(["bash", "-n", str(HELPER)], check=True)
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn('[[ "$#" -eq 0 ]]', text)
        self.assertIn("soren_null.monitor", text)
        self.assertIn("readonly RATE=48000", text)
        self.assertIn('--rate="$RATE"', text)
        self.assertIn('--channels="$CHANNELS"', text)
        self.assertIn("--format=s16le", text)
        self.assertIn("timeout --signal=INT", text)
        self.assertIn("92", text)
        self.assertIn("/tmp/docich-audio-gap-latest.json", text)
        self.assertIn("docich.audio_gap.v1", text)
        for forbidden in ("sudo ", "systemctl", " kill ", " renice ", "amixer"):
            self.assertNotIn(forbidden, text)

    def test_workflow_and_authorize_expose_the_bounded_operation(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("audio_gap_measure", workflow)
        self.assertIn("Measure production broadcast audio gaps once", workflow)
        self.assertIn("run_audio_gap_measure_once.sh", workflow)
        self.assertIn("(data.get(\"diagnostics\") or {}).get(\"audio_gaps\")", workflow)
        self.assertIn("no broadcast audio was flowing", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        authorize = AUTHORIZE.read_text(encoding="utf-8")
        self.assertIn("'audio_gap_measure'", authorize)
        self.assertIn("audio_gap_measure requires production/main", authorize)
        self.assertIn("audio_gap_measure are bounded read-only", authorize)
        # Bounded read-only measurement: owner-only gating above, and it must
        # not be added to the confirm=production-required operation set.
        head, _, tail = authorize.partition("if target=='production' and op in {")
        confirm_set = tail.split("}", 1)[0]
        self.assertNotIn("audio_gap_measure", confirm_set)

    def test_collector_registers_both_sections(self):
        text = COLLECTOR.read_text(encoding="utf-8")
        self.assertIn('"audio_priority": _collect_audio_priority()', text)
        self.assertIn('"audio_gaps": _collect_audio_gaps(now)', text)


if __name__ == "__main__":
    unittest.main()
