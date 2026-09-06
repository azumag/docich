import hashlib
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "hotfixes" / "apply_soren_radio_orphan_cache_hotfix.py"
spec = importlib.util.spec_from_file_location("hotfix", SCRIPT)
hotfix = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(hotfix)


def test_transform_applies_each_reviewed_fragment_once():
    text = "prefix\n" + "".join(old for old, _ in hotfix.REPLACEMENTS) + "suffix\n"
    transformed = hotfix.transform(text)
    for old, new in hotfix.REPLACEMENTS:
        assert old not in transformed
        assert new in transformed


def test_transform_refuses_fragment_drift():
    try:
        hotfix.transform("not the reviewed preimage")
    except ValueError as exc:
        assert "preimage fragment mismatch" in str(exc)
    else:
        raise AssertionError("drift must be refused")


def test_apply_refuses_wrong_full_file_hash(tmp_path):
    target = tmp_path / "radio_state.sh"
    target.write_text("unreviewed live drift\n")
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    try:
        hotfix.apply(target)
    except ValueError as exc:
        assert "refusing unreviewed live drift" in str(exc)
    else:
        raise AssertionError("unreviewed file must be refused")
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before


def test_apply_is_atomic_and_idempotent_for_reviewed_fragments(tmp_path, monkeypatch):
    target = tmp_path / "radio_state.sh"
    old = ("prefix\n" + "".join(old for old, _ in hotfix.REPLACEMENTS) + "suffix\n").encode()
    new = hotfix.transform(old.decode()).encode()
    target.write_bytes(old)
    target.chmod(0o755)
    monkeypatch.setattr(hotfix, "OLD_SHA256", hashlib.sha256(old).hexdigest())
    monkeypatch.setattr(hotfix, "NEW_SHA256", hashlib.sha256(new).hexdigest())

    assert hotfix.apply(target) == "applied"
    assert target.read_bytes() == new
    assert target.stat().st_mode & 0o777 == 0o755
    assert hotfix.apply(target) == "already_applied"


def test_cli_is_production_path_only():
    assert hotfix.TARGET == Path("/home/ubuntu/soren/broadcast/radio_state.sh")
    assert hotfix.OLD_SHA256 == "bb14926fb71dc4516b7a8651aa77f0ab3dec4af4c3c5575da27df902c6c8ef2c"
    assert hotfix.NEW_SHA256 == "0c81ef551a7647ae393a8a876478d5e30bb1e3a9665af120f5f6ed2b542a3ea6"
