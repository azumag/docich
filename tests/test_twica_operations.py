"""Deployment gates and private checkpoint contracts; never live host changes."""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from docich.twica_checkpoint import read_checkpoint, save_checkpoint
from docich.twica_operator import NotReady, prepare
from docich.twica_state import atomic_json, heartbeat, new_control, read_json, status

ROOT = Path(__file__).resolve().parents[1]

def helper():
    spec = importlib.util.spec_from_file_location('twica_ops_fixture', ROOT/'ops/vm_actions/twica_common.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checkpoint_private_target_bound_and_bounded(tmp_path):
    class Context:
        async def storage_state(self):
            return {'cookies': [], 'origins': []}
    class Page:
        context = Context()
        async def evaluate(self, expression):
            return {'processed-history': '42', 'cursor': 'sentinel-private'}
    directory=tmp_path/'private'
    url='https://example.test/overlay/fixture'
    asyncio.run(save_checkpoint(directory,url,Page()))
    value=read_checkpoint(directory,url)
    assert value['session']['processed-history']=='42'
    assert not read_checkpoint(directory,'https://example.test/overlay/other')
    path=directory/'browser-state.json'
    assert path.stat().st_mode & 0o077 == 0
    path.chmod(0o644);assert not read_checkpoint(directory,url)
    path.unlink();path.symlink_to(tmp_path/'missing');assert not read_checkpoint(directory,url)
    assert 'sentinel-private' not in json.dumps(status(directory))


def test_prepare_rejects_dangling_policy_and_preserves_old_subscriptions(tmp_path):
    directory=tmp_path/'control';prepare(directory)
    assert read_json(directory/'control.json')['preserve_legacy'] is True
    (directory/'control.json').unlink()
    (directory/'control.json').symlink_to(tmp_path/'absent')
    with pytest.raises(NotReady,match='invalid_policy'):
        prepare(directory)


def test_no_optimistic_health_when_legacy_proofs_missing(tmp_path):
    prepare(tmp_path/'control')
    assert status(tmp_path/'control')['legacy_healthy'] is False


def test_arm_never_interrupts_before_explicit_confirmation_and_guard_readiness(tmp_path,monkeypatch):
    ops=helper();directory=tmp_path/'control';prepare(directory)
    monkeypatch.setenv('DOCICH_TWICA_STATE_DIR',str(directory))
    called=[];monkeypatch.setattr(ops,'checked',lambda *a,**k:called.append(a))
    with pytest.raises(NotReady,match='stream_restart_confirmation_required'):
        ops.arm_stream(False)
    with pytest.raises(NotReady,match='renderer_not_ready'):
        ops.arm_stream(True)
    heartbeat(directory,'renderer.json',ready=True,state='standby')
    with pytest.raises(NotReady,match='legacy_guards_not_ready'):
        ops.arm_stream(True)
    assert called==[]


def test_active_pipeline_arm_is_idempotent_without_restart(tmp_path,monkeypatch):
    ops=helper();directory=tmp_path/'control';prepare(directory)
    monkeypatch.setenv('DOCICH_TWICA_STATE_DIR',str(directory))
    heartbeat(directory,'pipeline.json',ready=True)
    monkeypatch.setattr(ops,'checked',lambda *a,**k:pytest.fail('unexpected mutation'))
    ops.arm_stream(False)


def test_operations_fail_without_production_confirmation(monkeypatch):
    ops=helper();monkeypatch.setattr(ops,'install',lambda:pytest.fail('must not install'))
    for action in ['prepare','arm','activate','rollback','enable','restart-renderer']:
        assert ops.main([action])==3


def test_operator_workflow_is_manual_fixed_and_retains_private_gateway_boundary():
    source=(ROOT/'.github/workflows/twica-common-operator.yml').read_text()
    assert 'workflow_dispatch:' in source and 'workflow_run:' not in source and '\n  push:' not in source
    for guard in ["github.repository == 'azumag/docich'", "github.actor_id == '9018513'", "github.triggering_actor == 'azumag'", 'github.ref_protected == true', 'StrictHostKeyChecking=yes', 'git diff --quiet HEAD --', 'exec docich production $sha']:
        assert guard in source
    assert 'inputs.command' not in source
    assert 'confirm_stream_restart' in source and 'confirm_idle' in source
    unit=(ROOT/'deploy/twica-common/docich-twica-common.service').read_text()
    assert 'KillMode=control-group' in unit and 'soren-runtime' not in unit


def test_component_registry_matches_health_surface_and_independent_owner(tmp_path):
    registry=json.loads((ROOT/'ops/vm_actions/twica_common_registry.json').read_text())
    assert registry['ownership']=='common_broadcast' and registry['required'] is False
    assert registry['unit']=='docich-twica-common.service'
    assert set(registry['health_fields'])==set(status(tmp_path))-{'schema'}
    assert 'browser-state' not in json.dumps(registry)


def test_operator_reuses_existing_user_bus_without_changing_it(monkeypatch):
    ops = helper()
    monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
    monkeypatch.delenv('DBUS_SESSION_BUS_ADDRESS', raising=False)
    ops.user_bus_environment()
    import os
    assert os.environ['XDG_RUNTIME_DIR'] == f'/run/user/{os.getuid()}'
    assert os.environ['DBUS_SESSION_BUS_ADDRESS'] == f'unix:path=/run/user/{os.getuid()}/bus'
    monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/fixture/bus')
    ops.user_bus_environment()
    assert os.environ['DBUS_SESSION_BUS_ADDRESS'] == 'unix:path=/fixture/bus'


def test_renderer_prepare_wait_requires_fresh_readiness(tmp_path, monkeypatch):
    ops = helper()
    directory = tmp_path / 'control'
    prepare(directory)
    monkeypatch.setenv('DOCICH_TWICA_STATE_DIR', str(directory))
    with pytest.raises(NotReady, match='renderer_not_ready'):
        ops.wait_renderer_ready(timeout_sec=0.01)
    heartbeat(directory, 'renderer.json', ready=True, state='standby')
    ops.wait_renderer_ready(timeout_sec=0.1)


def test_arm_rejects_duplicate_guards_before_interrupting(tmp_path, monkeypatch):
    ops = helper()
    directory = tmp_path / 'control'
    prepare(directory)
    monkeypatch.setenv('DOCICH_TWICA_STATE_DIR', str(directory))
    heartbeat(directory, 'renderer.json', ready=True, state='standby')
    for i, role in enumerate(['game', 'game', 'shared']):
        heartbeat(directory, f'legacy-{i}.json', role=role, subscribed=True)
    monkeypatch.setattr(ops, 'checked', lambda *a, **k: pytest.fail('must not interrupt'))
    with pytest.raises(NotReady, match='legacy_guards_not_ready'):
        ops.arm_stream(True)
