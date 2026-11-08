import subprocess
import os
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops/vm_actions/restart_direct_stream_overlay.sh"
REFRESH_HELPER = ROOT / "ops/vm_actions/refresh_soren_inline_overlay.sh"
WATCHER_HELPER = ROOT / "ops/vm_actions/restart_soren_overlay_watchers.sh"
WORKFLOW = ROOT / ".github/workflows/vm-operations.yml"


class DirectStreamOverlayRestartTests(unittest.TestCase):
    @unittest.skipUnless(Path('/proc/self/cmdline').exists(), 'Linux process identity contract')
    def test_watcher_replacement_requires_fresh_render(self):
        for fresh in (False, True):
            with self.subTest(fresh=fresh), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                state = root / 'tmp/state'
                state.mkdir(parents=True)
                (root / '.env').write_text('SOREN_STATUS_OVERLAY_WATCHERS_ENABLED=1\nSOREN_UNIFIED_OVERLAY_ENABLED=1\n')
                output = state / 'soren_overlay.html'
                output.write_text('SOREN CONTROL DECK')
                script = root / 'generate_soren_overlay.sh'
                script.write_text('exec -a "$0 watch" sleep 30\n')
                pid_file = state / 'soren_overlay_watch.pid'
                # Limit retries, but use real processes and rendered files.
                bin_dir = root / 'bin'
                bin_dir.mkdir()
                seq = bin_dir / 'seq'
                seq.write_text('#!/bin/sh\necho 1\necho 2\n')
                seq.chmod(0o755)
                children = []
                def launch():
                    child = subprocess.Popen(['bash', str(script), 'watch'])
                    children.append(child)
                    pid_file.write_text(str(child.pid))
                    return child
                old = launch()
                def respawn():
                    old.wait()
                    launch()
                    if fresh:
                        replacement = state / 'new.html'
                        replacement.write_text('SOREN CONTROL DECK')
                        replacement.replace(output)
                supervisor = threading.Thread(target=respawn)
                supervisor.start()
                try:
                    proc = subprocess.run(['bash', str(WATCHER_HELPER), '--root', str(root)],
                        env={**os.environ, 'PATH': str(bin_dir) + ':' + os.environ['PATH']},
                        capture_output=True, text=True, timeout=5)
                    self.assertEqual(proc.returncode, 0 if fresh else 1, proc.stderr)
                    self.assertIsNotNone(old.poll())
                finally:
                    if old.poll() is None:
                        old.terminate()
                    supervisor.join(timeout=2)
                    for child in children:
                        if child.poll() is None:
                            child.terminate()
                        child.wait(timeout=2)

    def test_helper_is_inert_outside_ffmpeg_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".env").write_text("SOREN_STREAM_BACKEND=obs\n", encoding="utf-8")
            proc = subprocess.run(
                ["bash", str(HELPER), "--root", str(root)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_inline_refresh_helper_is_inert_outside_ffmpeg_dashboard(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "tools").mkdir()
            (root / "tools/refresh_inline_broadcast_overlay.mjs").write_text("", encoding="utf-8")
            (root / ".env").write_text(
                "SOREN_STREAM_BACKEND=obs\n"
                "SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED=1\n"
                "SOREN_DIRECT_STAGE_LAYOUT=dashboard\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                ["bash", str(REFRESH_HELPER), "--root", str(root)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_workflow_refreshes_active_inline_rails_after_soren_gitlink_change(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        marker = "- name: Refresh active Soren91 GAME OPS rails after reviewed Soren update"
        self.assertIn(marker, text)
        block = text.split(marker, 1)[1].split("- name: Restart docich webui systemd unit", 1)[0]
        self.assertIn("games/soviet_now", block)
        self.assertIn("soren_inline_overlay_refresh_epoch", block)
        self.assertIn("refresh_soren_inline_overlay.sh", block)
        self.assertIn("github.event_name == 'push'", block)
        self.assertIn("steps.auth.outputs.target == 'production'", block)

    def test_overlay_watcher_helper_is_inert_when_watchers_are_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".env").write_text(
                "SOREN_STATUS_OVERLAY_WATCHERS_ENABLED=0\n"
                "SOREN_UNIFIED_OVERLAY_ENABLED=1\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                ["bash", str(WATCHER_HELPER), "--root", str(root)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_workflow_restarts_status_watchers_after_soren_gitlink_change(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        marker = "- name: Restart reviewed Soren status overlay watchers after Soren update"
        self.assertIn(marker, text)
        block = text.split(marker, 1)[1].split("- name: Restart docich webui systemd unit", 1)[0]
        self.assertIn("games/soviet_now", block)
        self.assertIn("restart_soren_overlay_watchers.sh", block)
        self.assertIn("github.event_name == 'push'", block)
        self.assertIn("steps.auth.outputs.target == 'production'", block)

    def test_workflow_restart_is_epoch_gated(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        marker = "- name: Restart direct stream for reviewed overlay runtime epoch"
        self.assertIn(marker, text)
        block = text.split(marker, 1)[1].split("- name: Restart docich webui systemd unit", 1)[0]
        self.assertIn("direct_stream_overlay_restart_epoch", block)
        self.assertIn("restart_direct_stream_overlay.sh", block)
        self.assertIn("github.event_name == 'push'", block)
        self.assertIn("steps.auth.outputs.target == 'production'", block)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(Path('/proc/self/cmdline').exists(), 'Linux process identity contract')
class DirectStreamOverlayRestartOwnerTests(unittest.TestCase):
    """The one-shot restart works for the legacy runner and for a docich-owned runner."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='restart-overlay-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'tmp/state/direct_stream').mkdir(parents=True)
        (self.root / 'lib').mkdir()
        (self.root / 'overlays').mkdir()
        (self.root / '.env').write_text('SOREN_STREAM_BACKEND=ffmpeg\n')
        (self.root / 'lib/direct_overlay.mjs').write_text('INLINE_BROADCAST_REFRESH_MS')
        (self.root / 'overlays/direct_broadcast_overlay.html').write_text('GAME + OPS')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        seq = self.bin / 'seq'
        seq.write_text('#!/bin/sh\nfor i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do echo $i; done\n')
        seq.chmod(0o755)
        self.children = []
        self.addCleanup(self.reap)

    def reap(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            if child.stdin is not None:
                child.stdin.close()

    def launch(self, name):
        """A real process whose /proc cmdline is exactly `name` (a single argv entry, no extra args).

        `cat` takes no argument, so unlike `exec -a NAME sleep N` the command line is not
        polluted with a trailing duration. It blocks on its stdin pipe until killed.
        """
        import time
        child = subprocess.Popen(['bash', '-c', 'exec -a "$0" cat', name], stdin=subprocess.PIPE,
                                 start_new_session=True)
        self.children.append(child)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                if Path(f'/proc/{child.pid}/cmdline').read_bytes() == name.encode() + b'\0':
                    return child
            except OSError:
                pass
            time.sleep(0.02)
        self.fail('process did not take its name')

    def run_helper(self):
        return subprocess.run(['bash', str(HELPER), '--root', str(self.root)], capture_output=True, text=True, timeout=30,
                              env={**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}'})

    def respawn_after_exit(self, old, name, record):
        def work():
            old.wait()
            new = self.launch(name)
            record(new)
        thread = threading.Thread(target=work)
        thread.start()
        self.addCleanup(thread.join, 5)

    def test_a_docich_runner_without_a_pid_file_is_restarted_via_the_status_pid(self):
        name = f'python3 -m docich.stream_supervisor --root {self.root} run'
        old = self.launch(name)
        status = self.root / 'tmp/state/direct_stream/status.json'
        status.write_text(f'{{"pid": {old.pid}, "running": true}}')
        # a stale legacy pid file must not hide the live docich runner
        (self.root / 'tmp/state/direct_stream.pid').write_text('999999')
        self.respawn_after_exit(old, name, lambda new: status.write_text(f'{{"pid": {new.pid}, "running": true}}'))
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNotNone(old.poll(), 'the old runner received SIGTERM')

    def test_a_legacy_runner_is_still_restarted_via_the_pid_file(self):
        name = f'python3 {self.root}/lib/direct_stream.py run'
        old = self.launch(name)
        pid_file = self.root / 'tmp/state/direct_stream.pid'
        pid_file.write_text(str(old.pid))
        self.respawn_after_exit(old, name, lambda new: pid_file.write_text(str(new.pid)))
        proc = self.run_helper()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNotNone(old.poll())

    def test_a_stranger_is_never_signalled(self):
        for name in ('sleep 60', f'python3 -m docich.stream_supervisor --root {self.root} status',
                     f'python3 -m docich.stream_supervisor --root /other run',
                     'python3.12x -m docich.stream_supervisor run'):
            with self.subTest(name=name):
                stranger = self.launch(name)
                (self.root / 'tmp/state/direct_stream/status.json').write_text(f'{{"pid": {stranger.pid}}}')
                proc = self.run_helper()
                self.assertNotEqual(proc.returncode, 0)
                self.assertIsNone(stranger.poll(), 'a non-runner must not receive SIGTERM')

    def test_a_missing_runner_is_a_failure_not_a_success(self):
        proc = self.run_helper()
        self.assertNotEqual(proc.returncode, 0)

    def test_the_replacement_must_be_a_runner_too(self):
        name = f'python3 -m docich.stream_supervisor --root {self.root} run'
        old = self.launch(name)
        status = self.root / 'tmp/state/direct_stream/status.json'
        status.write_text(f'{{"pid": {old.pid}}}')
        self.respawn_after_exit(old, 'sleep 60', lambda new: status.write_text(f'{{"pid": {new.pid}}}'))
        self.assertNotEqual(self.run_helper().returncode, 0)
