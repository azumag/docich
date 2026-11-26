"""Tests for the hanjuku-speedrun fork generator (tools/hanjuku_speedrun_fork.py)."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'hanjuku_speedrun_fork.py'


def _load_tool():
    spec = importlib.util.spec_from_file_location('hanjuku_speedrun_fork_under_test', TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


# ---------------------------------------------------------------------------
# fixture repository
# ---------------------------------------------------------------------------

BOT_PY = textwrap.dedent('''\
    #!/usr/bin/env python3
    # -*- coding: utf-8 -*-
    """Original hanjuku-hero bot. This docstring must not be rewritten."""
    from __future__ import annotations

    from pathlib import Path
    import importlib

    from . import hanjuku_run
    from .hanjuku.flows import battle
    from .game_switch import atomic_write_json
    from docich import hanjuku_experience
    from docich.hanjuku.flows import battle as battle_abs
    from docich.hanjuku_corner import helper
    import docich.hanjuku_run as run_alias

    ROOT = Path(__file__).resolve().parents[2]
    ROM = 'games/roms/hanjuku-hero.sfc'
    GAME = 'hanjuku-hero'
    CONFIG = 'config/games/hanjuku-hero.toml'
    SHARED = 'hanjuku_experience.json'
    RESUME = 'hanjuku-resume'
    PREDICTIONS = 'hanjuku_predictions.json'
    PAUSED = 'hanjuku_predictions.paused'
    LOCK = 'hanjuku-predictions.lock'
    CANCEL = 'hanjuku_prediction_cancel_'


    def delayed():
        from . import hanjuku_chart
        from .hanjuku.state import regions
        return importlib.import_module('docich.hanjuku_run'), hanjuku_chart, regions
    ''')

FLOW_BATTLE_PY = textwrap.dedent('''\
    from __future__ import annotations

    from pathlib import Path

    from ... import hanjuku_run
    from ... import naming
    from .. import decision_log
    from ...hanjuku import chart

    ROOT = Path(__file__).resolve().parents[3]
    GAME = 'hanjuku-hero'
    ''')

REGIONS_PY = textwrap.dedent('''\
    from __future__ import annotations

    from .. import hanjuku_chart
    ''')

BRAIN_BOT_PY = textwrap.dedent('''\
    #!/usr/bin/env python3
    from pathlib import Path

    from docich import hanjuku_run
    from docich.hanjuku import fault_issue, month_audit, state_budget

    ROOT = Path(__file__).resolve().parents[2]
    GAME = 'hanjuku-hero'


    def side_effects(runtime, records, state, meta, obs, frame):
        for label, effect in (
                ('persist_failed', lambda: persist(
                    runtime, records, state, meta,
                            frame_sha256=frame.digest(), frame=frame)),
                ('state_write_failed', lambda: None)):
            pass
    ''')

BRAIN_PY = textwrap.dedent('''\
    from pathlib import Path

    STATE_SUBDIR = Path("run") / "brain" / "hanjuku"
    ''')

TEST_SAMPLE_PY = textwrap.dedent('''\
    """Sample copied test; the generator must keep this docstring."""
    from pathlib import Path
    from unittest import mock

    from docich import hanjuku_run
    from docich.hanjuku.flows import battle
    from docich.hanjuku_runner import untouched
    from test_hanjuku_sibling import value
    from tests.test_hanjuku_sibling import value as absolute
    import test_hanjuku_sibling as sibling_alias
    from hanjuku_helper import cache
    from test_retroarch_safe_boundary import adapter
    import test_retro_corner as corner_harness

    ROOT = Path(__file__).resolve().parents[1]
    FIXTURE = Path(__file__).parent / 'fixtures/hanjuku/sample.png'
    GAME = 'hanjuku-hero'
    CONFIG = 'config/games/hanjuku-hero.toml'
    ROM = 'games/roms/hanjuku-hero.sfc'
    STATE = 'states/hanjuku-hero.state'
    BRAIN = 'brains/hanjuku/bot.py'
    SHARED = 'hanjuku_experience.json'
    MODULE = 'docich.hanjuku_run.event'
    OTHER = 'docich.hanjuku_runner.event'
    PLUGIN = 'test_hanjuku_sibling'


    def test_patches():
        with mock.patch('docich.hanjuku_run.event'):
            pass
        with mock.patch('docich.hanjuku.flows.battle.step'):
            pass
        with mock.patch('docich.retro_corner.step'):
            pass
    ''')

TEST_HELPER_PY = 'cache = 1\n'
TEST_SIBLING_PY = 'value = 1\n'
TEST_EXCLUDED_PY = 'VALUE = 1\n'
TEST_FSTRING_PY = textwrap.dedent('''\
    import importlib

    NAME = 'hanjuku_run'
    MODULE = importlib.import_module(f'docich.{NAME}')
    ''')

CONFIG_TOML = textwrap.dedent('''\
    [game]
    name = "hanjuku-hero"
    title = "半熟英雄 (SFC)"
    adapter = "retroarch"

    [agent]
    command = ["python3", "brains/hanjuku/bot.py"]

    [hanjuku]
    # チャート版の説明
    chart_edition = "ab"

    [hanjuku.predictions]
    enabled = true
    seed_best_cleared = 1
    ending_window_seconds = 1800

    [twitch]
    category_id = "21236"
    title_prefix = "[半熟英雄]"

    [retro_corner]
    enabled = true
    unattended = true

    [retroarch]
    rom = "games/roms/hanjuku-hero.sfc"
    ''')


def _git_env(home: Path) -> dict:
    env = tool._git_env()
    env.update({
        'HOME': str(home),
        'GIT_AUTHOR_NAME': 'Fork Test',
        'GIT_AUTHOR_EMAIL': 'fork@example.invalid',
        'GIT_COMMITTER_NAME': 'Fork Test',
        'GIT_COMMITTER_EMAIL': 'fork@example.invalid',
    })
    return env


def _run_git(repo: Path, args: list[str], home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ['git', '--no-pager', *args],
        cwd=repo,
        env=_git_env(home),
        text=True,
        capture_output=True,
        check=False,
    )


def _make_repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    repo = tmp_path / 'repo'
    repo.mkdir()
    home = tmp_path / 'home'
    home.mkdir()
    init = _run_git(repo, ['init', '-q'], home)
    assert init.returncode == 0, init.stderr
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    add = _run_git(repo, ['add', '-A'], home)
    assert add.returncode == 0, add.stderr
    commit = _run_git(
        repo,
        ['-c', 'user.name=Fork Test', '-c', 'user.email=fork@example.invalid',
         'commit', '-q', '-m', 'fixture'],
        home,
    )
    assert commit.returncode == 0, commit.stderr
    sha = _run_git(repo, ['rev-parse', 'HEAD'], home)
    assert sha.returncode == 0, sha.stderr
    return repo, sha.stdout.strip()


@pytest.fixture()
def fixture_repo(tmp_path: Path) -> tuple[Path, str]:
    return _make_repo(tmp_path, {
        'src/docich/hanjuku_bot.py': BOT_PY,
        'src/docich/hanjuku_run.py': 'VALUE = 1\n',
        'src/docich/hanjuku_chart.py': 'VALUE = 1\n',
        'src/docich/hanjuku_experience.py': 'VALUE = 1\n',
        'src/docich/hanjuku_corner.py': 'VALUE = 1\n',
        'src/docich/hanjuku/decision_log.py': 'VALUE = 1\n',
        'src/docich/hanjuku/flows/__init__.py': '',
        'src/docich/hanjuku/flows/battle.py': FLOW_BATTLE_PY,
        'src/docich/hanjuku/state/__init__.py': '',
        'src/docich/hanjuku/state/regions.py': REGIONS_PY,
        'brains/hanjuku/__init__.py': '',
        'brains/hanjuku/bot.py': BRAIN_BOT_PY,
        'brains/hanjuku/brain.py': BRAIN_PY,
        'config/games/hanjuku-hero.toml': CONFIG_TOML,
    })


def _run_tool(tmp_path: Path, args: list[str], *, repo: Path | None = None,
              output_root: Path | None = None) -> subprocess.CompletedProcess:
    command = [sys.executable, str(TOOL)]
    if repo is not None:
        command += ['--repo', str(repo)]
    command += args
    if output_root is not None:
        command += ['--output-root', str(output_root)]
    env = _git_env(tmp_path / 'tool-home') if (tmp_path / 'tool-home').exists() else os.environ.copy()
    # The harness may export a malformed GIT_CONFIG_COUNT; the tool sanitizes
    # it itself, and so does _git_env.  Keep the explicit environment here so a
    # test never depends on the caller's git identity.
    return subprocess.run(command, env=env, text=True, capture_output=True, check=False)


def _make_test_repo(tmp_path: Path) -> tuple[Path, str]:
    return _make_repo(tmp_path, {
        'src/docich/hanjuku_run.py': 'VALUE = 1\n',
        'src/docich/hanjuku/flows/__init__.py': '',
        'src/docich/hanjuku/flows/battle.py': 'VALUE = 1\n',
        'tests/test_hanjuku_sample.py': TEST_SAMPLE_PY,
        'tests/test_hanjuku_sibling.py': TEST_SIBLING_PY,
        'tests/hanjuku_helper.py': TEST_HELPER_PY,
        'tests/test_retroarch_safe_boundary.py': 'adapter = 1\n',
        'tests/test_retro_corner.py': 'VALUE = 1\n',
        'tests/test_other.py': TEST_EXCLUDED_PY,
        'tests/test_hanjuku_family.py': TEST_EXCLUDED_PY,
        'tests/test_hanjuku_speedrun_corner_cli.py': TEST_EXCLUDED_PY,
        'tests/test_hanjuku_speedrun_fork.py': TEST_EXCLUDED_PY,
        'tests/fixtures/hanjuku/sample.png': 'not copied',
    })


# ---------------------------------------------------------------------------
# transformation rules
# ---------------------------------------------------------------------------


def test_generate_rewrites_imports_paths_names_and_shared_files(tmp_path, fixture_repo):
    repo, sha = fixture_repo
    out = tmp_path / 'out'
    result = _run_tool(tmp_path, ['generate', '--source', 'HEAD'],
                       repo=repo, output_root=out)
    assert result.returncode == 0, result.stderr
    assert 'generated' in result.stdout

    bot = (out / 'src/docich/hanjuku_speedrun/hanjuku_bot.py').read_text(encoding='utf-8')
    lines = bot.splitlines()
    assert lines[0].startswith('#!/usr/bin/env python3')
    assert lines[1].startswith('# -*- coding: utf-8 -*-')
    assert lines[2].startswith('# GENERATED by tools/hanjuku_speedrun_fork.py from ')
    assert sha in lines[2]

    # Relative and absolute imports are written as absolute copied names.
    assert 'from docich.hanjuku_speedrun import hanjuku_run' in bot
    assert 'from docich.hanjuku_speedrun.hanjuku.flows import battle' in bot
    assert 'from docich.game_switch import atomic_write_json' in bot
    assert 'from docich.hanjuku_speedrun import hanjuku_experience' in bot
    assert 'import docich.hanjuku_speedrun.hanjuku_run as run_alias' in bot
    # Delayed imports are rewritten too.
    assert 'from docich.hanjuku_speedrun import hanjuku_chart' in bot
    assert 'from docich.hanjuku_speedrun.hanjuku.state import regions' in bot
    assert "import_module('docich.hanjuku_speedrun.hanjuku_run')" in bot
    # Excluded modules stay on the original package.
    assert 'from docich.hanjuku_corner import helper' in bot
    assert 'docich.hanjuku_speedrun.hanjuku_corner' not in bot
    # Docstrings are not rewritten.
    assert 'Original hanjuku-hero bot.' in bot

    # Path depth: top-level src file moves one directory deeper.
    assert 'parents[3]' in bot
    assert 'parents[2]' not in bot
    # Game names, config paths and shared file names.
    assert "GAME = 'hanjuku-speedrun'" in bot
    assert "CONFIG = 'config/games/hanjuku-speedrun.toml'" in bot
    assert "SHARED = 'hanjuku_speedrun_experience.json'" in bot
    assert "RESUME = 'hanjuku-speedrun-resume'" in bot
    assert "PREDICTIONS = 'hanjuku_speedrun_predictions.json'" in bot
    assert "PAUSED = 'hanjuku_speedrun_predictions.paused'" in bot
    assert "LOCK = 'hanjuku-speedrun-predictions.lock'" in bot
    assert "CANCEL = 'hanjuku_speedrun_prediction_cancel_'" in bot
    # The ROM is shared and must not be renamed.
    assert "ROM = 'games/roms/hanjuku-hero.sfc'" in bot

    battle = (out / 'src/docich/hanjuku_speedrun/hanjuku/flows/battle.py').read_text(
        encoding='utf-8',
    )
    assert 'from docich.hanjuku_speedrun import hanjuku_run' in battle
    assert 'from docich import naming' in battle
    assert 'from docich.hanjuku_speedrun.hanjuku import decision_log' in battle
    assert 'from docich.hanjuku_speedrun.hanjuku import chart' in battle
    assert 'parents[4]' in battle

    brain = (out / 'brains/hanjuku_speedrun/bot.py').read_text(encoding='utf-8')
    assert 'from docich.hanjuku_speedrun import hanjuku_run' in brain
    # brains/ has the same depth as before.
    assert 'parents[2]' in brain
    assert 'parents[3]' not in brain
    assert "GAME = 'hanjuku-speedrun'" in brain

    external_brain = (out / 'brains/hanjuku_speedrun/brain.py').read_text(encoding='utf-8')
    assert 'Path("run") / "brain" / "hanjuku_speedrun"' in external_brain

    # Excluded modules are not copied at all.
    assert not (out / 'src/docich/hanjuku_speedrun/hanjuku_corner.py').exists()
    init = out / 'src/docich/hanjuku_speedrun/__init__.py'
    assert init.is_file()
    assert '"""' in init.read_text(encoding='utf-8')


def test_config_diff_keeps_comments_and_rom(tmp_path, fixture_repo):
    repo, _sha = fixture_repo
    out = tmp_path / 'out'
    result = _run_tool(tmp_path, ['generate', '--source', 'HEAD'],
                       repo=repo, output_root=out)
    assert result.returncode == 0, result.stderr

    config_path = out / 'config/games/hanjuku-speedrun.toml'
    text = config_path.read_text(encoding='utf-8')
    assert text.splitlines()[0].startswith('# GENERATED by tools/hanjuku_speedrun_fork.py')
    assert '# チャート版の説明' in text
    assert tool.CHART_EDITION_COMMENT in text
    assert 'games/roms/hanjuku-hero.sfc' in text

    parsed = tomllib.loads(text)
    assert parsed['game']['name'] == 'hanjuku-speedrun'
    assert parsed['game']['title'] == '半熟英雄 スピードラン (SFC)'
    assert parsed['agent']['command'] == ['python3', 'brains/hanjuku_speedrun/bot.py']
    assert parsed['hanjuku']['chart_edition'] == 'speedrun'
    assert parsed['hanjuku']['predictions']['enabled'] is False
    assert parsed['hanjuku']['predictions']['seed_best_cleared'] == 1
    assert parsed['hanjuku']['predictions']['ending_window_seconds'] == 1800
    assert parsed['twitch']['category_id'] == '21236'
    assert parsed['twitch']['title_prefix'] == '[半熟英雄RTA]'
    assert parsed['retro_corner'] == {'enabled': True, 'unattended': True}
    assert parsed['retroarch']['rom'] == 'games/roms/hanjuku-hero.sfc'
    assert not (out / 'config/games/hanjuku-hero.toml').exists()


def test_fork_json_is_sorted_and_hashes_match(tmp_path, fixture_repo):
    repo, sha = fixture_repo
    out = tmp_path / 'out'
    result = _run_tool(tmp_path, ['generate', '--source', 'HEAD'],
                       repo=repo, output_root=out)
    assert result.returncode == 0, result.stderr

    fork_path = out / 'src/docich/hanjuku_speedrun/FORK.json'
    raw = fork_path.read_text(encoding='utf-8')
    assert raw.endswith('\n')
    document = json.loads(raw)
    assert document['schema'] == 1
    assert document['source_commit'] == sha
    assert document['frozen'] is False
    assert list(document['files']) == sorted(document['files'])
    assert 'src/docich/hanjuku_speedrun/FORK.json' not in document['files']
    for relative, digest in document['files'].items():
        import hashlib
        assert hashlib.sha256((out / relative).read_bytes()).hexdigest() == digest


def test_generation_is_byte_identical_twice(tmp_path, fixture_repo):
    repo, _sha = fixture_repo
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    one = _run_tool(tmp_path, ['generate', '--source', 'HEAD'],
                    repo=repo, output_root=first)
    two = _run_tool(tmp_path, ['generate', '--source', 'HEAD'],
                    repo=repo, output_root=second)
    assert one.returncode == 0, one.stderr
    assert two.returncode == 0, two.stderr

    first_files = sorted(path.relative_to(first) for path in first.rglob('*') if path.is_file())
    second_files = sorted(path.relative_to(second) for path in second.rglob('*') if path.is_file())
    assert first_files == second_files
    for relative in first_files:
        assert (first / relative).read_bytes() == (second / relative).read_bytes()


def test_generate_preserves_files_it_did_not_generate(tmp_path, fixture_repo):
    repo, _sha = fixture_repo
    out = tmp_path / 'out'
    extra = out / 'src/docich/hanjuku_speedrun/hand_added.py'
    extra.parent.mkdir(parents=True)
    extra.write_text('# hand added after the freeze\n', encoding='utf-8')

    result = _run_tool(tmp_path, ['generate', '--source', 'HEAD'],
                       repo=repo, output_root=out)
    assert result.returncode == 0, result.stderr
    assert extra.read_text(encoding='utf-8') == '# hand added after the freeze\n'


# ---------------------------------------------------------------------------
# test copying
# ---------------------------------------------------------------------------


def test_generate_copies_and_rewrites_tests(tmp_path):
    repo, sha = _make_test_repo(tmp_path)
    out = tmp_path / 'out'
    result = _run_tool(tmp_path, ['generate', '--source', 'HEAD'],
                       repo=repo, output_root=out)
    assert result.returncode == 0, result.stderr

    copied = out / 'tests/hanjuku_speedrun/test_hanjuku_sample.py'
    text = copied.read_text(encoding='utf-8')
    assert text.splitlines()[0].startswith(
        '# GENERATED by tools/hanjuku_speedrun_fork.py from ')
    assert sha in text.splitlines()[0]
    assert '"""Sample copied test; the generator must keep this docstring."""' in text

    # Module imports: copied modules move, common/excluded ones do not.
    assert 'from docich.hanjuku_speedrun import hanjuku_run' in text
    assert 'from docich.hanjuku_speedrun.hanjuku.flows import battle' in text
    assert 'from docich.hanjuku_runner import untouched' in text
    # Sibling imports become package-relative, so pytest names the copy
    # ``hanjuku_speedrun.test_x`` and never binds a same-named tests/ module.
    assert 'from .test_hanjuku_sibling import value\n' in text
    assert 'from .test_hanjuku_sibling import value as absolute' in text
    assert 'from . import test_hanjuku_sibling as sibling_alias' in text
    assert 'from .hanjuku_helper import cache' in text
    # Test modules that are not copied stay in tests/ and keep absolute names.
    assert 'from test_retroarch_safe_boundary import adapter' in text
    assert 'import test_retro_corner as corner_harness' in text
    # Dynamic module names (sys.modules / importlib / pytest_plugins) use the
    # package-qualified name so they cannot load the original test module.
    assert "PLUGIN = 'hanjuku_speedrun.test_hanjuku_sibling'" in text

    # The copied test is one directory deeper.
    assert 'ROOT = Path(__file__).resolve().parents[2]' in text
    assert "Path(__file__).parent.parent / 'fixtures/hanjuku/sample.png'" in text

    # Game/config/shared-file names change; ROM-derived names do not.
    assert "GAME = 'hanjuku-speedrun'" in text
    assert "CONFIG = 'config/games/hanjuku-speedrun.toml'" in text
    assert "ROM = 'games/roms/hanjuku-hero.sfc'" in text
    assert "STATE = 'states/hanjuku-hero.state'" in text
    assert "BRAIN = 'brains/hanjuku_speedrun/bot.py'" in text
    assert "SHARED = 'hanjuku_speedrun_experience.json'" in text

    # String module references use a module boundary, not a prefix.
    assert "MODULE = 'docich.hanjuku_speedrun.hanjuku_run.event'" in text
    assert "OTHER = 'docich.hanjuku_runner.event'" in text
    assert "'docich.hanjuku_speedrun.hanjuku_run.event'" in text
    assert "'docich.retro_corner.step'" in text

    # The helper moves with the tests; fixtures and excluded tests do not.
    helper = out / 'tests/hanjuku_speedrun/hanjuku_helper.py'
    assert helper.read_text(encoding='utf-8').startswith('# GENERATED')
    for name in ('test_hanjuku_family.py', 'test_hanjuku_speedrun_corner_cli.py',
                 'test_hanjuku_speedrun_fork.py', 'test_other.py'):
        assert not (out / 'tests/hanjuku_speedrun' / name).exists(), name
    assert not (out / 'tests/fixtures').exists()
    assert not (out / 'tests/conftest.py').exists()

    # The copied tests are one package: pytest names them
    # ``hanjuku_speedrun.test_x`` instead of colliding with ``test_x``.
    package = out / 'tests/hanjuku_speedrun'
    init = package / '__init__.py'
    assert init.is_file()
    init_text = init.read_text(encoding='utf-8')
    assert init_text.splitlines()[0].startswith(
        '# GENERATED by tools/hanjuku_speedrun_fork.py from ')
    assert sha in init_text.splitlines()[0]
    # An empty package: the generated marker is the only content.
    assert init_text.splitlines()[1:] == []

    conftest = (package / 'conftest.py').read_text(encoding='utf-8')
    assert conftest.splitlines()[0].startswith('# GENERATED')
    assert 'def pytest_collection_modifyitems(config, items):' in conftest
    for nodeid, reason in tool.TEST_SKIPS.items():
        assert repr(nodeid) in conftest
        assert repr(reason) in conftest

    fork = json.loads(
        (out / 'src/docich/hanjuku_speedrun/FORK.json').read_text(encoding='utf-8'))
    assert list(fork['files']) == sorted(fork['files'])
    assert list(fork['test_files']) == sorted(fork['test_files'])
    assert not any(key.startswith('tests/') for key in fork['files'])
    assert 'tests/hanjuku_speedrun/__init__.py' in fork['test_files']
    assert 'tests/hanjuku_speedrun/test_hanjuku_sample.py' in fork['test_files']
    assert 'tests/hanjuku_speedrun/hanjuku_helper.py' in fork['test_files']
    assert 'tests/hanjuku_speedrun/conftest.py' in fork['test_files']
    assert 'tests/hanjuku_speedrun/hanjuku_speedrun_test_support.py' in fork['test_files']
    assert 'tests/hanjuku_speedrun/test_hanjuku_family.py' not in fork['test_files']
    for relative, digest in fork['test_files'].items():
        import hashlib
        assert hashlib.sha256((out / relative).read_bytes()).hexdigest() == digest


def test_no_tests_keeps_the_source_only_copy(tmp_path):
    repo, _sha = _make_test_repo(tmp_path)
    out = tmp_path / 'out'
    result = _run_tool(tmp_path, ['generate', '--source', 'HEAD', '--no-tests'],
                       repo=repo, output_root=out)
    assert result.returncode == 0, result.stderr
    assert not (out / 'tests/hanjuku_speedrun').exists()
    fork = json.loads(
        (out / 'src/docich/hanjuku_speedrun/FORK.json').read_text(encoding='utf-8'))
    assert 'test_files' not in fork

    check = _run_tool(tmp_path, ['check', '--no-tests'], repo=repo, output_root=out)
    assert check.returncode == 0, check.stderr
    assert 'check: OK' in check.stdout


def test_copied_tests_are_byte_identical_twice(tmp_path):
    repo, _sha = _make_test_repo(tmp_path)
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    one = _run_tool(tmp_path, ['generate', '--source', 'HEAD'],
                    repo=repo, output_root=first)
    two = _run_tool(tmp_path, ['generate', '--source', 'HEAD'],
                    repo=repo, output_root=second)
    assert one.returncode == 0, one.stderr
    assert two.returncode == 0, two.stderr

    first_files = sorted(p.relative_to(first) for p in first.rglob('*') if p.is_file())
    second_files = sorted(p.relative_to(second) for p in second.rglob('*') if p.is_file())
    assert first_files == second_files
    assert any(path.parts[:2] == ('tests', 'hanjuku_speedrun') for path in first_files)
    for relative in first_files:
        assert (first / relative).read_bytes() == (second / relative).read_bytes()


def test_copied_package_and_original_tests_share_one_pytest_session(tmp_path):
    """The whole point of the package: no ``import file mismatch``.

    The copied tests have the same basenames as the originals.  Without
    ``tests/hanjuku_speedrun/__init__.py`` and relative sibling imports, the
    same pytest worker imports both as ``test_x`` and collection aborts.
    """
    repo, _sha = _make_repo(tmp_path, {
        'src/docich/hanjuku_run.py': 'VALUE = 1\n',
        'tests/test_hanjuku_collect_peer.py': textwrap.dedent('''\
            VALUE = 1


            def test_peer():
                assert VALUE == 1
            '''),
        'tests/test_hanjuku_collect_use.py': textwrap.dedent('''\
            from test_hanjuku_collect_peer import VALUE
            from test_shared_harness import SHARED


            def test_use():
                assert (VALUE, SHARED) == (1, 2)
            '''),
        'tests/test_shared_harness.py': 'SHARED = 2\n',
    })
    out = tmp_path / 'out'
    generated = _run_tool(tmp_path, ['generate', '--source', 'HEAD'],
                          repo=repo, output_root=out)
    assert generated.returncode == 0, generated.stderr

    copied_use = (out / 'tests/hanjuku_speedrun/test_hanjuku_collect_use.py').read_text(
        encoding='utf-8')
    assert 'from .test_hanjuku_collect_peer import VALUE' in copied_use
    # A shared module that stays in tests/ keeps its absolute name; pytest's
    # package root insertion puts tests/ on sys.path for the copied package.
    assert 'from test_shared_harness import SHARED' in copied_use

    # Recreate the scratch layout from the job: the original tests/ tree with
    # the generated package overlaid.  Both sides are collected in one session.
    shutil.copytree(repo / 'tests', out / 'tests', dirs_exist_ok=True)
    collected = subprocess.run(
        [sys.executable, '-m', 'pytest', '--collect-only', '-q',
         '-p', 'no:cacheprovider', 'tests'],
        cwd=out,
        text=True,
        capture_output=True,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert 'import file mismatch' not in collected.stdout + collected.stderr
    nodeids = [line for line in collected.stdout.splitlines() if '::test_' in line]
    assert len(nodeids) == 4, collected.stdout
    assert any(
        'hanjuku_speedrun/test_hanjuku_collect_use.py::test_use' in line
        for line in nodeids
    ), collected.stdout
    # The package has its own import identity, separate from the original.
    assert 'tests/hanjuku_speedrun/test_hanjuku_collect_peer.py::test_peer' in collected.stdout


def test_package_conftest_does_not_skip_the_original_tests(tmp_path):
    """A skip in the copied package must not hide the same original test."""
    repo, _sha = _make_repo(tmp_path, {
        'src/docich/hanjuku_run.py': 'VALUE = 1\n',
        'tests/test_hanjuku_new_game.py': textwrap.dedent('''\
            def test_new_game_queue_survives_restart_and_refuses_other_start_modes():
                assert True
            '''),
    })
    out = tmp_path / 'out'
    generated = _run_tool(tmp_path, ['generate', '--source', 'HEAD'],
                          repo=repo, output_root=out)
    assert generated.returncode == 0, generated.stderr
    shutil.copytree(repo / 'tests', out / 'tests', dirs_exist_ok=True)

    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '-rs',
         '-p', 'no:cacheprovider',
         'tests/test_hanjuku_new_game.py',
         'tests/hanjuku_speedrun/test_hanjuku_new_game.py'],
        cwd=out,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # The original passes; only the copy receives the skip.
    assert '1 passed, 1 skipped' in result.stdout, result.stdout
    assert 'SKIPPED [1] tests/hanjuku_speedrun/test_hanjuku_new_game.py' in result.stdout
    assert 'SKIPPED [1] tests/test_hanjuku_new_game.py' not in result.stdout


def test_check_detects_a_changed_copied_test(tmp_path):
    repo, _sha = _make_test_repo(tmp_path)
    out = tmp_path / 'out'
    generated = _run_tool(tmp_path, ['generate'], repo=repo, output_root=out)
    assert generated.returncode == 0, generated.stderr

    target = out / 'tests/hanjuku_speedrun/test_hanjuku_sample.py'
    target.write_text(target.read_text(encoding='utf-8') + '\n# hand edit\n',
                      encoding='utf-8')
    broken = _run_tool(tmp_path, ['check'], repo=repo, output_root=out)
    assert broken.returncode == 1
    assert 'tests/hanjuku_speedrun/test_hanjuku_sample.py' in broken.stderr
    assert 'tests/hanjuku_speedrun/test_hanjuku_sibling.py' not in broken.stderr


def test_fstring_module_name_fails_unless_allowed(tmp_path):
    files = {
        'src/docich/hanjuku_run.py': 'VALUE = 1\n',
        'tests/test_hanjuku_fstring.py': TEST_FSTRING_PY,
    }
    repo, _sha = _make_repo(tmp_path, files)
    out = tmp_path / 'out'
    failed = _run_tool(tmp_path, ['generate'], repo=repo, output_root=out)
    assert failed.returncode != 0
    assert 'dynamic module name' in failed.stderr
    assert not (out / 'tests/hanjuku_speedrun/test_hanjuku_fstring.py').exists()

    allowed = _run_tool(tmp_path, ['generate', '--allow-unresolved'],
                        repo=repo, output_root=out)
    assert allowed.returncode == 0, allowed.stderr
    text = (out / 'tests/hanjuku_speedrun/test_hanjuku_fstring.py').read_text(
        encoding='utf-8')
    assert "f'docich.{NAME}'" in text


def test_conftest_serializes_skip_keys_and_reasons(monkeypatch):
    monkeypatch.setitem(tool.TEST_SKIPS, 'test_hanjuku_x.py::test_y', 'because')
    body = tool._conftest_body()
    assert "TEST_SKIPS = {" in body
    assert "'test_hanjuku_x.py::test_y': 'because'" in body
    assert 'pytest.mark.skip(reason=reason)' in body
    # The key is relative to tests/hanjuku_speedrun/, so a rootdir-qualified
    # node id must match by suffix.
    assert 'nodeid.endswith("/" + key)' in body
    # The hook is session-wide; the original test with the same suffix must
    # not receive the copied test's skip.
    assert "PACKAGE_DIR = Path(__file__).resolve().parent" in body
    assert 'if PACKAGE_DIR not in Path(str(item.path)).resolve().parents:' in body
    assert body.index('PACKAGE_DIR not in') < body.index('item.add_marker')


def test_excluded_tests_are_documented():
    assert {
        'tests/test_hanjuku_speedrun_fork.py',
        'tests/test_hanjuku_family.py',
        'tests/test_hanjuku_speedrun_corner_cli.py',
    } <= set(tool.EXCLUDED_TESTS)
    assert all(tool.EXCLUDED_TESTS.values())
    assert 'tests/test_hanjuku_runtime_isolation.py' not in tool.EXCLUDED_TESTS
    assert all(tool.TEST_SKIPS.values())


# ---------------------------------------------------------------------------
# check / frozen behaviour
# ---------------------------------------------------------------------------


def test_check_matches_then_reports_a_single_changed_file(tmp_path, fixture_repo):
    repo, _sha = fixture_repo
    out = tmp_path / 'out'
    generated = _run_tool(tmp_path, ['generate'],
                          repo=repo, output_root=out)
    assert generated.returncode == 0, generated.stderr

    ok = _run_tool(tmp_path, ['check'], repo=repo, output_root=out)
    assert ok.returncode == 0, ok.stderr
    assert 'check: OK' in ok.stdout

    target = out / 'src/docich/hanjuku_speedrun/hanjuku_bot.py'
    target.write_text(target.read_text(encoding='utf-8') + '\n# hand edit\n',
                      encoding='utf-8')
    broken = _run_tool(tmp_path, ['check'], repo=repo, output_root=out)
    assert broken.returncode == 1
    assert 'src/docich/hanjuku_speedrun/hanjuku_bot.py' in broken.stderr
    assert 'src/docich/hanjuku_speedrun/hanjuku_run.py' not in broken.stderr

    # A file that the generator does not produce is not a drift.
    extra = out / 'src/docich/hanjuku_speedrun/hand_added.py'
    extra.write_text('# extra\n', encoding='utf-8')
    still_broken = _run_tool(tmp_path, ['check'], repo=repo, output_root=out)
    assert still_broken.returncode == 1
    assert 'hand_added.py' not in still_broken.stderr


def test_check_uses_fork_source_commit_by_default(tmp_path, fixture_repo):
    repo, sha = fixture_repo
    out = tmp_path / 'out'
    generated = _run_tool(tmp_path, ['generate', '--source', 'HEAD'],
                          repo=repo, output_root=out)
    assert generated.returncode == 0, generated.stderr
    document = json.loads((out / 'src/docich/hanjuku_speedrun/FORK.json').read_text())
    assert document['source_commit'] == sha

    result = _run_tool(tmp_path, ['check'], repo=repo, output_root=out)
    assert result.returncode == 0, result.stderr


def test_frozen_check_succeeds_and_generate_refuses_until_forced(tmp_path, fixture_repo):
    repo, _sha = fixture_repo
    out = tmp_path / 'out'
    generated = _run_tool(tmp_path, ['generate'], repo=repo, output_root=out)
    assert generated.returncode == 0, generated.stderr

    fork_path = out / 'src/docich/hanjuku_speedrun/FORK.json'
    document = json.loads(fork_path.read_text(encoding='utf-8'))
    document['frozen'] = True
    fork_path.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                         encoding='utf-8')

    target = out / 'src/docich/hanjuku_speedrun/hanjuku_bot.py'
    target.write_text('# edited after freeze\n', encoding='utf-8')
    frozen = _run_tool(tmp_path, ['check'], repo=repo, output_root=out)
    assert frozen.returncode == 0, frozen.stderr
    assert 'frozen' in frozen.stdout

    refused = _run_tool(tmp_path, ['generate'], repo=repo, output_root=out)
    assert refused.returncode != 0
    assert 'frozen' in refused.stderr
    assert target.read_text(encoding='utf-8') == '# edited after freeze\n'

    forced = _run_tool(tmp_path, ['generate', '--force-unfreeze'],
                       repo=repo, output_root=out)
    assert forced.returncode == 0, forced.stderr
    document = json.loads(fork_path.read_text(encoding='utf-8'))
    assert document['frozen'] is False
    assert 'GENERATED' in target.read_text(encoding='utf-8')


def test_unresolved_path_fails_unless_allowed(tmp_path):
    repo, _sha = _make_repo(tmp_path, {
        'src/docich/hanjuku_badpath.py': textwrap.dedent('''\
            from pathlib import Path

            INDEX = 2
            ROOT = Path(__file__).resolve().parents[INDEX]
            '''),
    })
    out = tmp_path / 'out'
    failed = _run_tool(tmp_path, ['generate'], repo=repo, output_root=out)
    assert failed.returncode != 0
    assert 'unresolved' in failed.stderr
    assert not (out / 'src/docich/hanjuku_speedrun/hanjuku_badpath.py').exists()

    allowed = _run_tool(tmp_path, ['generate', '--allow-unresolved'],
                        repo=repo, output_root=out)
    assert allowed.returncode == 0, allowed.stderr
    text = (out / 'src/docich/hanjuku_speedrun/hanjuku_badpath.py').read_text(encoding='utf-8')
    assert 'parents[INDEX]' in text


# ---------------------------------------------------------------------------
# real repository integration
# ---------------------------------------------------------------------------


def _string_literals_outside_docstrings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.body:
                continue
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_real_head_is_a_clean_generated_copy(tmp_path):
    out = tmp_path / 'real'
    result = _run_tool(tmp_path, ['generate', '--source', 'HEAD', '--repo', str(ROOT)],
                       output_root=out)
    assert result.returncode == 0, result.stderr

    python_files = sorted(out.rglob('*.py'))
    assert len(python_files) > 100
    for path in python_files:
        compile(path.read_text(encoding='utf-8'), str(path), 'exec')

    run_path = out / 'src/docich/hanjuku_speedrun/hanjuku_run.py'
    assert run_path.is_file()
    literals = _string_literals_outside_docstrings(run_path)
    assert not any('hanjuku-hero' in value for value in literals)

    config = tomllib.loads((out / 'config/games/hanjuku-speedrun.toml').read_text(encoding='utf-8'))
    assert config['game']['name'] == 'hanjuku-speedrun'
    assert config['hanjuku']['chart_edition'] == 'speedrun'
    assert config['hanjuku']['predictions']['enabled'] is False
    assert config['retroarch']['rom'] == 'games/roms/hanjuku-hero.sfc'
    assert config['retro_corner']['enabled'] is True
    assert config['retro_corner']['unattended'] is True

    fork = json.loads((out / 'src/docich/hanjuku_speedrun/FORK.json').read_text(encoding='utf-8'))
    head = subprocess.run(
        ['git', '--no-pager', 'rev-parse', 'HEAD'],
        cwd=str(ROOT),
        env=_git_env(tmp_path),
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert fork['source_commit'] == head
    assert fork['schema'] == 1
    assert fork['frozen'] is False
    assert 'config/games/hanjuku-speedrun.toml' in fork['files']
    assert 'brains/hanjuku_speedrun/bot.py' in fork['files']
    assert not (out / 'src/docich/hanjuku_speedrun/hanjuku_corner.py').exists()
    assert any(path.name == 'hanjuku_run.py' for path in python_files)

    # The test copy is part of the same deterministic output.
    test_files = fork['test_files']
    assert len(test_files) > 600
    assert list(test_files) == sorted(test_files)
    assert 'tests/hanjuku_speedrun/__init__.py' in test_files
    assert 'tests/hanjuku_speedrun/conftest.py' in test_files
    assert 'tests/hanjuku_speedrun/hanjuku_speedrun_test_support.py' in test_files
    assert 'tests/hanjuku_speedrun/hanjuku_frame_read_cache.py' in test_files
    assert 'tests/hanjuku_speedrun/test_hanjuku_runtime_isolation.py' in test_files
    # Siblings are relative so the package never loads tests/ copies.
    house = (out / 'tests/hanjuku_speedrun/test_hanjuku_house.py').read_text(
        encoding='utf-8')
    assert 'from .test_hanjuku_chart_bot import' in house
    assert 'from test_hanjuku_chart_bot import' not in house
    support = (out / 'tests/hanjuku_speedrun/test_hanjuku_current_roster_budget.py').read_text(
        encoding='utf-8')
    assert 'from .hanjuku_speedrun_test_support import speedrun_game' in support
    for excluded in tool.EXCLUDED_TESTS:
        assert excluded not in test_files
        assert not (out / 'tests/hanjuku_speedrun' / Path(excluded).name).exists()
    for relative, digest in test_files.items():
        assert hashlib.sha256((out / relative).read_bytes()).hexdigest() == digest


MAIN_RESUME_PY = textwrap.dedent('''\
    from .retroarch_boundary import (BOUNDARY_FILE, MANUAL_SAVE_FILE, checkpoint_digest,
                                    identity, matches, read_record)


    def scan(runtimes):
        for runtime in runtimes.glob('g*'):
            # RUN_FILE is Hanjuku-specific. Saved states alone also constitute
            relevant = False
            if not relevant:
                continue
    ''')

MAIN_RECOVERY_PY = textwrap.dedent('''\
    from .retroarch_boundary import BOUNDARY_FILE, MANUAL_SAVE_FILE, checkpoint_digest


    def scan(base):
        for runtime in base.glob('g*'):
            if runtime.is_symlink():
                return False
            if not runtime.is_dir():
                continue
            relevant = any((runtime / n).exists() or (runtime / n).is_symlink()
                           for n in ('hanjuku_run.json',))
            if relevant:
                return False
        return True
    ''')

MAIN_NARRATION_PY = textwrap.dedent('''\
    from .retroarch_boundary import read_record

    STATE = 'hanjuku_narration.json'


    def scan(runtime_root):
        for runtime_dir in sorted(runtime_root.iterdir()):
            if runtime_dir.is_symlink() or not runtime_dir.is_dir():
                continue
            state_path = runtime_dir / STATE
            yield state_path
    ''')


def test_runtime_isolation_filters_are_restored_in_the_copy(tmp_path):
    """The main ref scans every runtime; the copy must not adopt foreign ones."""
    repo, _sha = _make_repo(tmp_path, {
        'src/docich/hanjuku_resume.py': MAIN_RESUME_PY,
        'src/docich/hanjuku_resume_recovery.py': MAIN_RECOVERY_PY,
        'src/docich/hanjuku_narration.py': MAIN_NARRATION_PY,
    })
    out = tmp_path / 'out'
    result = _run_tool(tmp_path, ['generate', '--source', 'HEAD'],
                       repo=repo, output_root=out)
    assert result.returncode == 0, result.stderr

    resume = (out / 'src/docich/hanjuku_speedrun/hanjuku_resume.py').read_text(
        encoding='utf-8')
    recovery = (out / 'src/docich/hanjuku_speedrun/hanjuku_resume_recovery.py').read_text(
        encoding='utf-8')
    narration = (out / 'src/docich/hanjuku_speedrun/hanjuku_narration.py').read_text(
        encoding='utf-8')
    for text, guard in ((resume, '_runtime_game(runtime) not in (None, \'hanjuku-speedrun\')'),
                        (recovery, '_runtime_game(runtime) not in (None, \'hanjuku-speedrun\')'),
                        (narration, '_runtime_game(runtime_dir) not in (None, \'hanjuku-speedrun\')')):
        assert guard in text
        assert 'runtime_game as _runtime_game' in text
    assert "hanjuku-hero" not in resume


def test_runtime_isolation_filter_patch_is_a_noop_when_already_present():
    """The feature branch already carries the filter until main catches up."""
    text = (
        "from docich.retroarch_boundary import read_record, runtime_game as _runtime_game\n"
        "for runtime in runtimes.glob('g*'):\n"
        "    if _runtime_game(runtime) not in (None, 'hanjuku-speedrun'):\n"
        "        continue\n"
    )
    assert tool._apply_source_patches(
        'src/docich/hanjuku_resume.py', text) == text


def test_hotload_dynamic_names_are_patched_to_the_copy(tmp_path):
    """The copy's trampoline must import its own package, not the original's."""
    repo_root = Path(__file__).resolve().parents[1]
    output = tmp_path / 'out'
    result = subprocess.run(
        [sys.executable, str(repo_root / 'tools' / 'hanjuku_speedrun_fork.py'),
         'generate', '--output-root', str(output)],
        cwd=repo_root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    text = (output / 'src/docich/hanjuku_speedrun/hanjuku_hotload.py').read_text(encoding='utf-8')
    assert "f'docich.hanjuku_speedrun.{name}'" in text
    assert "sys.modules['docich.hanjuku_speedrun']" in text
    assert "f'docich.{name}'" not in text
    assert "sys.modules['docich']" not in text


def test_committed_copy_matches_its_recorded_source():
    """CI gate: a hand edit or stale regeneration of the committed copy fails here."""
    repo_root = Path(__file__).resolve().parents[1]
    fork = json.loads((repo_root / 'src/docich/hanjuku_speedrun/FORK.json').read_text(encoding='utf-8'))
    if fork.get('frozen'):
        pytest.skip('copy is frozen')
    commit = fork['source_commit']
    resolved = subprocess.run(['git', 'cat-file', '-e', f'{commit}^{{commit}}'],
                              cwd=repo_root, capture_output=True)
    if resolved.returncode != 0:
        pytest.skip('source commit is not in this clone (shallow checkout or squash merge)')
    result = subprocess.run(
        [sys.executable, str(repo_root / 'tools' / 'hanjuku_speedrun_fork.py'), 'check'],
        cwd=repo_root, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
