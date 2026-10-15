#!/usr/bin/env python3
"""Temporary branch-only development helper; never executed on production.

Removed before the PR is merged. Applies only the reviewed source edits in
an isolated Actions checkout and publishes a fast-forward feature commit.
"""
from __future__ import annotations
import ast
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = 'azumag/docich'
BRANCH = 'fix/hanjuku-retreat-value-20261015'
ALLOWED = {
    'src/docich/hanjuku/judgment/battle.py',
    'src/docich/hanjuku/judgment/retreat.py',
    'src/docich/hanjuku/flows/battle.py',
    'src/docich/hanjuku/flows/battle_menus.py',
    'src/docich/hanjuku/version.py',
    'tests/test_hanjuku_chart_bot.py',
    'tests/test_hanjuku_combat_strength.py',
}
OLD_VERSION = 'hanjuku-chart-v222-hero-losing-melee-rescue'
NEW_VERSION = 'hanjuku-chart-v223-value-aware-retreat'


def function_edit(path, name, transform):
    file = Path(path)
    text = file.read_text()
    tree = ast.parse(text)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(nodes) != 1:
        raise ValueError(f'expected exactly one function: {name}')
    node = nodes[0]
    lines = text.splitlines(keepends=True)
    old = ''.join(lines[node.lineno - 1:node.end_lineno])
    new = transform(old)
    lines[node.lineno - 1:node.end_lineno] = [new]
    result = ''.join(lines)
    ast.parse(result)
    file.write_text(result)


def once(text, before, after):
    if after in text:
        return text
    if text.count(before) != 1:
        raise ValueError('reviewed source anchor no longer matches')
    return text.replace(before, after, 1)


def update_expectations():
    path = 'tests/test_hanjuku_combat_strength.py'
    old_name = 'test_hp_double_alone_cannot_release_retreat_protection_for_weak_attack'
    new_name = 'test_hp_double_is_not_advantage_but_weak_general_can_accept_risk'
    if 'def ' + old_name in Path(path).read_text():
        function_edit(path, old_name, lambda old:
            old.replace(old_name, new_name, 1)
            .replace('assert p._hero_retreat_needed(cur) is True',
                     'assert p._hero_retreat_needed(cur) is False', 1)
            .replace("assert p._hero_retreat_open({'battle': cur}, cur) == [p.pad('b')]",
                     "assert p._hero_retreat_open({'battle': cur}, cur) is None\n"
                     "    assert cur['retreat_assessment']['reason'] == 'accept_risk_after_resources_exhausted'\n"
                     "    cur['survival']['exhausted'] = False\n"
                     "    assert p._hero_retreat_needed(cur) is True", 1))
    function_edit(path, 'test_observed_zero_is_not_a_conserved_rescue_resource', lambda old: once(old,
        '        assert p._hero_retreat_needed(cur)\n',
        '        assert not p._hero_retreat_needed(cur)\n'
        "        assert cur['retreat_assessment']['reason'] == 'accept_risk_after_resources_exhausted'\n"))
    function_edit(path, 'test_hotloaded_conservation_rechecks_a_new_named_zero_before_continuing', lambda old: once(old,
        "    assert cur['survival']['exhausted'] is True and p._hero_retreat_needed(cur)\n",
        "    assert cur['survival']['exhausted'] is True and not p._hero_retreat_needed(cur)\n"
        "    assert cur['retreat_assessment']['reason'] == 'accept_risk_after_resources_exhausted'\n"
        "    assert mem['egg_uses']['チコリ'] == 0\n"))


def apply():
    path = Path('src/docich/hanjuku/judgment/battle.py')
    if 'def _default_retreat_needed(cur):' not in path.read_text():
        function_edit(str(path), '_hero_retreat_needed', lambda old:
            old.replace('def _hero_retreat_needed(cur):', 'def _default_retreat_needed(cur):', 1)
            + '\n\ndef _hero_retreat_needed(cur):\n'
            + '    """Value-aware ordinary retreat with the existing special-case guards."""\n'
            + '    from .retreat import retreat_needed\n'
            + '    return retreat_needed(cur)\n')
    path = Path('src/docich/hanjuku/judgment/retreat.py')
    text = path.read_text()
    text = once(text,
        "    if (cur.get('ally') in (None, NAME, 'しゅじんこう')\n",
        "    if (cur.get('side') != 'attack'\n"
        "            or (cur.get('hero_retreat') or {}).get('selected')\n"
        "            or cur.get('ally') in (None, NAME, 'しゅじんこう')\n")
    path.write_text(text)
    function_edit('src/docich/hanjuku/flows/battle.py', '_hero_retreat_open', lambda old: once(old,
        "'melee_balance': _melee_balance(cur)},",
        "'melee_balance': _melee_balance(cur),\n"
        "                             'retreat_assessment': dict(cur.get('retreat_assessment') or {})},"))
    function_edit('src/docich/hanjuku/flows/battle_menus.py', '_hero_retreat_menu', lambda old: once(old,
        "observed_metric={'ally_hp': cur['ally_hp'], 'enemy_hp': cur['enemy_hp']},",
        "observed_metric={'ally_hp': cur['ally_hp'], 'enemy_hp': cur['enemy_hp'],\n"
        "                             'retreat_assessment': dict(cur.get('retreat_assessment') or {})},"))
    for name in ('src/docich/hanjuku/version.py', 'tests/test_hanjuku_chart_bot.py'):
        path = Path(name)
        text = path.read_text()
        if OLD_VERSION not in text and NEW_VERSION not in text:
            raise ValueError('bot version changed concurrently')
        path.write_text(text.replace(OLD_VERSION, NEW_VERSION))
    update_expectations()
    subprocess.run(['git', 'diff', '--check'], check=True)
    report = Path('retreat-development-report')
    report.mkdir(exist_ok=True)
    (report / 'source.diff').write_bytes(subprocess.check_output(['git', 'diff', '--', *sorted(ALLOWED)]))
    print('Reviewed retreat patch applied in isolated checkout.')


def api(endpoint, payload=None):
    command = ['gh', 'api', endpoint]
    if payload is not None:
        command += ['--method', 'POST', '--input', '-']
    result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f'GitHub request failed for reviewed endpoint ({result.returncode})')
    return json.loads(result.stdout)


def publish():
    if (os.environ.get('GITHUB_REPOSITORY') != REPO
            or os.environ.get('GITHUB_REF') != 'refs/heads/' + BRANCH):
        raise ValueError('feature-branch authorization denied')
    parent = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if parent != os.environ.get('GITHUB_SHA'):
        raise ValueError('checkout is not event SHA')
    changed = subprocess.check_output(['git', 'diff', '--name-only'], text=True).splitlines()
    if not changed or not set(changed) <= ALLOWED:
        raise ValueError('unexpected or empty patch')
    current = api(f'repos/{REPO}/git/ref/heads/{BRANCH}')['object']['sha']
    if current != parent:
        raise ValueError('feature branch advanced; refusing stale update')
    base = api(f'repos/{REPO}/git/commits/{parent}')['tree']['sha']
    elements = []
    for name in sorted(changed):
        blob = api(f'repos/{REPO}/git/blobs', {
            'content': base64.b64encode(Path(name).read_bytes()).decode('ascii'), 'encoding': 'base64'})
        elements.append({'path': name, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
    tree = api(f'repos/{REPO}/git/trees', {'base_tree': base, 'tree': elements})
    commit = api(f'repos/{REPO}/git/commits', {
        'message': 'fix(hanjuku): integrate value-aware retreat with resource-first guards',
        'tree': tree['sha'], 'parents': [parent]})
    if api(f'repos/{REPO}/git/ref/heads/{BRANCH}')['object']['sha'] != parent:
        raise ValueError('feature branch advanced; refusing stale update')
    command = ['gh', 'api', '--method', 'PATCH', f'repos/{REPO}/git/refs/heads/{BRANCH}', '--input', '-']
    result = subprocess.run(command, input=json.dumps({'sha': commit['sha'], 'force': False}),
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('fast-forward feature update failed')
    report = {'feature_commit': commit['sha'], 'parent': parent, 'paths': sorted(changed)}
    Path('retreat-development-report/commit.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Feature commit:', commit['sha'])


if __name__ == '__main__':
    {'apply': apply, 'publish': publish}[sys.argv[1]]()
