"""docich#5708: the NetHack birth window outlives the game since Issue #4344.

``create_game_session_owned`` opens a bare persistent shell and types the game
into it, so when NetHack exits (death disclosure finished, or a normal save)
the birth window stays -- back at a shell prompt, and renamed by tmux's
automatic rename.  The adapter used to read "the window exists" as "the game
still runs", so every boundary and every cancel after a death waited on a
corpse: the 2026-11-26 incident sat in ``draining`` for ~3.5 h with
``prompt_not_pending`` / ``prompt_class=unknown`` / 15 survivors.

These tests pin the replacement contract: only a birth window *proven* to host
nothing but its launch shell counts as an ended game; anything unprovable keeps
the old, conservative reading.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich import tmux as tmux_mod  # noqa: E402
from docich.adapters import nethack as nethack_adapter  # noqa: E402
from docich.game_switch import ReadinessTimeoutError, RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402
from docich.process_tree import ProcessInfo  # noqa: E402
from docich.tmux import TmuxOwnership  # noqa: E402

DEAD_DISCLOSURE = (
    "Do you want your possessions identified? [ynq] (n)\n"
    "\n"
    "                       ------\n"
    "                       |....@|\n"
    "                       ------\n"
    "\n"
    "Docich the Stripling          St:16 Dx:12 Co:18 In:8 Wi:9 Ch:7 Neutral\n"
    "Dlvl:3 $:12 HP:0(16) Pw:1(1) AC:6 Xp:2/31 T:1505\n"
)
LIVE_MAP = (
    "\n"
    "                       ------\n"
    "                       |..@.|\n"
    "                       ------\n"
    "Dlvl:3 $:12 HP:9(16) Pw:1(1) AC:6 Xp:2/31 T:1500\n"
)
SHELL_AFTER_EXIT = "Goodbye docich the Valkyrie...\n$ \n"
_UNSET = object()


def _spec(root: Path) -> RuntimeSpec:
    names = runtime_names(1)
    return RuntimeSpec(
        game="nethack",
        adapter="cli",
        generation=1,
        runtime_id="g1-abcdef",
        lease_id=str(uuid.uuid4()),
        runtime_dir=root / "run" / "runtimes" / "g1-abcdef",
        game_window=names.game_window,
        agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


def _root(save_dir: Path) -> Path:
    root = save_dir.parent / "repo"
    (root / "config" / "games").mkdir(parents=True, exist_ok=True)
    (root / "config" / "docich.toml").write_text(
        '[paths]\nstate_dir = "run"\ngames_dir = "config/games"\n',
        encoding="utf-8",
    )
    (root / "config" / "games" / "nethack.toml").write_text(
        '[game]\nname = "nethack"\ntitle = "NetHack"\nadapter = "cli"\n\n'
        '[cli]\ncommand = "nethack"\ncols = 80\nrows = 24\n\n'
        '[nethack]\npersistent_run = true\nplayer_name = "docich"\n'
        f'save_dir = "{save_dir}"\n',
        encoding="utf-8",
    )
    return root


class ShellFirstTmux:
    """A runtime session whose birth window is a persistent launch shell.

    ``probe`` overrides ``pane_runs_only_launch_shell``: ``_UNSET`` derives the
    verdict from whether the game still runs, ``None``/``False`` simulate an
    unprovable/negative proof.  ``rename_on_exit`` mirrors tmux's automatic
    rename (``nethack-console`` -> ``bash``); turning it off proves the
    adapter no longer depends on that accident.
    """

    def __init__(self, spec, save_dir, *, pane_text=LIVE_MAP, game_running=True,
                 probe=_UNSET, rename_on_exit=True):
        self.spec = spec
        self.save_dir = save_dir
        self.pane_text = pane_text
        self.game_running = game_running
        self.probe = probe
        self.rename_on_exit = rename_on_exit
        self.birth_name = "nethack-console" if game_running or not rename_on_exit else "bash"
        self.calls: list[tuple] = []
        # Shell + presentation + supervisor + browser + agent stay alive.
        self.survivors = tuple(range(1000, 1015))

    # -- session / ownership -------------------------------------------------
    def session_target_exists(self, session, *, strict=False):
        self.calls.append(("session_target_exists", session))
        return True

    def read_session_ownership(self, session):
        return TmuxOwnership(
            runtime_id=self.spec.runtime_id,
            generation=self.spec.generation,
            role="adapter",
        )

    def list_windows(self):
        return [self.birth_name, self.spec.game_window, self.spec.agent_window]

    def window_target_exists(self, target, *, strict=False):
        self.calls.append(("window_target_exists", target, strict))
        return target == f"{self.spec.adapter_session}:{self.birth_name}"

    def capture_pane(self, target):
        self.calls.append(("capture_pane", target))
        return self.pane_text

    def owned_processes_alive(self, ownership):
        return self.survivors

    def pane_runs_only_launch_shell(self, target, ownership):
        self.calls.append(("pane_runs_only_launch_shell", target, ownership.role))
        if self.probe is not _UNSET:
            return self.probe
        return not self.game_running

    # -- the game reacting to keys -------------------------------------------
    def _exit_game(self):
        self.game_running = False
        self.pane_text = SHELL_AFTER_EXIT
        if self.rename_on_exit:
            self.birth_name = "bash"

    def send_keys(self, target, keys, literal=False):
        self.calls.append(("send_keys", target, list(keys), literal))
        if not self.game_running:
            return
        if keys == ["q"] and self.pane_text == DEAD_DISCLOSURE:
            self._exit_game()
        elif keys == ["S"]:
            self.pane_text = "Really save? [yn] (n)\n" + LIVE_MAP
        elif keys == ["y"] and self.pane_text.startswith("Really save?"):
            (self.save_dir / "1000docich").write_bytes(b"save")
            self._exit_game()


class TestShellFirstBirthWindow(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        base = Path(self.tempdir.name)
        self.save_dir = base / "save"
        self.save_dir.mkdir()
        self.root = _root(self.save_dir)
        self.g = config.load_global(self.root)
        self.spec = _spec(self.root)
        self.spec.runtime_dir.mkdir(parents=True)
        self.game = config.load_game(self.g, "nethack")

    def tearDown(self):
        self.tempdir.cleanup()

    def adapter(self, tmux):
        adapter = nethack_adapter.NethackCoordinatorAdapter(self.g, self.game, self.spec)
        adapter.tmux = tmux
        return adapter

    def _boundary(self):
        return json.loads(
            (self.spec.runtime_dir / nethack_adapter.BOUNDARY_RESULT_FILENAME).read_text()
        )

    def _diag(self):
        return json.loads(
            (self.spec.runtime_dir / nethack_adapter.BOUNDARY_DIAG_FILENAME).read_text()
        )

    @staticmethod
    def _keys(tmux):
        return [call for call in tmux.calls if call[0] == "send_keys"]

    # -- cancel: the 2026-11-26 incident -------------------------------------

    def test_cancel_releases_a_drain_whose_game_exited_behind_a_surviving_shell(self):
        tmux = ShellFirstTmux(
            self.spec, self.save_dir, pane_text=SHELL_AFTER_EXIT, game_running=False
        )
        adapter = self.adapter(tmux)

        self.assertTrue(
            adapter.cancel_round_boundary("req-1", time.monotonic() + 1.0, None)
        )

        self.assertEqual(self._keys(tmux), [])
        self.assertEqual(self._boundary()["outcome"], "ended")
        diag = self._diag()
        self.assertEqual(diag["reason"], "process_target_absent")
        self.assertIs(diag["process_target_present"], False)
        self.assertEqual(diag["boundary_outcome"], "ended")
        self.assertIn(
            ("pane_runs_only_launch_shell", f"{self.spec.adapter_session}:bash", "adapter"),
            tmux.calls,
        )

    def test_cancel_keeps_a_left_over_save_as_suspended(self):
        (self.save_dir / "1000docich").write_bytes(b"save")
        tmux = ShellFirstTmux(
            self.spec, self.save_dir, pane_text=SHELL_AFTER_EXIT, game_running=False
        )

        self.assertTrue(
            self.adapter(tmux).cancel_round_boundary("req-1", time.monotonic() + 1.0, None)
        )
        self.assertEqual(self._boundary()["outcome"], "suspended")

    def test_cancel_without_the_shell_proof_still_refuses_like_the_incident(self):
        # Pre-#5708 behaviour must remain the fallback whenever the proof
        # cannot be completed: no key, refusal with the survivor count.
        for verdict in (None, False):
            with self.subTest(verdict=verdict):
                tmux = ShellFirstTmux(
                    self.spec, self.save_dir, pane_text=SHELL_AFTER_EXIT,
                    game_running=False, probe=verdict,
                )
                self.assertFalse(
                    self.adapter(tmux).cancel_round_boundary(
                        "req-1", time.monotonic() + 1.0, None
                    )
                )
                self.assertEqual(self._keys(tmux), [])
                diag = self._diag()
                self.assertEqual(diag["reason"], "prompt_not_pending")
                self.assertEqual(diag["prompt_class"], "unknown")
                self.assertEqual(diag["orphan_processes_present"], 15)

    def test_cancel_never_releases_a_live_game_off_the_save_prompt(self):
        tmux = ShellFirstTmux(self.spec, self.save_dir, pane_text=LIVE_MAP)

        self.assertFalse(
            self.adapter(tmux).cancel_round_boundary("req-1", time.monotonic() + 1.0, None)
        )
        self.assertEqual(self._keys(tmux), [])
        self.assertEqual(self._diag()["reason"], "prompt_not_pending")
        self.assertFalse((self.spec.runtime_dir / "nethack_boundary.json").exists())

    def test_cancel_finishes_a_death_disclosure_even_though_the_shell_survives(self):
        tmux = ShellFirstTmux(self.spec, self.save_dir, pane_text=DEAD_DISCLOSURE)

        self.assertTrue(
            self.adapter(tmux).cancel_round_boundary("req-1", time.monotonic() + 1.0, None)
        )
        self.assertEqual(
            self._keys(tmux),
            [("send_keys", f"{self.spec.adapter_session}:nethack-console", ["q"], True)],
        )
        self.assertEqual(self._boundary()["outcome"], "ended")
        self.assertEqual(self._diag()["prompt_class"], "dead_disclosure")

    # -- request: the switch itself must not wedge ----------------------------

    def test_request_accepts_a_game_that_already_exited(self):
        tmux = ShellFirstTmux(
            self.spec, self.save_dir, pane_text=SHELL_AFTER_EXIT, game_running=False
        )

        self.adapter(tmux).request_round_boundary("req-1", time.monotonic() + 1.0, None)

        self.assertEqual(self._keys(tmux), [])
        self.assertEqual(self._boundary()["outcome"], "ended")

    def test_request_finishes_a_death_disclosure_even_though_the_shell_survives(self):
        # Before #5708 the re-resolved target was the renamed shell window,
        # which raised "terminal process identityが変化しました".
        tmux = ShellFirstTmux(self.spec, self.save_dir, pane_text=DEAD_DISCLOSURE)

        self.adapter(tmux).request_round_boundary("req-1", time.monotonic() + 1.0, None)

        self.assertEqual(
            self._keys(tmux),
            [("send_keys", f"{self.spec.adapter_session}:nethack-console", ["q"], True)],
        )
        self.assertEqual(self._boundary()["outcome"], "ended")

    def test_normal_save_completes_without_relying_on_the_window_rename(self):
        tmux = ShellFirstTmux(self.spec, self.save_dir, rename_on_exit=False)

        self.adapter(tmux).request_round_boundary("req-1", time.monotonic() + 1.0, None)

        target = f"{self.spec.adapter_session}:nethack-console"
        self.assertEqual(
            self._keys(tmux),
            [
                ("send_keys", target, ["Escape"], False),
                ("send_keys", target, ["S"], True),
                ("send_keys", target, ["y"], True),
            ],
        )
        boundary = self._boundary()
        self.assertEqual(boundary["outcome"], "suspended")
        self.assertEqual(boundary["save_file"], "1000docich")

    def test_unprovable_shell_proof_keeps_the_save_boundary_fail_closed(self):
        tmux = ShellFirstTmux(self.spec, self.save_dir, rename_on_exit=False, probe=None)

        with self.assertRaises(ReadinessTimeoutError):
            self.adapter(tmux).request_round_boundary(
                "req-1", time.monotonic() + 0.3, None
            )
        self.assertFalse((self.spec.runtime_dir / "nethack_boundary.json").exists())

    def test_missing_probe_capability_keeps_the_old_reading(self):
        tmux = ShellFirstTmux(
            self.spec, self.save_dir, pane_text=SHELL_AFTER_EXIT, game_running=False
        )
        tmux.pane_runs_only_launch_shell = None  # not callable

        self.assertFalse(
            self.adapter(tmux).cancel_round_boundary("req-1", time.monotonic() + 1.0, None)
        )

    def test_a_raising_probe_is_never_an_exit(self):
        tmux = ShellFirstTmux(
            self.spec, self.save_dir, pane_text=SHELL_AFTER_EXIT, game_running=False
        )
        tmux.pane_runs_only_launch_shell = mock.Mock(side_effect=RuntimeError("probe"))

        self.assertFalse(
            self.adapter(tmux).cancel_round_boundary("req-1", time.monotonic() + 1.0, None)
        )
        self.assertEqual(self._diag()["reason"], "prompt_not_pending")


class TestPaneRunsOnlyLaunchShell(unittest.TestCase):
    """The read-only proof behind the adapter change; ``None`` is fail-closed."""

    TARGET = "docich-game-g1:bash"

    def setUp(self):
        self.tmux = tmux_mod.Tmux("docich-game-g1")
        self.ownership = TmuxOwnership("g1-abcdef", 1, "adapter")

    def _probe(self, *, panes=None, table=None, descendants=(), env_hits=(),
               protected=frozenset(), visible=(100, 200, 300)):
        if panes is None:
            panes = [tmux_mod.PaneLaunchState(dead=False, pid=100, has_start_command=False)]
        if table is None:
            table = {100: ProcessInfo(pid=100, ppid=1, state="S")}
        pane_mock = (
            mock.patch.object(self.tmux, "pane_launch_states_checked", side_effect=panes)
            if isinstance(panes, Exception)
            else mock.patch.object(self.tmux, "pane_launch_states_checked", return_value=panes)
        )
        with (
            pane_mock,
            mock.patch("docich.tmux.process_table", return_value=table),
            mock.patch("docich.tmux.descendant_pids", return_value=list(descendants)),
            mock.patch("docich.tmux.process_ids", return_value=list(visible)),
            mock.patch("docich.tmux.processes_with_env", return_value=list(env_hits)) as env,
            mock.patch.object(
                self.tmux,
                "_protected_teardown_pids",
                return_value=tmux_mod.ProtectedTeardownPids(protected),
            ),
        ):
            result = self.tmux.pane_runs_only_launch_shell(self.TARGET, self.ownership)
        return result, env

    def test_an_idle_launch_shell_is_proven(self):
        result, env = self._probe(env_hits=[100])  # the shell itself is tagged
        self.assertIs(result, True)
        self.assertEqual(
            env.call_args.args[0],
            {
                "DOCICH_TMUX_RUNTIME_ID": "g1-abcdef",
                "DOCICH_TMUX_GENERATION": "1",
                "DOCICH_TMUX_ROLE": "adapter",
            },
        )

    def test_a_running_game_under_the_shell_is_not_an_exit(self):
        result, _ = self._probe(
            table={
                100: ProcessInfo(pid=100, ppid=1, state="S"),
                101: ProcessInfo(pid=101, ppid=100, state="S"),
            },
            descendants=[101],
        )
        self.assertIs(result, False)

    def test_an_unreaped_zombie_is_not_a_running_game(self):
        result, _ = self._probe(
            table={
                100: ProcessInfo(pid=100, ppid=1, state="S"),
                101: ProcessInfo(pid=101, ppid=100, state="Z"),
            },
            descendants=[101],
        )
        self.assertIs(result, True)

    def test_a_descendant_missing_from_the_snapshot_is_not_an_exit(self):
        result, _ = self._probe(descendants=[102])
        self.assertIs(result, False)

    def test_a_command_launched_pane_keeps_window_existence_semantics(self):
        result, _ = self._probe(
            panes=[tmux_mod.PaneLaunchState(dead=False, pid=100, has_start_command=True)]
        )
        self.assertIs(result, False)

    def test_a_live_tagged_orphan_outside_the_tree_is_not_an_exit(self):
        result, _ = self._probe(env_hits=[100, 300])
        self.assertIs(result, False)

    def test_a_protected_tagged_process_does_not_block_the_proof(self):
        result, _ = self._probe(env_hits=[300], protected=frozenset({300}))
        self.assertIs(result, True)

    def test_unprovable_shapes_return_none(self):
        cases = {
            "dead pane": dict(
                panes=[tmux_mod.PaneLaunchState(dead=True, pid=100, has_start_command=False)]
            ),
            "two panes": dict(
                panes=[
                    tmux_mod.PaneLaunchState(dead=False, pid=100, has_start_command=False),
                    tmux_mod.PaneLaunchState(dead=False, pid=101, has_start_command=False),
                ]
            ),
            "empty table": dict(table={}),
            "leader not visible": dict(table={5: ProcessInfo(pid=5, ppid=1, state="S")}),
            "leader zombie": dict(table={100: ProcessInfo(pid=100, ppid=1, state="Z")}),
            "no pid listing": dict(visible=()),
            "pane probe failed": dict(panes=tmux_mod.TmuxError("gone")),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                result, _ = self._probe(**kwargs)
                self.assertIsNone(result)

    def test_protection_failure_is_unprovable(self):
        with (
            mock.patch.object(
                self.tmux,
                "pane_launch_states_checked",
                return_value=[
                    tmux_mod.PaneLaunchState(dead=False, pid=100, has_start_command=False)
                ],
            ),
            mock.patch(
                "docich.tmux.process_table",
                return_value={100: ProcessInfo(pid=100, ppid=1, state="S")},
            ),
            mock.patch("docich.tmux.descendant_pids", return_value=[]),
            mock.patch.object(
                self.tmux, "_protected_teardown_pids", side_effect=tmux_mod.TmuxError("x")
            ),
        ):
            self.assertIsNone(
                self.tmux.pane_runs_only_launch_shell(self.TARGET, self.ownership)
            )

    @mock.patch("docich.tmux.procs.run")
    def test_launch_state_listing_reads_only_a_boolean(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="0\t4242\t0\n", stderr=""
        )
        self.assertEqual(
            self.tmux.pane_launch_states_checked(self.TARGET),
            [tmux_mod.PaneLaunchState(dead=False, pid=4242, has_start_command=False)],
        )
        argv = mock_run.call_args.args[0]
        self.assertIn("#{pane_dead}\t#{pane_pid}\t#{?pane_start_command,1,0}", argv)

    @mock.patch("docich.tmux.procs.run")
    def test_malformed_launch_state_listing_raises(self, mock_run):
        for stdout in ("0\t4242\n", "0\t4242\tsleep 30\n", "2\t1\t0\n", "0\tx\t0\n", ""):
            with self.subTest(stdout=stdout):
                mock_run.return_value = subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=stdout, stderr=""
                )
                with self.assertRaises(tmux_mod.TmuxError):
                    self.tmux.pane_launch_states_checked(self.TARGET)


@unittest.skipUnless(
    shutil.which("tmux") and sys.platform.startswith("linux"),
    "requires tmux and Linux /proc",
)
class TestRealTmuxShellFirstSession(unittest.TestCase):
    """End to end against a private tmux server (``TMUX_TMPDIR`` isolation)."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="tmx")
        tmux_tmpdir = Path(self.tempdir.name)
        os.chmod(tmux_tmpdir, 0o700)
        self.env = mock.patch.dict(os.environ, {"TMUX_TMPDIR": str(tmux_tmpdir)})
        self.env.start()
        self.session = "docich-game-g1"
        self.tmux = tmux_mod.Tmux(self.session)
        self.ownership = TmuxOwnership("g1-abcdef", 1, "adapter")

    def tearDown(self):
        subprocess.run(["tmux", "kill-server"], capture_output=True, check=False)
        self.env.stop()
        self.tempdir.cleanup()

    def _birth_target(self):
        names = self.tmux.list_windows()
        self.assertEqual(len(names), 1)
        return f"{self.session}:{names[0]}"

    def _wait_for(self, expected, timeout=5.0):
        deadline = time.monotonic() + timeout
        result = None
        while time.monotonic() < deadline:
            result = self.tmux.pane_runs_only_launch_shell(
                self._birth_target(), self.ownership
            )
            if result is expected:
                return result
            time.sleep(0.05)
        return result

    def test_the_birth_window_survives_the_game_and_is_then_proven_empty(self):
        self.tmux.create_game_session_owned(
            self.session, ["sleep", "1"], 80, 24, self.ownership
        )
        self.assertIs(self._wait_for(False), False)  # game (sleep) running
        self.assertIs(self._wait_for(True), True)  # game exited, shell remains
        self.assertTrue(self.tmux.session_target_exists(self.session))


if __name__ == "__main__":
    unittest.main()
