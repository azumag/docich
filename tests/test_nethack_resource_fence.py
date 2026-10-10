"""Real inode/flock contention, with every resource launcher replaced locally."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docich.nethack_resource_fence import resource_fence, FenceUnproven, FENCE_FILE


def test_shared_producers_coexist_but_commit_excludes_them(tmp_path):
    root = tmp_path.resolve()
    with resource_fence(root):
        identity = (root / FENCE_FILE).stat().st_ino
        with resource_fence(root):
            with pytest.raises(FenceUnproven, match='busy'):
                with resource_fence(root, exclusive=True):
                    pytest.fail('commit entered with producer alive')
    with resource_fence(root, exclusive=True):
        assert (root / FENCE_FILE).stat().st_ino == identity
        with pytest.raises(FenceUnproven, match='busy'):
            with resource_fence(root):
                pytest.fail('producer entered during commit')
    with resource_fence(root):
        pass


@pytest.mark.parametrize('unsafe', ['symlink', 'hardlink', 'writable', 'directory'])
def test_unsafe_fence_cannot_supply_new_inode_or_be_removed(tmp_path, unsafe):
    root = tmp_path.resolve(); (root / 'locks').mkdir()
    lock = root / FENCE_FILE
    if unsafe == 'symlink':
        target = root / 'target'; target.touch(); lock.symlink_to(target)
    elif unsafe == 'hardlink':
        target = root / 'target'; target.touch(); lock.hardlink_to(target)
    elif unsafe == 'writable':
        lock.touch(); lock.chmod(0o666)
    else:
        lock.mkdir()
    before = lock.lstat()
    with pytest.raises(FenceUnproven):
        with resource_fence(root, exclusive=True):
            pytest.fail('unsafe fence accepted')
    assert lock.lstat().st_ino == before.st_ino


@pytest.mark.parametrize('entry', ['materialize_runtime', 'start_agent'])
def test_both_adapter_spawn_entries_refuse_before_any_base_call(tmp_path, monkeypatch, entry):
    from docich.adapters.nethack import NethackCoordinatorAdapter
    from docich.adapters.cli_game import CliCoordinatorAdapter
    root = tmp_path.resolve()
    invoked = Mock()
    monkeypatch.setattr(CliCoordinatorAdapter, entry, invoked)
    adapter = object.__new__(NethackCoordinatorAdapter)
    adapter.g = SimpleNamespace(state_dir=root)
    with resource_fence(root, exclusive=True):
        with pytest.raises(FenceUnproven, match='busy'):
            getattr(adapter, entry)(99, None)
    invoked.assert_not_called()
    getattr(adapter, entry)(99, None)
    invoked.assert_called_once()


def test_tiles_holds_fence_through_fallback_and_final_cleanup(tmp_path, monkeypatch):
    from docich.nethack_tiles_supervisor import NethackTilesSupervisor
    root = tmp_path.resolve()
    supervisor = object.__new__(NethackTilesSupervisor); supervisor.state_dir = root
    def lifecycle():
        for phase in ('spawn', 'fallback', 'cleanup'):
            with pytest.raises(FenceUnproven, match='busy'):
                with resource_fence(root, exclusive=True):
                    pytest.fail(phase)
        return 0
    supervisor._run_fenced = lifecycle
    assert supervisor.run() == 0
    with resource_fence(root, exclusive=True):
        with pytest.raises(FenceUnproven, match='busy'): supervisor.run()


@pytest.mark.parametrize('entry', ['daily', 'canary', 'container', 'resolver', 'resolver-once', 'resolver-daemon'])
def test_host_entries_refuse_before_any_job_or_container(tmp_path, monkeypatch, entry):
    root = tmp_path.resolve(); g = SimpleNamespace(state_dir=root)
    if entry == 'daily':
        import docich.nethack_daily_improve as module
        monkeypatch.setattr(module, '_load_config', lambda g: SimpleNamespace(enabled=True))
        work = Mock(); monkeypatch.setattr(module, '_run_daily_improvement', work)
        call = lambda: module.run_daily_improvement(g)
    elif entry == 'canary':
        import docich.nethack_canary as module
        work = Mock(); monkeypatch.setattr(module, '_production_live', work)
        call = lambda: module.run_canary(g, None)
    elif entry == 'container':
        import docich.nethack_canary_container as module
        monkeypatch.setattr(module, '_fixed_fence_state_dir', lambda: root)
        work = Mock(); monkeypatch.setattr(module, '_run_container_worker', work)
        call = lambda: module.run_container_worker('{}')
    else:
        import docich.resolver.improve as module
        target = {'resolver':'_evaluate','resolver-once':'_improve_once','resolver-daemon':'_run_daemon'}[entry]
        work = Mock(); monkeypatch.setattr(module, target, work)
        if entry == 'resolver': call = lambda: module.evaluate(None, {}, g, 'nethack', 1)
        else: call = lambda: getattr(module, target.removeprefix('_'))(g, 'nethack')
    with resource_fence(root, exclusive=True):
        with pytest.raises(FenceUnproven, match='busy'): call()
    work.assert_not_called()


def test_daily_and_host_container_hold_lock_until_child_cleanup_returns(tmp_path, monkeypatch):
    import docich.nethack_daily_improve as daily
    import docich.nethack_canary_container as container
    root = tmp_path.resolve(); g = SimpleNamespace(state_dir=root)
    def work(*args, **kwargs):
        with pytest.raises(FenceUnproven, match='busy'):
            with resource_fence(root, exclusive=True): pytest.fail('cleanup not fenced')
        return {'status': 'cleaned'}
    monkeypatch.setattr(daily, '_load_config', lambda g: SimpleNamespace(enabled=True))
    monkeypatch.setattr(daily, '_run_daily_improvement', work)
    monkeypatch.setattr(container, '_fixed_fence_state_dir', lambda: root)
    monkeypatch.setattr(container, '_run_container_worker', work)
    assert daily.run_daily_improvement(g)['status'] == 'cleaned'
    assert container.run_container_worker('{}')['status'] == 'cleaned'


def test_other_resolver_games_keep_existing_execution(tmp_path, monkeypatch):
    import docich.resolver.improve as module
    root = tmp_path.resolve()
    work = Mock(return_value={'score': 1}); monkeypatch.setattr(module, '_evaluate', work)
    with resource_fence(root, exclusive=True):
        assert module.evaluate(None, {}, SimpleNamespace(state_dir=root), 'robots', 1) == {'score': 1}
    work.assert_called_once()


def test_disabled_daily_does_not_create_state(tmp_path, monkeypatch):
    import docich.nethack_daily_improve as module
    root = tmp_path.resolve() / 'absent'
    monkeypatch.setattr(module, '_load_config', lambda g: SimpleNamespace(enabled=False))
    assert module.run_daily_improvement(SimpleNamespace(state_dir=root))['status'] == 'disabled'
    assert not root.exists()
