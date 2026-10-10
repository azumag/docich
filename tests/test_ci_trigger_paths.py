"""Contract for the path filters of the heavy, source-scoped CI workflows.

``retro-corner-regressions``, ``nethack-tiles-gui-smoke``, ``hanjuku-predictions-ci``,
``trading-regressions`` and ``overlay-queue-interoperability`` used to start on almost any
change under ``src/docich`` (``src/docich/**``, ``src/docich/adapters/**``).  Their trigger
lists now name only the modules the tests of that workflow import or load at runtime
(measured with an audit-hook file trace of the real test run plus the static import graph).
``full-suite`` in ``ci.yml`` stays the safety net for every ``src/**/*.py`` change.

These tests pin the behaviour that matters and the consistency rules a hand edit can break:

* the narrowing itself (unrelated modules no longer start the heavy workflows),
* the dependencies that were deliberately kept (``webui.py`` is imported / ``mock.patch``-ed
  by the tests, so it still starts three workflows),
* pull_request and push filters stay identical,
* the ``soren-gitlink-gate`` root-path list stays identical to the trigger list,
* every test file a workflow runs also starts that workflow.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ci_trigger_paths", ROOT / ".github/scripts/ci_trigger_paths.py")
ctp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ctp)

RETRO = "retro-corner-regressions.yml"
TILES = "nethack-tiles-gui-smoke.yml"
HANJUKU = "hanjuku-predictions-ci.yml"
TRADING = "trading-regressions.yml"
OVERLAY = "overlay-queue-interoperability.yml"
NARROWED = (RETRO, TILES, HANJUKU, TRADING, OVERLAY)
GATED = (RETRO, TRADING, OVERLAY)


def _text(name: str) -> str:
    return (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")


def _starts(changed: list[str], event: str = "pull_request") -> set[str]:
    return {name for name, state in ctp.triggered(changed, event).items() if state["starts"]}


# --- matcher semantics (documented GitHub filter-pattern rules) -------------------------


@pytest.mark.parametrize(
    "patterns,path,expected",
    [
        (["src/docich/*.py"], "src/docich/tmux.py", True),
        (["src/docich/*.py"], "src/docich/adapters/base.py", False),  # * stops at /
        (["src/docich/**"], "src/docich/adapters/base.py", True),  # ** crosses /
        (["src/docich/hanjuku*.py"], "src/docich/hanjuku_chart.py", True),
        (["src/docich/**", "!src/docich/weather.py"], "src/docich/weather.py", False),
        (["src/docich/**", "!src/docich/weather.py"], "src/docich/tmux.py", True),
        # the last matching pattern decides: a later positive pattern re-includes
        (["src/docich/**", "!src/docich/weather.py", "src/docich/weather.py"], "src/docich/weather.py", True),
        (["!src/docich/weather.py"], "src/docich/weather.py", False),
        (["conftest.py"], "tests/conftest.py", False),  # anchored on the whole path
    ],
)
def test_path_selected_follows_documented_rules(patterns, path, expected):
    assert ctp.path_selected(patterns, path) is expected


# --- narrowing: unrelated modules no longer start the heavy workflows -------------------


def test_registry_lazy_adapter_no_longer_starts_retro_tiles_or_hanjuku():
    # adapters/__init__.py imports external_video_program lazily inside a function; none of
    # these workflows ever loaded it during a full run of their tests.
    started = _starts(["src/docich/adapters/external_video_program.py"])
    assert started.isdisjoint(NARROWED)


def test_weather_adapter_no_longer_starts_retro_or_tiles():
    assert _starts(["src/docich/adapters/weather_program.py"]).isdisjoint(NARROWED)


def test_market_paper_modules_do_not_start_trading_overlay_or_hanjuku():
    started = _starts(["src/docich/trading/markets/core.py"])
    assert started.isdisjoint({TRADING, OVERLAY, HANJUKU})


def test_comment_context_module_does_not_start_any_narrowed_workflow():
    assert _starts(["src/docich/comment/sorengame_context.py"]).isdisjoint(NARROWED)


def test_nethack_policy_only_starts_retro_not_tiles_or_hanjuku():
    started = _starts(["src/docich/nethack_policy.py"])
    assert RETRO in started
    assert started.isdisjoint({TILES, HANJUKU})


def test_docs_only_change_starts_no_narrowed_workflow():
    assert _starts(["docs/operations/foo.md", "README.md"]).isdisjoint(NARROWED)


def test_external_video_receiver_alone_does_not_start_narrowed_workflows():
    assert _starts(["src/docich/external_video_receiver.py"]).isdisjoint(NARROWED)


# --- deliberately kept dependencies ------------------------------------------------------


def test_webui_still_starts_workflows_whose_tests_patch_it():
    # tests patch docich.webui._enqueue_audio_text / call webui._Handler directly
    started = _starts(["src/docich/webui.py"])
    assert {HANJUKU, TRADING, OVERLAY} <= started
    assert started.isdisjoint({RETRO, TILES})


def test_core_modules_still_start_the_workflows_that_import_them():
    assert {RETRO, HANJUKU, TILES} <= _starts(["src/docich/tmux.py"])
    assert {RETRO, HANJUKU, OVERLAY} <= _starts(["src/docich/retro_corner.py"])
    assert {RETRO, TILES} <= _starts(["src/docich/adapters/nethack.py"])
    assert {TRADING, OVERLAY} <= _starts(["src/docich/overlay_queue.py"])
    assert {TILES} <= _starts(["src/docich/nethack_tiles.py"])
    assert {RETRO, HANJUKU} <= _starts(["tests/test_hanjuku_chart_bot.py"])


# --- consistency rules a hand edit can break --------------------------------------------


@pytest.mark.parametrize("name", NARROWED)
def test_pull_request_and_push_filters_are_identical(name):
    text = _text(name)
    paths = ctp.event_paths(text, "pull_request")
    assert paths, name
    assert paths == ctp.event_paths(text, "push")


@pytest.mark.parametrize("name", NARROWED)
def test_filters_have_no_negation_and_no_broad_src_glob(name):
    paths = ctp.event_paths(_text(name), "pull_request")
    assert not [p for p in paths if p.startswith("!")]
    assert "src/docich/**" not in paths
    assert "src/docich/adapters/**" not in paths


@pytest.mark.parametrize("name", GATED)
def test_gitlink_gate_root_paths_match_the_trigger_list(name):
    text = _text(name)
    triggers = [p for p in ctp.event_paths(text, "pull_request") if p != "games/soviet_now"]
    # games/soviet_now is the gitlink itself; the gate handles it separately
    assert sorted(ctp.gate_root_paths(text)) == sorted(triggers)


def test_retro_gate_lets_every_trigger_start_the_heavy_job():
    # these test files used to start the workflow but not the heavy job
    state = ctp.triggered(["tests/test_config.py"])[RETRO]
    assert state == {"starts": True, "heavy": True}
    state = ctp.triggered(["src/docich/adapters/external_video_program.py"])[RETRO]
    assert state["heavy"] is False


def _executed_test_files(text: str) -> set[str]:
    body = text.split("\njobs:", 1)[1]
    body = re.sub(r"--(?:root|soren)-path\s+'[^']*'", "", body)
    return set(re.findall(r"(?:ops/vm_actions/)?tests/[A-Za-z0-9_./-]+\.py", body))


@pytest.mark.parametrize("name", NARROWED)
def test_every_test_file_the_workflow_runs_starts_it(name):
    text = _text(name)
    paths = ctp.event_paths(text, "pull_request")
    executed = _executed_test_files(text)
    assert executed, name
    for test_file in sorted(executed):
        assert (ROOT / test_file).is_file(), test_file
        assert ctp.path_selected(paths, test_file), (name, test_file)


@pytest.mark.parametrize("name", NARROWED)
def test_workflow_file_and_conftest_start_the_workflow(name):
    paths = ctp.event_paths(_text(name), "pull_request")
    assert ctp.path_selected(paths, f".github/workflows/{name}")
    assert ctp.path_selected(paths, "conftest.py")
