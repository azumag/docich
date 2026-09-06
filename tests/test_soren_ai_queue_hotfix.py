import hashlib
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "hotfixes" / "apply_soren_ai_queue_hotfix.py"
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
    target = tmp_path / "ai_generate.sh"
    target.write_text("unreviewed live drift\n")
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    try:
        hotfix.apply(target)
    except ValueError as exc:
        assert "refusing unreviewed live drift" in str(exc)
    else:
        raise AssertionError("unreviewed file must be refused")
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before


def test_cli_is_production_path_only():
    assert hotfix.TARGET == Path("/home/ubuntu/soren/lib/ai_generate.sh")
    assert len(hotfix.OLD_SHA256) == 64
    assert len(hotfix.NEW_SHA256) == 64
