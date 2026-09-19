from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from docich.nethack_spectator_live import (
    LiveNethackSpectator,
    NethackSpectatorLiveError,
    active_nethack_runtime,
    process_window_name,
)
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
        capture: str = "msg\n.@..\n.|>.\nHP:10\nDlvl:2\n",
    ) -> None:
        self.session = session
        self.ownership = ownership or TmuxOwnership(RUNTIME_ID, 3, "game")
        self.capture = capture
        self.calls: list[tuple[str, str]] = []

    def read_window_ownership(self, target: str) -> TmuxOwnership:
        self.calls.append(("ownership", target))
        return self.ownership

    def capture_pane_checked(self, target: str) -> str:
        self.calls.append(("capture", target))
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
                ("ownership", "docich-game-g3:game-g3"),
                ("capture", "docich-game-g3:game-g3"),
            ],
        )
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
            ["game-g3", "agent-g3"],                                  # no birth window
            ["game-g3", "agent-g3", "nethack-console", "stray"],      # two candidates
        ):
            with self.subTest(names=names):
                self.assertIsNone(process_window_name(names, self.runtime()))

    def test_another_generations_agent_window_is_not_excluded(self) -> None:
        # Only this runtime's own agent window is excluded, so a leftover from
        # another generation makes the resolution ambiguous instead of wrong.
        names = ["game-g3", "agent-g3", "nethack-console", "agent-g2"]
        self.assertIsNone(process_window_name(names, self.runtime()))

    def test_non_string_entries_are_ignored(self) -> None:
        names = ["game-g3", "agent-g3", "nethack-console", "", None]
        self.assertEqual(process_window_name(names, self.runtime()), "nethack-console")


if __name__ == "__main__":
    unittest.main()
