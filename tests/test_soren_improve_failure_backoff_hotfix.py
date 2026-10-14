import hashlib
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "hotfixes" / "apply_soren_improve_failure_backoff_hotfix.py"
spec = importlib.util.spec_from_file_location("hotfix", SCRIPT)
hotfix = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(hotfix)


def reviewed_shape() -> str:
    return "prefix\n" + hotfix.OLD_MARKER + hotfix.OLD_PRIMARY + hotfix.OLD_LIST_CALL + "suffix\n"


def test_transform_replaces_all_reviewed_fragments_once():
    transformed = hotfix.transform(reviewed_shape())
    assert hotfix.NEW_MARKER in transformed
    assert hotfix.NEW_PRIMARY in transformed
    assert hotfix.NEW_LIST_CALL in transformed
    assert hotfix.OLD_PRIMARY not in transformed
    assert hotfix.OLD_LIST_CALL not in transformed


def test_transform_refuses_fragment_drift():
    try:
        hotfix.transform("not the reviewed preimage")
    except ValueError as exc:
        assert "preimage mismatch" in str(exc)
    else:
        raise AssertionError("drift must be refused")


def test_apply_refuses_wrong_full_file_hash(tmp_path):
    target = tmp_path / "ai.sh"
    target.write_text("unreviewed live drift\n")
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    try:
        hotfix.apply(target)
    except ValueError as exc:
        assert "refusing unreviewed live drift" in str(exc)
    else:
        raise AssertionError("unreviewed file must be refused")
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before


def test_apply_is_atomic_and_idempotent_on_reviewed_shape(tmp_path, monkeypatch):
    target = tmp_path / "ai.sh"
    target.write_text(reviewed_shape())
    expected = hotfix.transform(target.read_text()).encode()
    monkeypatch.setattr(hotfix, "OLD_SHA256", hashlib.sha256(target.read_bytes()).hexdigest())
    monkeypatch.setattr(hotfix, "NEW_SHA256", hashlib.sha256(expected).hexdigest())
    assert hotfix.apply(target) == "applied"
    assert target.read_bytes() == expected
    assert hotfix.apply(target) == "already_applied"


def test_cli_is_production_path_only():
    assert hotfix.TARGET == Path("/home/ubuntu/soren/strategy/ai.sh")
    assert len(hotfix.OLD_SHA256) == 64
    assert len(hotfix.NEW_SHA256) == 64
