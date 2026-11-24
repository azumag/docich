import json
from types import SimpleNamespace

from docich.corner_boundary import REGISTRY_FILE, release_program_owner


def _fake_g(root):
    return SimpleNamespace(webui=SimpleNamespace(soren_root=str(root)))


def _state_dir(tmp_path):
    root = tmp_path / 'tmp' / 'state'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_state(path):
    path.write_text(json.dumps({'status': 'failed'}), encoding='utf-8')


def _write_registry(root, owner_state):
    registry = root / REGISTRY_FILE
    registry.write_text(json.dumps({'owner_state': str(owner_state)}), encoding='utf-8')
    return registry


def test_release_program_owner_accepts_relative_path_for_same_file(tmp_path, monkeypatch):
    root = _state_dir(tmp_path)
    g = _fake_g(tmp_path)
    owner = root / 'doushi_corner.json'
    _write_state(owner)
    monkeypatch.chdir(tmp_path)
    registry = _write_registry(root, 'tmp/state/doushi_corner.json')

    assert release_program_owner(g, owner.resolve()) is True
    assert not registry.exists()


def test_release_program_owner_accepts_symlink_path_for_same_file(tmp_path):
    root = _state_dir(tmp_path)
    g = _fake_g(tmp_path)
    owner = root / 'doushi_corner.json'
    _write_state(owner)
    alias = tmp_path / 'state-alias'
    alias.symlink_to(root, target_is_directory=True)
    registry = _write_registry(root, alias / owner.name)

    assert release_program_owner(g, owner) is True
    assert not registry.exists()


def test_release_program_owner_rejects_different_existing_file(tmp_path):
    root = _state_dir(tmp_path)
    g = _fake_g(tmp_path)
    owner = root / 'doushi_corner.json'
    other = root / 'weather_corner.json'
    _write_state(owner)
    _write_state(other)
    registry = _write_registry(root, other)

    assert release_program_owner(g, owner) is False
    assert json.loads(registry.read_text(encoding='utf-8'))['owner_state'] == str(other)


def test_release_program_owner_rejects_missing_registry_owner_path(tmp_path):
    root = _state_dir(tmp_path)
    g = _fake_g(tmp_path)
    owner = root / 'doushi_corner.json'
    _write_state(owner)
    missing = root / 'missing_corner.json'
    registry = _write_registry(root, missing)

    assert release_program_owner(g, owner) is False
    assert json.loads(registry.read_text(encoding='utf-8'))['owner_state'] == str(missing)
