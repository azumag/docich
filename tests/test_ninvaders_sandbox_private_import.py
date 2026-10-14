"""Regression tests for the nInvaders policy import boundary."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.ninvaders import sandbox as S  # noqa: E402


@pytest.mark.parametrize("name", ["__loader__", "__spec__", "__dict__"])
def test_static_gate_rejects_private_from_math_imports(name):
    source = (
        f"from math import {name} as escape\n"
        "def decide(obs, state):\n"
        "    return []\n"
    )
    with pytest.raises(S.PolicyRejected, match="private import"):
        S.validate_policy_source(source)


def test_static_gate_blocks_loader_escape_that_can_import_os():
    source = (
        "from math import __loader__ as loader\n"
        "def decide(obs, state):\n"
        "    os = loader.load_module('os')\n"
        "    os.system('true')\n"
        "    return []\n"
    )
    with pytest.raises(S.PolicyRejected, match="private import"):
        S.validate_policy_source(source)


def test_runtime_guard_rejects_private_fromlist_defense_in_depth():
    with pytest.raises(ImportError):
        S._guarded_import("math", fromlist=("__loader__",))
    with pytest.raises(ImportError):
        S._guarded_import("math", fromlist=("*",))
    assert S._guarded_import("math", fromlist=("sqrt",)).sqrt(9) == 3
