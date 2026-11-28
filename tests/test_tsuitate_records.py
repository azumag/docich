import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich import tsuitate_beta_control as control  # noqa: E402
from docich import tsuitate_records  # noqa: E402

SECRET = "fixture-only-control-capability-not-credential"
ENV = {"DOCICH_BETA_CONTROL_SECRET": SECRET,
       "DOCICH_BETA_CONTROL_URL": "https://docich-tsuitate-bot.fixture-only.workers.dev"}


def receipt(index: int) -> dict:
    return {"runId": f"background-2026112322{index:04d}000000",
            "gameId": f"game-{index}", "brainVersion": "tsuitate-brain-v13"}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class TestRecordsClient(unittest.TestCase):
    def test_projection_is_a_closed_public_allowlist(self):
        valid = {"schema": 1, "records": [receipt(1)], "truncated": False}
        self.assertEqual(control.project_records(valid), valid)
        for bad in [
            {"schema": 2, "records": [], "truncated": False},
            {"schema": 1, "records": [], "truncated": "no"},
            {"schema": 1, "records": [], "truncated": False, "extra": 1},
            {"schema": 1, "records": [{"runId": "one", "gameId": "g", "brainVersion": "v", "outcome": "win"}],
             "truncated": False},
            {"schema": 1, "records": [{"runId": "../one", "gameId": "g", "brainVersion": "v"}], "truncated": False},
            {"schema": 1, "records": [{"runId": "one", "gameId": "bad id", "brainVersion": "v"}], "truncated": False},
            {"schema": 1, "records": [{"runId": "one", "gameId": "g", "brainVersion": ""}], "truncated": False},
            {"schema": 1, "records": [receipt(1), receipt(1)], "truncated": False},
            {"schema": 1, "records": [receipt(index) for index in range(51)], "truncated": True},
        ]:
            with self.subTest(bad=bad), self.assertRaisesRegex(control.ControlError, "^control_unavailable$"):
                control.project_records(bad)

    def test_request_uses_the_fixed_action_and_projects_only_identity(self):
        body = {"schema": 1, "records": [receipt(2), receipt(1)], "truncated": True}
        opener = mock.Mock()
        opener.open.return_value = Response(json.dumps(body).encode())
        with mock.patch.dict(os.environ, ENV, clear=True), \
                mock.patch.object(control.urllib.request, "build_opener", return_value=opener):
            result = control.call_beta_records()
        self.assertEqual(json.loads(opener.open.call_args.args[0].data), {"action": "records"})
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 5)
        self.assertEqual(result["records"], [receipt(2), receipt(1)])
        self.assertIs(result["truncated"], True)

    def test_missing_origin_never_sends_and_oversized_body_is_rejected(self):
        for override in [{"DOCICH_BETA_CONTROL_SECRET": ""},
                         {"DOCICH_BETA_CONTROL_URL": "http://127.0.0.1"},
                         {"DOCICH_BETA_CONTROL_URL": ENV["DOCICH_BETA_CONTROL_URL"] + "/redirect"}]:
            with mock.patch.dict(os.environ, {**ENV, **override}), \
                    mock.patch.object(control.urllib.request, "build_opener") as opener:
                with self.assertRaises(control.ControlError):
                    control.call_beta_records()
                opener.assert_not_called()
        opener = mock.Mock()
        opener.open.return_value = Response(b"x" * 16385)
        with mock.patch.dict(os.environ, ENV, clear=True), \
                mock.patch.object(control.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(control.ControlError, "^control_unavailable$"):
                control.call_beta_records()

    def test_signed_request_never_carries_a_run_id_for_records(self):
        request = control.signed_request(ENV["DOCICH_BETA_CONTROL_URL"], SECRET, "records", now=1791100000)
        self.assertEqual(request.data, b'{"action":"records"}')


class TestRecordsCli(unittest.TestCase):
    def test_prints_the_bounded_listing(self):
        listing = {"schema": 1, "records": [receipt(1)], "truncated": False}
        with mock.patch.object(tsuitate_records, "install_control_environment"), \
                mock.patch.object(tsuitate_records, "call_beta_records", return_value=listing), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(tsuitate_records.main(), 0)
        self.assertEqual(json.loads(out.getvalue()), listing)

    def test_unusable_capability_and_control_failure_have_fixed_codes(self):
        with mock.patch.object(tsuitate_records, "install_control_environment",
                               side_effect=tsuitate_records.BackgroundStartError("control_env_invalid:1")), \
                mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(tsuitate_records.main(), 76)
        with mock.patch.object(tsuitate_records, "install_control_environment"), \
                mock.patch.object(tsuitate_records, "call_beta_records",
                                  side_effect=control.ControlError("control_authentication_failed")), \
                mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(tsuitate_records.main(), 77)


if __name__ == "__main__":
    unittest.main()
