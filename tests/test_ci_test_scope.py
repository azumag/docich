"""Which pull requests may skip the full suite, and what they run instead."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.github/scripts'))
_spec = importlib.util.spec_from_file_location('ci_test_scope', ROOT / '.github/scripts/ci_test_scope.py')
scope = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scope)

STRATEGY = 'src/docich/hanjuku/strategy/goals.py'


@pytest.fixture(scope='module')
def seams():
    return scope.seam_modules()


@pytest.mark.parametrize('changed', [
    [STRATEGY],
    [STRATEGY, 'tests/test_hanjuku_goals.py'],
    [STRATEGY, 'tests/fixtures/hanjuku/g1097-map-sortie-starved.png', 'docs/hanjuku_script_bot.md'],
    [STRATEGY, 'ops/vm_actions/collect_diagnostics.py', 'ops/vm_actions/tests/test_collect_diagnostics.py'],
    ['brains/hanjuku/bot.py', 'config/games/hanjuku-hero.toml'],
    ['tests/test_hanjuku_goals.py'],
])
def test_hanjuku_only_changes_get_the_hanjuku_suite(changed, seams):
    assert scope.choose_suite(changed, seams)[0] == 'hanjuku'


@pytest.mark.parametrize('outside', [
    'src/docich/retro_corner.py',
    'src/docich/adapters/retroarch.py',
    'tests/test_retro_corner.py',
    'tests/conftest.py',
    'conftest.py',
    'requirements-test.txt',
    '.node-version',
    '.github/workflows/ci.yml',
    '.github/scripts/ci_test_scope.py',
    'ops/vm_actions/gateway.py',
    'games/soviet_now',
    'tests/fixtures/other.png',
    'src/docich/hanjukuish/module.py',
])
def test_any_change_outside_hanjuku_gets_the_full_suite(outside, seams):
    assert scope.choose_suite([STRATEGY, outside], seams)[0] == 'full'


@pytest.mark.parametrize('seam', [
    'src/docich/hanjuku_hotload.py',            # lazy import inside a function
    'src/docich/hanjuku_log_retention.py',      # module-level import
    'src/docich/hanjuku_pixels.py',             # compatibility alias
    'src/docich/hanjuku/os_layer/pixels.py',    # the module behind that alias
    'src/docich/hanjuku_policy.py',             # facade over every layer
])
def test_a_module_imported_from_outside_hanjuku_gets_the_full_suite(seam, seams):
    suite, reason = scope.choose_suite([STRATEGY, seam], seams)
    assert suite == 'full'
    assert seam in reason


def test_the_policy_facade_does_not_turn_all_of_hanjuku_into_seams(seams):
    assert STRATEGY not in seams
    assert len(seams) < 30


@pytest.mark.parametrize('changed', [
    ['docs/hanjuku_script_bot.md'],
    ['README.md', 'docs/operations/ops-brief.md'],
    ['ops/vm_actions/gateway.py'],
    ['.github/workflows/vm-operations.yml'],
    ['tests/fixtures/hanjuku/g1097-map-sortie-starved.png'],
    ['games/soviet_now'],
])
def test_a_change_no_test_can_see_runs_no_tests(changed, seams):
    assert scope.choose_suite(changed, seams)[0] == 'none'


@pytest.mark.parametrize('relevant', [
    'src/docich/cli.py', 'tests/test_cli.py', 'conftest.py', 'requirements-test.txt', '.node-version',
    'brains/bastet/bot.py', 'config/games/bastet.toml',
    '.github/workflows/ci.yml', '.github/scripts/ci_test_scope.py', '.github/scripts/ci_trigger_paths.py',
])
def test_one_relevant_file_runs_tests_even_beside_documentation(relevant, seams):
    assert scope.choose_suite(['docs/a.md', relevant], seams)[0] == 'full'


def test_no_change_list_gets_the_full_suite(seams):
    assert scope.choose_suite([], seams)[0] == 'full'


@pytest.mark.parametrize('base,head', [('', ''), ('', 'abc'), ('abc', '')])
def test_a_run_without_pull_request_shas_is_the_full_suite(base, head):
    assert scope.decide(base, head)[0] == 'full'


def test_an_unknown_commit_is_the_full_suite():
    assert scope.decide('0' * 40, '1' * 40)[0] == 'full'


def test_a_broken_seam_scan_is_the_full_suite(monkeypatch):
    monkeypatch.setattr(scope, 'changed_files', lambda *args: [STRATEGY])
    def broken(*args):
        raise SyntaxError('unparsable module')
    monkeypatch.setattr(scope, 'seam_modules', broken)
    suite, reason = scope.decide('a', 'b')
    assert (suite, 'could not decide' in reason) == ('full', True)


def _git(root, *args):
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', *args],
                   cwd=root, check=True, capture_output=True)


def test_changed_files_reports_both_sides_of_a_move_into_hanjuku(tmp_path):
    _git(tmp_path, 'init', '-q')
    module = tmp_path / 'src/docich/shared.py'
    module.parent.mkdir(parents=True)
    module.write_text('value = 1\n' * 20)
    _git(tmp_path, 'add', '.')
    _git(tmp_path, 'commit', '-qm', 'base')
    _git(tmp_path, 'branch', 'base')
    (tmp_path / 'src/docich/hanjuku').mkdir()
    _git(tmp_path, 'mv', 'src/docich/shared.py', 'src/docich/hanjuku/shared.py')
    _git(tmp_path, 'commit', '-qm', 'move')
    changed = scope.changed_files('base', 'HEAD', tmp_path)
    assert sorted(changed) == ['src/docich/hanjuku/shared.py', 'src/docich/shared.py']
    assert scope.choose_suite(changed, set())[0] == 'full'


def test_decide_writes_only_the_github_output_line_on_stdout():
    result = subprocess.run([sys.executable, str(ROOT / '.github/scripts/ci_test_scope.py'), 'decide',
                             '--base', '', '--head', ''],
                            capture_output=True, text=True, check=True)
    assert result.stdout == 'suite=full\n'
    assert 'not a pull request' in result.stderr


def test_hanjuku_suite_keeps_every_hanjuku_test_and_every_test_naming_hanjuku():
    targets = scope.hanjuku_targets()
    assert targets == sorted(set(targets))
    expected = {path.relative_to(ROOT).as_posix() for path in (ROOT / 'tests').glob('test_*.py')
                if path.name.startswith('test_hanjuku') or 'hanjuku' in path.read_text(encoding='utf-8')}
    assert set(targets) == expected - {scope.SERIAL_TEST}
    assert {'tests/test_retro_corner.py', 'tests/test_webui.py', 'tests/test_corner_rotation.py'} <= set(targets)
    assert 'tests/test_nethack_policy.py' not in targets


def test_targets_command_prints_pytest_arguments():
    run = lambda suite: subprocess.run(  # noqa: E731
        [sys.executable, str(ROOT / '.github/scripts/ci_test_scope.py'), 'targets', suite],
        capture_output=True, text=True, check=True).stdout.split()
    assert run('full') == ['tests/']
    assert run('hanjuku') == scope.hanjuku_targets()
