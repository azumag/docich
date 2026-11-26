"""Guards around the speedrun fork's hand-written source patches."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'hanjuku_speedrun_fork.py'
RUN_REL = 'src/docich/hanjuku_run.py'
COPY_REL = 'src/docich/hanjuku_speedrun/hanjuku_run.py'


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        'hanjuku_speedrun_fork_guards_under_test', TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


def test_only_the_owning_checkout_is_treated_as_real(tmp_path):
    assert tool._is_real_repository(ROOT) is True
    assert tool._is_real_repository(tmp_path) is False


def test_the_real_generated_copy_contains_the_rotation_hook():
    generated = (ROOT / COPY_REL).read_text(encoding='utf-8')
    assert 'before_rotate' in generated
    files = {rel: (ROOT / rel).read_bytes()
             for rel in tool.REQUIRED_SOURCE_PATCH_MARKERS}
    files[COPY_REL] = generated.encode('utf-8')
    tool._verify_required_source_patches(files, require_real=True)


def test_a_real_copy_without_the_patch_is_a_hard_error():
    text = b'        os.replace(path,previous)\n'
    with pytest.raises(tool.ToolError):
        tool._verify_required_source_patches(
            {COPY_REL: text}, require_real=True)


def test_a_synthetic_fixture_may_lack_the_patch():
    text = b'        os.replace(path,previous)\n'
    # Synthetic repositories are what the unit tests generate; they have no
    # writer to hook and must stay buildable.
    tool._verify_required_source_patches(
        {COPY_REL: text}, require_real=False)


def test_the_guard_skips_a_stub_without_append_log():
    text = '        os.replace(path,previous)\n'
    assert tool._apply_source_patches(RUN_REL, text) == text


def test_the_guard_patches_the_real_writer():
    source = (ROOT / RUN_REL).read_text(encoding='utf-8')
    patched = tool._apply_source_patches(RUN_REL, source)
    assert 'before_rotate' in patched


def test_generate_refuses_a_real_copy_without_the_hook(tmp_path, monkeypatch):
    generated = tool.Generated(
        files={COPY_REL: b'VALUE = 1\n'},
        test_files={},
        diagnostics=[],
        reviews=[],
    )
    monkeypatch.setattr(tool, '_build', lambda *_args, **_kwargs: (generated, 'a' * 40))
    monkeypatch.setattr(tool, '_find_repo_root', lambda _repo: ROOT)
    monkeypatch.setattr(tool, '_is_real_repository', lambda _repo: True)
    args = argparse.Namespace(
        repo=str(ROOT), output_root=str(tmp_path / 'out'), source='HEAD',
        no_tests=True, allow_unresolved=False, force_unfreeze=False,
    )
    with pytest.raises(tool.ToolError):
        tool.command_generate(args)
    assert not (tmp_path / 'out').exists()


def test_check_refuses_a_real_copy_without_the_hook(tmp_path, monkeypatch):
    generated = tool.Generated(
        files={COPY_REL: b'VALUE = 1\n'},
        test_files={},
        diagnostics=[],
        reviews=[],
    )
    out = tmp_path / 'out'
    fork_path = out / tool.FORK_REL
    fork_path.parent.mkdir(parents=True)
    fork_path.write_text('{"frozen": false, "source_commit": "' + 'a' * 40 + '"}',
                         encoding='utf-8')
    monkeypatch.setattr(tool, '_build', lambda *_args, **_kwargs: (generated, 'a' * 40))
    monkeypatch.setattr(tool, '_find_repo_root', lambda _repo: ROOT)
    monkeypatch.setattr(tool, '_is_real_repository', lambda _repo: True)
    args = argparse.Namespace(
        repo=str(ROOT), output_root=str(out), source=None,
        no_tests=True, allow_unresolved=False,
    )
    with pytest.raises(tool.ToolError):
        tool.command_check(args)
