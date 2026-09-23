import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "profile_cpu.py"
SPEC = importlib.util.spec_from_file_location("profile_cpu", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

SECRET = "sk-live-DO-NOT-LEAK-1234"


class FakeProc:
    """Minimal writable /proc tree driven by the test between samples."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.host = [0, 0, 0, 0, 0]  # busy, idle, iowait, forks, ctxt
        self.uptime = 1000.0

    def write_host(self):
        busy, idle, io, forks, ctxt = self.host
        (self.root / "stat").write_text(
            f"cpu  {busy} 0 0 {idle} {io} 0 0 0 0 0\ncpu0 1 1 1 1 1 1 1 1\n"
            f"ctxt {ctxt}\nprocesses {forks}\n"
        )
        (self.root / "loadavg").write_text("1.50 1.00 0.50 3/200 999\n")
        (self.root / "uptime").write_text(f"{self.uptime} 0\n")

    def put(self, pid, argv, ticks, ppid=1, starttime=10, comm="x", vcs=0, nvcs=0,
            rss_kb=1024, threads=1):
        d = self.root / str(pid)
        d.mkdir(exist_ok=True)
        utime = ticks // 2
        stime = ticks - utime
        fields = ["S", str(ppid)] + ["0"] * 9 + [str(utime), str(stime)] + ["0"] * 4 \
            + [str(threads), "0", str(starttime)] + ["0"] * 10
        (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
        (d / "status").write_text(
            f"Name:\t{comm}\nVmRSS:\t{rss_kb} kB\n"
            f"voluntary_ctxt_switches:\t{vcs}\nnonvoluntary_ctxt_switches:\t{nvcs}\n"
        )
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + (b"\0" if argv else b""))

    def remove(self, pid):
        d = self.root / str(pid)
        for f in d.iterdir():
            f.unlink()
        d.rmdir()


class ClassifyTests(unittest.TestCase):
    def test_fixed_labels(self):
        c = mod.classify
        self.assertEqual(c(["/usr/bin/ffmpeg", "-f", "x11grab", "-i", ":1", "-frames:v", "1", "out.png"], "ffmpeg"),
                         "ffmpeg:capture")
        self.assertEqual(c(["ffmpeg", "-i", "x", "rtmp://example/" + SECRET], "ffmpeg"), "ffmpeg:stream")
        self.assertEqual(c(["/usr/bin/retroarch", "-L", "core.so"], "retroarch"), "retroarch")
        self.assertEqual(c([], "kthreadd"), "kernel")
        self.assertEqual(c(["python3", "-m", "docich", "--config", "/etc/x.toml", "run", "trading"], "python3"),
                         "docich:run")
        self.assertEqual(c(["python3", "-m", "docich.paper_corner_fast"], "python3"), "py:docich.paper_corner_fast")
        self.assertEqual(c(["/usr/bin/python3", "/home/ubuntu/soren/webui/app.py", "--token", SECRET], "python3"),
                         "py:app.py")
        self.assertEqual(c(["bash", "/home/ubuntu/soren/radio_worker.sh"], "bash"), "sh:radio_worker.sh")
        self.assertEqual(c(["bash", "-c", "echo " + SECRET], "bash"), "shell")
        self.assertEqual(c(["python3", "-c", "print(1)"], "python3"), "python")
        self.assertEqual(c(["/opt/weird/" + SECRET], "weird"), "other")
        self.assertEqual(c(None, "x"), "other")

    def test_untrusted_tokens_never_become_labels(self):
        label = mod.classify(["python3", "-m", "docich", "Sk-" + SECRET + "/../x"], "python3")
        self.assertEqual(label, "docich")
        label = mod.classify(["python3", SECRET + " spaced.py"], "python3")
        self.assertEqual(label, "python")


class SampleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proc = FakeProc(self.tmp.name + "/proc")
        self.proc.root.mkdir()
        self.soren = pathlib.Path(self.tmp.name) / "soren"
        (self.soren / "tmp/state").mkdir(parents=True)
        (self.soren / "tmp/state/radio_worker.pid").write_text("200\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_sample(self, steps):
        """steps: list of callables applied before each post-start scan."""
        clock = [0.0]
        calls = iter(steps)

        def fake_sleep(sec):
            clock[0] += sec
            step = next(calls, None)
            if step:
                step()

        return mod.sample(self.proc.root, len(steps), 1.0, self.soren, "unit",
                          sleep=fake_sleep, monotonic=lambda: clock[0])

    def test_components_spawns_and_no_argv_leak(self):
        p = self.proc
        p.host = [100, 900, 0, 5000, 10000]
        p.write_host()
        p.put(100, ["/usr/bin/retroarch"], ticks=1000, comm="retroarch", vcs=10, nvcs=5)
        p.put(200, ["bash", "/home/ubuntu/soren/radio_worker.sh", SECRET], ticks=50)
        p.put(300, ["python3", "-c", "import os; os.system('" + SECRET + "')"], ticks=10, ppid=200)

        def step1():
            p.host = [200, 1300, 0, 5010, 10500]
            p.uptime += 1
            p.write_host()
            p.put(100, ["/usr/bin/retroarch"], ticks=1080, comm="retroarch", vcs=30, nvcs=9)
            # capture spawned by the worker's python child during the window
            p.put(400, ["ffmpeg", "-f", "x11grab", "-frames:v", "1", "/tmp/" + SECRET + ".png"],
                  ticks=7, ppid=300, starttime=100050, comm="ffmpeg")

        def step2():
            p.host = [300, 1700, 0, 5020, 11000]
            p.uptime += 1
            p.write_host()
            p.put(100, ["/usr/bin/retroarch"], ticks=1160, comm="retroarch", vcs=50, nvcs=13)
            p.put(300, ["python3", "-c", "x"], ticks=30, ppid=200)
            p.remove(400)

        report = self.run_sample([step1, step2])
        blob = json.dumps(report)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("/home/ubuntu", blob)
        comps = {r["component"]: r for r in report["components"]}
        self.assertEqual(report["components"][0]["component"], "retroarch")
        self.assertEqual(comps["retroarch"]["cpu_pct"], 80.0)  # 160 ticks / 100 Hz / 2 s
        self.assertEqual(comps["retroarch"]["vcs_per_sec"], 20.0)
        # generic python child inherits its worker ancestor
        self.assertIn("worker:radio_worker", comps)
        self.assertEqual(comps["worker:radio_worker"]["processes"], 2)
        self.assertEqual(comps["ffmpeg:capture"]["spawned"], 1)
        self.assertEqual(report["spawns"], [{"component": "ffmpeg:capture",
                                             "spawner": "worker:radio_worker", "count": 1}])
        self.assertEqual(report["host"]["forks"], 20)
        self.assertEqual(report["host"]["cpu_busy_pct"], 20.0)  # 200 busy / 1000 total
        self.assertEqual(report["meta"]["worker_pidfiles_resolved"], 1)
        text = mod.render_text(report)
        self.assertIn("retroarch", text)
        self.assertNotIn(SECRET, text)

    def test_pid_reuse_is_a_new_process(self):
        p = self.proc
        p.write_host()
        p.put(500, ["sleep", "1"], ticks=100, starttime=10, comm="sleep")

        def step():
            p.uptime += 1
            p.write_host()
            p.put(500, ["xdotool", "key", "a"], ticks=3, starttime=100050, comm="xdotool")

        report = self.run_sample([step])
        comps = {r["component"]: r for r in report["components"]}
        self.assertEqual(comps["xdotool"]["spawned"], 1)
        self.assertEqual(comps["sleep"]["cpu_pct"], 0.0)

    def test_output_is_bounded(self):
        p = self.proc
        p.write_host()
        for i in range(mod.MAX_COMPONENTS + 15):
            p.put(1000 + i, [f"python3", f"tool_{i}.py"], ticks=i)
        report = self.run_sample([lambda: None])
        self.assertEqual(len(report["components"]), mod.MAX_COMPONENTS)
        self.assertEqual(report["components_truncated"], 15)
        self.assertLessEqual(len(report["top_processes"]), mod.MAX_TOP_PROCESSES)


class CompareAndCliTests(unittest.TestCase):
    def test_compare_reports_deltas(self):
        before = {"meta": {"scenario": "a"}, "host": {"cpu_busy_pct": 90.0},
                  "components": [{"component": "ffmpeg:capture", "cpu_pct": 40.0, "spawned": 60}]}
        after = {"meta": {"scenario": "b"}, "host": {"cpu_busy_pct": 70.0},
                 "components": [{"component": "ffmpeg:capture", "cpu_pct": 10.0, "spawned": 5},
                                {"component": "retroarch", "cpu_pct": 55.0, "spawned": 0}]}
        out = mod.compare(before, after)
        self.assertEqual(out["host"]["cpu_busy_pct"]["delta"], -20.0)
        self.assertEqual(out["components"][0]["component"], "retroarch")
        row = {r["component"]: r for r in out["components"]}["ffmpeg:capture"]
        self.assertEqual(row["cpu_pct_delta"], -30.0)
        self.assertEqual(row["spawned_delta"], -55)

    def test_scenario_label_is_validated(self):
        with self.assertRaises(SystemExit):
            mod.main(["sample", "--scenario", "Bad Label!", "--duration", "10"])

    def test_percentile(self):
        self.assertIsNone(mod.percentile([], 50))
        self.assertEqual(mod.percentile([1, 2, 3, 4], 50), 2)
        self.assertEqual(mod.percentile(list(range(1, 101)), 95), 95)


if __name__ == "__main__":
    unittest.main()
