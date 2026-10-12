"""Layer contract of the deterministic Hanjuku policy (docich.hanjuku).

The package is split so an improvement touches one layer:

    orchestrator > flows (手順層) > judgment (判断層) > strategy (戦略層) > os_layer (OS層)

A module may import only lower layers (or siblings in its own layer). The
strategy and judgment layers additionally never read the screen and never
build pad input. Known violations are listed in ``KNOWN_DEBT`` with the
reason; the list may only shrink: a new violation fails, and a fixed one
must be removed from the list.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'src' / 'docich' / 'hanjuku'

# Rank of every layer inside docich.hanjuku. Equal ranks in different layers
# (flows vs reporting) may not import each other either.
LAYER_RANK = {
    '_compat': 0, 'decision_log': 0, 'version': 0,
    'os_layer': 1,
    'strategy': 2,
    'judgment': 3,
    'flows': 4, 'reporting': 4,
    'orchestrator': 5,
}

# Pre-layer modules outside the package, by the layer they belong to.
# 'data' is static game knowledge every layer may read.
LEGACY_LAYER = {
    'hanjuku_chart': 'data', 'hanjuku_reference': 'data', 'hanjuku_egg_reference': 'data',
    'hanjuku_egg_stock': 'data',
    'hanjuku_chart_adjust': 'strategy', 'hanjuku_experience': 'strategy',
}
LEGACY_RANK = {'data': 0, 'os_layer': 1, 'strategy': 2, 'flows': 4}
# Compatibility names over this package; no layer module may depend on them.
# hanjuku_policy/hanjuku_bot re-export the split modules; the others are
# sys.modules aliases of the module that moved into the package.
FACADES = {'hanjuku_policy', 'hanjuku_bot'}
MOVED = {
    'hanjuku_pixels': 'os_layer.pixels', 'hanjuku_glyphs': 'os_layer.glyphs',
    'hanjuku_font': 'os_layer.font', 'hanjuku_screen': 'os_layer.screen',
    'hanjuku_roster': 'os_layer.roster',
    'hanjuku_house': 'flows.house', 'hanjuku_camp_recheck': 'flows.camp_recheck',
    'hanjuku_starter': 'flows.starter',
}

# (importing module, imported module): why it is still allowed.
KNOWN_DEBT = {}

# Modules that turn intents into pad input; strategy/judgment must not use them.
INPUT_MODULES = {'os_layer.commands', 'os_layer.navigation'}
PURE_LAYERS = ('strategy', 'judgment')


def _modules():
    for path in sorted(PACKAGE.rglob('*.py')):
        rel = path.relative_to(PACKAGE).with_suffix('')
        parts = list(rel.parts)
        is_package = parts[-1] == '__init__'
        if is_package:
            parts = parts[:-1]
        yield '.'.join(parts), path, is_package


def _is_module(dotted):
    path = ROOT / 'src' / Path(*dotted.split('.'))
    return path.with_suffix('.py').is_file() or (path / '__init__.py').is_file()


def _imports(name, path, is_package):
    """Imported targets as package-relative names ('flows.map') or legacy 'hanjuku_x'."""
    here = ['docich', 'hanjuku', *name.split('.')] if name else ['docich', 'hanjuku']
    package = here if is_package else here[:-1]
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:len(package) - (node.level - 1)]
                module = base + (node.module.split('.') if node.module else [])
            else:
                module = (node.module or '').split('.')
            # ``from ..os_layer import commands`` imports the submodule.
            submodules = ['.'.join(module + [alias.name]) for alias in node.names
                          if _is_module('.'.join(module + [alias.name]))]
            targets = submodules or ['.'.join(module)]
        else:
            continue
        for target in targets:
            if target.startswith('docich.hanjuku.'):
                found.add(target[len('docich.hanjuku.'):])
            elif target.startswith('docich.hanjuku_'):
                found.add(target.split('.')[1])
    return found


def _layer(name):
    return name.split('.')[0]


def _violations():
    out = set()
    for name, path, is_package in _modules():
        if not name:
            continue
        layer = _layer(name)
        for target in _imports(name, path, is_package):
            if target in FACADES or target in MOVED:
                out.add((name, target))
            elif target.startswith('hanjuku_'):
                legacy = LEGACY_LAYER.get(target)
                assert legacy is not None, f'{name} imports unclassified {target}; add it to LEGACY_LAYER'
                if LEGACY_RANK[legacy] > LAYER_RANK[layer]:
                    out.add((name, target))
            else:
                target_layer = _layer(target)
                if target_layer == layer:
                    continue
                if LAYER_RANK[target_layer] >= LAYER_RANK[layer]:
                    out.add((name, target))
                elif layer in PURE_LAYERS and target in INPUT_MODULES:
                    out.add((name, target))
    return out


def test_every_layer_module_has_a_rank():
    for name, _, _ in _modules():
        if name:
            assert _layer(name) in LAYER_RANK, f'{name}: add its layer to LAYER_RANK'


def test_layers_import_only_lower_layers():
    violations = _violations()
    new = violations - set(KNOWN_DEBT)
    assert not new, f'layer violations (import of an equal/higher layer or a facade): {sorted(new)}'


def test_known_debt_is_still_real():
    fixed = set(KNOWN_DEBT) - _violations()
    assert not fixed, f'remove fixed entries from KNOWN_DEBT: {sorted(fixed)}'


@pytest.mark.parametrize('layer', PURE_LAYERS)
def test_strategy_and_judgment_never_read_the_screen_or_press(layer):
    for path in sorted((PACKAGE / layer).glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                params = {a.arg for a in (*node.args.args, *node.args.kwonlyargs)}
                assert not params & {'screen', 'frame'}, (
                    f'{path.name}:{node.name} takes the screen; move the reading to os_layer '
                    'or the procedure to flows')
            if isinstance(node, ast.Name) and node.id == 'pad':
                pytest.fail(f'{path.name}:{node.lineno} builds pad input; move it to flows')


def test_facades_reexport_and_forward_rebinding(monkeypatch):
    from docich import hanjuku_bot, hanjuku_policy
    from docich.hanjuku.flows import map as map_flow
    from docich.hanjuku.os_layer import navigation, phase
    from docich.hanjuku import orchestrator

    assert hanjuku_policy.nav_step is navigation.nav_step is map_flow.nav_step
    sentinel = object()
    monkeypatch.setattr(hanjuku_policy, 'nav_step', sentinel)
    assert navigation.nav_step is sentinel and map_flow.nav_step is sentinel
    monkeypatch.undo()
    assert navigation.nav_step is hanjuku_policy.nav_step is map_flow.nav_step
    assert navigation.nav_step is not sentinel

    monkeypatch.setattr(hanjuku_bot, 'classify', sentinel)
    assert phase.classify is sentinel and orchestrator.classify is sentinel
    monkeypatch.undo()
    assert orchestrator.classify is phase.classify is hanjuku_bot.classify


def test_facade_rebinding_skips_unrelated_objects(monkeypatch):
    # The bot's pad (ms) and the policy's pad (frames) are different
    # functions; rebinding one facade name must not touch the other.
    from docich import hanjuku_bot, hanjuku_policy
    from docich.hanjuku import orchestrator
    from docich.hanjuku.os_layer import commands

    assert hanjuku_bot.pad is orchestrator.pad is not commands.pad
    monkeypatch.setattr(hanjuku_policy, 'pad', object())
    assert orchestrator.pad is hanjuku_bot.pad


def test_moved_modules_are_aliases_of_their_layer_module():
    import importlib

    for old, new in MOVED.items():
        legacy = importlib.import_module(f'docich.{old}')
        layered = importlib.import_module(f'docich.hanjuku.{new}')
        assert legacy is layered, old
