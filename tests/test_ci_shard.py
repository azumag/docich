"""The CI file shards must cover every collected test exactly once."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / '.github/scripts'
_spec = importlib.util.spec_from_file_location('ci_shard', SCRIPTS / 'ci_shard.py')
shard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shard)

WEIGHTS = {f'tests/test_{name}.py': weight for name, weight in
           [('a', 90), ('b', 40), ('c', 40), ('d', 25), ('e', 10), ('f', 3), ('g', 1), ('h', 1)]}


@pytest.mark.parametrize('spec,expected', [('1/1', (1, 1)), ('1/2', (1, 2)), ('3/3', (3, 3))])
def test_a_valid_shard_spec_is_parsed(spec, expected):
    assert shard.parse_shard(spec) == expected


@pytest.mark.parametrize('spec', ['', '1', '0/2', '3/2', '1/0', 'a/b', '1/2/3', '-1/2', '1/-2'])
def test_a_malformed_shard_spec_is_rejected_instead_of_running_everything(spec):
    with pytest.raises(ValueError):
        shard.parse_shard(spec)


@pytest.mark.parametrize('count', [1, 2, 3, 4])
def test_every_file_belongs_to_exactly_one_shard(count):
    owner = shard.assign(WEIGHTS, count)
    assert set(owner) == set(WEIGHTS)
    assert set(owner.values()) <= set(range(1, count + 1))


def test_the_partition_does_not_depend_on_input_order():
    forward = shard.assign(WEIGHTS, 2)
    backward = shard.assign(dict(reversed(list(WEIGHTS.items()))), 2)
    assert forward == backward


def test_the_heaviest_shard_is_close_to_an_even_split():
    owner = shard.assign(WEIGHTS, 2)
    load = [sum(w for path, w in WEIGHTS.items() if owner[path] == n) for n in (1, 2)]
    assert sum(load) == sum(WEIGHTS.values())
    # Longest-processing-time packing is within 4/3 of the optimum; here it is exact.
    assert max(load) - min(load) <= max(WEIGHTS.values()) // 2


def test_one_file_never_splits_across_shards():
    nodeids = ['tests/test_a.py::T::test_1', 'tests/test_a.py::T::test_2[x]', 'tests/test_b.py::test_1']
    assert [shard.file_of(nodeid) for nodeid in nodeids] == ['tests/test_a.py', 'tests/test_a.py', 'tests/test_b.py']


def _project(tmp_path: Path) -> Path:
    for name, tests in [('test_big', 6), ('test_mid', 3), ('test_small', 2), ('test_tiny', 1)]:
        body = '\n'.join(f'def test_{n}():\n    pass\n' for n in range(tests))
        (tmp_path / f'{name}.py').write_text(body)
    return tmp_path


def _collect(project: Path, spec: str | None) -> list[str]:
    env = {key: value for key, value in os.environ.items() if key != shard.ENV}
    env['PYTHONPATH'] = str(SCRIPTS)
    if spec:
        env[shard.ENV] = spec
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '--collect-only', '-q', '-p', 'ci_shard', '-p', 'no:cacheprovider',
         '--rootdir', str(project), str(project)],
        cwd=project, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return sorted(line for line in result.stdout.splitlines() if '::' in line)


def test_the_shards_together_collect_exactly_the_unsharded_tests(tmp_path):
    project = _project(tmp_path)
    everything = _collect(project, None)
    assert len(everything) == 12
    first, second = _collect(project, '1/2'), _collect(project, '2/2')
    assert first and second
    assert set(first).isdisjoint(second)
    assert sorted(first + second) == everything


def test_a_bad_shard_spec_fails_the_run_loudly(tmp_path):
    project = _project(tmp_path)
    env = {**os.environ, 'PYTHONPATH': str(SCRIPTS), shard.ENV: '3/2'}
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '--collect-only', '-q', '-p', 'ci_shard', '-p', 'no:cacheprovider',
         '--rootdir', str(project), str(project)],
        cwd=project, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
