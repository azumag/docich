"""Compatibility against the checked-out legacy classifier, never a copied port.

CI requires the submodule before this file runs. Only temporary state and
synthetic rows are used; every transport is mocked and no shell/VM is invoked.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.semantic_decision import transport as core
from docich.semantic_decision.routes import resolve_route
from docich.semantic_decision.validator import dumps, validate_response

SOURCE = ROOT / "games/soviet_now/lib/comment_classifier_jev.py"
FIXTURE = json.loads((ROOT / "tests/fixtures/semantic_choice_direct.json").read_text())
KEY = "SYNTHETIC_COMPAT_KEY"


@pytest.fixture(scope="module")
def legacy():
    if not SOURCE.is_file():
        pytest.skip("legacy submodule absent; required by semantic-decision CI")
    spec = importlib.util.spec_from_file_location("legacy_jev_semantic_contract", SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def adapter(request, key, timeout):
    # Test-only bridge, not installed in any production consumer.
    return core.request_once(request, route="direct", env={"TYPESAFE_API_KEY": key},
                             timeout_ms=round(timeout * 1000))


def call(legacy, tmp_path, *, response=None, raw=None, rows=None):
    response = deepcopy(FIXTURE["response"]) if response is None else response
    raw = dumps({"status": "ok", "data": response}).encode() if raw is None else raw
    with patch.object(core, "_bounded_process", return_value=raw) as spawn:
        result = legacy.classify(rows or FIXTURE["rows"], legacy.Config(), KEY, tmp_path,
                                 transport=adapter)
    return (*result, spawn)


def test_exact_legacy_request_and_validation_projection(legacy):
    request = legacy.build_request(FIXTURE["rows"][:2], legacy.Config().model)
    assert request == FIXTURE["request"]
    clean = validate_response(FIXTURE["response"], request, resolve_route())
    assert legacy.validate_response(clean, request) == legacy.validate_response(FIXTURE["response"], request)
    assert legacy.Config().timeout_ms == 1500
    assert legacy.Config().min_confidence == .70
    assert legacy.MAX_COMMENTS == 8


def test_category_only_stdout_shape_low_confidence_and_notification_protection(legacy, tmp_path):
    output, event, spawn = call(legacy, tmp_path)
    assert [r["category"] for r in output] == FIXTURE["expected_categories"]
    for old, new in zip(FIXTURE["rows"], output):
        assert {k: v for k, v in new.items() if k != "category"} == {k: v for k, v in old.items() if k != "category"}
        assert set(old) == set(new)
    assert [r["status"] for r in event["rows"]] == ["jev", "low_confidence", "local_notification"]
    assert json.loads(spawn.call_args.kwargs["data"]) == FIXTURE["request"]
    assert spawn.call_count == 1
    for value in (KEY, "VIEWER_SENTINEL", "音声が聞こえません", "RAID_NOTIFICATION_SENTINEL"):
        assert value not in dumps(event)
    # Legacy stdout is still a JSON array of the original canonical rows.
    assert json.loads(legacy.dumps(output)) == output


@pytest.mark.parametrize("confidence,accepted", [(0, False), (.69999, False), (.70, True), (1, True)])
def test_threshold_remains_consumer_policy(legacy, tmp_path, confidence, accepted):
    response = deepcopy(FIXTURE["response"])
    response["answers"]["c1"]["confidence"] = confidence
    output, _, _ = call(legacy, tmp_path, response=response)
    assert output[0]["category"] == ("stream_bug_report" if accepted else "chitchat")


def test_model_cannot_manufacture_platform_notification(legacy, tmp_path):
    response = deepcopy(FIXTURE["response"])
    answer = response["answers"]["c1"]
    answer.update(choice="raid", confidence=1, probabilities={label: int(label == "raid") for label in legacy.CRITERIA})
    output, event, _ = call(legacy, tmp_path, response=response)
    assert output == FIXTURE["rows"]
    assert event["rows"][0]["status"] == "unconfirmed_notification"


def test_projection_strips_context_even_when_present_on_input_rows(legacy, tmp_path):
    rows = deepcopy(FIXTURE["rows"])
    sentinels = {key: key.upper() + "_PRIVATE_SENTINEL" for key in
                 ("persona", "game", "history", "heuristic_prediction", "baseline_labels")}
    for row in rows:
        row.update(sentinels)
    _, _, spawn = call(legacy, tmp_path, rows=rows)
    payload = spawn.call_args.kwargs["data"].decode()
    assert json.loads(payload) == FIXTURE["request"]
    for value in (*sentinels.values(), "VIEWER_SENTINEL", "RAID_NOTIFICATION_SENTINEL"):
        assert value not in payload


@pytest.mark.parametrize("status", ["auth_error", "rate_limited", "overloaded", "server_error",
                                     "network_error", "timeout", "invalid_response", "http_error"])
def test_core_failure_returns_unchanged_heuristic_rows(legacy, tmp_path, status):
    raw = dumps({"status": status, "detail": "RAW_PRIVATE_ERROR"}).encode()
    output, event, spawn = call(legacy, tmp_path, raw=raw)
    assert output == FIXTURE["rows"]
    assert event["status"] == status
    assert spawn.call_count == 1
    assert "RAW_PRIVATE_ERROR" not in dumps(event)


def test_invalid_schema_falls_back_as_a_batch(legacy, tmp_path):
    response = deepcopy(FIXTURE["response"])
    del response["answers"]["c2"]["confidence"]
    output, event, _ = call(legacy, tmp_path, response=response)
    assert output == FIXTURE["rows"]
    assert event["status"] == "invalid_response"


def test_core_unavailable_does_not_escalate_to_generation(legacy, tmp_path):
    def unavailable(*args):
        raise ModuleNotFoundError("PRIVATE_EXCEPTION")
    output, event = legacy.classify(FIXTURE["rows"], legacy.Config(), KEY, tmp_path,
                                    transport=unavailable)
    assert output == FIXTURE["rows"]
    assert event["status"] == "invalid_response"
    assert "PRIVATE_EXCEPTION" not in dumps(event)


def test_legacy_rollback_can_run_without_importing_docich(legacy, tmp_path):
    code = f'''
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('legacy_only', {str(SOURCE)!r})
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
rows = {FIXTURE['rows']!r}
output, event = m.classify(rows, m.Config(), '', Path({str(tmp_path)!r}))
assert event['status'] == 'missing_key'
assert not any(n == 'docich' or n.startswith('docich.') for n in sys.modules)
print(m.dumps(output))
'''
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                            env={"LANG": "C.UTF-8"}, timeout=3, check=True)
    assert json.loads(result.stdout) == FIXTURE["rows"]
    assert result.stderr == b""
