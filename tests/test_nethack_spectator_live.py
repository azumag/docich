from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from docich.nethack_spectator_live import (
    LiveNethackSpectator,
    MAX_FRAME_JSON_BYTES,
    NethackFrameReader,
    NethackFrameSnapshot,
    NethackSpectatorLiveError,
    SnapshotStore,
    encode_snapshot_json,
    active_nethack_runtime,
    process_window_name,
)
from docich.nethack_spectator import parse_tty
from docich.tmux import TmuxOwnership


RUNTIME_ID = "g3-deadbeef"


def ready_state(*, game: str = "nethack", phase: str = "ready") -> dict[str, object]:
    return {
        "phase": phase,
        "active": {
            "game": game,
            "adapter": "cli",
            "generation": 3,
            "runtime_id": RUNTIME_ID,
            "lease_id": None,
            "game_window": "game-g3",
            "agent_window": "agent-g3",
            "adapter_session": "docich-game-g3",
            "started_at": "2026-09-16T00:00:00Z",
        },
    }


class FakeTmux:
    def __init__(
        self,
        session: str,
        *,
        ownership: TmuxOwnership | None = None,
        windows: list[str] | None = None,
        capture: str = "msg\n.@..\n.|>.\nHP:10\nDlvl:2\n",
    ) -> None:
        self.session = session
        self.ownership = ownership or TmuxOwnership(RUNTIME_ID, 3, "game")
        self.session_ownership = TmuxOwnership(RUNTIME_ID, 3, "adapter")
        self.windows = (
            ["nethack-console", "game-g3", "agent-g3"]
            if windows is None
            else windows
        )
        self.capture = capture
        self.calls: list[tuple[str, str]] = []
        self.bounded_capture_error: Exception | None = None

    def read_session_ownership(self, session: str) -> TmuxOwnership:
        self.calls.append(("session_ownership", session))
        return self.session_ownership

    def read_session_ownership_bounded(self, session: str) -> TmuxOwnership:
        return self.read_session_ownership(session)

    def read_window_ownership(self, target: str) -> TmuxOwnership:
        self.calls.append(("ownership", target))
        return self.ownership

    def read_window_ownership_bounded(self, target: str) -> TmuxOwnership:
        return self.read_window_ownership(target)

    def list_windows(self) -> list[str]:
        self.calls.append(("list", self.session))
        return list(self.windows)

    def list_windows_bounded(self, _session: str) -> list[str]:
        return self.list_windows()

    def capture_pane_checked(self, target: str) -> str:
        self.calls.append(("capture", target))
        return self.capture

    def capture_pane_bounded_checked(
        self, target: str, *, cols: int, rows: int, max_bytes: int, timeout_s: float
    ) -> str:
        self.calls.append(("capture_bounded", target))
        if self.bounded_capture_error is not None:
            raise self.bounded_capture_error
        if len(self.capture.encode("utf-8")) > max_bytes:
            raise RuntimeError("bounded capture rejected")
        return self.capture


class TestActiveRuntime(unittest.TestCase):
    def test_committed_nethack_is_selected(self) -> None:
        runtime = active_nethack_runtime(ready_state())
        self.assertIsNotNone(runtime)
        assert runtime is not None
        self.assertEqual(runtime.runtime_id, RUNTIME_ID)
        self.assertEqual(runtime.generation, 3)
        self.assertEqual(runtime.target, "docich-game-g3:game-g3")

    def test_transitional_or_other_game_is_not_followed(self) -> None:
        self.assertIsNone(active_nethack_runtime(ready_state(phase="starting")))
        self.assertIsNone(active_nethack_runtime(ready_state(game="robots")))


class TestLiveSpectator(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.output = self.root / "spectator" / "index.html"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _spectator(self, state, *, tmux_factory=None, visual_mode="tiles") -> LiveNethackSpectator:
        kwargs = {}
        if tmux_factory is not None:
            kwargs["tmux_factory"] = tmux_factory
        return LiveNethackSpectator(
            state_dir=self.root / "run",
            output=self.output,
            cols=8,
            rows=5,
            interval_ms=500,
            refresh_ms=750,
            visual_mode=visual_mode,
            state_loader=lambda: state,
            now=lambda: 1234.5,
            **kwargs,
        )

    def test_idle_writes_obs_safe_standby_page(self) -> None:
        spectator = self._spectator({"phase": "idle", "active": None})
        self.assertEqual(spectator.render_once(), "idle")
        rendered = self.output.read_text(encoding="utf-8")
        self.assertIn("NetHackコーナー待機中です。", rendered)
        self.assertIn("window.location.reload()", rendered)
        self.assertIn("mode-tiles", rendered)
        status = json.loads((self.output.parent / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "idle")
        self.assertEqual(status["visual_mode"], "tiles")
        self.assertEqual(status["updated_at"], 1234.5)

    def test_active_capture_verifies_ownership_before_reading(self) -> None:
        made: list[FakeTmux] = []

        def factory(session: str) -> FakeTmux:
            tmux = FakeTmux(session)
            made.append(tmux)
            return tmux

        spectator = self._spectator(ready_state(), tmux_factory=factory)
        self.assertEqual(spectator.render_once(), "active")
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0].session, "docich-game-g3")
        self.assertEqual(
            made[0].calls,
            [
                ("session_ownership", "docich-game-g3"),
                ("ownership", "docich-game-g3:game-g3"),
                ("list", "docich-game-g3"),
                ("capture_bounded", "docich-game-g3:nethack-console"),
                ("session_ownership", "docich-game-g3"),
                ("ownership", "docich-game-g3:game-g3"),
                ("list", "docich-game-g3"),
            ],
        )
        self.assertNotIn(("capture_bounded", "docich-game-g3:game-g3"), made[0].calls)
        rendered = self.output.read_text(encoding="utf-8")
        self.assertIn("AI、ダンジョンに潜る", rendered)
        self.assertIn('class="cell player"', rendered)
        self.assertIn('href="#tile-player"', rendered)
        status = json.loads((self.output.parent / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "active")
        self.assertEqual(status["runtime_id"], RUNTIME_ID)
        self.assertEqual(status["generation"], 3)
        self.assertEqual(status["visual_mode"], "tiles")

    def test_ascii_visual_mode_is_operational_fallback(self) -> None:
        made: list[FakeTmux] = []

        def factory(session: str) -> FakeTmux:
            tmux = FakeTmux(session)
            made.append(tmux)
            return tmux

        spectator = self._spectator(
            ready_state(), tmux_factory=factory, visual_mode="ascii"
        )
        self.assertEqual(spectator.render_once(), "active")
        rendered = self.output.read_text(encoding="utf-8")
        self.assertIn("mode-ascii", rendered)
        self.assertNotIn('href="#tile-player"', rendered)
        status = json.loads((self.output.parent / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["visual_mode"], "ascii")

    def test_invalid_visual_mode_is_rejected(self) -> None:
        with self.assertRaises(NethackSpectatorLiveError):
            self._spectator({"phase": "idle", "active": None}, visual_mode="broken")

    def test_ownership_mismatch_keeps_last_good_frame(self) -> None:
        self.output.parent.mkdir(parents=True)
        self.output.write_text("LAST-GOOD", encoding="utf-8")

        def factory(session: str) -> FakeTmux:
            return FakeTmux(
                session,
                ownership=TmuxOwnership("g3-feedface", 3, "game"),
            )

        spectator = self._spectator(ready_state(), tmux_factory=factory)
        self.assertEqual(spectator.render_once(), "degraded")
        self.assertEqual(self.output.read_text(encoding="utf-8"), "LAST-GOOD")
        status = json.loads((self.output.parent / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["runtime_id"], RUNTIME_ID)
        self.assertIn("ownership", status["error"])

    def test_ambiguous_or_foreign_process_window_keeps_last_good_frame(self) -> None:
        cases = (
            [],
            ["game-g3", "agent-g3"],
            ["nethack-console", "game-g3", "agent-g3", "stray"],
            ["nethack-console", "game-g3", "agent-g3", "agent-g2"],
        )
        for windows in cases:
            with self.subTest(windows=windows):
                self.output.parent.mkdir(parents=True, exist_ok=True)
                self.output.write_text("LAST-GOOD", encoding="utf-8")
                made: list[FakeTmux] = []

                def factory(session: str) -> FakeTmux:
                    tmux = FakeTmux(session, windows=windows)
                    made.append(tmux)
                    return tmux

                spectator = self._spectator(ready_state(), tmux_factory=factory)
                self.assertEqual(spectator.render_once(), "degraded")
                self.assertEqual(self.output.read_text(encoding="utf-8"), "LAST-GOOD")
                self.assertEqual(
                    [call for call in made[0].calls if call[0] == "capture_bounded"], []
                )
                status = json.loads(
                    (self.output.parent / "status.json").read_text(encoding="utf-8")
                )
                self.assertEqual(status["status"], "degraded")
                self.assertIn("process window", status["error"])

    def test_transition_never_opens_candidate_tmux(self) -> None:
        opened: list[str] = []

        def factory(session: str):
            opened.append(session)
            raise AssertionError("transition must not open tmux")

        spectator = self._spectator(
            ready_state(phase="quiescing"),
            tmux_factory=factory,
        )
        self.assertEqual(spectator.render_once(), "idle")
        self.assertEqual(opened, [])


class TestProcessWindowName(unittest.TestCase):
    """game-g<N> runs only the mirroring xterm; the TTY is in the birth window."""

    def runtime(self):
        return active_nethack_runtime(ready_state())

    def test_production_window_listing_resolves_the_birth_window(self) -> None:
        # Observed on production (generation 244): the map was in the console
        # window, while game-g<N> held xterm's own keysym warnings.
        names = ["nethack-console", "game-g3", "agent-g3"]
        self.assertEqual(process_window_name(names, self.runtime()), "nethack-console")

    def test_order_does_not_matter(self) -> None:
        self.assertEqual(
            process_window_name(["agent-g3", "game-g3", "nethack-console"], self.runtime()),
            "nethack-console",
        )

    def test_missing_presentation_window_is_not_evidence(self) -> None:
        # A torn/empty listing must never be read as "the game window is the rest".
        for names in ([], ["nethack-console"], ["nethack-console", "agent-g3"]):
            with self.subTest(names=names):
                self.assertIsNone(process_window_name(names, self.runtime()))

    def test_ambiguous_or_absent_birth_window_fails_closed(self) -> None:
        for names in (
            ["game-g3", "agent-g3"],
            ["game-g3", "agent-g3", "nethack-console", "stray"],
        ):
            with self.subTest(names=names):
                self.assertIsNone(process_window_name(names, self.runtime()))

    def test_another_generations_agent_window_is_not_excluded(self) -> None:
        names = ["game-g3", "agent-g3", "nethack-console", "agent-g2"]
        self.assertIsNone(process_window_name(names, self.runtime()))

    def test_non_string_entries_are_ignored(self) -> None:
        names = ["game-g3", "agent-g3", "nethack-console", "", None]
        self.assertEqual(process_window_name(names, self.runtime()), "nethack-console")


class TestNethackFrameReader(unittest.TestCase):
    TTY = "msg\n.@!>\n.|d?\nHP:10\nDlvl:2\n"

    def setUp(self) -> None:
        self.runtime = active_nethack_runtime(ready_state())
        assert self.runtime is not None
        self.tmux = FakeTmux(
            "docich-game-g3",
            capture=self.TTY,
        )
        self.now = [100.0]

    def _reader(self, state_loader=None) -> NethackFrameReader:
        return NethackFrameReader(
            expected_runtime=self.runtime,
            state_loader=state_loader or (lambda: ready_state()),
            cols=8,
            rows=5,
            presentation_epoch="p-test-epoch",
            stale_after_ms=1000,
            tmux_factory=lambda _session: self.tmux,
            now_monotonic=lambda: self.now[0],
        )

    def test_explicit_empty_epoch_is_rejected(self) -> None:
        with self.assertRaises(NethackSpectatorLiveError):
            NethackFrameReader(
                expected_runtime=self.runtime,
                state_loader=lambda: ready_state(),
                presentation_epoch="",
            )

    def test_capture_is_pinned_before_and_after_and_advances_sequences(self) -> None:
        states = iter((ready_state(), ready_state(), ready_state(), ready_state()))
        reader = self._reader(lambda: next(states))
        first = reader.read_once()
        second = reader.read_once()
        self.assertEqual(first.state, "active")
        self.assertEqual(first.capture_seq, 1)
        self.assertEqual(first.content_seq, 1)
        self.assertEqual(second.capture_seq, 2)
        self.assertEqual(second.content_seq, 1)
        self.assertEqual(
            [call[0] for call in self.tmux.calls],
            [
                "session_ownership", "ownership", "list", "capture_bounded",
                "session_ownership", "ownership", "list",
                "session_ownership", "ownership", "list", "capture_bounded",
                "session_ownership", "ownership", "list",
            ],
        )

    def test_content_sequence_moves_only_when_the_terminal_changes(self) -> None:
        reader = self._reader()
        first = reader.read_once()
        self.tmux.capture = self.TTY.replace("HP:10", "HP:9")
        second = reader.read_once()
        self.assertEqual((first.capture_seq, first.content_seq), (1, 1))
        self.assertEqual((second.capture_seq, second.content_seq), (2, 2))

    def test_generation_change_during_capture_masks_the_frame(self) -> None:
        states = iter((ready_state(), {"phase": "quiescing", "active": ready_state()["active"]}))
        reader = self._reader(lambda: next(states))
        frame = reader.read_once()
        self.assertEqual(frame.state, "standby")
        self.assertEqual(frame.reason, "runtime_not_committed")
        self.assertIsNone(frame.frame)
        self.assertEqual(frame.capture_seq, 0)

    def test_missing_or_foreign_windows_fail_closed(self) -> None:
        for windows in ([], ["game-g3", "agent-g3"], ["nethack-console", "game-g3", "agent-g3", "stray"]):
            with self.subTest(windows=windows):
                self.tmux.windows = windows
                frame = self._reader().read_once()
                self.assertEqual(frame.state, "unavailable")
                self.assertEqual(frame.reason, "window_unavailable")
                self.assertFalse(any(call[0] == "capture_bounded" for call in self.tmux.calls))
                self.tmux.calls.clear()
        self.tmux.windows = ["nethack-console", "game-g3", "agent-g3"]

    def test_ownership_mismatch_never_captures(self) -> None:
        self.tmux.session_ownership = TmuxOwnership("g3-feedface", 3, "adapter")
        frame = self._reader().read_once()
        self.assertEqual(frame.state, "unavailable")
        self.assertEqual(frame.reason, "ownership_mismatch")
        self.assertFalse(any(call[0] == "capture_bounded" for call in self.tmux.calls))

    def test_same_runtime_capture_failure_is_stale_then_expires(self) -> None:
        reader = self._reader()
        first = reader.read_once()
        self.tmux.bounded_capture_error = RuntimeError("private child output")
        self.now[0] += 0.5
        stale = reader.read_once()
        self.assertEqual(stale.state, "stale")
        self.assertIs(stale.frame, first.frame)
        self.assertEqual(stale.reason, "capture_failed")
        self.now[0] += 1.1
        expired = reader.read_once()
        self.assertEqual(expired.state, "unavailable")
        self.assertEqual(expired.reason, "stale_timeout")
        self.assertIsNone(expired.frame)

    def test_snapshot_json_is_bounded_and_masks_expired_content(self) -> None:
        snapshot = self._reader().read_once()
        body = encode_snapshot_json(
            snapshot,
            now_monotonic=self.now[0],
            stale_after_ms=1000,
        )
        self.assertLessEqual(len(body), 262_144)
        self.assertIn(b'"capture_seq":1', body)
        self.assertIn(b'"tile_key":"player"', body)
        expired = encode_snapshot_json(
            snapshot,
            now_monotonic=self.now[0] + 2,
            stale_after_ms=1000,
        )
        self.assertIn(b'"state":"unavailable"', expired)
        self.assertIn(b'"frame_kind":"placeholder"', expired)
        self.assertNotIn(b'"tile_key":"player"', expired)

    def test_default_80_by_24_map_snapshot_fits_json_response_bound(self) -> None:
        map_lines = ["#" * 80 for _ in range(21)]
        center = list(map_lines[10])
        center[40] = "@"
        map_lines[10] = "".join(center)
        terminal = "Map status\n" + "\n".join(map_lines) + "\nHP:10\nDlvl:2\n"
        frame = parse_tty(terminal, cols=80, rows=24)
        self.assertEqual(frame.frame_kind, "map")
        snapshot = NethackFrameSnapshot(
            runtime=self.runtime,
            presentation_epoch="p-test-epoch",
            capture_seq=1,
            content_seq=1,
            state="active",
            frame=frame,
            captured_monotonic=self.now[0],
        )
        body = encode_snapshot_json(
            snapshot,
            now_monotonic=self.now[0],
            stale_after_ms=1000,
        )
        self.assertLessEqual(len(body), MAX_FRAME_JSON_BYTES)

    def test_snapshot_store_rejects_identity_and_sequence_regressions(self) -> None:
        from dataclasses import replace

        snapshot = self._reader().read_once()
        store = SnapshotStore(snapshot)
        with self.assertRaises(NethackSpectatorLiveError):
            store.publish(replace(snapshot, capture_seq=0))
        with self.assertRaises(NethackSpectatorLiveError):
            store.publish(replace(snapshot, presentation_epoch="p-other"))

if __name__ == "__main__":
    unittest.main()
