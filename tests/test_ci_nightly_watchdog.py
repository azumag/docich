"""Deduplicated alert and self-heal for a missing nightly full suite."""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts"))
_spec = importlib.util.spec_from_file_location(
    "ci_nightly_watchdog", ROOT / ".github/scripts/ci_nightly_watchdog.py"
)
watchdog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watchdog)

NOW = dt.datetime(2026, 11, 23, 19, 0, tzinfo=dt.timezone.utc)


def ts(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run(
    event: str = "schedule",
    hours_ago: float = 1,
    status: str = "completed",
    conclusion: str = "success",
) -> dict[str, str]:
    return {
        "event": event,
        "created_at": ts(hours_ago),
        "status": status,
        "conclusion": conclusion,
    }


class FakeRunner:
    def __init__(self, runs=(), alert=None, fail=False):
        self.runs = list(runs)
        self.alert = alert
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        if self.fail:
            raise watchdog.QueryError("query failed")
        if args[0] == "api" and "-X" not in args:
            return "".join(
                "\t".join(
                    [r["event"], r["created_at"], r["status"], r["conclusion"] or ""]
                )
                + "\n"
                for r in self.runs
            )
        if args[:2] == ["issue", "list"]:
            return f"{self.alert}\n" if self.alert else ""
        return ""


def invoke(runner, **kwargs):
    return watchdog.run(
        runner,
        repo="azumag/docich",
        server="https://github.com",
        run_id="123",
        now=NOW,
        **kwargs,
    )


def test_classify_fresh_in_flight_and_missing():
    assert watchdog.classify([make_run(hours_ago=1)], NOW, 30)[0] == "fresh"
    assert watchdog.classify([make_run(hours_ago=40)], NOW, 30)[0] == "missing"
    assert (
        watchdog.classify(
            [make_run(hours_ago=2, status="in_progress", conclusion="")], NOW, 30
        )[0]
        == "in-flight"
    )
    assert (
        watchdog.classify(
            [make_run(hours_ago=40, status="in_progress", conclusion="")], NOW, 30
        )[0]
        == "missing"
    )
    assert (
        watchdog.classify(
            [make_run(hours_ago=40, status="queued", conclusion="")], NOW, 30
        )[0]
        == "missing"
    )
    assert watchdog.classify([make_run(hours_ago=40), make_run(hours_ago=2)], NOW, 30)[0] == "fresh"


def test_decide():
    assert watchdog.decide("fresh", alert_open=False) == "ok"
    assert watchdog.decide("fresh", alert_open=True) == "recover"
    assert watchdog.decide("in-flight", alert_open=True) == "ok"
    assert watchdog.decide("missing", alert_open=False) == "alert"


def test_missing_nightly_opens_alert_and_dispatches_once():
    runner = FakeRunner(runs=[make_run(hours_ago=40)])
    assert invoke(runner) == watchdog.EXIT_OK
    assert any(args[:2] == ["issue", "create"] for args in runner.calls)
    assert any(
        args[:3] == ["api", "-X", "POST"] and "dispatches" in " ".join(args)
        for args in runner.calls
    )


def test_recent_dispatch_suppresses_a_second_dispatch():
    old = make_run(hours_ago=40)
    attempted = make_run(
        event="workflow_dispatch", hours_ago=2, conclusion="failure"
    )
    runner = FakeRunner(runs=[old, attempted], alert=7)
    assert invoke(runner) == watchdog.EXIT_OK
    assert any(args[:2] == ["issue", "edit"] and "7" in args for args in runner.calls)
    assert not any(args[:3] == ["api", "-X", "POST"] for args in runner.calls)


def test_fresh_nightly_closes_existing_alert_without_dispatch():
    runner = FakeRunner(runs=[make_run(hours_ago=2)], alert=9)
    assert invoke(runner) == watchdog.EXIT_OK
    assert any(args[:2] == ["issue", "close"] and "9" in args for args in runner.calls)
    assert not any(args[:3] == ["api", "-X", "POST"] for args in runner.calls)


def test_query_failure_changes_nothing():
    runner = FakeRunner(alert=9, fail=True)
    assert invoke(runner) == watchdog.EXIT_QUERY_FAILED
    assert len(runner.calls) == 1


def test_body_has_fixed_fields_only():
    body = watchdog.render_body(
        repo="azumag/docich",
        server="https://github.com",
        run_id="123",
        created="2026-11-22T23:38:59Z",
        age_hours=19.4,
        threshold_hours=30,
    )
    assert "2026-11-22T23:38:59Z" in body
    assert "19.4" in body
    assert "https://github.com/azumag/docich/actions/runs/123" in body
