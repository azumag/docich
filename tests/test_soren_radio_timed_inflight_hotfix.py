import hashlib
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "hotfixes" / "apply_soren_radio_timed_inflight_hotfix.py"
spec = importlib.util.spec_from_file_location("hotfix", SCRIPT)
hotfix = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(hotfix)


def test_transform_ports_owner_aware_recovery_without_unrelated_scheduler_change():
    transformed = hotfix.transform("prefix\n" + hotfix.OLD_FRAGMENT + "suffix\n")
    assert hotfix.OLD_FRAGMENT not in transformed
    assert hotfix.NEW_FRAGMENT in transformed
    assert '_timed_corner_claim_inflight()' in transformed
    assert "${BASHPID:-$$}" in transformed
    assert '"$inflight/owner"' in transformed
    # Production still carries an older timing-window policy.  This hotfix must
    # not silently deploy that unrelated source drift while porting #195.
    assert '[ "$diff" -lt 0 ] && diff=$((-diff))' in transformed
    assert 'future slots must not' not in hotfix.NEW_FRAGMENT


def test_transform_refuses_fragment_drift():
    try:
        hotfix.transform("not the reviewed preimage")
    except ValueError as exc:
        assert "preimage fragment mismatch" in str(exc)
    else:
        raise AssertionError("fragment drift must be refused")


def test_apply_refuses_wrong_full_file_hash(tmp_path):
    target = tmp_path / "scheduler.sh"
    target.write_text("unreviewed live drift\n")
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    try:
        hotfix.apply(target)
    except ValueError as exc:
        assert "refusing unreviewed live drift" in str(exc)
    else:
        raise AssertionError("unreviewed file must be refused")
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before


def test_apply_is_atomic_and_idempotent_for_reviewed_fragment(tmp_path, monkeypatch):
    target = tmp_path / "scheduler.sh"
    old = ("prefix\n" + hotfix.OLD_FRAGMENT + "suffix\n").encode()
    new = hotfix.transform(old.decode()).encode()
    target.write_bytes(old)
    target.chmod(0o755)
    monkeypatch.setattr(hotfix, "OLD_SHA256", hashlib.sha256(old).hexdigest())
    monkeypatch.setattr(hotfix, "NEW_SHA256", hashlib.sha256(new).hexdigest())

    assert hotfix.apply(target) == "applied"
    assert target.read_bytes() == new
    assert target.stat().st_mode & 0o777 == 0o755
    assert hotfix.apply(target) == "already_applied"


def test_cli_is_exact_production_path_and_observed_hash_pair():
    assert hotfix.TARGET == Path("/home/ubuntu/soren/broadcast/scheduler.sh")
    assert hotfix.SOURCE_FIX_SHA == "16ba1026af1d0aad2d2297003d7e8f8d9210b352"
    assert hotfix.OLD_SHA256 == "4e1f4278747ed04c344c79ad1c6e7f39c0020dc1057b6a928ba196d084547a79"
    assert hotfix.NEW_SHA256 == "918ebfd5f76511ca5c376eaa3f713f2511a5729c6aad68a22a63fb614367b041"
