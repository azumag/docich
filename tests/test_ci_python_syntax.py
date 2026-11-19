"""Contract for the ``python-syntax`` gate in ``ci.yml``.

The job used to run ``python3 -m compileall -q src tests`` only, so a pull
request could land a syntax-broken file under ``ops/`` behind a green check:
#4740 pushed an unresolved conflict marker into
``ops/vm_actions/collect_diagnostics.py`` and only the test job reported the
SyntaxError (#4741). These tests pin ``ops`` on the compile list and prove the
declared command actually fails on a broken file there.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/ci.yml'
ROOT = WORKFLOW.parents[2]


def _command() -> list[str]:
    line = next(
        line.strip() for line in WORKFLOW.read_text().splitlines()
        if line.strip().startswith('run: python3 -m compileall')
    )
    return line.removeprefix('run: ').split()


def test_the_compile_list_names_ops_beside_src_and_tests():
    tokens = _command()
    assert tokens == ['python3', '-m', 'compileall', '-q', 'src', 'tests', 'ops']
    # compileall fails on a missing directory, so a typo here can only break the
    # job, never silently drop a tree; assert the layout anyway.
    for target in tokens[4:]:
        assert (ROOT / target).is_dir(), target


def _compile(tmp_path: Path, targets: list[str], broken: str | None = None) -> subprocess.CompletedProcess:
    for target in targets:
        directory = tmp_path / target
        directory.mkdir(parents=True)
        (directory / 'ok.py').write_text('VALUE = 1\n')
    if broken is not None:
        (tmp_path / broken).write_text('def broken(:\n')
    return subprocess.run(
        ['python3', '-m', 'compileall', '-q', *targets],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )


def test_the_declared_command_passes_on_a_clean_tree(tmp_path):
    result = _compile(tmp_path, _command()[4:])
    assert result.returncode == 0, result.stderr


def test_the_declared_command_fails_on_a_syntax_error_under_ops(tmp_path):
    targets = _command()[4:]
    assert 'ops' in targets
    result = _compile(tmp_path, targets, broken='ops/broken.py')
    assert result.returncode != 0
    assert 'broken.py' in result.stderr + result.stdout
