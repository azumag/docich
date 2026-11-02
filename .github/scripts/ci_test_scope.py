#!/usr/bin/env python3
"""Decide how much of tests/ a pull request has to run.

Usage:
    python3 .github/scripts/ci_test_scope.py decide --base SHA --head SHA
    python3 .github/scripts/ci_test_scope.py targets {full|hanjuku}

``decide`` prints ``suite=full``, ``suite=hanjuku`` or ``suite=none`` on stdout,
ready for ``$GITHUB_OUTPUT``, and the reason on stderr. ``targets`` prints the pytest
arguments of that suite.

About four in five pull requests change Hanjuku and nothing else, and the rest of
the suite cannot see most of those changes. A change gets the Hanjuku suite when

* every changed file is in ``HANJUKU_ONLY``, and
* no changed file is a seam module: a Hanjuku module that code outside Hanjuku
  imports directly, because that code then runs the changed module.

The Hanjuku suite is ``tests/test_hanjuku*.py`` plus every other test file that
names Hanjuku, so no test list has to be maintained. Retro corner regressions and
Hanjuku prediction contracts still start on these pull requests, and a nightly run
on main covers the whole suite.

A change that touches none of ``TEST_RELEVANT`` runs no tests. CI still starts on
every pull request, because main requires its ``ci-required`` check and a required
check that never starts blocks the pull request forever.

Whatever cannot be decided, including any error in this script, means ``full``.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ci_trigger_paths import path_selected  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = 'src/docich'
HANJUKU_MODULES = [f'{PACKAGE}/hanjuku*.py', f'{PACKAGE}/hanjuku/**']
TEST_RELEVANT = [
    '.node-version',
    'src/**/*.py',
    'tests/**/*.py',
    'conftest.py',
    'requirements*.txt',
    'brains/**',
    'config/games/**',
    '.github/workflows/ci.yml',
    '.github/scripts/ci_test_scope.py',
    '.github/scripts/ci_trigger_paths.py',
]
HANJUKU_ONLY = [
    *HANJUKU_MODULES,
    'tests/test_hanjuku*.py',
    'tests/fixtures/hanjuku/**',
    'brains/hanjuku/**',
    'config/games/hanjuku-hero.toml',
    'docs/**',
    # Hanjuku pull requests add their chart version to this file's allow-list.
    # Its own tests run in vm-operations-ci and hanjuku-predictions-ci.
    'ops/vm_actions/collect_diagnostics.py',
    'ops/vm_actions/tests/test_collect_diagnostics.py',
    'ops/vm_actions/tests/test_hanjuku*.py',
]
# Runs in its own serial step: the file needs a process with exactly one thread.
SERIAL_TEST = 'tests/test_hanjuku_scene_process.py'


def changed_files(base: str, head: str, root: Path = ROOT) -> list[str]:
    # --no-renames reports both sides, so moving a file into Hanjuku is not "Hanjuku only".
    output = subprocess.run(
        ['git', 'diff', '--name-only', '--no-renames', f'{base}...{head}'],
        cwd=root, check=True, capture_output=True, text=True).stdout
    return output.splitlines()


def _module_file(root: Path, dotted: str) -> str | None:
    relative = dotted.replace('.', '/')
    for candidate in (f'src/{relative}.py', f'src/{relative}/__init__.py'):
        if (root / candidate).is_file():
            return candidate
    return None


def _imported_modules(path: Path, package: str) -> set[str]:
    """Dotted names ``path`` imports, at any depth, including lazy function-level imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ''
            if node.level:
                parts = package.split('.')
                parts = parts[:len(parts) - (node.level - 1)]
                base = '.'.join(parts + ([node.module] if node.module else []))
            names.add(base)
            names.update(f'{base}.{alias.name}' for alias in node.names)
    return names


def _package_of(root: Path, path: Path) -> str:
    parent = path.relative_to(root / 'src').parent
    return '.'.join(parent.parts)


def _is_alias_module(path: Path) -> bool:
    """A compatibility shim that only replaces itself with the module that moved.

    ``hanjuku_policy.py`` also assigns ``sys.modules[__name__]``, but it is a facade
    over every layer. Following it would make all of Hanjuku a seam, so a module
    counts as an alias only when it does nothing else.
    """
    source = path.read_text(encoding='utf-8')
    statements = [node for node in ast.parse(source).body
                  if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
    return 'sys.modules[__name__]' in source and len(statements) <= 3


def seam_modules(root: Path = ROOT) -> set[str]:
    """Hanjuku module files that code outside Hanjuku imports directly."""
    seams: set[str] = set()
    pending: list[str] = []
    for path in sorted((root / PACKAGE).rglob('*.py')):
        relative = path.relative_to(root).as_posix()
        if path_selected(HANJUKU_MODULES, relative):
            continue
        for name in _imported_modules(path, _package_of(root, path)):
            target = _module_file(root, name)
            if target and path_selected(HANJUKU_MODULES, target):
                pending.append(target)
    while pending:
        target = pending.pop()
        if target in seams:
            continue
        seams.add(target)
        path = root / target
        if _is_alias_module(path):
            for name in _imported_modules(path, _package_of(root, path)):
                moved = _module_file(root, name)
                if moved and path_selected(HANJUKU_MODULES, moved):
                    pending.append(moved)
    return seams


def choose_suite(changed: list[str], seams: set[str]) -> tuple[str, str]:
    if not changed:
        return 'full', 'no changed files reported'
    if not any(path_selected(TEST_RELEVANT, path) for path in changed):
        return 'none', f'{len(changed)} changed files, none relevant to tests/'
    outside = [path for path in changed if not path_selected(HANJUKU_ONLY, path)]
    if outside:
        return 'full', f'changes outside Hanjuku: {outside[:5]}'
    touched = sorted(seams.intersection(changed))
    if touched:
        return 'full', f'changes a module non-Hanjuku code imports: {touched[:5]}'
    return 'hanjuku', f'{len(changed)} changed files, all Hanjuku-only'


def decide(base: str, head: str, root: Path = ROOT) -> tuple[str, str]:
    if not base or not head:
        return 'full', 'not a pull request'
    try:
        return choose_suite(changed_files(base, head, root), seam_modules(root))
    except Exception as error:  # fail open to the full suite, never to fewer tests
        return 'full', f'could not decide: {error!r}'


def hanjuku_targets(root: Path = ROOT) -> list[str]:
    targets = []
    for path in sorted((root / 'tests').glob('test_*.py')):
        relative = path.relative_to(root).as_posix()
        if relative != SERIAL_TEST and (path.name.startswith('test_hanjuku')
                                        or 'hanjuku' in path.read_text(encoding='utf-8')):
            targets.append(relative)
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    decide_parser = commands.add_parser('decide')
    decide_parser.add_argument('--base', default='')
    decide_parser.add_argument('--head', default='')
    targets_parser = commands.add_parser('targets')
    targets_parser.add_argument('suite', choices=('full', 'hanjuku'))
    args = parser.parse_args(argv)
    if args.command == 'decide':
        suite, reason = decide(args.base, args.head)
        print(f'{suite} suite: {reason}', file=sys.stderr)
        print(f'suite={suite}')
    else:
        print(' '.join(hanjuku_targets()) if args.suite == 'hanjuku' else 'tests/')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
