"""CI scope for the shared broadcast overlay and the PR3 timer tests.

An overlay change is production UI, so it must get the full suite.  The
speedrun timer tests only cover the Hanjuku corner, so they stay on the
Hanjuku suite.  tests/test_ci_test_scope.py is intentionally left unchanged.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.github/scripts'))
_spec = importlib.util.spec_from_file_location(
    'ci_test_scope_overlays', ROOT / '.github/scripts/ci_test_scope.py')
scope = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scope)


@pytest.mark.parametrize('changed', [
    ['overlays/direct_broadcast_overlay.html'],
    ['overlays/direct_broadcast_overlay_state.mjs'],
    ['overlays/active_game_context.mjs'],
    ['overlays/shared_overlay_geometry.mjs'],
    ['overlays/direct_broadcast_overlay.html', 'tests/test_speedrun_timer_view.mjs'],
])
def test_an_overlay_change_gets_the_full_suite(changed):
    suite, reason = scope.choose_suite(changed, set())
    assert suite == 'full'
    assert 'outside Hanjuku' in reason


@pytest.mark.parametrize('changed', [
    ['tests/test_speedrun_timer_state.mjs'],
    ['tests/test_speedrun_timer_view.mjs'],
    ['tests/test_speedrun_timer_overlay.py'],
    ['tests/test_speedrun_timer_state_ownership.py'],
    ['tests/test_speedrun_timer_state.mjs', 'tests/test_speedrun_timer_view.mjs',
     'tests/test_speedrun_timer_overlay.py', 'tests/test_speedrun_timer_state_ownership.py'],
])
def test_the_timer_tests_alone_stay_on_the_hanjuku_suite(changed):
    suite, _reason = scope.choose_suite(changed, set())
    assert suite == 'hanjuku'


def test_the_ci_scope_test_itself_is_not_a_speedrun_test():
    # tests/test_ci_test_scope*.py covers CI routing, not the corner, so a
    # change to it is not silently folded into the Hanjuku-only list.
    suite, _reason = scope.choose_suite(['tests/test_ci_test_scope_overlays.py'], set())
    assert suite == 'full'


def test_the_hanjuku_suite_still_collects_the_browser_timer_test():
    # The pytest browser test names hanjuku in its content, so the generated
    # Hanjuku target list keeps it even though the file name does not.
    assert 'tests/test_speedrun_timer_overlay.py' in scope.hanjuku_targets()
