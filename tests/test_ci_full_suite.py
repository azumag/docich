"""Keep full-suite coverage while measuring runtime and avoiding redundant setup."""
from __future__ import annotations

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


def test_full_suite_keeps_both_python_versions_and_all_tests():
    text = WORKFLOW.read_text()
    assert "python: ['3.11', '3.12']" in text
    assert 'fail-fast: false' in text
    command = next(line.strip()[5:] for line in text.splitlines()
                   if line.strip().startswith('run: python3 -m pytest'))
    assert command.split() == ['python3', '-m', 'pytest', '-q', 'tests/',
                               '--durations=50', '--durations-min=0.1']
    assert 'continue-on-error:' not in text


def test_dependency_changes_trigger_both_pr_and_main_checks():
    text = WORKFLOW.read_text().split('\npermissions:', 1)[0]
    for pattern in ('src/**/*.py', 'tests/**/*.py', 'conftest.py',
                    'requirements*.txt', '.github/workflows/ci.yml'):
        assert text.count(f"      - '{pattern}'") == 2


def test_pip_cache_tracks_all_root_requirements_files():
    text = WORKFLOW.read_text()
    assert re.search(r'          cache: pip\n', text)
    assert "          cache-dependency-path: 'requirements*.txt'" in text
    # Restoring downloaded packages must not bypass dependency installation.
    assert 'run: python3 -m pip install -r requirements-test.txt' in text


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
@pytest.mark.parametrize('failure', ['update', 'install'])
def test_system_setup_propagates_package_errors(tmp_path, failure):
    result, commands = _run_setup(tmp_path, fail=failure)
    assert result.returncode == 42
    assert all(not line.startswith(('ffmpeg ', 'ffprobe ')) for line in commands)
    if failure == 'update':
        assert commands == ['sudo apt-get update']


@pytest.mark.skipif(os.name != 'posix' or not Path('/bin/bash').exists(), reason='bash required')
@pytest.mark.parametrize('failure', ['ffmpeg', 'ffprobe'])
def test_system_setup_rejects_broken_preinstalled_binary(tmp_path, failure):
    result, commands = _run_setup(tmp_path, ('ffmpeg', 'ffprobe'), fail=failure)
    assert result.returncode == 43
    assert all(not line.startswith('sudo ') for line in commands)
