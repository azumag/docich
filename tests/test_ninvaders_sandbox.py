"""LLM 生成コードの隔離: 静的ゲート・ワーカープロセス・タイムアウト・環境の遮断。"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.ninvaders import sandbox as S  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "brains" / "ninvaders" / "policy.py"

OK_SRC = "def decide(obs, state):\n    return ['Left', 'Space']\n"


def write(tmp_path, src, name="p.py"):
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return path


# ---------------------------------------------------------------- static gate

def test_baseline_and_simple_policy_pass_the_gate():
    S.validate_policy_source(BASELINE.read_text(encoding="utf-8"))
    S.validate_policy_source(OK_SRC)


@pytest.mark.parametrize("src, needle", [
    ("import os\ndef decide(o, s):\n    return []\n", "許可されないimport"),
    ("import subprocess\ndef decide(o, s):\n    return []\n", "許可されないimport"),
    ("import random\ndef decide(o, s):\n    random._os.system('true')\n    return []\n", "許可されないimport"),
    ("from os import path\ndef decide(o, s):\n    return []\n", "許可されないimport"),
    ("import math.foo\ndef decide(o, s):\n    return []\n", "許可されないimport"),
    ("from math import *\ndef decide(o, s):\n    return []\n", "import *"),
    ("def decide(o, s):\n    return open('/etc/passwd')\n", "禁止された名前"),
    ("def decide(o, s):\n    return eval('1')\n", "禁止された名前"),
    ("def decide(o, s):\n    return getattr(o, 'x')\n", "禁止された名前"),
    ("def decide(o, s):\n    return ().__class__\n", "禁止された属性"),
    ("def decide(o, s):\n    return o.__dict__\n", "禁止された属性"),
    ("def decide(o, s):\n    return math.__init__\n", "禁止された属性"),
    ("def decide(o, s):\n    return type(o)\n", "禁止された名前"),
    ("import random\ndef decide(o, s):\n    return []\n", "許可されないimport"),
    ("class P:\n    pass\ndef decide(o, s):\n    return []\n", "class定義"),
    ("def decide(o, s):\n    return __builtins__\n", "dunder"),
    ("x = 1\n", "decide"),
    ("def decide(o):\n    return []\n", "decide"),
    ("def decide(o, s, t):\n    return []\n", "decide"),
    ("def decide(*a):\n    return []\n", "decide"),
    ("def decide(o, s:\n", "構文エラー"),
    ("", "空"),
])
def test_static_gate_rejects(src, needle):
    with pytest.raises(S.PolicyRejected) as exc:
        S.validate_policy_source(src)
    assert needle in str(exc.value)


def test_static_gate_rejects_oversized_source():
    with pytest.raises(S.PolicyRejected):
        S.validate_policy_source("# " + "x" * S.MAX_SOURCE_BYTES + "\ndef decide(o, s):\n    return []\n")


def test_only_math_import_works_inside_the_worker(tmp_path):
    path = write(tmp_path, "import math\n"
                           "def decide(obs, state):\n    return ['Space'] if math.sqrt(4) == 2 else []\n")
    with S.PolicyProcess(path) as proc:
        assert proc.tick({"x": 1}) == ["Space"]


# ---------------------------------------------------------------- process behaviour

def test_keys_are_sanitized_and_left_right_cancel(tmp_path):
    path = write(tmp_path, "def decide(o, s):\n    return ['Left', 'Right', 'Space', 'Space', 'Enter', 5]\n")
    with S.PolicyProcess(path) as proc:
        assert proc.tick({}) == ["Space"]
    assert S.sanitize_keys(['Right', 'Space', 'Left', 'Space', 'Enter']) == ['Space']
    assert S.sanitize_keys('Left') == [] and S.sanitize_keys(None) == []
    assert S.sanitize_keys(['Left', 'Space']) == ['Left', 'Space']


def test_state_persists_across_ticks_within_one_worker(tmp_path):
    path = write(tmp_path, "def decide(o, s):\n    s['n'] = s.get('n', 0) + 1\n"
                           "    return ['Left'] if s['n'] % 2 else ['Right']\n")
    with S.PolicyProcess(path) as proc:
        assert [proc.tick({}) for _ in range(4)] == [['Left'], ['Right'], ['Left'], ['Right']]


def test_runtime_exception_is_counted_and_reported_without_killing_the_worker(tmp_path):
    path = write(tmp_path, "def decide(o, s):\n    return [][1] if o.get('boom') else ['Space']\n")
    with S.PolicyProcess(path) as proc:
        assert proc.tick({"boom": True}) == []
        assert "IndexError" in proc.last_error and proc.errors == 1 and proc.restarts == 0
        assert proc.tick({}) == ["Space"]  # same worker keeps serving


def test_hung_policy_times_out_restarts_and_eventually_dies(tmp_path):
    path = write(tmp_path, "def decide(o, s):\n    while True:\n        pass\n")
    with S.PolicyProcess(path, tick_timeout_s=0.2, max_restarts=2) as proc:
        for _ in range(4):
            assert proc.tick({}) == []
        assert proc.dead is True and proc.timeouts >= 3 and proc.restarts == 2
        assert proc.tick({}) == []  # dead workers stay quiet, never block


def test_import_time_hang_is_contained(tmp_path):
    path = write(tmp_path, "while True:\n    pass\ndef decide(o, s):\n    return []\n")
    with S.PolicyProcess(path, tick_timeout_s=0.2, max_restarts=1) as proc:
        assert proc.tick({}) == [] and proc.tick({}) == []
        assert proc.dead is True


def test_worker_relative_policy_path_is_resolved_absolute(tmp_path, monkeypatch):
    write(tmp_path, OK_SRC, "rel.py")
    monkeypatch.chdir(tmp_path)
    with S.PolicyProcess("rel.py") as proc:  # worker itself runs with cwd=/
        assert proc.tick({}) == ["Left", "Space"]


def test_worker_environment_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCICH_SECRET_SENTINEL", "leak-me")
    seen = {}
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        seen["env"] = kwargs.get("env")
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)
    with S.PolicyProcess(write(tmp_path, OK_SRC)):
        pass
    assert "DOCICH_SECRET_SENTINEL" not in seen["env"] and set(seen["env"]) <= {
        "PATH", "LANG", "PYTHONIOENCODING", "PYTHONHASHSEED"}


def test_rejected_source_never_spawns_a_process(tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append(a))
    with pytest.raises(S.PolicyRejected):
        S.PolicyProcess(write(tmp_path, "import os\ndef decide(o, s):\n    return []\n"))
    assert spawned == []


def test_guarded_import_blocks_modules_outside_the_whitelist_at_runtime():
    with pytest.raises(ImportError):
        S._guarded_import("os")
    with pytest.raises(ImportError):
        S._guarded_import("math.foo")
    assert S._guarded_import("math").sqrt(9) == 3
