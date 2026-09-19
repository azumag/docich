"""Recovery path for a manual NetHack corner wedged in ``active``/``draining``.

These tests drive the real ``ManualNethackCornerManager`` and the real operator
functions against a real canonical ``game_switch.json`` on disk.  Only the
coordinator is a double, and it mutates that same file the way the real one
does, so every branch of the reader / lock / state code is the production code.
"""
import json
import sys
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import game_switch  # noqa: E402
from docich import nethack_corner_operator as operator  # noqa: E402
from docich.naming import runtime_names  # noqa: E402
from docich.nethack_corner import NethackCornerError, NethackCornerManager  # noqa: E402
from docich.nethack_corner_manual import (  # noqa: E402
    STOP_BLOCKED_PREFIX,
    ManualNethackCornerManager,
)
from docich.retro_corner import RetroCornerError  # noqa: E402
from test_nethack_corner import NethackCornerTestBase  # noqa: E402
from test_round_boundary import BoundaryAdapter, BoundaryFactory, _coordinator, _wait_for_phase  # noqa: E402
from docich.adapters import nethack as nethack_adapter  # noqa: E402


def _runtime(generation: int, game: str) -> dict:
    names = runtime_names(generation)
    return {
        "game": game,
        "adapter": "cli",
        "generation": generation,
        "runtime_id": f"g{generation}-abcdef",
        "lease_id": str(uuid.uuid4()),
        "game_window": names.game_window,
        "agent_window": names.agent_window,
        "adapter_session": names.adapter_session,
        "started_at": "2026-09-19T10:00:00Z",
    }


def _ok():
    return SimpleNamespace(status="succeeded", error_code=None, detail=None)


class StoreCoordinator:
    """Coordinator double that mutates the real canonical file like the real one."""

    def __init__(self, test, *, drain="expired"):
        self.test = test
        self.calls: list[tuple[str, object]] = []
        # expired: recover() cancels the drain (canonical -> ready, receipt
        #          reported as failed/timeout exactly like the real coordinator)
        # live:    recover() is read-only and reports busy
        # stuck:   the boundary cancel fails, canonical stays draining
        # raises:  recover() itself blows up
        self.drain = drain

    def start(self, game):
        self.calls.append(("start", game))
        self.test.set_canonical("ready", game)
        return _ok()

    def switch(self, game):
        self.calls.append(("switch", game))
        self.test.set_canonical("ready", game)
        return _ok()

    def stop(self):
        self.calls.append(("stop", None))
        self.test.set_canonical("idle")
        return _ok()

    def recover(self):
        self.calls.append(("recover", None))
        if self.drain == "raises":
            raise RuntimeError("boom")
        if self.drain == "live":
            return SimpleNamespace(status="busy", error_code="busy", detail="waiting")
        if self.drain == "stuck":
            return SimpleNamespace(
                status="failed", error_code="recovery_required", detail="cancel failed"
            )
        state, _ = self.test.store.canonical.load()
        self.test.set_canonical("ready", state["active"]["game"], keep_active=True)
        return SimpleNamespace(status="failed", error_code="timeout", detail="drain cancelled")


class ForceRecoverBase(NethackCornerTestBase):
    def setUp(self):
        super().setUp()
        self.store = game_switch.GameSwitchStore(self.g.state_dir)
        self.manual_path = Path(self.g.state_dir) / "nethack_corner_manual.json"

    # -- fixtures ----------------------------------------------------------
    def set_canonical(self, phase, game=None, *, keep_active=False):
        state = self.store.canonical.initialize()
        if keep_active:
            active = state["active"]
            generation = int(active["generation"])
        elif game is not None:
            generation = int(state["next_generation"])
            active = _runtime(generation, game)
        else:
            active, generation = None, 0
        state.update(
            phase=phase,
            active=active,
            candidate=None,
            previous=None,
            operation="switch" if phase == "draining" else None,
            request_id=str(uuid.uuid4()) if phase == "draining" else None,
            deadline_at=None,
            next_generation=max(int(state["next_generation"]), generation + 1),
        )
        self.store.canonical.save(state)

    def canonical(self):
        state, _ = self.store.canonical.load()
        game = state["active"]["game"] if state["active"] else None
        return state["phase"], game

    def set_manual(self, status, *, previous="sorengame", **extra):
        state = {
            "schema_version": 1,
            "status": status,
            "date": "2026-09-19",
            "game": "nethack",
            "previous_game": previous,
            "started_at": "2026-09-19T21:00:00+09:00",
            "ends_at": "2026-09-19T21:05:00+09:00",
            "completed_at": None,
            "last_error": None,
        }
        state.update(extra)
        self.manual_path.parent.mkdir(parents=True, exist_ok=True)
        self.manual_path.write_text(json.dumps(state), encoding="utf-8")

    def manual_state(self):
        return json.loads(self.manual_path.read_text(encoding="utf-8"))

    def make_manager(self, coordinator, **kwargs):
        kwargs.setdefault("ensure_runtime", lambda: None)
        return ManualNethackCornerManager(
            self.g,
            duration_minutes=5,
            config=self.cfg,
            coordinator=coordinator,
            now=lambda: self.now_value,
            sleep=lambda seconds: None,
            chat=self.chats.append,
            voice=self.voices.append,
            **kwargs,
        )

    def force_recover(self, coordinator):
        def factory(g, duration_minutes):
            return self.make_manager(coordinator)

        with mock.patch.object(operator, "load_global", return_value=self.g), mock.patch.object(
            operator, "ManualNethackCornerManager", side_effect=factory
        ):
            return operator.force_recover(self.g.config_path)

    def operator_recover(self, coordinator):
        def factory(g, duration_minutes):
            return self.make_manager(coordinator)

        with mock.patch.object(operator, "load_global", return_value=self.g), mock.patch.object(
            operator, "ManualNethackCornerManager", side_effect=factory
        ):
            return operator.recover(self.g.config_path)


class TestForceRecoverProductionShape(ForceRecoverBase):
    """draining + manual active + NetHack canonical active (the stuck production state)."""

    def test_inherited_stop_raises_on_draining_and_leaves_state_active(self):
        # Root-cause evidence: the canonical-phase read at the top of the
        # inherited ``_finish_locked`` raises for ``draining`` *before* its
        # ``failed`` bookkeeping, so the state stays ``active`` and every later
        # ``stop`` hits the same wall.  (The scheduled manager keeps this
        # behaviour; only the manual manager is hardened, see below.)
        self.set_canonical("draining", "nethack")
        self.set_manual("active")
        coord = StoreCoordinator(self)
        scheduled = NethackCornerManager(
            self.g,
            config=self.cfg,
            coordinator=coord,
            now=lambda: self.now_value,
            sleep=lambda s: None,
            ensure_runtime=lambda: None,
            chat=self.chats.append,
            voice=self.voices.append,
        )
        scheduled.state_path = self.manual_path
        with self.assertRaises(RetroCornerError) as ctx:
            scheduled.stop()
        self.assertIn("安定phase", str(ctx.exception))
        self.assertEqual(self.manual_state()["status"], "active")
        self.assertEqual(coord.calls, [])

    def test_force_recover_unwedges_drain_but_never_stops_nethack(self):
        self.set_canonical("draining", "nethack")
        self.set_manual("active")
        coord = StoreCoordinator(self, drain="expired")

        category = self.force_recover(coord)

        # NetHack is still the canonical game: fail closed, point at ``stop``.
        self.assertEqual(category, "nethack_active_use_stop")
        self.assertEqual(coord.calls, [("recover", None)])
        self.assertEqual(self.canonical(), ("ready", "nethack"))
        self.assertEqual(self.manual_state()["status"], "active")

    def test_full_chain_recover_then_stop_then_start_restarts_the_agent(self):
        self.set_canonical("draining", "nethack")
        self.set_manual("active")
        coord = StoreCoordinator(self, drain="expired")
        self.assertEqual(self.force_recover(coord), "nethack_active_use_stop")

        # 1) the ordinary, unchanged stop now goes through the coordinator
        #    switch-back (this is where the save boundary lives).
        result = self.make_manager(coord).stop()
        self.assertEqual(result.status, "completed")
        self.assertEqual(coord.calls[-1], ("switch", "sorengame"))
        self.assertEqual(self.canonical(), ("ready", "sorengame"))
        self.assertEqual(self.manual_state()["status"], "completed")

        # 2) start is possible again and switches into a *fresh* NetHack runtime.
        result = self.make_manager(coord).start()
        self.assertEqual(result.status, "completed")
        self.assertIn(("switch", "nethack"), coord.calls)

    def test_failed_manual_state_is_routed_to_the_reviewed_recover_op(self):
        self.set_canonical("draining", "nethack")
        self.set_manual("failed", last_error="boom")
        coord = StoreCoordinator(self, drain="expired")

        category = self.force_recover(coord)
        self.assertEqual(category, "nethack_active_use_recover")
        self.assertEqual(coord.calls, [("recover", None)])
        self.assertEqual(self.manual_state()["status"], "failed")

        result = self.operator_recover(coord)
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(coord.calls[-1], ("switch", "sorengame"))
        self.assertEqual(self.canonical(), ("ready", "sorengame"))
        self.assertEqual(self.make_manager(coord).start().status, "completed")


class TestForceRecoverResolvesToStartable(ForceRecoverBase):
    def test_wedged_start_transition_becomes_interrupted_and_startable(self):
        # A start that wedged while draining the *old* game: NetHack never ran.
        self.set_canonical("draining", "sorengame")
        self.set_manual("starting")
        coord = StoreCoordinator(self, drain="expired")

        self.assertEqual(self.force_recover(coord), "recovered")

        self.assertEqual(coord.calls, [("recover", None)])
        self.assertEqual(self.canonical(), ("ready", "sorengame"))
        state = self.manual_state()
        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(state["finish_reason"], "force_recover")
        self.assertIsNotNone(state["completed_at"])
        # start is no longer blocked by the manual state or the canonical phase.
        self.assertEqual(self.make_manager(coord).start().status, "completed")
        self.assertIn(("switch", "nethack"), coord.calls)

    def test_manual_active_but_game_already_switched_away_is_terminated(self):
        self.set_canonical("ready", "sorengame")
        self.set_manual("active")
        coord = StoreCoordinator(self)

        self.assertEqual(self.force_recover(coord), "recovered")

        self.assertEqual(coord.calls, [])  # stable canonical: coordinator untouched
        self.assertEqual(self.manual_state()["status"], "interrupted")

    def test_idle_canonical_with_manual_active_is_terminated(self):
        self.set_canonical("idle")
        self.set_manual("active")
        coord = StoreCoordinator(self)
        self.assertEqual(self.force_recover(coord), "recovered")
        self.assertEqual(self.manual_state()["status"], "interrupted")
        self.assertEqual(coord.calls, [])

    def test_recovered_drain_with_unowned_nethack_is_out_of_scope(self):
        self.set_canonical("draining", "nethack")
        self.set_manual("completed")
        coord = StoreCoordinator(self, drain="expired")
        # NetHack is still active with nobody owning it: not ours to end.
        self.assertEqual(self.force_recover(coord), "out_of_scope")
        self.assertEqual(coord.calls, [("recover", None)])

    def test_nothing_to_recover_is_a_noop(self):
        for status in ("completed", "interrupted", "failed"):
            with self.subTest(status=status):
                self.set_canonical("ready", "sorengame")
                self.set_manual(status)
                before = self.manual_path.read_text(encoding="utf-8")
                coord = StoreCoordinator(self)
                self.assertEqual(self.force_recover(coord), "nothing_to_recover")
                self.assertEqual(coord.calls, [])
                self.assertEqual(self.manual_path.read_text(encoding="utf-8"), before)

    def test_no_manual_state_file_is_a_noop(self):
        self.set_canonical("ready", "sorengame")
        coord = StoreCoordinator(self)
        self.assertEqual(self.force_recover(coord), "nothing_to_recover")
        self.assertFalse(self.manual_path.exists())


class TestForceRecoverFailsClosed(ForceRecoverBase):
    def test_nethack_canonical_active_is_never_stopped_or_switched(self):
        self.set_canonical("ready", "nethack")
        self.set_manual("active")
        before = self.manual_path.read_text(encoding="utf-8")
        coord = StoreCoordinator(self)

        self.assertEqual(self.force_recover(coord), "nethack_active_use_stop")

        self.assertEqual(coord.calls, [])  # not even recover(): canonical is stable
        self.assertEqual(self.canonical(), ("ready", "nethack"))
        self.assertEqual(self.manual_path.read_text(encoding="utf-8"), before)

    def test_starting_with_nethack_active_is_marked_failed_for_recover(self):
        self.set_canonical("ready", "nethack")
        self.set_manual("starting")
        coord = StoreCoordinator(self)

        self.assertEqual(self.force_recover(coord), "nethack_active_use_recover")

        self.assertEqual(coord.calls, [])
        self.assertEqual(self.manual_state()["status"], "failed")
        self.assertEqual(self.manual_state()["previous_game"], "sorengame")
        # ...which is exactly what unlocks the reviewed recover op.
        self.assertEqual(self.operator_recover(coord)["status"], "recovered")

    def test_unknown_restore_target_is_out_of_scope(self):
        for previous in (None, "", "nethack"):
            with self.subTest(previous=previous):
                self.set_canonical("ready", "nethack")
                self.set_manual("failed", previous=previous)
                coord = StoreCoordinator(self)
                self.assertEqual(self.force_recover(coord), "out_of_scope")
                self.assertEqual(coord.calls, [])
                self.assertEqual(self.canonical(), ("ready", "nethack"))

    def test_nethack_active_without_a_manual_corner_is_out_of_scope(self):
        for status in ("idle", "completed", "interrupted"):
            with self.subTest(status=status):
                self.set_canonical("ready", "nethack")
                self.set_manual(status)
                coord = StoreCoordinator(self)
                self.assertEqual(self.force_recover(coord), "out_of_scope")
                self.assertEqual(coord.calls, [])

    def test_start_over_a_live_nethack_would_not_start_a_fresh_runtime(self):
        # Why force-recover hands off to stop/recover instead of ``start``: with
        # NetHack still canonical, start's transition is a no-op (current ==
        # target), so the old runtime -- and its old-code agent -- keeps running.
        self.set_canonical("ready", "nethack")
        self.set_manual("interrupted")
        coord = StoreCoordinator(self)
        self.make_manager(coord).start()
        self.assertNotIn(("switch", "nethack"), coord.calls)
        self.assertNotIn(("start", "nethack"), coord.calls)
        self.assertEqual(self.canonical(), ("ready", "nethack"))

    def test_live_drain_is_left_untouched(self):
        self.set_canonical("draining", "nethack")
        self.set_manual("active")
        before = self.manual_path.read_text(encoding="utf-8")
        coord = StoreCoordinator(self, drain="live")

        self.assertEqual(self.force_recover(coord), "switch_busy")

        self.assertEqual(coord.calls, [("recover", None)])
        self.assertEqual(self.canonical(), ("draining", "nethack"))
        self.assertEqual(self.manual_path.read_text(encoding="utf-8"), before)

    def test_drain_that_cannot_be_cancelled_stays_recoverable(self):
        self.set_canonical("draining", "nethack")
        self.set_manual("active")
        before = self.manual_path.read_text(encoding="utf-8")
        coord = StoreCoordinator(self, drain="stuck")

        # the adapter refused to acknowledge the cancel (``recovery_required``)
        self.assertEqual(self.force_recover(coord), "drain_cancel_refused")

        self.assertEqual(self.canonical(), ("draining", "nethack"))
        self.assertEqual(self.manual_path.read_text(encoding="utf-8"), before)

    def test_other_failed_recoveries_keep_the_generic_category(self):
        self.set_canonical("draining", "nethack")
        self.set_manual("active")
        coord = StoreCoordinator(self, drain="stuck")
        coord.recover = lambda: SimpleNamespace(status="failed", error_code="x", detail="x")
        self.assertEqual(self.force_recover(coord), "switch_recover_failed")

    def test_coordinator_exception_is_categorised_not_raised(self):
        self.set_canonical("draining", "nethack")
        self.set_manual("active")
        coord = StoreCoordinator(self, drain="raises")
        self.assertEqual(self.force_recover(coord), "switch_recover_failed")
        self.assertEqual(self.manual_state()["status"], "active")

    def test_unrelated_transition_is_not_touched(self):
        self.set_canonical("draining", "sorengame")
        self.set_manual("completed")
        coord = StoreCoordinator(self)
        self.assertEqual(self.force_recover(coord), "out_of_scope")
        self.assertEqual(coord.calls, [])
        self.assertEqual(self.canonical(), ("draining", "sorengame"))

    def test_unreadable_manual_state_is_not_overwritten(self):
        self.set_canonical("ready", "sorengame")
        self.manual_path.parent.mkdir(parents=True, exist_ok=True)
        self.manual_path.write_text("{broken", encoding="utf-8")
        coord = StoreCoordinator(self)
        self.assertEqual(self.force_recover(coord), "manual_state_unreadable")
        self.assertEqual(self.manual_path.read_text(encoding="utf-8"), "{broken")
        self.assertEqual(coord.calls, [])

    def test_unreadable_canonical_state_is_reported(self):
        self.set_manual("active")
        canonical_path = Path(self.g.state_dir) / "game_switch.json"
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_path.write_text("{broken", encoding="utf-8")
        coord = StoreCoordinator(self)
        self.assertEqual(self.force_recover(coord), "switch_state_unreadable")
        self.assertEqual(coord.calls, [])
        self.assertEqual(self.manual_state()["status"], "active")

    def test_canonical_that_becomes_unreadable_after_recover_is_reported(self):
        self.set_canonical("draining", "nethack")
        self.set_manual("active")
        coord = StoreCoordinator(self)
        canonical_path = Path(self.g.state_dir) / "game_switch.json"

        def corrupt():
            canonical_path.write_text("{broken", encoding="utf-8")
            return SimpleNamespace(status="failed", error_code="x", detail="x")

        coord.recover = corrupt
        self.assertEqual(self.force_recover(coord), "switch_state_unreadable")

    def test_live_runner_holding_the_manual_lock_blocks_force_recover(self):
        self.set_canonical("draining", "nethack")
        self.set_manual("active")
        coord = StoreCoordinator(self)
        runner = self.make_manager(coord)
        with runner._locked():
            self.assertEqual(self.force_recover(coord), "manual_lock_busy")
        self.assertEqual(coord.calls, [])
        self.assertEqual(self.canonical(), ("draining", "nethack"))
        self.assertEqual(self.manual_state()["status"], "active")

    def test_unexpected_failure_is_categorised(self):
        self.set_canonical("ready", "sorengame")
        self.set_manual("active")
        coord = StoreCoordinator(self)
        with mock.patch.object(
            ManualNethackCornerManager, "_write_state", side_effect=OSError("disk")
        ):
            self.assertEqual(self.force_recover(coord), "unexpected_error")
        self.assertEqual(self.manual_state()["status"], "active")


class TestManualStopIsNeverLeftActive(ForceRecoverBase):
    def test_ensure_runtime_failure_records_failed_instead_of_active(self):
        self.set_canonical("ready", "nethack")
        self.set_manual("active")
        coord = StoreCoordinator(self)

        def broken():
            raise RuntimeError("display gone")

        manager = self.make_manager(coord, ensure_runtime=broken)
        with self.assertRaises(NethackCornerError) as ctx:
            manager.stop()

        self.assertIn(f"{STOP_BLOCKED_PREFIX}runtime_unavailable", str(ctx.exception))
        state = self.manual_state()
        self.assertEqual(state["status"], "failed")
        self.assertTrue(state["last_error"].startswith(f"{STOP_BLOCKED_PREFIX}runtime_unavailable"))
        self.assertEqual(coord.calls, [])  # nothing was switched
        self.assertEqual(operator.status_category(Path(self.g.state_dir)), "failed")
        # Not stuck: a second stop is a clean noop, and the reviewed recover
        # op (which does not need the runtime preflight) restores the game.
        self.assertEqual(self.make_manager(coord).stop().status, "noop")
        self.assertEqual(self.operator_recover(coord)["status"], "recovered")
        self.assertEqual(self.canonical(), ("ready", "sorengame"))

    def test_draining_canonical_records_failed_with_a_fixed_category(self):
        self.set_canonical("draining", "nethack")
        self.set_manual("active")
        coord = StoreCoordinator(self)

        with self.assertRaises(NethackCornerError) as ctx:
            self.make_manager(coord).stop()

        self.assertIn(f"{STOP_BLOCKED_PREFIX}switch_not_stable", str(ctx.exception))
        self.assertEqual(self.manual_state()["status"], "failed")
        self.assertEqual(coord.calls, [])
        self.assertEqual(self.canonical(), ("draining", "nethack"))
        # The way out of the wedge is the fixed force-recover op.
        self.assertEqual(self.force_recover(coord), "nethack_active_use_recover")

    def test_unreadable_canonical_records_failed_with_a_fixed_category(self):
        self.set_manual("active")
        canonical_path = Path(self.g.state_dir) / "game_switch.json"
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_path.write_text("{broken", encoding="utf-8")
        coord = StoreCoordinator(self)
        with self.assertRaises(NethackCornerError) as ctx:
            self.make_manager(coord).stop()
        self.assertIn(f"{STOP_BLOCKED_PREFIX}switch_state_unreadable", str(ctx.exception))
        self.assertEqual(self.manual_state()["status"], "failed")

    def test_error_message_carries_only_the_fixed_token(self):
        self.set_canonical("ready", "nethack")
        self.set_manual("active")

        def broken():
            raise RuntimeError("token=SUPERSECRET")

        with self.assertRaises(NethackCornerError) as ctx:
            self.make_manager(StoreCoordinator(self), ensure_runtime=broken).stop()
        self.assertNotIn("SUPERSECRET", str(ctx.exception))

    def test_state_write_failure_still_surfaces_the_category(self):
        self.set_canonical("ready", "nethack")
        self.set_manual("active")

        def broken():
            raise RuntimeError("display gone")

        manager = self.make_manager(StoreCoordinator(self), ensure_runtime=broken)
        with mock.patch.object(
            ManualNethackCornerManager, "_write_state", side_effect=OSError("disk")
        ):
            with self.assertRaises(NethackCornerError) as ctx:
                manager.stop()
        self.assertIn("runtime_unavailable", str(ctx.exception))
        self.assertEqual(self.manual_state()["status"], "active")

    def test_regular_stop_semantics_are_unchanged(self):
        self.set_canonical("ready", "nethack")
        self.set_manual("active")
        coord = StoreCoordinator(self)

        result = self.make_manager(coord).stop()

        self.assertEqual(result.status, "completed")
        self.assertEqual(coord.calls, [("switch", "sorengame")])
        self.assertEqual(self.canonical(), ("ready", "sorengame"))
        self.assertEqual(self.manual_state()["status"], "completed")

    def test_stop_when_not_active_is_still_a_noop(self):
        self.set_canonical("ready", "sorengame")
        self.set_manual("completed")
        coord = StoreCoordinator(self)
        result = self.make_manager(coord).stop()
        self.assertEqual((result.status, result.detail), ("noop", "not-active"))
        self.assertEqual(coord.calls, [])

    def test_switch_back_failure_still_records_failed_via_finish(self):
        self.set_canonical("ready", "nethack")
        self.set_manual("active")
        coord = StoreCoordinator(self)
        coord.switch = lambda game: SimpleNamespace(
            status="failed", error_code="timeout", detail="boundary timeout"
        )
        with self.assertRaises(RetroCornerError):
            self.make_manager(coord).stop()
        state = self.manual_state()
        self.assertEqual(state["status"], "failed")
        self.assertNotIn(STOP_BLOCKED_PREFIX, state["last_error"])
        self.assertIn("boundary timeout", state["last_error"])

    def test_scheduled_corner_stop_semantics_are_not_changed(self):
        # Only the manual manager is hardened: a scheduled corner keeps retrying
        # from its tick, so its stop must still leave the state untouched.
        self.set_canonical("ready", "nethack")
        self.set_manual("active")

        def broken():
            raise RuntimeError("display gone")

        scheduled = NethackCornerManager(
            self.g,
            config=self.cfg,
            coordinator=StoreCoordinator(self),
            now=lambda: self.now_value,
            sleep=lambda s: None,
            ensure_runtime=broken,
            chat=self.chats.append,
            voice=self.voices.append,
        )
        scheduled.state_path = self.manual_path
        with self.assertRaises(RuntimeError):
            scheduled.stop()
        self.assertEqual(self.manual_state()["status"], "active")


class ReleasingFactory(BoundaryFactory):
    """Boundary adapters block until released; new ones auto-release once armed."""

    auto_release = False

    def __call__(self, spec):
        fresh = (spec.game, spec.generation) not in self.adapters
        adapter = super().__call__(spec)
        if fresh and self.auto_release:
            adapter.boundary_release.set()
        return adapter


class TestForceRecoverWithRealCoordinator(ForceRecoverBase):
    """The headline scenario through the real GameSwitchCoordinator.recover()."""

    def test_expired_real_drain_then_stop_then_start_replaces_the_nethack_runtime(self):
        factory = ReleasingFactory()
        store, coordinator = _coordinator(factory, Path(self.g.state_dir))
        self.assertEqual(coordinator.start("nethack").status, "succeeded")
        old = factory.adapters[("nethack", 1)]
        request_id = str(uuid.uuid4())
        box = []
        worker = threading.Thread(
            target=lambda: box.append(
                coordinator.switch("robots", request_id=request_id, timeout_s=60.0)
            )
        )
        worker.start()
        try:
            _wait_for_phase(store, "draining")
            self.assertTrue(old.boundary_entered.wait(1.0))
            # The driver died mid-drain and its durable deadline has elapsed.
            with store.lock(exclusive=True, blocking=False):
                state, _ = store.canonical.load()
                state["deadline_at"] = "2000-01-01T00:00:00Z"
                store.canonical.save(state)
            self.set_manual("active", previous="robots")

            # The wedge: the inherited stop cannot make progress on ``draining``.
            with self.assertRaises(NethackCornerError):
                self.make_manager(coordinator).stop()
            self.assertEqual(self.manual_state()["status"], "failed")
            self.set_manual("active", previous="robots")  # restore the wedged shape

            self.assertEqual(self.force_recover(coordinator), "nethack_active_use_stop")
            # Real coordinator contract: drain cancelled, same runtime kept alive,
            # and force-recover itself did not stop or switch anything.
            self.assertEqual(self.canonical(), ("ready", "nethack"))
            self.assertTrue(old.runtime.alive)
            self.assertNotIn("stop_agent", old.runtime.events)
            self.assertEqual(old.cancel_request_ids, [request_id])
            self.assertEqual(self.manual_state()["status"], "active")
        finally:
            old.boundary_release.set()
            worker.join(3.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(box[0].status, "failed")

        factory.auto_release = True
        # 1) regular stop: switch back through the coordinator (boundary + teardown)
        self.assertEqual(self.make_manager(coordinator).stop().status, "completed")
        self.assertEqual(self.canonical(), ("ready", "robots"))
        self.assertIn("stop_agent", old.runtime.events)
        self.assertFalse(old.runtime.alive)
        # 2) start brings up a *new* NetHack runtime (new generation => new agent)
        self.assertEqual(self.make_manager(coordinator).start().status, "completed")
        generations = sorted(g for (game, g) in factory.adapters if game == "nethack")
        self.assertGreater(len(generations), 1)
        self.assertGreater(generations[-1], 1)
        self.assertEqual(self.canonical(), ("ready", "robots"))


class _NethackPane:
    """tmux double for the game window: shows a pane and reacts to ``n``."""

    def __init__(self, pane):
        self.pane_text = pane
        self.process_alive = True
        self.calls = []

    def session_target_exists(self, session):
        return True

    def window_target_exists(self, target, *, strict=False):
        return self.process_alive

    def capture_pane(self, target):
        return self.pane_text

    def send_keys(self, target, keys, literal=False):
        self.calls.append((list(keys), literal))
        if keys == ["n"]:
            self.pane_text = "Dlvl:1 HP:16(16)"


class NethackCancelAdapter(BoundaryAdapter):
    """Boundary adapter whose cancel is the *real* NetHack adapter's, over a fake pane."""

    def __init__(self, spec, tmux, **kwargs):
        super().__init__(spec, **kwargs)
        real = object.__new__(nethack_adapter.NethackCoordinatorAdapter)
        real.spec = SimpleNamespace(adapter_session="adapter")
        real.tmux = tmux
        real._check_active = lambda deadline, cancel: None
        real._verify_session_ownership = lambda: None
        real._runtime_process_window_target = lambda: "adapter:nethack"
        self._real = real

    def cancel_round_boundary(self, request_id, deadline, cancel):
        self.cancel_request_ids.append(request_id)
        return self._real.cancel_round_boundary(request_id, deadline, cancel)


class NethackCancelFactory(ReleasingFactory):
    def __init__(self, tmux):
        super().__init__()
        self.tmux = tmux

    def __call__(self, spec):
        key = (spec.game, spec.generation)
        if key not in self.adapters and spec.game == "nethack":
            adapter = NethackCancelAdapter(spec, self.tmux, required=True, method=True)
            self.adapters[key] = adapter
            if self.auto_release:
                adapter.boundary_release.set()
            return adapter
        return super().__call__(spec)


class TestForceRecoverWithTheRealNethackCancel(ForceRecoverBase):
    """The production wedge: NetHack parked on ``Really save? [yn] (n)`` after its driver died."""

    def _wedge(self, pane):
        tmux = _NethackPane(pane)
        factory = NethackCancelFactory(tmux)
        store, coordinator = _coordinator(factory, Path(self.g.state_dir))
        self.assertEqual(coordinator.start("nethack").status, "succeeded")
        old = factory.adapters[("nethack", 1)]
        request_id = str(uuid.uuid4())
        box = []
        worker = threading.Thread(
            target=lambda: box.append(coordinator.switch("robots", request_id=request_id, timeout_s=60.0))
        )
        worker.start()
        _wait_for_phase(store, "draining")
        self.assertTrue(old.boundary_entered.wait(1.0))
        with store.lock(exclusive=True, blocking=False):
            state, _ = store.canonical.load()
            state["deadline_at"] = "2000-01-01T00:00:00Z"  # the driver died long ago
            store.canonical.save(state)
        self.set_manual("active", previous="robots")
        return tmux, factory, coordinator, old, worker

    def test_pending_save_prompt_is_dismissed_so_the_drain_can_be_cancelled(self):
        tmux, factory, coordinator, old, worker = self._wedge("Really save? [yn] (n)\n|......@...|")
        try:
            self.assertEqual(self.force_recover(coordinator), "nethack_active_use_stop")
            self.assertEqual(tmux.calls, [(["n"], True)])  # exactly one key, and it is ``n``
            self.assertEqual(self.canonical(), ("ready", "nethack"))
            self.assertTrue(old.runtime.alive)
            self.assertNotIn("stop_agent", old.runtime.events)
            self.assertEqual(self.manual_state()["status"], "active")
        finally:
            old.boundary_release.set()
            worker.join(3.0)
        self.assertFalse(worker.is_alive())

        # ...and the regular stop then switches back through the coordinator.
        factory.auto_release = True
        self.assertEqual(self.make_manager(coordinator).stop().status, "completed")
        self.assertEqual(self.canonical(), ("ready", "robots"))

    def test_without_a_pending_prompt_the_refusal_is_reported_and_nothing_is_sent(self):
        tmux, factory, coordinator, old, worker = self._wedge("Dlvl:1 HP:16(16)")
        try:
            before = self.manual_path.read_text(encoding="utf-8")
            self.assertEqual(self.force_recover(coordinator), "drain_cancel_refused")
            self.assertEqual(tmux.calls, [])
            self.assertEqual(self.canonical(), ("draining", "nethack"))
            self.assertEqual(self.manual_path.read_text(encoding="utf-8"), before)
        finally:
            old.boundary_release.set()
            worker.join(3.0)
        self.assertFalse(worker.is_alive())
