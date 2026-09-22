"""Fail-closed recovery_required contract for failed improvement evidence."""

import pytest

from docich.corner_terminal import failed_improvement_is_terminal


@pytest.mark.parametrize("value", [True, "true", 1, {}, []])
def test_failed_improvement_rejects_non_false_recovery_required(value):
    status = {
        "status": "failed",
        "started_at": 101,
        "completed_at": 102,
        "recovery_required": value,
    }
    state = {"completed_at": 100}

    assert failed_improvement_is_terminal(status, state) is False


def test_failed_improvement_accepts_explicit_false_recovery_required():
    status = {
        "status": "failed",
        "started_at": 101,
        "completed_at": 102,
        "recovery_required": False,
    }
    state = {"completed_at": 100}

    assert failed_improvement_is_terminal(status, state) is True
