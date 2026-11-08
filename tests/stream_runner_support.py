"""Shared fakes for the stream runner process tests (not a test module)."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]

FAKE_FFMPEG = r'''#!{python}
import json, os, signal, sys, threading, time
mode = os.environ.get("FAKE_MODE", "normal")
events = os.environ["FAKE_EVENTS"]
def log(text):
    with open(events, "a") as handle:
        handle.write(text + "\n")
with open(os.environ["FAKE_STARTS"], "a") as handle:
    handle.write(str(os.getpid()) + "\n")
inodes = [int(x) for x in os.environ.get("FAKE_LOCK_INODES", "").split(",") if x]
leaked = []
for fd in range(3, 256):
    try:
        if os.fstat(fd).st_ino in inodes:
            leaked.append(fd)
    except OSError:
        pass
with open(os.environ["FAKE_FD_REPORT"], "w") as handle:
    handle.write(json.dumps(leaked))
def on_sigint(signum, frame):
    log("sigint")
    if mode != "ignore_all":
        os._exit(255)
signal.signal(signal.SIGINT, on_sigint)
def read_stdin():
    for line in sys.stdin:
        if line.strip() == "q":
            log("q")
            if mode == "normal":
                os._exit(0)
    log("stdin_eof")
    os._exit(0)
threading.Thread(target=read_stdin, daemon=True).start()
started = time.monotonic()
exit_after = float(os.environ.get("FAKE_EXIT_AFTER", "0"))
while time.monotonic() - started < float(os.environ.get("FAKE_MAX_SECONDS", "45")):
    print("frame=1\nfps=30.0\nspeed=1.0x\nprogress=continue", flush=True)
    if exit_after and time.monotonic() - started >= exit_after:
        os._exit(int(os.environ.get("FAKE_EXIT_CODE", "1")))
    time.sleep(0.1)
os._exit(99)
'''

DRIVER = r'''
import json, sys
spec = json.loads(sys.argv[1])
sys.path.insert(0, spec["src"])
import docich.stream_instance_lock as L
L.CANONICAL_STREAM_ENCODER_LOCK_PATH = spec["canonical"]
from pathlib import Path
from docich import stream_runner as R
from docich.stream_config import load_stream_config, public_stream_config
config = load_stream_config(spec["env"], repo_root=Path(spec["root"]), runtime_uid=0)
public = public_stream_config(config)
try:
    code = R.run_supervised(config, public, [spec["ffmpeg"]], mode=spec["mode"], captions_active=False,
                            reconnect=spec["reconnect"], audio_watchdog=None, cwd=spec["root"],
                            stop_timeouts=tuple(spec["stop_timeouts"]))
except R.AlreadyRunningError:
    sys.exit(3)
except R.RuntimeCheckError as exc:
    print(exc, file=sys.stderr)
    sys.exit(2)
sys.exit(code)
'''

CONTENDER = r'''
import fcntl, json, sys
spec = json.loads(sys.argv[1])
sys.path.insert(0, spec["src"])
import docich.stream_instance_lock as L
L.CANONICAL_STREAM_ENCODER_LOCK_PATH = spec["canonical"]
out = {}
try:
    held = L.open_canonical_stream_encoder_lock()
    out["canonical"] = "acquired"
    held.close()
except BlockingIOError:
    out["canonical"] = "blocked"
except OSError as exc:
    out["canonical"] = "error:" + type(exc).__name__
try:
    handle = open(spec["legacy"], "a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    out["legacy"] = "acquired"
    handle.close()
except BlockingIOError:
    out["legacy"] = "blocked"
print(json.dumps(out))
'''


def reconnect_policy(**override):
    base = {"enabled": True, "twitch_channel": "", "probe_interval_sec": 60, "offline_threshold": 2,
            "backoff_sec": 1, "max_consecutive_restarts": 3, "reload_relay": False}
    base.update(override)
    return base


class Sandbox:
    """A temp runtime root with a fake ffmpeg, plus helpers to run/observe it."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stream-runner-")
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.logs = self.root / "logs"
        self.canonical_dir = self.root / "canon"
        self.canonical_dir.mkdir(mode=0o700)
        self.canonical = self.canonical_dir / "direct_stream_encoder.lock"
        self.starts = self.root / "starts"
        self.events = self.root / "events"
        self.fd_report = self.root / "fd_report"
        self.ffmpeg = self.root / "fake-ffmpeg"
        self.ffmpeg.write_text(FAKE_FFMPEG.replace("{python}", sys.executable))
        self.ffmpeg.chmod(0o755)
        self.children = []
        self.processes = []

    @property
    def legacy_lock(self):
        return self.state / "direct_stream.lock"

    def env(self, **fake):
        base = {"PATH": os.environ.get("PATH", ""), "FAKE_EVENTS": str(self.events), "FAKE_STARTS": str(self.starts),
                "FAKE_FD_REPORT": str(self.fd_report), "FAKE_MAX_SECONDS": "40"}
        base.update({k: str(v) for k, v in fake.items()})
        return base

    def spec(self, mode="live", reconnect=None, stop_timeouts=(0.4, 0.4), fake_env=None):
        env = {"SOREN_DIRECT_STREAM_STATE_DIR": str(self.state), "SOREN_DIRECT_STREAM_LOG_FILE": str(self.logs / "s.log")}
        return {"src": str(ROOT / "src"), "canonical": str(self.canonical), "env": env, "root": str(self.root),
                "ffmpeg": str(self.ffmpeg), "mode": mode, "reconnect": reconnect,
                "stop_timeouts": list(stop_timeouts)}

    def start(self, spec, fake_env=None):
        env = self.env(**(fake_env or {}))
        env["FAKE_LOCK_INODES"] = ",".join(
            str(p.stat().st_ino) for p in (self.canonical, self.legacy_lock) if p.exists())
        process = subprocess.Popen([sys.executable, "-B", "-c", DRIVER, json.dumps(spec)], env=env, cwd=str(self.root),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.processes.append(process)
        return process

    def precreate_locks(self):
        """Create both lock files so the fake ffmpeg can recognise their inodes."""
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path in (self.canonical, self.legacy_lock):
            path.touch(mode=0o600)

    def contend(self):
        spec = {"src": str(ROOT / "src"), "canonical": str(self.canonical), "legacy": str(self.legacy_lock)}
        self.state.mkdir(parents=True, exist_ok=True)
        done = subprocess.run([sys.executable, "-B", "-c", CONTENDER, json.dumps(spec)], capture_output=True,
                              text=True, timeout=20, check=False)
        return json.loads(done.stdout)

    def status(self):
        try:
            return json.loads((self.state / "status.json").read_text())
        except (OSError, ValueError):
            return {}

    def started(self):
        try:
            return [int(x) for x in self.starts.read_text().split()]
        except OSError:
            return []

    def event_log(self):
        try:
            return self.events.read_text().split()
        except OSError:
            return []

    def wait_for(self, condition, timeout=15.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.05)
        raise AssertionError("condition not met within %.1fs" % timeout)

    def cleanup(self):
        for process in self.processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    process.kill()
            try:
                process.communicate(timeout=5)
            except Exception:
                pass
        for pid in self.started():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        self.tmp.cleanup()


# Drives docich.stream_supervisor.main() in a child process with the host
# validation and the fixed-lock path injected (the real CLI has no bypass).
# The trailing "docich.stream_supervisor run" argv tokens give the process the
# command-line shape stop_runner() recognises; the driver itself ignores them.
MAIN_DRIVER = r'''
import json, sys
spec = json.loads(sys.argv[1])
sys.path.insert(0, spec["src"])
import docich.stream_instance_lock as L
L.CANONICAL_STREAM_ENCODER_LOCK_PATH = spec["canonical"]
from docich import stream_supervisor as S
raise SystemExit(S.main(spec["argv"], environ=spec["env"], validate=lambda config, mode: False,
                        twica_enabled=lambda: spec.get("twica", False),
                        prepare_pulse=lambda env, config, watchdog: None))
'''
