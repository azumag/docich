import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[1] / "recover_bgm_mute.py"
spec = importlib.util.spec_from_file_location("recover_bgm_mute", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SHORT_SINKS = ("0\tsoren_null\tmodule-null-sink.c\ts16le 2ch 44100Hz\tIDLE\n"
               "1\talsa_output\tmodule-alsa-sink.c\ts16le 2ch 44100Hz\tIDLE\n")


def stream(index, sink, mute, pid=None, corked=None):
    lines = [f"Sink Input #{index}", "\tDriver: protocol-native.c",
             f"\tSink: {sink}", "\tSample Specification: s16le 2ch 44100Hz",
             "\tVolume: front-left: 100 / 100% / 0.00 dB",
             f"\tMute: {'yes' if mute else 'no'}"]
    if corked is not None:
        lines.append(f"\tCorked: {'yes' if corked else 'no'}")
    lines.append(f'\tapplication.process.id = "{pid}"' if pid is not None
                else '\tapplication.name = "ffplay"')
    lines.append('\tmedia.role = "music"')
    return "\n".join(lines) + "\n"


def make_proc(root, spec):
    for pid, (argv, ppid) in spec.items():
        slot = root / str(pid)
        slot.mkdir(parents=True, exist_ok=True)
        (slot / "cmdline").write_bytes(
            b"\0".join(part.encode() for part in argv) + b"\0")
        (slot / "stat").write_text(
            f"{pid} (proc-{pid}) S {ppid} 0 0 0", encoding="ascii")
    return root


OWNER = ["bash", "/home/ubuntu/soren/bgm_worker.sh"]
PLAYER = ["ffplay", "-nodisp", "-window_title", "soren-bgm-loop",
          "/home/ubuntu/soren/sorengame/assets/BGM/song.ogg"]
BRIDGE = ["node", "soviet_local.mjs"]
BRIDGE_PLAYER = ["ffplay", "-nodisp", "-volume", "25", "song.ogg"]
FOREIGN = ["/usr/bin/python3", "speech.py"]

BASE_PROC = {100: (OWNER, 1), 210: (PLAYER, 100),
             400: (BRIDGE, 1), 410: (BRIDGE_PLAYER, 400),
             999: (FOREIGN, 1)}


class FakeRun:
    """Scripted pactl responses in call order; records every argv."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, args, server=None, env=None):
        self.calls.append(list(args))
        returncode, stdout = self.responses.pop(0)
        return SimpleNamespace(returncode=returncode, stdout=stdout)

    def set_mutes(self):
        return [call[1] for call in self.calls
                if call[0] == "set-sink-input-mute"]


class FakeProbe:
    def __init__(self, pid):
        self.pid = pid
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


class FakeSpawn:
    def __init__(self, pid=555):
        self.pid = pid
        self.argv = None
        self.env = None
        self.probe = None

    def __call__(self, argv, env=None):
        self.argv = list(argv)
        self.env = dict(env)
        self.probe = FakeProbe(self.pid)
        return self.probe


def listing(*blocks):
    return "".join(blocks)


class RecoverBgmMuteTests(unittest.TestCase):
    def run_recovery(self, tmp, responses, proc_spec=None, **kwargs):
        proc = make_proc(tmp / "proc",
                         dict(BASE_PROC) if proc_spec is None else proc_spec)
        run = FakeRun(responses)
        spawn = FakeSpawn()
        kwargs.setdefault("run", run)
        kwargs.setdefault("proc", proc)
        kwargs.setdefault("spawn", spawn)
        kwargs.setdefault("which", lambda name: "/usr/bin/ffplay")
        kwargs.setdefault("server", [])
        kwargs.setdefault("sleep", lambda seconds: None)
        result = module.run_recovery(**kwargs)
        return result, run, spawn

    def test_recovers_owned_muted_target_and_verifies_probe(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            first = listing(stream(7, "0", True, pid=210),
                            stream(9, "0", True, pid=999),
                            stream(5, "1", True, pid=210))
            unmuted = listing(stream(7, "0", False, pid=210),
                              stream(9, "0", True, pid=999),
                              stream(5, "1", True, pid=210))
            with_probe = unmuted + stream(11, "0", False, pid=555)
            gone = unmuted
            responses = [(0, first), (0, SHORT_SINKS),
                         (0, ""),  # set-sink-input-mute 7 acknowledged
                         (0, unmuted), (0, SHORT_SINKS),
                         (0, with_probe), (0, SHORT_SINKS),
                         (0, gone), (0, SHORT_SINKS)]
            result, run, spawn = self.run_recovery(tmp, responses)
            self.assertEqual(result["status"], "recovered")
            self.assertIsNone(result["reason"])
            self.assertEqual(result["target"], {"index": 7, "sink": "soren_null"})
            self.assertTrue(result["target_unmuted"])
            self.assertEqual(result["probe"],
                             {"spawned": True, "born_muted": False,
                              "verified_unmuted": True, "leftover": False})
            self.assertEqual(result["counts"], {"owned_unmuted": 0, "skipped": 2})
            # Only the owned target is ever unmuted; foreign and other-sink
            # streams keep their mute.
            self.assertEqual(run.set_mutes(), ["7"])
            # The probe is the fixed silent argv on the fixed sink.
            self.assertEqual(spawn.argv, module.probe_argv())
            self.assertEqual(spawn.env["PULSE_SINK"], "soren_null")
            self.assertTrue(spawn.probe.terminated)
            # No raw daemon text, command lines, paths, or pids leak out.
            text = json.dumps(result, sort_keys=True)
            for leaked in ("ffplay", "bgm_worker", "soviet", "210", "555", "999"):
                self.assertNotIn(leaked, text)

    def test_probe_born_muted_is_unmuted_and_reverified(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            first = listing(stream(7, "0", True, pid=210))
            unmuted = listing(stream(7, "0", False, pid=210))
            probe_muted = unmuted + stream(11, "0", True, pid=555)
            probe_fixed = unmuted + stream(11, "0", False, pid=555)
            responses = [(0, first), (0, SHORT_SINKS),
                         (0, ""),  # set-sink-input-mute 7
                         (0, unmuted), (0, SHORT_SINKS),
                         (0, probe_muted), (0, SHORT_SINKS),
                         (0, ""),  # set-sink-input-mute 11
                         (0, probe_fixed), (0, SHORT_SINKS),
                         (0, unmuted), (0, SHORT_SINKS)]
            result, run, spawn = self.run_recovery(tmp, responses)
            self.assertEqual(result["status"], "recovered")
            self.assertTrue(result["probe"]["born_muted"])
            self.assertTrue(result["probe"]["verified_unmuted"])
            self.assertEqual(run.set_mutes(), ["7", "11"])

    def test_bridge_descendant_without_worker_tag_is_owned(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            first = listing(stream(7, "0", True, pid=410))
            unmuted = listing(stream(7, "0", False, pid=410))
            with_probe = unmuted + stream(11, "0", False, pid=555)
            responses = [(0, first), (0, SHORT_SINKS),
                         (0, ""),
                         (0, unmuted), (0, SHORT_SINKS),
                         (0, with_probe), (0, SHORT_SINKS),
                         (0, unmuted), (0, SHORT_SINKS)]
            result, run, _ = self.run_recovery(tmp, responses)
            self.assertEqual(result["status"], "recovered")
            self.assertEqual(run.set_mutes(), ["7"])

    def test_already_unmuted_is_noop_without_probe(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            responses = [(0, listing(stream(7, "0", False, pid=210))),
                         (0, SHORT_SINKS)]
            spawn = FakeSpawn()
            run = FakeRun(responses)
            proc = make_proc(tmp / "proc", dict(BASE_PROC))
            result = module.run_recovery(
                run=run, proc=proc, spawn=spawn,
                which=lambda name: "/usr/bin/ffplay", server=[],
                sleep=lambda seconds: None)
            self.assertEqual(result["status"], "already_unmuted")
            self.assertEqual(run.set_mutes(), [])
            self.assertIsNone(spawn.argv)

    def test_no_owned_stream_is_refused_without_mutation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            responses = [(0, listing(stream(9, "0", True, pid=999))),
                         (0, SHORT_SINKS)]
            run = FakeRun(responses)
            proc = make_proc(tmp / "proc", dict(BASE_PROC))
            with self.assertRaises(module.Refused) as raised:
                module.run_recovery(
                    run=run, proc=proc, spawn=FakeSpawn(),
                    which=lambda name: "/usr/bin/ffplay", server=[],
                    sleep=lambda seconds: None)
            self.assertEqual(raised.exception.reason, "no_target")
            self.assertEqual(run.set_mutes(), [])

    def test_missing_pid_is_ownership_unproven(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            responses = [(0, listing(stream(9, "0", True))),
                         (0, SHORT_SINKS)]
            run = FakeRun(responses)
            proc = make_proc(tmp / "proc", dict(BASE_PROC))
            with self.assertRaises(module.Refused) as raised:
                module.run_recovery(
                    run=run, proc=proc, spawn=FakeSpawn(),
                    which=lambda name: "/usr/bin/ffplay", server=[],
                    sleep=lambda seconds: None)
            self.assertEqual(raised.exception.reason, "ownership_unproven")
            self.assertEqual(run.set_mutes(), [])

    def test_multiple_owned_muted_targets_are_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            first = listing(stream(7, "0", True, pid=210),
                            stream(8, "0", True, pid=410))
            responses = [(0, first), (0, SHORT_SINKS)]
            run = FakeRun(responses)
            proc = make_proc(tmp / "proc", dict(BASE_PROC))
            with self.assertRaises(module.Refused) as raised:
                module.run_recovery(
                    run=run, proc=proc, spawn=FakeSpawn(),
                    which=lambda name: "/usr/bin/ffplay", server=[],
                    sleep=lambda seconds: None)
            self.assertEqual(raised.exception.reason, "multiple_targets")
            self.assertEqual(run.set_mutes(), [])

    def test_corked_target_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            responses = [(0, listing(stream(7, "0", True, pid=210, corked=True))),
                         (0, SHORT_SINKS)]
            run = FakeRun(responses)
            proc = make_proc(tmp / "proc", dict(BASE_PROC))
            with self.assertRaises(module.Refused) as raised:
                module.run_recovery(
                    run=run, proc=proc, spawn=FakeSpawn(),
                    which=lambda name: "/usr/bin/ffplay", server=[],
                    sleep=lambda seconds: None)
            self.assertEqual(raised.exception.reason, "target_corked")
            self.assertEqual(run.set_mutes(), [])

    def test_missing_probe_player_refuses_before_mutation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            responses = [(0, listing(stream(7, "0", True, pid=210))),
                         (0, SHORT_SINKS)]
            run = FakeRun(responses)
            proc = make_proc(tmp / "proc", dict(BASE_PROC))
            with self.assertRaises(module.Refused) as raised:
                module.run_recovery(
                    run=run, proc=proc, spawn=FakeSpawn(),
                    which=lambda name: None, server=[],
                    sleep=lambda seconds: None)
            self.assertEqual(raised.exception.reason, "probe_unavailable")
            self.assertEqual(run.set_mutes(), [])

    def test_unparsable_listing_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            run = FakeRun([(0, "Sink Input ???\nMute: vielleicht\n")])
            proc = make_proc(tmp / "proc", dict(BASE_PROC))
            with self.assertRaises(module.Refused) as raised:
                module.run_recovery(
                    run=run, proc=proc, spawn=FakeSpawn(),
                    which=lambda name: "/usr/bin/ffplay", server=[],
                    sleep=lambda seconds: None)
            self.assertEqual(raised.exception.reason, "unparsable")

    def test_pactl_failure_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            run = FakeRun([(1, "")])
            proc = make_proc(tmp / "proc", dict(BASE_PROC))
            with self.assertRaises(module.Refused) as raised:
                module.run_recovery(
                    run=run, proc=proc, spawn=FakeSpawn(),
                    which=lambda name: "/usr/bin/ffplay", server=[],
                    sleep=lambda seconds: None)
            self.assertEqual(raised.exception.reason, "pactl_failed")

    def test_unmute_that_does_not_stick_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            first = listing(stream(7, "0", True, pid=210))
            responses = [(0, first), (0, SHORT_SINKS),
                         (0, ""),
                         (0, first), (0, SHORT_SINKS)]
            run = FakeRun(responses)
            proc = make_proc(tmp / "proc", dict(BASE_PROC))
            with self.assertRaises(module.Refused) as raised:
                module.run_recovery(
                    run=run, proc=proc, spawn=FakeSpawn(),
                    which=lambda name: "/usr/bin/ffplay", server=[],
                    sleep=lambda seconds: None)
            self.assertEqual(raised.exception.reason, "unmute_failed")

    def test_remuted_target_is_refused_after_probe(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            first = listing(stream(7, "0", True, pid=210))
            unmuted = listing(stream(7, "0", False, pid=210))
            with_probe = unmuted + stream(11, "0", False, pid=555)
            remuted = listing(stream(7, "0", True, pid=210))
            responses = [(0, first), (0, SHORT_SINKS),
                         (0, ""),
                         (0, unmuted), (0, SHORT_SINKS),
                         (0, with_probe), (0, SHORT_SINKS),
                         (0, remuted), (0, SHORT_SINKS)]
            run = FakeRun(responses)
            proc = make_proc(tmp / "proc", dict(BASE_PROC))
            with self.assertRaises(module.Refused) as raised:
                module.run_recovery(
                    run=run, proc=proc, spawn=FakeSpawn(),
                    which=lambda name: "/usr/bin/ffplay", server=[],
                    sleep=lambda seconds: None)
            self.assertEqual(raised.exception.reason, "remuted")

    def test_probe_missing_is_refused(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            first = listing(stream(7, "0", True, pid=210))
            unmuted = listing(stream(7, "0", False, pid=210))
            responses = [(0, first), (0, SHORT_SINKS),
                         (0, ""),
                         (0, unmuted), (0, SHORT_SINKS),
                         (0, unmuted), (0, SHORT_SINKS)]
            run = FakeRun(responses)
            proc = make_proc(tmp / "proc", dict(BASE_PROC))
            with mock.patch.object(module.time, "monotonic",
                                   side_effect=[1000.0, 2000.0]):
                # The probe pid never appears in any listing; the expired
                # poll window must refuse instead of waiting it out.
                with self.assertRaises(module.Refused) as raised:
                    module.run_recovery(
                        run=run, proc=proc, spawn=FakeSpawn(pid=777),
                        which=lambda name: "/usr/bin/ffplay", server=[],
                        sleep=lambda seconds: None)
            self.assertEqual(raised.exception.reason, "probe_missing")

    def test_only_fixed_argv_reaches_child_processes(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for banned in ("shell=True", "os.system", "os.popen",
                       "subprocess.call(", "subprocess.check_output(",
                       "subprocess.check_call("):
            self.assertNotIn(banned, source)
        # The probe argv is one fixed list; unmute takes only a validated
        # integer index plus the fixed verb "0".
        self.assertEqual(module.probe_argv()[0], "ffplay")
        self.assertNotIn("sh", module.probe_argv())
        self.assertNotIn("-c", module.probe_argv())

    def test_refusal_envelope_has_fixed_shape_only(self):
        for reason in sorted(module.REASONS):
            envelope = module._refused_envelope(reason)
            self.assertEqual(envelope,
                             {"status": "refused", "reason": reason,
                              "target": None, "target_unmuted": None,
                              "probe": None,
                              "counts": {"owned_unmuted": 0, "skipped": 0}})

    def test_wrapper_rejects_arguments_and_foreign_roots(self):
        wrapper = SCRIPT.with_name("recover_bgm_mute.sh")
        self.assertEqual(
            subprocess.run(["bash", str(wrapper), "x"],
                           capture_output=True).returncode, 64)
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                subprocess.run(["bash", str(wrapper)], cwd=directory,
                               capture_output=True).returncode, 65)


if __name__ == "__main__":
    unittest.main()
