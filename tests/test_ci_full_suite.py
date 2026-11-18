"""Keep full-suite coverage while measuring runtime and avoiding redundant setup."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import subprocess
import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/ci.yml'


def _system_setup() -> str:
    text = WORKFLOW.read_text()
    step = text.split('      - name: Install system dependencies\n', 1)[1]
    step = step.split('      - name:', 1)[0]
    body = step.split('        run: |\n', 1)[1]
    return '\n'.join(line[10:] for line in body.splitlines())


def test_full_suite_runs_every_test_on_the_supported_python():
    text = WORKFLOW.read_text()
    # Python 3.11 is no longer a supported target: the suite runs once, on the
    # interpreter the production hosts actually provide.
    assert "python: ['3.11', '3.12']" not in text
    assert "          python-version: '3.12'\n" in text
    command = next(line.strip()[5:] for line in text.splitlines()
                   if line.strip().startswith('run: python3 -m pytest'))
    assert command.split() == ['python3', '-m', 'pytest', '-q', '-p', 'ci_shard',
                               '$(python3', '.github/scripts/ci_test_scope.py', 'targets',
                               '"$TEST_SUITE")',
                               '--durations=50', '--durations-min=0.1',
                               '-n', '4', '--dist', 'loadfile',
                               '--ignore=tests/test_hanjuku_scene_process.py',
                               '--junitxml="${{runner.temp}}/junit-${{matrix.shard}}.xml"']
    assert 'continue-on-error:' not in text


def test_per_test_timings_survive_a_cancelled_run():
    text = WORKFLOW.read_text()
    job = text.split('\n  full-suite:\n', 1)[1].split('\n  ci-required:', 1)[0]
    # A cancelled or timed-out run never prints the --durations table, so the
    # junit report is uploaded on every outcome, red or green (#4624).
    assert '--junitxml=' in job
    upload = job.split('      - name: Upload the pytest junit report\n', 1)[1]
    assert '        if: always()\n' in upload
    assert '        uses: actions/upload-artifact@' in upload


def test_the_suite_is_cut_into_three_shards_that_each_keep_the_whole_contract():
    text = WORKFLOW.read_text()
    job = text.split('\n  full-suite:\n', 1)[1].split('\n  ci-required:', 1)[0]
    # Three runners, none of which may cancel the others: a red shard must not hide
    # another red shard, and ci-required reads the aggregate result of the matrix.
    # A third shard costs nothing on a public repository and keeps the slowest
    # shard inside the job timeout as the suite grows (#4624).
    assert '      fail-fast: false\n' in job
    assert '        shard: [1, 2, 3]\n' in job
    assert '      CI_SHARD: ${{ matrix.shard }}/${{ strategy.job-total }}\n' in job
    assert 'PYTHONPATH: .github/scripts' in job
    # The one-thread file is outside the sharded collection, so exactly one shard runs it.
    assert job.count('tests/test_hanjuku_scene_process.py') >= 2
    serial = job.split('      - name: Single-threaded scene-process contracts (serial)\n', 1)[1]
    assert '        if: matrix.shard == 1\n' in serial.split('        run:', 1)[0]
    # The shard plugin ships beside the scope script it is loaded from.
    assert (WORKFLOW.parent.parent / 'scripts/ci_shard.py').is_file()


def test_every_run_outside_a_pull_request_is_the_full_suite():
    text = WORKFLOW.read_text()
    assert "    - cron: '23 18 * * *'\n" in text
    # Only a pull request has these, so schedule and dispatch decide on empty SHAs.
    assert 'BASE_SHA: ${{ github.event.pull_request.base.sha }}' in text
    assert 'HEAD_SHA: ${{ github.event.pull_request.head.sha }}' in text
    assert "    needs: test-scope\n" in text


def test_single_thread_sensitive_file_runs_on_the_serial_runner():
    text = WORKFLOW.read_text()
    ignored = 'tests/test_hanjuku_scene_process.py'
    assert (WORKFLOW.parents[2] / ignored).is_file(), ignored
    # Excluded from the parallel step, but covered by its own serial step: the
    # module proves the scene owner runs in a one-thread process, and every
    # pytest-xdist worker carries an execnet receiver thread.
    assert f'--ignore={ignored}' in text
    assert f'run: python3 -m pytest -q {ignored} --junitxml="${{{{runner.temp}}}}/junit-serial.xml"\n' in text


def test_ci_starts_on_every_pull_request_because_main_requires_it():
    text = WORKFLOW.read_text().split('\npermissions:', 1)[0]
    # A required check that never starts leaves a pull request blocked forever,
    # so the relevance of a change is decided in test-scope, not by a paths filter.
    assert '\n    paths:' not in text
    assert '\n  pull_request:\n' in text
    # A push to main does not re-run the full suite: the per-area workflows
    # cover the areas a push actually touched, the nightly schedule covers the
    # whole suite, and workflow_dispatch is the manual entry point.
    assert '\n  push:' not in text
    assert '\n  schedule:' in text
    assert '\n  workflow_dispatch:' in text


def _gate() -> str:
    text = WORKFLOW.read_text().split('  ci-required:\n', 1)[1]
    assert '    if: ${{ always() }}\n' in text
    assert '    needs: [test-scope, python-syntax, full-suite]\n' in text
    return '\n'.join(line[10:] for line in text.split('        run: |\n', 1)[1].splitlines())


@pytest.mark.skipif(os.name != 'posix' or not Path('/bin/bash').exists(), reason='bash required')
@pytest.mark.parametrize('suite,scope,syntax,tests,passes', [
    ('full', 'success', 'success', 'success', True),
    ('hanjuku', 'success', 'success', 'success', True),
    ('none', 'success', 'skipped', 'skipped', True),
    ('full', 'success', 'success', 'failure', False),
    ('hanjuku', 'success', 'failure', 'success', False),
    ('full', 'success', 'success', 'cancelled', False),
    ('full', 'success', 'skipped', 'skipped', False),   # a skipped suite is not a pass
    ('hanjuku', 'success', 'success', 'skipped', False),
    ('', 'failure', 'skipped', 'skipped', False),       # the decision itself failed
    ('none', 'failure', 'skipped', 'skipped', False),
    ('none', 'success', 'success', 'failure', False),
])
def test_required_check_passes_only_when_every_needed_job_passed(suite, scope, syntax, tests, passes):
    result = subprocess.run(
        ['/bin/bash', '--noprofile', '--norc', '-e', '-c', _gate()],
        env={'SUITE': suite, 'SCOPE_RESULT': scope, 'SYNTAX_RESULT': syntax, 'TESTS_RESULT': tests},
        capture_output=True, text=True, timeout=10,
    )
    assert (result.returncode == 0) is passes, result.stderr


def test_parallel_collection_ids_are_deterministic():
    # pytest-xdist aborts the entire run when workers collect different node
    # ids. That happens when a parametrize value is built from uuid4()/time at
    # import time, so keep those out of parametrize arguments.
    volatile = {'uuid1', 'uuid4', 'time', 'now', 'today', 'utcnow'}
    offenders = []
    for path in sorted(Path(__file__).resolve().parent.glob('test_*.py')):
        source = path.read_text()
        if 'parametrize' not in source:
            continue
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if 'parametrize' not in ast.unparse(decorator.func):
                    continue
                for call in ast.walk(decorator):
                    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                            and call.func.attr in volatile):
                        offenders.append(f'{path.name}::{node.name}')
    assert offenders == []


def test_pip_cache_tracks_all_root_requirements_files():
    text = WORKFLOW.read_text()
    assert re.search(r'          cache: pip\n', text)
    assert "          cache-dependency-path: 'requirements*.txt'" in text
    # Restoring downloaded packages must not bypass dependency installation.
    assert 'run: python3 -m pip install -r requirements-test.txt' in text


def test_system_dependencies_restore_the_cached_apt_archives():
    text = WORKFLOW.read_text()
    job = text.split('\n  full-suite:\n', 1)[1].split('\n  ci-required:', 1)[0]
    # A slow mirror can starve a large deb even inside the bounded retries
    # (#4624, #4625 run 44370031644), so a green shard persists the .deb files
    # the install stages through ~/apt-debs and the restore must run first.
    cache_name = '      - name: Cache the downloaded apt archives\n'
    assert job.index(cache_name) < job.index('      - name: Install system dependencies\n')
    step = job.split(cache_name, 1)[1].split('      - name:', 1)[0]
    # Same suite guard as the install: a hanjuku-scoped run skips both.
    assert "        if: needs.test-scope.outputs.suite != 'hanjuku'\n" in step
    assert '        uses: actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830\n' in step
    assert '          path: ~/apt-debs\n' in step
    assert '          key: apt-debs-ubuntu-24.04-full-suite\n' in step
    # Shared prefix with the presenter-smoke apt caches so either workflow can
    # seed the other's ffmpeg deb.
    assert 'apt-debs-ubuntu-24.04-' in step.split('restore-keys: |', 1)[1]
    body = _system_setup()
    assert 'sudo cp -n ~/apt-debs/*.deb /var/cache/apt/archives/' in body
    assert "trap 'cp -n /var/cache/apt/archives/*.deb ~/apt-debs/" in body


def _run_setup(tmp_path: Path, available=(), *, fail=''):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    log = tmp_path / 'commands'
    # Use an isolated PATH: these are fakes, never the host's sudo/apt/ffmpeg.
    fake = r'''#!/bin/sh
name=${0##*/}
printf '%s' "$name" >> "$CI_SETUP_LOG"
for arg in "$@"; do printf ' %s' "$arg" >> "$CI_SETUP_LOG"; done
printf '\n' >> "$CI_SETUP_LOG"
if [ "$name" = sudo ]; then
    if [ -n "$CI_SETUP_FAIL" ] && [ "$CI_SETUP_FAIL" = "$2" ]; then exit 42; fi
    if [ "$2" = install ]; then
        /bin/cp "$0" "$PATH/ffmpeg"
        /bin/cp "$0" "$PATH/ffprobe"
        /bin/chmod +x "$PATH/ffmpeg" "$PATH/ffprobe"
    fi
elif [ "$CI_SETUP_FAIL" = "$name" ]; then
    exit 43
fi
'''
    for name in ('sudo', *available):
        path = bindir / name
        path.write_text(fake)
        path.chmod(0o755)
    # The step bounds apt calls with `timeout` and paces retries with `sleep`.
    # timeout drops its duration and runs the wrapped command; sleep is a
    # no-op. Neither logs: the contract under test is the apt/ffmpeg sequence.
    for name, body in [('timeout', '#!/bin/sh\nshift\nexec "$@"\n'),
                       ('sleep', '#!/bin/sh\nexit 0\n')]:
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    result = subprocess.run(
        ['/bin/bash', '--noprofile', '--norc', '-e', '-c', _system_setup()],
        env={'PATH': str(bindir), 'CI_SETUP_LOG': str(log), 'CI_SETUP_FAIL': fail},
        capture_output=True, text=True, timeout=10,
    )
    return result, log.read_text().splitlines() if log.exists() else []


@pytest.mark.skipif(os.name != 'posix' or not Path('/bin/bash').exists(), reason='bash required')
@pytest.mark.parametrize('available', [(), ('ffmpeg',), ('ffprobe',), ('ffmpeg', 'ffprobe')])
def test_system_setup_installs_only_when_needed(tmp_path, available):
    result, commands = _run_setup(tmp_path, available)
    assert result.returncode == 0, result.stderr
    expected = [] if len(available) == 2 else [
        'sudo apt-get update', 'sudo apt-get install -y ffmpeg']
    assert commands == expected + ['ffmpeg -version', 'ffprobe -version']


@pytest.mark.skipif(os.name != 'posix' or not Path('/bin/bash').exists(), reason='bash required')
def test_every_apt_call_is_bounded_and_the_index_refresh_is_retried():
    step = _system_setup()
    # A stalled mirror once hung the step until the job timeout killed it
    # (#4624). Every apt call carries a bound, and the index refresh gets
    # three bounded attempts before the step fails fast.
    assert 'timeout 120 sudo apt-get update' in step
    assert 'timeout 600 sudo apt-get install -y ffmpeg' in step
    assert 'for i in 1 2 3' in step
    assert 'apt-get install failed 3 times' in step


@pytest.mark.skipif(os.name != 'posix' or not Path('/bin/bash').exists(), reason='bash required')
def test_system_setup_propagates_package_errors(tmp_path):
    result, commands = _run_setup(tmp_path, fail='install')
    # A failing install is retried three times, then the step fails fast (#4624:
    # a single 300 s bound killed a slow-but-working mirror in CI).
    assert result.returncode == 1
    assert commands == ['sudo apt-get update'] + ['sudo apt-get install -y ffmpeg'] * 3
    assert 'apt-get install failed 3 times' in result.stderr


@pytest.mark.skipif(os.name != 'posix' or not Path('/bin/bash').exists(), reason='bash required')
def test_system_setup_retries_a_stuck_index_refresh_then_fails_fast(tmp_path):
    result, commands = _run_setup(tmp_path, fail='update')
    assert result.returncode == 1
    assert commands == ['sudo apt-get update'] * 3
    assert 'apt-get update failed 3 times' in result.stderr
    assert all(not line.startswith(('ffmpeg ', 'ffprobe ')) for line in commands)


@pytest.mark.skipif(os.name != 'posix' or not Path('/bin/bash').exists(), reason='bash required')
@pytest.mark.parametrize('failure', ['ffmpeg', 'ffprobe'])
def test_system_setup_rejects_broken_preinstalled_binary(tmp_path, failure):
    result, commands = _run_setup(tmp_path, ('ffmpeg', 'ffprobe'), fail=failure)
    assert result.returncode == 43
    assert all(not line.startswith('sudo ') for line in commands)
