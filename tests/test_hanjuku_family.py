"""Contract tests for the Hanjuku family dispatcher (``hanjuku_family``).

The common layer (game switch, corner rotation/adapters, WebUI, telemetry)
must call the original corner's modules exactly as before, while being able to
resolve the mechanically generated speedrun copy under
``docich.hanjuku_speedrun`` once that package exists.

The last test is a source-level guard: a common-side ``'hanjuku-hero'`` string
literal must either be a deliberate original-only decision (marked
``# family: original-only`` on the same line) or an explicitly reviewed
allowlist entry.  This keeps new hard-coded game names from creeping into the
shared layer.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys
import types

import pytest

import docich
import docich.hanjuku_hotload as original_hotload
import docich.hanjuku_run as original_run
from docich.hanjuku_family import (
    FAMILY,
    ORIGINAL,
    PACKAGES,
    SPEEDRUN,
    hotload,
    is_hanjuku,
    is_original,
    module,
    name_of,
)


class FakeGame:
    """Minimal Game-like object: only ``.name`` matters to the dispatcher."""

    def __init__(self, name):
        self.name = name


#: Common-side literals that are knowingly left in place.  Keyed by
#: (path relative to ``src/docich``, stripped source line) so the test survives
#: unrelated line-number shifts.
ALLOWED_LITERALS = {
    ("comment/game_profile.py", 'HANJUKU = "hanjuku-hero"'):
        "comment/game_profile.py is handled by the separate profile work; "
        "this change does not touch it",
    ("comment/sorengame_context.py", '"hanjuku-hero": game_profile.HANJUKU_BLURB,'):
        "original-corner blurb table, owned by the separate profile work",
}

#: The generated speedrun copy is not part of the shared layer.
EXCLUDED_TOP_LEVEL = ("hanjuku_", "hanjuku")


def _common_sources():
    root = Path(docich.__file__).resolve().parent
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        parts = rel.parts
        if any(part.startswith(EXCLUDED_TOP_LEVEL) for part in parts):
            continue
        yield rel, path


def test_packages_and_family():
    assert ORIGINAL == "hanjuku-hero"
    assert SPEEDRUN == "hanjuku-speedrun"
    assert PACKAGES == {
        ORIGINAL: "docich",
        SPEEDRUN: "docich.hanjuku_speedrun",
    }
    assert FAMILY == frozenset({ORIGINAL, SPEEDRUN})


@pytest.mark.parametrize("game", [ORIGINAL, FakeGame(ORIGINAL)])
def test_is_hanjuku_accepts_str_and_game_like(game):
    assert is_hanjuku(game) is True
    assert is_original(game) is True
    assert name_of(game) == ORIGINAL


@pytest.mark.parametrize("game", [SPEEDRUN, FakeGame(SPEEDRUN)])
def test_is_hanjuku_accepts_speedrun(game):
    assert is_hanjuku(game) is True
    assert is_original(game) is False
    assert name_of(game) == SPEEDRUN


@pytest.mark.parametrize(
    "game",
    [None, "", "nethack", 42, object(), types.SimpleNamespace(name=None),
     types.SimpleNamespace(name=42), types.SimpleNamespace()],
)
def test_non_family_values_are_false(game):
    assert is_hanjuku(game) is False
    assert is_original(game) is False
    # A plain string keeps its own name; non-string non-Game values are None.
    expected = game if isinstance(game, str) else None
    assert name_of(game) == expected


def test_name_of_other_game():
    assert name_of("nethack") == "nethack"
    assert name_of(FakeGame("nethack")) == "nethack"


def test_original_module_is_the_same_object_as_sys_modules():
    assert module(ORIGINAL, "hanjuku_run") is original_run
    assert sys.modules["docich.hanjuku_run"] is original_run
    assert hotload(ORIGINAL) is original_hotload
    # Dotted submodule names (``hanjuku/`` inside the package) dispatch too.
    assert module(ORIGINAL, "hanjuku.state_budget") is sys.modules[
        "docich.hanjuku.state_budget"]


def test_monkeypatch_on_original_module_is_visible(monkeypatch):
    marker = object()
    monkeypatch.setattr(original_run, "_family_test_marker", marker, raising=False)
    resolved = module(ORIGINAL, "hanjuku_run")
    assert resolved is original_run
    assert resolved._family_test_marker is marker


def test_module_is_not_cached_by_the_dispatcher(monkeypatch):
    replacement = types.ModuleType("docich.hanjuku_run")
    monkeypatch.setitem(sys.modules, "docich.hanjuku_run", replacement)
    assert module(ORIGINAL, "hanjuku_run") is replacement


@pytest.mark.parametrize("name", ["hanjuku_run", "hanjuku_hotload", "hanjuku.state_budget"])
def test_speedrun_modules_resolve_to_their_own_package(name):
    speedrun = module(SPEEDRUN, name)
    original = module(ORIGINAL, name)
    assert speedrun is not original
    assert speedrun.__name__ == f"docich.hanjuku_speedrun.{name}"
    assert original.__name__ == f"docich.{name}"


def test_speedrun_hotload_is_bound_to_the_speedrun_game():
    assert module(SPEEDRUN, "hanjuku_hotload").RUN_GAME == SPEEDRUN
    assert module(ORIGINAL, "hanjuku_hotload").RUN_GAME == ORIGINAL


@pytest.mark.parametrize("game", [None, "nethack", 42])
def test_module_outside_family_raises_key_error(game):
    with pytest.raises(KeyError):
        module(game, "hanjuku_run")


def test_no_untracked_hanjuku_hero_literals_in_common_layer():
    violations = []
    for rel, path in _common_sources():
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if not (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value == ORIGINAL):
                continue
            line = lines[node.lineno - 1]
            if "# family: original-only" in line:
                continue
            if (rel.as_posix(), line.strip()) in ALLOWED_LITERALS:
                continue
            violations.append(f"{rel}:{node.lineno}: {line.strip()}")
    assert not violations, (
        "common-side 'hanjuku-hero' literals must be marked "
        "'# family: original-only' or listed in ALLOWED_LITERALS:\n"
        + "\n".join(violations)
    )


def test_allowlist_entries_still_exist():
    # An allowlist entry for a line that no longer exists hides a real new
    # violation behind a stale excuse; fail instead of drifting.
    for (rel, line), reason in ALLOWED_LITERALS.items():
        path = Path(docich.__file__).resolve().parent / rel
        assert path.is_file(), rel
        assert any(raw.strip() == line for raw in path.read_text(encoding="utf-8").splitlines()), (
            f"stale ALLOWED_LITERALS entry for {rel}: {line!r} ({reason})"
        )
