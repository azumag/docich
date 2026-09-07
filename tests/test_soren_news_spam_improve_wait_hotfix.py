import hashlib
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "hotfixes" / "apply_soren_news_spam_improve_wait_hotfix.py"
spec = importlib.util.spec_from_file_location("hotfix", SCRIPT)
hotfix = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(hotfix)


def test_transform_applies_reviewed_fragment_once():
    text = "prefix\n" + hotfix.OLD_FRAGMENT + "\nsuffix\n"
    transformed = hotfix.transform(text)
    assert hotfix.OLD_FRAGMENT not in transformed
    assert hotfix.NEW_FRAGMENT in transformed
    assert "NEWS_SPAM_CHECK_IMPROVE_WAIT_MAX_SEC" in transformed


def test_transform_refuses_fragment_drift():
    for text in ("not the reviewed preimage", hotfix.OLD_FRAGMENT + hotfix.OLD_FRAGMENT):
        try:
            hotfix.transform(text)
        except ValueError as exc:
            assert "preimage fragment mismatch" in str(exc)
        else:
            raise AssertionError("fragment drift must be refused")


def test_apply_refuses_wrong_full_file_hash(tmp_path):
    target = tmp_path / "radio_news.sh"
    target.write_text("unreviewed live drift\n")
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    try:
        hotfix.apply(target)
    except ValueError as exc:
        assert "refusing unreviewed live drift" in str(exc)
    else:
        raise AssertionError("unreviewed file must be refused")
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before


def test_apply_is_atomic_idempotent_and_preserves_mode(tmp_path, monkeypatch):
    target = tmp_path / "radio_news.sh"
    old = ("prefix\n" + hotfix.OLD_FRAGMENT + "\nsuffix\n").encode()
    new = hotfix.transform(old.decode()).encode()
    target.write_bytes(old)
    target.chmod(0o755)
    monkeypatch.setattr(hotfix, "OLD_SHA256", hashlib.sha256(old).hexdigest())
    monkeypatch.setattr(hotfix, "NEW_SHA256", hashlib.sha256(new).hexdigest())
    assert hotfix.apply(target) == "applied"
    assert target.read_bytes() == new
    assert target.stat().st_mode & 0o777 == 0o755
    assert hotfix.apply(target) == "already_applied"


def test_cli_is_production_path_and_reviewed_hash_only():
    assert hotfix.TARGET == Path("/home/ubuntu/soren/broadcast/radio_news.sh")
    assert hotfix.OLD_SHA256 == "54c76ecce50aae4a1c372ba69dbfe3e5b936b78d3a749fbf7aaa2a49608391b8"
    assert hotfix.NEW_SHA256 == "3614091c7c2bd2ddd089ec5c72220fc47d3769a329ff37a084514233c6f52520"
