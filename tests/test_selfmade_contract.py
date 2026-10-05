"""Pure #380 prep regressions: no generated execution, network or dependencies."""
import json
import sys
import unittest
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich import selfmade_contract as c
from docich import selfmade_prototype as p

FIXTURE = Path(__file__).parent / "fixtures" / "selfmade_key_switch"
IMAGE = "sha256:" + "a" * 64
GUARANTEES = ("os_isolation", "non_root", "network_disabled", "no_capabilities",
              "bundle_read_only", "host_home_hidden", "credentials_hidden",
              "docker_socket_hidden", "no_host_fallback")


def encode(value):
    return json.dumps(value, allow_nan=False).encode()


def profile():
    return {"version": c.CONTRACT_VERSION, "image_digest": IMAGE,
            "cpu_millicores": 1000, "memory_bytes": 256 * 1024 * 1024,
            "process_limit": 16, "output_bytes": 16 * 1024 * 1024,
            "tmp_bytes": 64 * 1024 * 1024,
            **{name: True for name in GUARANTEES}}


def request(**changes):
    return {"frame_id": "current-frame", "seq": 1, "buttons": ["RIGHT"],
            "ticks": 4, **changes}


def proposal(expected, **changes):
    return {"version": c.CONTRACT_VERSION, "seq": 1,
            "tick": expected.tick, "state": asdict(expected), **changes}


class PreflightTests(unittest.TestCase):
    def test_matching_report_is_data_only_and_detached(self):
        report = profile()
        prepared = c.validate_preflight(report, expected_image_digest=IMAGE)
        report["image_digest"] = "changed"
        self.assertEqual(prepared.image_digest, IMAGE)
        self.assertEqual(prepared.version, c.CONTRACT_VERSION)
        self.assertFalse(hasattr(prepared, "start"))
        with self.assertRaises(FrozenInstanceError):
            prepared.image_digest = "changed"

    def test_all_guarantees_are_mandatory_and_exact_booleans(self):
        for field in GUARANTEES:
            for bad in (False, None, 1, "true", []):
                with self.subTest(field=field, bad=bad):
                    report = profile()
                    report[field] = bad
                    with self.assertRaisesRegex(p.Rejected, "preflight_incomplete"):
                        c.validate_preflight(report, expected_image_digest=IMAGE)

    def test_missing_unknown_fields_and_non_objects_fail_closed(self):
        for report in (None, [], {**profile(), "host_fallback": True}):
            with self.subTest(report=report):
                with self.assertRaises(p.Rejected):
                    c.validate_preflight(report, expected_image_digest=IMAGE)
        for field in profile():
            with self.subTest(missing=field):
                report = profile()
                del report[field]
                with self.assertRaises(p.Rejected):
                    c.validate_preflight(report, expected_image_digest=IMAGE)

    def test_unpinned_different_image_and_contract_version_are_rejected(self):
        for image in (None, "latest", "node:22", "sha256:" + "A" * 64, IMAGE + "x"):
            with self.subTest(image=image):
                with self.assertRaisesRegex(p.Rejected, "invalid_image_digest"):
                    c.validate_preflight(profile(), expected_image_digest=image)
        for field, bad in (("image_digest", None), ("image_digest", "sha256:" + "b" * 64),
                           ("version", True), ("version", 1.0), ("version", 2)):
            with self.subTest(field=field, bad=bad):
                report = profile()
                report[field] = bad
                with self.assertRaises(p.Rejected):
                    c.validate_preflight(report, expected_image_digest=IMAGE)

    def test_fixed_public_resource_contract_is_not_loosened(self):
        for field in ("cpu_millicores", "memory_bytes", "process_limit", "output_bytes"):
            original = profile()[field]
            for bad in (0, -1, True, float(original), original - 1, original + 1):
                with self.subTest(field=field, bad=bad):
                    report = profile()
                    report[field] = bad
                    with self.assertRaisesRegex(p.Rejected, "resource_contract_mismatch"):
                        c.validate_preflight(report, expected_image_digest=IMAGE)

    def test_tmp_requires_explicit_positive_limit_without_default(self):
        for bad in (0, -1, True, 1.0, None, "67108864"):
            with self.subTest(tmp=bad):
                report = profile()
                report["tmp_bytes"] = bad
                with self.assertRaisesRegex(p.Rejected, "tmp_limit_required"):
                    c.validate_preflight(report, expected_image_digest=IMAGE)
        for size in (1, 64 * 1024 * 1024):
            report = profile()
            report["tmp_bytes"] = size
            self.assertEqual(c.validate_preflight(report, expected_image_digest=IMAGE).tmp_bytes, size)


class InputTests(unittest.TestCase):
    def parse(self, raw):
        return c.parse_input(raw, frame_id="current-frame", previous_seq=0)

    def test_normal_noop_all_keys_and_exact_input_boundary(self):
        for button in (None, "UP", "DOWN", "LEFT", "RIGHT"):
            value = self.parse(encode(request(buttons=[button] if button else [])))
            self.assertEqual(value, c.SolverInput("current-frame", 1, button, 4))
            with self.assertRaises(FrozenInstanceError):
                value.seq = 99
        raw = encode(request())
        self.assertEqual(self.parse(raw + b" " * (1024 - len(raw))).seq, 1)
        with self.assertRaisesRegex(p.Rejected, "input_limit"):
            self.parse(raw + b" " * (1025 - len(raw)))

    def test_unknown_keys_types_bounds_and_old_identifiers_are_rejected(self):
        changes = ({"seq": 0}, {"seq": 2}, {"seq": True}, {"seq": 1.0},
                   {"frame_id": "old"}, {"frame_id": 1}, {"ticks": 0},
                   {"ticks": -1}, {"ticks": 61}, {"ticks": True}, {"ticks": 1.5},
                   {"buttons": ["WIN"]}, {"buttons": ["UP", "DOWN"]},
                   {"buttons": [1]}, {"buttons": "RIGHT"}, {"success": True})
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaises(p.Rejected):
                    self.parse(encode(request(**change)))
        for bad in ("text", bytearray(b"{}"), None):
            with self.subTest(type=type(bad)):
                with self.assertRaisesRegex(p.Rejected, "input_limit"):
                    self.parse(bad)

    def test_bad_json_duplicate_nonfinite_unicode_and_deep_payloads(self):
        for raw, reason in ((b"{", "invalid_json"), (b"\xff", "invalid_json"),
                            (b'{"seq":1,"seq":2}', "duplicate_field"),
                            (b'{"ticks":NaN}', "nonfinite"),
                            (b'{"ticks":Infinity}', "nonfinite"),
                            (b"[" * 500 + b"]" * 500, "invalid_fields")):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(p.Rejected, reason):
                    self.parse(raw)

    def test_sequence_advances_only_when_trusted_caller_accepts(self):
        raw = encode(request())
        self.assertEqual(self.parse(raw), self.parse(raw))
        value = self.parse(raw)
        with self.assertRaisesRegex(p.Rejected, "invalid_seq"):
            c.parse_input(raw, frame_id=value.frame_id, previous_seq=value.seq)
        next_value = c.parse_input(encode(request(seq=2)), frame_id=value.frame_id,
                                  previous_seq=value.seq)
        self.assertEqual(next_value.seq, 2)
        for bad in (True, -1, p.MAX_TICKS):
            with self.assertRaises(p.Rejected):
                c.parse_input(raw, frame_id="current-frame", previous_seq=bad)

    def test_existing_session_and_pure_parser_agree_without_changing_legacy_protocol(self):
        artifact = p.Artifact.load(FIXTURE)
        for change in ({}, {"seq": 2}, {"seq": True}, {"ticks": 60}, {"ticks": 61},
                       {"buttons": []}, {"buttons": ["LEFT", "RIGHT"]},
                       {"frame_id": "old"}, {"unknown": True}):
            with self.subTest(change=change):
                session = p.FixtureSession(FIXTURE, artifact, clock=lambda: 0)
                raw = encode({**request(frame_id=session.frame_id), **change})
                try:
                    c.parse_input(raw, frame_id=session.frame_id, previous_seq=session.seq)
                    parsed = True
                except p.Rejected:
                    parsed = False
                self.assertEqual(session.submit(raw), parsed)
                session.cancel()


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.artifact = p.Artifact.load(FIXTURE)
        self.initial = p.State(0, self.artifact.rules.player,
                               gates=(False,) * len(self.artifact.rules.gates))
        self.expected = p.transition(self.artifact.rules, self.initial, "RIGHT")
        self.budget = c.OutputBudget()

    def parse(self, raw, expected=None, seq=1, message_limit=4096):
        return c.parse_proposal(raw, expected_seq=seq,
                                expected_state=expected or self.expected, budget=self.budget,
                                max_message_bytes=message_limit)

    def test_matching_proposal_returns_trusted_state_with_no_state_advance(self):
        original = p.state_hash(self.initial)
        self.assertIs(self.parse(encode(proposal(self.expected))), self.expected)
        self.assertEqual(p.state_hash(self.initial), original)
        next_tick = p.transition(self.artifact.rules, self.expected, "RIGHT")
        self.assertIs(self.parse(encode(proposal(next_tick)), next_tick), next_tick)
        with self.assertRaisesRegex(p.Rejected, "invalid_tick"):
            self.parse(encode(proposal(self.expected)), next_tick)

    def test_version_seq_tick_and_shape_mismatch_are_rejected(self):
        changes = ({"version": True}, {"version": 1.0}, {"version": 2},
                   {"seq": 0}, {"seq": True}, {"seq": 2}, {"seq": 1.0},
                   {"tick": 0}, {"tick": True}, {"tick": 2}, {"tick": 1.0},
                   {"tick": p.MAX_TICKS + 1}, {"event": "WIN"})
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaises(p.Rejected):
                    self.parse(encode(proposal(self.expected, **change)))
        for bad in (True, 0, p.MAX_TICKS + 1):
            with self.assertRaises(p.Rejected):
                self.parse(encode(proposal(self.expected)), seq=bad)

    def test_state_forgery_unknown_fields_and_types_cannot_be_judge_evidence(self):
        changes = ({"candidate_win": True}, {"player": [5, 1]}, {"key": True},
                   {"switch": True}, {"alive": False}, {"gates": [True]},
                   {"success": True}, {"tick": True}, {"key": 0})
        original = p.state_hash(self.expected)
        for change in changes:
            with self.subTest(change=change):
                raw = encode(proposal(self.expected, state={**asdict(self.expected), **change}))
                with self.assertRaisesRegex(p.Rejected, "invalid_artifact"):
                    self.parse(raw)
                self.assertEqual(p.state_hash(self.expected), original)
        for state in (None, [], {"success": True}):
            with self.assertRaises(p.Rejected):
                self.parse(encode(proposal(self.expected, state=state)))

    def test_invalid_output_is_charged_and_bounded_before_json_parse(self):
        for raw in (b"{", b"\xff", b'{"version":1,"version":2}',
                    b'{"state":NaN}', b"[" * 1200 + b"]" * 1200):
            before = self.budget.used
            with self.assertRaises(p.Rejected):
                self.parse(raw)
            self.assertEqual(self.budget.used, before + len(raw))
        raw = b"x" * (c.MAX_OUTPUT_BYTES + 1)
        with patch.object(c, "_json", side_effect=AssertionError("must not parse over budget")):
            with self.assertRaisesRegex(p.Rejected, "output_limit"):
                self.parse(raw)
        self.assertTrue(self.budget.exhausted)

    def test_combined_streams_exact_limit_and_overflow_are_sticky(self):
        raw = encode(proposal(self.expected))
        # Charge synthetic stderr/drawing bytes to the SAME budget as proposals.
        self.budget.consume(b"x" * (c.MAX_OUTPUT_BYTES - len(raw)))
        self.assertIs(self.parse(raw), self.expected)
        self.assertEqual(self.budget.used, c.MAX_OUTPUT_BYTES)
        with self.assertRaisesRegex(p.Rejected, "output_limit"):
            self.budget.consume(b"x")
        with self.assertRaisesRegex(p.Rejected, "output_limit"):
            self.budget.consume(b"")
        with self.assertRaisesRegex(p.Rejected, "output_limit"):
            self.parse(raw)

    def test_wrong_output_type_does_not_enter_json_or_charge_budget(self):
        for raw in (None, "{}", bytearray(b"{}")):
            with self.assertRaisesRegex(p.Rejected, "invalid_output_bytes"):
                self.parse(raw)
        self.assertEqual(self.budget.used, 0)

    def test_message_limit_is_explicit_exact_and_checked_before_json_parse(self):
        raw = encode(proposal(self.expected))
        self.assertIs(self.parse(raw, message_limit=len(raw)), self.expected)
        with patch.object(c, "_json", side_effect=AssertionError("must not decode oversized message")):
            with self.assertRaisesRegex(p.Rejected, "message_limit"):
                self.parse(raw, message_limit=len(raw) - 1)
        self.assertEqual(self.budget.used, len(raw) * 2)
        for bad in (True, 0, -1, 1.0, c.MAX_OUTPUT_BYTES + 1):
            with self.assertRaises(p.Rejected):
                self.parse(raw, message_limit=bad)

    def test_all_prep_paths_and_legacy_fixture_cannot_launch_any_runtime(self):
        with patch("subprocess.Popen", side_effect=AssertionError("no process")), \
             patch("subprocess.run", side_effect=AssertionError("no command")), \
             patch("socket.create_connection", side_effect=AssertionError("no network")):
            c.validate_preflight(profile(), expected_image_digest=IMAGE)
            c.parse_input(encode(request()), frame_id="current-frame", previous_seq=0)
            self.parse(encode(proposal(self.expected)))
            before = self.artifact.identity
            with self.assertRaisesRegex(p.Rejected, "sandbox_violation"):
                p.start_generated(FIXTURE)
            self.assertEqual(p.Artifact.load(FIXTURE).identity, before)
            self.assertIsNone(json.loads(dict(self.artifact.files)["manifest.json"])["image_digest"])
            session = p.FixtureSession(FIXTURE, self.artifact)
            self.assertTrue(session.submit(encode({"frame_id": session.frame_id, "seq": 1,
                                                   "buttons": ["RIGHT"], "ticks": 16})))
            self.assertEqual(session.verify(), "verified_win")
            self.assertEqual(session.report()["execution"], "trusted_synthetic_fixture")


if __name__ == "__main__":
    unittest.main()
