"""Teardown evidence for game-switch cleanup events (Issue #1936).

A ``cleaned`` event used to carry an empty ``detail``: teardown reported
success without recording what it stopped or how it proved the runtime was
gone.  Issue #1105 could not be closed because of exactly that gap — the log
said ``cleaned`` while an orphaned moon-buggy kept burning CPU.

These tests pin the evidence contract:

* ``cleanup_started`` records the runtime's claimed tmux objects *before* the
  teardown removes them,
* ``cleaned`` records the window/session ids, the pane process group and
  cgroup, the signals actually sent, any escaped processes reclaimed, and the
  liveness proof,
* a teardown that could not confirm the runtime is gone emits ``cleanup_failed``
  with what survived and why, instead of silently succeeding,
* the rendered line stays inside the event log's ``detail`` budget and leaks no
  secrets, command lines or URLs.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from docich import game_switch, tmux as tmux_mod
from docich.process_tree import TerminationResult
from docich.teardown_evidence import (
    MAX_EVIDENCE_DETAIL_CHARS,
    PaneScopeEvidence,
    TeardownEvidence,
    adapter_recorder,
    bind_recorder,
    evidence_from_mapping,
)
from docich.tmux import TmuxOwnership

RUNTIME_ID = "g339-ba49eb5b"
GENERATION = 339
OWNER_AGENT = TmuxOwnership(runtime_id=RUNTIME_ID, generation=GENERATION, role="agent")
OWNER_GAME = TmuxOwnership(runtime_id=RUNTIME_ID, generation=GENERATION, role="game")
OWNER_ADAPTER = TmuxOwnership(runtime_id=RUNTIME_ID, generation=GENERATION, role="adapter")
CGROUP = "0::/user.slice/user-1000.slice/tmux-spawn-abc123.scope\n"
SCOPE = tmux_mod.PaneProcessScope(123, CGROUP)

RUNTIME = {
    "game": "moon-buggy",
    "adapter": "cli",
    "generation": GENERATION,
    "runtime_id": RUNTIME_ID,
    "lease_id": "12345678-1234-5678-1234-567812345678",
    "game_window": "game-g339",
    "agent_window": "agent-g339",
    "adapter_session": "docich-game-g339",
    "started_at": "2026-09-24T06:00:00Z",
}


class RecordingCliAdapter:
    """A CLI-shaped adapter whose teardown goes through the real tmux wrapper."""

    name = "cli"
    agent_enabled = True

    def __init__(self, *, alive_after: bool = False):
        self.tmux = tmux_mod.Tmux("docich-game-g339")
        self.alive_after = alive_after
        self.eval_tmux = tmux_mod.Tmux(server="docich-eval")

    def preflight(self, deadline, cancel):
        pass

    def materialize_runtime(self, deadline, cancel):
        pass

    def readiness(self, deadline, cancel):
        pass

    def start_agent(self, deadline, cancel):
        pass

    def stop_agent(self, deadline, cancel):
        self.tmux.kill_window_owned("docich-game-g339:agent-g339", OWNER_AGENT)

    def cleanup_runtime(self, deadline, cancel):
        self.tmux.kill_window_owned("docich-game-g339:game-g339", OWNER_GAME)
        self.tmux.kill_session_owned("docich-game-g339", OWNER_ADAPTER)

    def alive(self, deadline, cancel):
        return self.alive_after


class TeardownEvidenceTestBase(unittest.TestCase):
    """Drives the real retiring-cleanup loop with tmux I/O mocked out."""

    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.state_dir = Path(self._tempdir.name) / "run"
        self.store = game_switch.GameSwitchStore(self.state_dir)
        self.store.initialize()
        self.adapter = RecordingCliAdapter()
        self.coordinator = self._coordinator()
        self._mock_tmux()

    def _coordinator(self) -> game_switch.GameSwitchCoordinator:
        return game_switch.GameSwitchCoordinator(
            self.store,
            lambda spec: self.adapter,
            quiesce_verify_timeout_s=0.3,
            poll_interval_s=0.01,
            default_timeout_s=30,
            step_timeouts=game_switch.StepTimeouts(
                cleanup_s=5.0, stop_agent_s=5.0, probe_s=1.0
            ),
        )

    def _mock_tmux(self) -> None:
        """Replace only tmux transport; every helper under test stays real.

        The originals are restored in tearDown so a later test class is not
        left running against another class's doubles.
        """

        self._saved = {
            name: getattr(tmux_mod.Tmux, name)
            for name in (
                "window_target_exists", "session_target_exists",
                "_kill_after_process_stop", "read_window_ownership",
                "read_session_ownership", "_target_id", "_pane_pids",
                "_protected_teardown_pids", "_server_pid",
                "_server_pane_leaders", "kill_window_owned",
                "_reap_escaped_processes",
            )
        }
        self._saved_process = {
            "process_pgid": tmux_mod.process_pgid,
            "process_cgroup": tmux_mod.process_cgroup,
            "terminate_process_tree": tmux_mod.terminate_process_tree,
        }
        self.addCleanup(self._restore_tmux)
        tmux_mod.Tmux.window_target_exists = (
            lambda self, target, strict=False: True
        )
        tmux_mod.Tmux.session_target_exists = (
            lambda self, session, strict=False: True
        )
        tmux_mod.Tmux._kill_after_process_stop = lambda self, args, operation: None

        def read_window_ownership(self, target):
            return OWNER_AGENT if ":agent-g339" in target else OWNER_GAME

        tmux_mod.Tmux.read_window_ownership = read_window_ownership
        tmux_mod.Tmux.read_session_ownership = lambda self, session: OWNER_ADAPTER
        tmux_mod.Tmux._target_id = (
            lambda self, target, window: ("@7" if window else "$3")
        )
        tmux_mod.Tmux._pane_pids = lambda self, target: [123]
        tmux_mod.Tmux._protected_teardown_pids = lambda self, pane_leaders: frozenset()
        tmux_mod.Tmux._server_pid = lambda self: 999
        tmux_mod.Tmux._server_pane_leaders = lambda self: [123]
        tmux_mod.process_pgid = lambda pid: 123
        tmux_mod.process_cgroup = lambda pid: CGROUP
        tmux_mod.terminate_process_tree = lambda roots: TerminationResult(
            tuple(roots), tuple(roots), (), ()
        )

    def _restore_tmux(self) -> None:
        for name, original in self._saved.items():
            setattr(tmux_mod.Tmux, name, original)
        for name, original in self._saved_process.items():
            setattr(tmux_mod, name, original)

    def _mock_orphan_sweep(self, victims=(4242,), *, survived=(), kill_sent=()):
        """Make the session teardown reclaim ``victims``, some of which survive."""

        def reap(self, ownership, roles, pane_groups, *, operation, protected=None):
            if operation != "session停止":
                return ()
            outcome = TerminationResult(
                tuple(victims), tuple(victims), tuple(kill_sent), tuple(survived)
            )
            self._record_reclaim(victims, outcome)
            return tuple(survived)

        tmux_mod.Tmux._reap_escaped_processes = reap

    def _mock_distinct_panes(self):
        def pane_pids(self, target):
            if ":agent-" in target:
                return [111111]
            if ":game-" in target:
                return [222222]
            return [111111, 222222]

        tmux_mod.Tmux._pane_pids = pane_pids
        tmux_mod.process_pgid = lambda pid: pid
        tmux_mod.process_cgroup = lambda pid: f"0::/tmux-spawn-{pid}.scope"

    def run_cleanup(self) -> tuple[bool, dict[str, dict]]:
        """Put one runtime in ``retiring`` and run the real cleanup loop."""

        held = game_switch.GameSwitchLock(self.store.state_dir).acquire(exclusive=True)
        try:
            tx = game_switch.GameSwitchTransaction(self.store, held)
            tx.transition(
                {"idle"}, "idle",
                updates={"retiring": [dict(RUNTIME)], "next_generation": GENERATION + 1},
            )
            pending = self.coordinator._finalize_locked(
                tx, time.monotonic() + 20, []
            )
        finally:
            held.release()
        events = {}
        for event in self.coordinator.event_log.read_all():
            events[str(event["event"])] = event
        return pending, events


class TestCleanedEventRecordsEvidence(TeardownEvidenceTestBase):
    def test_multiple_panes_keep_success_probe_and_omission_count(self):
        self._mock_distinct_panes()
        self._mock_orphan_sweep(victims=(333333,), kill_sent=(333333,))
        pending, events = self.run_cleanup()
        self.assertFalse(pending)
        detail = events["cleaned"]["detail"]
        self.assertLessEqual(len(detail), 240)
        for field in (
            "confirmed=yes", "probe=adapter.alive=false", "signals=term,kill",
            "reclaimed=333333",
        ):
            self.assertIn(field, detail)
        self.assertRegex(detail, r"panes=\[.*\+\d+\]")

    def test_cleaned_records_target_scopes_signals_and_proof(self):
        self._mock_orphan_sweep()
        pending, events = self.run_cleanup()

        self.assertFalse(pending)
        cleaned = events["cleaned"]
        detail = cleaned["detail"]
        self.assertIsNotNone(detail)
        # The window/session ids of what was actually stopped.
        self.assertIn("windows=game:@7,agent:@7,session:$3", detail)
        # The proven ownership of the runtime that was stopped.
        self.assertIn("roles=agent,game,adapter", detail)
        # The process group and cgroup scope of the pane that was stopped.
        self.assertIn("pid=123", detail)
        self.assertIn("pgid=123", detail)
        self.assertIn("cg=tmux-spawn-abc123", detail)
        # The signal actually used, and the orphan the sweep reclaimed.
        self.assertIn("signals=term", detail)
        self.assertIn("reclaimed=4242", detail)
        # The liveness proof: this is what makes the event verifiable later.
        self.assertIn("confirmed=yes", detail)
        self.assertIn("probe=adapter.alive=false", detail)
        self.assertEqual(cleaned["result"], "cleaned")
        self.assertEqual(cleaned["runtime_id"], RUNTIME_ID)
        self.assertEqual(cleaned["generation"], GENERATION)

    def test_cleanup_started_records_the_targets_before_they_are_gone(self):
        self._mock_orphan_sweep()
        _pending, events = self.run_cleanup()

        started = events["cleanup_started"]["detail"]
        # The tmux objects exist only until the teardown runs, so the
        # pre-teardown record is the only place they can be captured.
        self.assertIn("game_window=game-g339", started)
        self.assertIn("agent_window=agent-g339", started)
        self.assertIn("adapter_session=docich-game-g339", started)
        self.assertIn(RUNTIME_ID, started)

    def test_evidence_fits_the_detail_budget(self):
        self._mock_orphan_sweep()
        _pending, events = self.run_cleanup()

        detail = events["cleaned"]["detail"]
        self.assertLessEqual(len(detail), MAX_EVIDENCE_DETAIL_CHARS + len(RUNTIME_ID) + 1)
        # game_switch truncates detail at 240 chars; nothing may be cut off.
        self.assertLessEqual(len(detail), 240)


class TestCleanupFailureRecordsWhatSurvived(TeardownEvidenceTestBase):
    def test_exception_argv_is_redacted_before_remark_and_event_budgets(self):
        for prefix_length in (60, 190, 230):
            with self.subTest(prefix_length=prefix_length):
                fixture = (
                    "fixture failure " * (prefix_length // 15)
                    + "argv=['--input', 'fixture-secret-1', '--tail', '"
                    + "x" * 80 + "']"
                )

                def boom(self, target, expected):
                    raise tmux_mod.TmuxError(fixture)

                tmux_mod.Tmux.kill_window_owned = boom
                pending, events = self.run_cleanup()
                self.assertTrue(pending)
                detail = events["cleanup_failed"]["detail"]
                self.assertLessEqual(len(detail), 240)
                self.assertNotIn("fixture-secret-1", detail)
                self.assertNotIn("--tail", detail)
                self.assertNotIn("--input", detail)

    def test_multiple_panes_keep_failure_diagnostics_in_final_event(self):
        self._mock_distinct_panes()
        self._mock_orphan_sweep(
            victims=(333333,), survived=(444444,), kill_sent=(333333,)
        )
        pending, events = self.run_cleanup()
        self.assertTrue(pending)
        self.assertEqual(
            {pane.pane_pid for pane in self.coordinator.last_teardown_evidence.pane_scopes},
            {111111, 222222},
        )
        detail = events["cleanup_failed"]["detail"]
        self.assertLessEqual(len(detail), 240)
        for field in (
            "confirmed=no", "remaining=444444", "reason=teardown後も停止を確認できなかった",
            "probe=消滅を確認できませんでした", "error_code=internal",
            "reclaimed=333333", "panes=[+2]",
        ):
            self.assertIn(field, detail)

    def test_unconfirmed_teardown_logs_cleanup_failed_with_survivors(self):
        self._mock_orphan_sweep(survived=(4242,))
        pending, events = self.run_cleanup()

        # The runtime stays tracked so recovery can retry it.
        self.assertTrue(pending)
        self.assertIn("cleanup_failed", events)
        self.assertNotIn("cleaned", events)
        failed = events["cleanup_failed"]
        self.assertEqual(failed["result"], "failed")
        self.assertTrue(failed["cleanup_pending"])
        detail = failed["detail"]
        self.assertIn("confirmed=no", detail)
        self.assertIn("remaining=4242", detail)
        self.assertIn("reason=", detail)

    def test_unreclaimable_runtime_is_listed_in_cleanup_pending(self):
        self._mock_orphan_sweep(survived=(4242,))
        _pending, events = self.run_cleanup()

        self.assertIn(RUNTIME_ID, events["cleanup_pending"]["detail"])

    def test_adapter_failure_records_the_error_code_and_detail(self):
        def boom(self, target, expected):
            raise tmux_mod.TmuxError("tmux window停止に失敗しました: permission denied")

        tmux_mod.Tmux.kill_window_owned = boom
        pending, events = self.run_cleanup()

        self.assertTrue(pending)
        detail = events["cleanup_failed"]["detail"]
        self.assertIn("confirmed=no", detail)
        self.assertIn("error_code=", detail)
        self.assertIn("permission denied", detail)

    def test_failed_teardown_reports_no_false_confirmation(self):
        self.adapter.alive_after = True
        self._mock_orphan_sweep()
        pending, events = self.run_cleanup()

        self.assertTrue(pending)
        self.assertIn("confirmed=no", events["cleanup_failed"]["detail"])
        self.assertNotIn("confirmed=yes", events["cleanup_failed"]["detail"])


class TestEventSchemaStaysCompatible(TeardownEvidenceTestBase):
    """Evidence is additive: existing consumers must keep working."""

    def test_schema_version_is_not_bumped(self):
        self._mock_orphan_sweep()
        _pending, events = self.run_cleanup()

        # Bumping the version would make the read-only diagnostics collector
        # (which accepts schema_version == 1 only) discard every cleanup row,
        # i.e. hide exactly the evidence this change exists to surface.
        self.assertEqual(events["cleaned"]["schema_version"], 1)
        self.assertEqual(events["cleanup_started"]["schema_version"], 1)

    def test_existing_event_fields_are_preserved(self):
        self._mock_orphan_sweep()
        _pending, events = self.run_cleanup()

        for name in ("cleanup_started", "cleaned"):
            event = events[name]
            for field in (
                "timestamp", "duration_ms", "event", "request_id", "operation",
                "from_game", "to_game", "target", "generation", "runtime_id",
                "phase", "result", "error_code", "detail", "cleanup_pending",
            ):
                self.assertIn(field, event)
            self.assertIsInstance(event["duration_ms"], int)
            self.assertIsInstance(event["cleanup_pending"], bool)


class TestTeardownEvidenceRendering(unittest.TestCase):
    def test_event_log_redacts_complete_and_preclipped_command_expressions(self):
        for expression in (
            "argv=['--input', 'fixture-secret-1', '--tail', '" + "x" * 80 + "']",
            "argv=['--input', 'fixture-secret-1', '--tail'",
            "command=\"fixture-tool fixture-secret-1 --tail",
            "args=('--input', 'fixture-secret-1', '--tail'",
        ):
            with self.subTest(expression=expression), tempfile.TemporaryDirectory() as directory:
                log = game_switch.EventLog(Path(directory) / "events.jsonl")
                log.emit("cleanup_failed", detail="fixture failure " * 10 + expression)
                detail = log.read_all()[0]["detail"]
                self.assertIn("<redacted>", detail)
                self.assertLessEqual(len(detail), 240)
                self.assertNotIn("fixture-secret-1", detail)
                self.assertNotIn("--tail", detail)

    def test_full_diagnostic_fields_are_redacted_before_each_field_budget(self):
        fixture = (
            "fixture failure " * 4
            + "argv=['--input', 'fixture-secret-1', '--tail', '" + "x" * 80 + "']"
        )
        for field in ("remaining_reason", "probe", "error_code", "remarks"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                evidence = TeardownEvidence()
                setattr(evidence, field, (fixture,) if field == "remarks" else fixture)
                log = game_switch.EventLog(Path(directory) / "events.jsonl")
                log.emit("cleanup_failed", detail=game_switch._teardown_detail(evidence, RUNTIME))
                detail = log.read_all()[0]["detail"]
                self.assertLessEqual(len(detail), 240)
                self.assertNotIn("fixture-secret-1", detail)
                self.assertNotIn("--tail", detail)

    def test_all_populated_fields_preserve_bounded_diagnostics(self):
        evidence = TeardownEvidence(
            pane_scopes=tuple(PaneScopeEvidence(pid, pid, CGROUP) for pid in range(111111, 111131)),
            reclaimed=tuple(range(333333, 333353)),
            remaining=tuple(range(444444, 444464)),
            remaining_reason="fixture-reason " * 20,
            probe="fixture-probe " * 20,
            error_code="round_boundary_unsupported",
            signals=("term", "kill"),
        )
        with tempfile.TemporaryDirectory() as directory:
            log = game_switch.EventLog(Path(directory) / "events.jsonl")
            log.emit("cleanup_failed", detail=game_switch._teardown_detail(evidence, RUNTIME))
            detail = log.read_all()[0]["detail"]
        self.assertLessEqual(len(evidence.render()), MAX_EVIDENCE_DETAIL_CHARS)
        self.assertLessEqual(len(detail), 240)
        for field in (
            "remaining=444444", "reason=fixture-reason", "probe=fixture-probe",
            "error_code=round_boundary_unsupported", "signals=term,kill",
        ):
            self.assertIn(field, detail)
        self.assertRegex(detail, r"remaining=[0-9,]+,\+\d+")

    def test_empty_evidence_renders_only_the_confirmation(self):
        self.assertEqual(TeardownEvidence().render(), "confirmed=no")

    def test_render_omits_unset_fields(self):
        evidence = TeardownEvidence(confirmed=True, probe="adapter.alive=false")
        self.assertEqual(evidence.render(), "confirmed=yes; probe=adapter.alive=false")

    def test_term_only_and_term_then_kill_are_distinguishable(self):
        term_only = TeardownEvidence(signals=("term",), confirmed=True, probe="adapter.alive=false")
        escalated = TeardownEvidence(signals=("term", "kill"), confirmed=True, probe="adapter.alive=false")
        self.assertRegex(term_only.render(), r"signals=term(?:;|$)")
        self.assertIn("signals=term,kill", escalated.render())
        # "term" must not be reported as an escalation that never happened.
        self.assertNotIn("signals=term,kill", term_only.render())

    def test_cgroup_leaf_is_kept_but_the_full_path_is_not(self):
        evidence = TeardownEvidence(
            pane_scopes=(
                PaneScopeEvidence(pane_pid=123, pgid=123, cgroup=CGROUP),
            )
        )
        rendered = evidence.render()
        self.assertIn("cg=tmux-spawn-abc123", rendered)
        self.assertNotIn("user.slice", rendered)

    def test_long_lists_are_capped_with_a_truncation_marker(self):
        evidence = TeardownEvidence(
            remaining=tuple(range(100, 200)),
            remaining_reason="teardown後も停止を確認できなかった",
        )
        rendered = evidence.render()
        self.assertIn("remaining=100,", rendered)
        self.assertIn("+", rendered)
        self.assertNotIn("199", rendered)

    def test_secret_bearing_evidence_is_redacted_by_the_log_sanitizer(self):
        """Evidence fields and EventLog share the same redaction policy.

        ``EventLog.emit`` runs ``_sanitize_log_detail`` on every ``detail``, so
        a teardown that surfaces an adapter message containing a token, a
        command line or a URL must still produce a clean log line.  This is
        exactly the #1105 shape: the interesting evidence (PIDs, scopes,
        signals) has to survive the redaction intact.
        """

        evidence = TeardownEvidence(
            game_window_id="@7",
            confirmed=True,
            probe="adapter.alive=false",
            remaining=(4242,),
            remaining_reason="stopping failed: https://hooks.example.invalid/x?token=SECRET-123 argv=['--key','hunter2hunter2hunter2hunter2AB']",
        )
        sanitized = game_switch._sanitize_log_detail(evidence.render())

        self.assertIn("confirmed=yes", sanitized)
        self.assertIn("remaining=4242", sanitized)
        for leaked in (
            "SECRET-123",
            "hooks.example.invalid",
            "--key",
            "hunter2hunter2hunter2hunter2AB",
            "argv",
        ):
            self.assertNotIn(leaked, sanitized)
        self.assertIn("<redacted", sanitized)

    def test_remarks_are_capped(self):
        evidence = TeardownEvidence()
        for index in range(50):
            evidence.add_remark(f"note {index}")
        self.assertLessEqual(len(evidence.remarks), 12)
        self.assertIn("note 0", evidence.render())

    def test_successful_requires_confirmation_without_survivors(self):
        self.assertTrue(TeardownEvidence(confirmed=True).successful)
        self.assertFalse(TeardownEvidence(confirmed=False).successful)
        self.assertFalse(TeardownEvidence(confirmed=True, remaining=(7,)).successful)


class TestRecorderBinding(unittest.TestCase):
    def test_bind_recorder_reaches_every_tmux_the_adapter_owns(self):
        class Adapter:
            def __init__(self):
                self.tmux = tmux_mod.Tmux("docich-game-g1")
                self.eval_tmux = tmux_mod.Tmux(server="docich-eval")

        adapter = Adapter()
        evidence = TeardownEvidence()
        bind_recorder(adapter, evidence)

        self.assertIs(adapter_recorder(adapter), evidence)
        self.assertIs(adapter.tmux._active_recorder(), evidence)
        self.assertIs(adapter.eval_tmux._active_recorder(), evidence)

    def test_unbound_tmux_records_nothing(self):
        self.assertIsNone(tmux_mod.Tmux()._active_recorder())

    def test_evidence_from_mapping_round_trips(self):
        payload = {
            "game_window_id": "@7",
            "session_id": "$3",
            "signals": ["term", "kill"],
            "confirmed": True,
            "probe": "adapter.alive=false",
            "remaining": [4242],
            "remaining_reason": "停止を確認できませんでした",
            "reclaimed": [4242],
            "pane_scopes": [{"pane_pid": 123, "pgid": 123, "cgroup": CGROUP}],
            "unknown_future_field": "ignored",
        }
        evidence = evidence_from_mapping(payload)  # type: ignore[arg-type]

        self.assertEqual(evidence.game_window_id, "@7")
        self.assertEqual(evidence.signals, ("term", "kill"))
        self.assertTrue(evidence.confirmed)
        self.assertEqual(evidence.remaining, (4242,))
        self.assertEqual(evidence.pane_scopes[0].pgid, 123)
        self.assertIn("cg=tmux-spawn-abc123", evidence.render())

    def test_evidence_from_mapping_tolerates_garbage(self):
        for payload in (None, "not a mapping", [], {}, {"signals": "not a list"}):
            self.assertEqual(evidence_from_mapping(payload).render(), "confirmed=no")


class TestReclaimSignalCollection(TeardownEvidenceTestBase):
    def test_reclaim_outcomes_join_pane_signals_in_the_final_event(self):
        for pane_present in (True, False):
            for escalated in (False, True):
                with self.subTest(pane_present=pane_present, escalated=escalated):
                    tmux_mod.Tmux._pane_pids = lambda self, target: [123] if pane_present else []
                    self._mock_orphan_sweep(kill_sent=(4242,) if escalated else ())
                    pending, events = self.run_cleanup()
                    self.assertFalse(pending)
                    expected = ("term", "kill") if escalated else ("term",)
                    evidence = self.coordinator.last_teardown_evidence
                    self.assertEqual(evidence.signals, expected)
                    self.assertEqual(evidence.reclaim_kill_sent, (4242,) if escalated else ())
                    detail = events["cleaned"]["detail"]
                    self.assertRegex(detail, "signals=" + ",".join(expected) + r"(?:;|$)")
                    self.assertIn("reclaimed=4242", detail)


if __name__ == "__main__":
    unittest.main()
