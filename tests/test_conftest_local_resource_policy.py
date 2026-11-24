"""Local machine resource policy in the root ``conftest.py``.

Local macOS runs lower their CPU and disk I/O priority, cap pytest-xdist workers and run at
most one full-suite session at a time; CI and other platforms are untouched.
"""

from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_root_conftest():
    spec = importlib.util.spec_from_file_location("docich_root_conftest_under_test", ROOT / "conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


conf = _load_root_conftest()


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("darwin", {}, True),
        ("darwin", {"CI": "true"}, False),
        ("darwin", {"DOCICH_TEST_FOREGROUND": "1"}, False),
        ("linux", {}, False),
    ],
)
def test_policy_applies_only_to_local_macos_runs(platform, env, expected):
    assert conf._local_resource_policy_enabled(env, platform) is expected


def test_worker_cap_shrinks_local_popen_workers():
    option = SimpleNamespace(tx=["popen"] * 8, numprocesses=8)

    assert conf._cap_xdist_workers(option, 4) == 8
    assert option.tx == ["popen"] * 4
    assert option.numprocesses == 4


@pytest.mark.parametrize(
    "tx",
    [None, [], ["popen"] * 4, ["popen"] * 3, ["ssh=build-host//python=python3"] * 8],
)
def test_worker_cap_leaves_small_or_remote_runs_alone(tx):
    option = SimpleNamespace(tx=tx, numprocesses=len(tx or []))

    assert conf._cap_xdist_workers(option, 4) is None
    assert option.tx == tx


def _option(**overrides):
    values = dict(keyword="", markexpr="", lf=False, deselect=None, collectonly=False)
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("args", "overrides", "expected"),
    [
        (["tests/"], {}, True),
        (["tests"], {}, True),
        (["."], {}, True),
        ([], {}, True),
        (["tests/test_x.py"], {}, False),
        (["tests/test_x.py::test_a"], {}, False),
        (["tests/"], {"keyword": "hanjuku"}, False),
        (["tests/"], {"markexpr": "slow"}, False),
        (["tests/"], {"lf": True}, False),
        (["tests/"], {"deselect": ["tests/test_x.py::test_a"]}, False),
        (["tests/"], {"collectonly": True}, False),
    ],
)
def test_full_suite_detection(tmp_path, args, overrides, expected):
    (tmp_path / "tests").mkdir()

    assert conf._is_full_suite_run(_option(**overrides), args, tmp_path, tmp_path) is expected


def test_full_suite_detection_accepts_absolute_tests_dir(tmp_path):
    (tmp_path / "tests").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    assert conf._is_full_suite_run(_option(), [str(tmp_path / "tests")], tmp_path, elsewhere) is True


def test_second_full_suite_session_waits_for_the_first(tmp_path):
    lock_path = tmp_path / "suite.lock"
    first = conf._acquire_full_suite_lock(lock_path, 0, sleep=lambda _s: None, stderr=io.StringIO())
    assert first is not None
    try:
        sleeps: list[float] = []
        err = io.StringIO()

        second = conf._acquire_full_suite_lock(lock_path, 10, sleep=sleeps.append, stderr=err)

        assert second is None  # gave up after the configured wait and runs unlocked
        assert sleeps == [5, 5]
        assert "another local full-suite run is in progress" in err.getvalue()
        assert f"pid={os.getpid()}" in err.getvalue()
    finally:
        os.close(first)

    # Closing the holder's descriptor releases the lock for the next session.
    third = conf._acquire_full_suite_lock(lock_path, 0, sleep=lambda _s: None, stderr=io.StringIO())
    assert third is not None
    os.close(third)


def test_unusable_lock_path_runs_without_lock(tmp_path):
    err = io.StringIO()

    fd = conf._acquire_full_suite_lock(tmp_path / "missing-dir" / "suite.lock", 0, stderr=err)

    assert fd is None
    assert "running without it" in err.getvalue()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS I/O policy API")
def test_low_priority_is_applied_and_inherited_by_subprocesses():
    probe = (
        "import ctypes, ctypes.util, os; "
        "libc = ctypes.CDLL(ctypes.util.find_library('c')); "
        "print(os.getpriority(os.PRIO_PROCESS, 0), libc.getiopolicy_np(0, 0))"
    )
    code = (
        "import importlib.util, subprocess, sys\n"
        f"spec = importlib.util.spec_from_file_location('c', {str(ROOT / 'conftest.py')!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "m._enter_low_priority()\n"
        f"exec({probe!r})\n"
        f"print(subprocess.run([sys.executable, '-c', {probe!r}], capture_output=True, text=True).stdout.strip())\n"
    )

    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()

    # parent and child: nice >= 10 and the utility (4) disk I/O tier
    assert int(out[0]) >= 10 and out[1] == "4"
    assert int(out[2]) >= 10 and out[3] == "4"
