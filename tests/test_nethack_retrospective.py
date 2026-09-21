from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from docich import config
from docich.nethack_retrospective import (
    NethackRetrospectiveEngine,
    normalize_death_signature,
)


class TestDeathSignature(unittest.TestCase):
    def test_conservative_normalization(self) -> None:
        self.assertEqual(
            normalize_death_signature("killed by a water elemental"),
            "killed_by:water elemental",
        )
        self.assertEqual(normalize_death_signature("starved to death"), "starvation")
        self.assertIsNone(normalize_death_signature(None))


class TestRetrospectiveEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "repo"
        self.state = self.root / "run"
        self.playground = Path(self.tempdir.name) / "playground"
        self.dump_dir = self.playground / "dumps"
        self.save_dir = self.playground / "save"
        self.xlogfile = self.playground / "xlogfile"
        self.dump_dir.mkdir(parents=True)
        self.save_dir.mkdir(parents=True)
        (self.root / "config" / "games").mkdir(parents=True)
        (self.root / "config" / "docich.toml").write_text(
            '[paths]\nstate_dir = "run"\ngames_dir = "config/games"\n',
            encoding="utf-8",
        )
        (self.root / "config" / "games" / "nethack.toml").write_text(
            '[game]\nname = "nethack"\ntitle = "NetHack"\nadapter = "cli"\n\n'
            '[cli]\ncommand = "nethack"\n\n'
            '[nethack]\npersistent_run = true\nplayer_name = "docich"\n'
            f'save_dir = "{self.save_dir}"\n'
            f'xlogfile = "{self.xlogfile}"\n'
            f'dump_dir = "{self.dump_dir}"\n',
            encoding="utf-8",
        )
        self.g = config.load_global(self.root)
        self.runs = self.state / "nethack" / "runs"
        self.runs.mkdir(parents=True)
        self.now = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def make_run(
        self,
        expedition: int,
        *,
        status: str = "dead",
        death: str | None = "killed by a water elemental",
        started_epoch: int = 1_789_540_000,
        end_epoch: int = 1_789_543_600,
        dump_file: str | None = None,
    ) -> dict[str, object]:
        run_id = str(uuid.uuid4())
        run = {
            "schema_version": 1,
            "run_id": run_id,
            "expedition": expedition,
            "player_name": "docich",
            "status": status,
            "started_at": datetime.fromtimestamp(started_epoch, tz=timezone.utc).isoformat(),
            "started_epoch": started_epoch,
            "last_started_at": datetime.fromtimestamp(started_epoch, tz=timezone.utc).isoformat(),
            "last_finished_at": datetime.fromtimestamp(end_epoch, tz=timezone.utc).isoformat(),
            "sessions": [],
            "score": 100 * expedition,
            "turns": 1000 * expedition,
            "max_depth": 3 + expedition,
            "death_reason": death,
            "role": "Val",
            "race": "Hum",
            "gender": "Fem",
            "alignment": "Law",
            "achievement_bits": 0,
            "got_amulet": False,
            "dump_file": dump_file,
            "terminal": {
                "source": "xlogfile",
                "death": death,
                "endtime": end_epoch,
            },
            "lessons": [],
        }
        (self.runs / f"{run_id}.json").write_text(
            json.dumps(run, ensure_ascii=False), encoding="utf-8"
        )
        return run

    def advisory(self, *events: dict[str, object]) -> None:
        path = self.state / "nethack" / "strategist" / "advisory.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as stream:
            for event in events:
                payload = {"schema_version": 1, **event}
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def test_repeated_death_creates_candidate_lesson_and_is_idempotent(self) -> None:
        self.make_run(1)
        second = self.make_run(2)
        engine = NethackRetrospectiveEngine(self.g)
        result = engine.generate(run_id=second["run_id"], now=self.now)
        self.assertEqual(result["same_death_prior_count"], 1)
        self.assertEqual(result["same_death_total_count"], 2)
        categories = {item["category"] for item in result["candidate_lessons"]}
        self.assertIn("repeated_death", categories)
        self.assertIn("evidence_gap", categories)
        self.assertEqual(result["policy_effect"], "none")

        # Re-running the same run replaces P5a candidates; aggregate evidence
        # must not be incremented twice.
        again = engine.generate(run_id=second["run_id"], now=self.now)
        self.assertEqual(again["same_death_prior_count"], 1)
        memory = json.loads((self.state / "nethack" / "lessons.json").read_text(encoding="utf-8"))
        repeated = [item for item in memory["lessons"] if item["category"] == "repeated_death"]
        self.assertEqual(len(repeated), 1)
        self.assertEqual(repeated[0]["evidence_count"], 1)
        self.assertEqual(memory["policy_effect"], "none")

    def test_survival_advisory_is_correlated_without_claiming_causation(self) -> None:
        run = self.make_run(1)
        self.advisory(
            {"ts": 1_789_539_000, "status": "proposed", "intent": "too_early"},
            {
                "ts": 1_789_543_000,
                "status": "proposed",
                "intent": "survival_emergency",
                "reason": "visible HP is critical",
                "proposal": {"kind": "rest"},
                "evaluation": {"status": "approved", "reason": "public-state checks passed"},
                "narrated": True,
            },
            {"ts": 1_789_550_000, "status": "proposed", "intent": "too_late"},
        )
        result = NethackRetrospectiveEngine(self.g).generate(
            run_id=run["run_id"], now=self.now
        )
        advisory = result["advisory_evidence"]
        self.assertEqual(advisory["event_count"], 1)
        self.assertEqual(advisory["recent"][0]["intent"], "survival_emergency")
        categories = {item["category"] for item in result["candidate_lessons"]}
        self.assertIn("survival_signal", categories)

    def test_rejected_proposals_are_kept_as_review_evidence(self) -> None:
        run = self.make_run(1)
        self.advisory(
            {
                "ts": 1_789_543_000,
                "status": "proposed",
                "intent": "survival_emergency",
                "proposal": {"kind": "consume"},
                "evaluation": {"status": "rejected", "reason": "inventory changed"},
            }
        )
        result = NethackRetrospectiveEngine(self.g).generate(run_id=run["run_id"], now=self.now)
        self.assertEqual(result["advisory_evidence"]["rejected_evaluations"], 1)
        categories = {item["category"] for item in result["candidate_lessons"]}
        self.assertIn("proposal_drift", categories)

    def test_dump_tail_is_bounded_evidence(self) -> None:
        dump = self.dump_dir / "docich.dump"
        dump.write_text("header\n" + "\n".join(f"line-{i}" for i in range(30)), encoding="utf-8")
        run = self.make_run(1, dump_file=dump.name)
        result = NethackRetrospectiveEngine(self.g).generate(run_id=run["run_id"], now=self.now)
        evidence = result["dump_evidence"]
        self.assertEqual(evidence["status"], "ok")
        self.assertEqual(evidence["file"], dump.name)
        self.assertLessEqual(len(evidence["tail_excerpt"]), 12)
        self.assertEqual(len(evidence["tail_sha256"]), 64)

    def test_ended_unknown_never_invents_death_reason(self) -> None:
        run = self.make_run(1, status="ended_unknown", death=None)
        run["terminal"] = {"source": "xlogfile", "analysis_error": "xlogfile-missing"}
        (self.runs / f"{run['run_id']}.json").write_text(json.dumps(run), encoding="utf-8")
        result = NethackRetrospectiveEngine(self.g).generate(run_id=run["run_id"], now=self.now)
        self.assertIsNone(result["death_reason"])
        self.assertIsNone(result["death_signature"])
        categories = {item["category"] for item in result["candidate_lessons"]}
        self.assertEqual(categories, {"terminal_evidence"})

    def test_ascended_run_has_retrospective_but_no_death_lessons(self) -> None:
        run = self.make_run(1, status="ascended", death="ascended")
        run["got_amulet"] = True
        (self.runs / f"{run['run_id']}.json").write_text(json.dumps(run), encoding="utf-8")
        result = NethackRetrospectiveEngine(self.g).generate(run_id=run["run_id"], now=self.now)
        self.assertTrue(result["got_amulet"])
        self.assertEqual(result["candidate_lessons"], [])

    def test_default_target_is_latest_terminal_expedition(self) -> None:
        self.make_run(1)
        latest = self.make_run(2, death="killed by a grid bug")
        result = NethackRetrospectiveEngine(self.g).generate(now=self.now)
        self.assertEqual(result["run_id"], latest["run_id"])
        self.assertEqual(result["expedition"], 2)


if __name__ == "__main__":
    unittest.main()
