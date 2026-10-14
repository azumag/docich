from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from docich.adapters.base import Observation
from docich.agent.brains import NethackPolicyBrain
from docich.nethack_observation import normalize_tty
from docich.nethack_shadow import (
    NethackShadowConfig,
    NethackShadowController,
    compare_public_observations,
    parse_shadow_snapshot,
)


def tty_text(*, hp: str = "10(10)") -> str:
    return (
        "msg\n"
        "###@.\n"
        "     \n"
        f"Dlvl:2 HP:{hp} Pw:4(4) AC:5 Exp:2 $:7\n"
        "T:12\n"
    )


def public_payload(obs, *, captured_at=100.0, source="shadow-test") -> dict[str, object]:
    v = obs.vitals
    return {
        "schema_version": 1,
        "source": source,
        "captured_at": captured_at,
        "public": {
            "message": obs.message,
            "map_rows": list(obs.map_rows),
            "player": list(obs.player) if obs.player is not None else None,
            "vitals": {
                "hp": v.hp,
                "hp_max": v.hp_max,
                "power": v.power,
                "power_max": v.power_max,
                "ac": v.ac,
                "experience_level": v.experience_level,
                "dungeon_level": v.dungeon_level,
                "gold": v.gold,
                "turn": v.turn,
            },
            "conditions": list(obs.conditions),
            "prompt": obs.prompt,
        },
    }


class TestShadowSchema(unittest.TestCase):
    def test_unknown_prompt_and_new_tty_conditions_round_trip_and_compare(self) -> None:
        for condition in ("Stone", "TermIll"):
            text = tty_text().replace("msg", "Unknown question?").replace("T:12", f"T:12 {condition}")
            obs = normalize_tty(text, cols=80, rows=5)
            self.assertEqual(obs.prompt, "unknown")
            self.assertIn(condition, obs.conditions)
            snapshot = parse_shadow_snapshot(json.dumps(public_payload(obs)))
            self.assertEqual(snapshot.to_observation().prompt, "unknown")
            self.assertEqual(snapshot.to_observation().conditions, obs.conditions)
            self.assertTrue(compare_public_observations(obs, snapshot).matches)
            changed = public_payload(obs)
            changed["public"]["prompt"] = "none"
            self.assertIn("prompt", compare_public_observations(obs, parse_shadow_snapshot(changed)).mismatches)

    def test_valid_public_snapshot_round_trips(self) -> None:
        obs = normalize_tty(tty_text(), cols=80, rows=5)
        snapshot = parse_shadow_snapshot(public_payload(obs))
        self.assertEqual(snapshot.source, "shadow-test")
        self.assertEqual(snapshot.player, obs.player)
        comparison = compare_public_observations(obs, snapshot)
        self.assertTrue(comparison.matches)
        self.assertEqual(comparison.mismatches, ())
        self.assertGreater(comparison.map_cells_compared, 0)

    def test_unknown_or_hidden_fields_are_rejected(self) -> None:
        obs = normalize_tty(tty_text(), cols=80, rows=5)
        payload = public_payload(obs)
        payload["public"]["monster_id"] = 42
        with self.assertRaises(ValueError):
            parse_shadow_snapshot(payload)

        payload2 = public_payload(obs)
        payload2["hidden_map"] = [[1, 2, 3]]
        with self.assertRaises(ValueError):
            parse_shadow_snapshot(payload2)

        payload3 = public_payload(obs)
        payload3["public"]["conditions"] = ["PeacefulMonsterKnown"]
        with self.assertRaises(ValueError):
            parse_shadow_snapshot(payload3)

    def test_non_finite_captured_at_is_rejected(self) -> None:
        obs = normalize_tty(tty_text(), cols=80, rows=5)
        for captured_at in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(captured_at=captured_at):
                payload = public_payload(obs, captured_at=captured_at)
                with self.assertRaises(ValueError):
                    parse_shadow_snapshot(json.dumps(payload))

    def test_public_difference_is_telemetry_not_truth_selection(self) -> None:
        tty = normalize_tty(tty_text(hp="10(10)"), cols=80, rows=5)
        payload = public_payload(tty)
        payload["public"]["vitals"]["hp"] = 1
        snapshot = parse_shadow_snapshot(payload)
        comparison = compare_public_observations(tty, snapshot)
        self.assertFalse(comparison.matches)
        self.assertIn("vitals.hp", comparison.mismatches)


class TestShadowController(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.g = SimpleNamespace(state_dir=self.root / "state", repo_root=self.root)
        self.game = SimpleNamespace(raw={})
        self.snapshot = self.root / "shadow.json"
        self.clock = [101.0]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def controller(self, *, max_age_s=5.0):
        return NethackShadowController(
            self.g,
            self.game,
            config=NethackShadowConfig(
                enabled=True,
                path=str(self.snapshot),
                max_age_s=max_age_s,
                max_bytes=131072,
            ),
            wall_time=lambda: self.clock[0],
        )

    def test_default_relative_state_path_is_not_double_prefixed(self) -> None:
        ctl = NethackShadowController(
            SimpleNamespace(state_dir=Path("run")),
            self.game,
            config=NethackShadowConfig(enabled=False),
        )
        self.assertEqual(ctl.snapshot_path, Path("run/nethack/shadow/latest.json"))
        self.assertEqual(ctl.log_path, Path("run/nethack/shadow/comparisons.jsonl"))

    def test_match_and_mismatch_are_logged_with_no_policy_effect(self) -> None:
        obs = normalize_tty(tty_text(), cols=80, rows=5)
        self.snapshot.write_text(json.dumps(public_payload(obs)), encoding="utf-8")
        ctl = self.controller()
        self.assertEqual(ctl.compare(obs).status, "match")

        payload = public_payload(obs)
        payload["public"]["vitals"]["hp"] = 1
        self.snapshot.write_text(json.dumps(payload), encoding="utf-8")
        outcome = ctl.compare(obs)
        self.assertEqual(outcome.status, "mismatch")
        self.assertIn("vitals.hp", outcome.comparison.mismatches)

        log = (self.root / "state" / "nethack" / "shadow" / "comparisons.jsonl").read_text(
            encoding="utf-8"
        )
        self.assertIn('"policy_effect":"none"', log)

    def test_stale_snapshot_is_not_compared(self) -> None:
        obs = normalize_tty(tty_text(), cols=80, rows=5)
        self.snapshot.write_text(json.dumps(public_payload(obs, captured_at=90.0)), encoding="utf-8")
        outcome = self.controller(max_age_s=5.0).compare(obs)
        self.assertEqual(outcome.status, "stale")
        self.assertIsNone(outcome.comparison)


class TestShadowPolicyIsolation(unittest.TestCase):
    def test_conflicting_shadow_hp_does_not_change_tty_policy_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            snapshot_path = root / "shadow.json"
            text = tty_text(hp="10(10)")
            tty = normalize_tty(text, cols=80, rows=5)
            payload = public_payload(tty, captured_at=time.time())
            payload["public"]["vitals"]["hp"] = 1
            snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

            game = SimpleNamespace(
                name="nethack",
                adapter="cli",
                raw={
                    "cli": {"cols": 80, "rows": 5},
                    "nethack": {
                        "shadow": {
                            "enabled": True,
                            "path": str(snapshot_path),
                            "max_age_s": 30.0,
                            "max_bytes": 131072,
                        }
                    },
                },
            )
            brain = NethackPolicyBrain(
                SimpleNamespace(state_dir=state_dir, repo_root=root),
                game,
            )
            actions = brain.decide(
                Observation(
                    game="nethack",
                    title="NetHack",
                    adapter="cli",
                    ts=1.0,
                    kind="text",
                    text=text,
                )
            )
            self.assertEqual(len(actions), 1)
            self.assertIn(actions[0].text, {"h", "j", "k", "l"})
            self.assertEqual(brain.last_shadow.status, "mismatch")
            self.assertIn("vitals.hp", brain.last_shadow.comparison.mismatches)


if __name__ == "__main__":
    unittest.main()
