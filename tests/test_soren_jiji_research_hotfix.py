import hashlib
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "hotfixes" / "apply_soren_jiji_research_hotfix.py"
spec = importlib.util.spec_from_file_location("hotfix", SCRIPT)
hotfix = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(hotfix)


def _synthetic(spec):
    body = "prefix\n"
    for old, _new in spec.replacements:
        body += old + "\nmarker\n"
    return (body + "suffix\n").encode()


def test_transform_applies_every_reviewed_fragment_once():
    for patch in hotfix.SPECS:
        old = _synthetic(patch).decode()
        new = hotfix.transform(old, patch)
        for before, after in patch.replacements:
            assert before not in new
            assert after in new


def test_transform_refuses_fragment_drift():
    for patch in hotfix.SPECS:
        try:
            hotfix.transform("not the reviewed preimage", patch)
        except ValueError as exc:
            assert "preimage fragment mismatch" in str(exc)
        else:
            raise AssertionError("drift must be refused")


def test_apply_preflights_all_files_before_mutation(tmp_path, monkeypatch):
    root = tmp_path / "soren"
    root.mkdir()
    synthetic_specs = []
    for index, patch in enumerate(hotfix.SPECS):
        path = root / patch.relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        old = _synthetic(patch)
        new = hotfix.transform(old.decode(), patch).encode()
        if index == 1:
            path.write_bytes(b"unreviewed live drift\n")
        else:
            path.write_bytes(old)
        synthetic_specs.append(
            hotfix.PatchSpec(
                patch.relpath,
                hashlib.sha256(old).hexdigest(),
                hashlib.sha256(new).hexdigest(),
                patch.replacements,
            )
        )
    monkeypatch.setattr(hotfix, "SPECS", tuple(synthetic_specs))
    first = root / synthetic_specs[0].relpath
    before = first.read_bytes()
    try:
        hotfix.apply(root)
    except ValueError as exc:
        assert "refusing unreviewed live drift" in str(exc)
    else:
        raise AssertionError("unreviewed file must be refused")
    assert first.read_bytes() == before


def test_apply_updates_both_files_and_is_idempotent(tmp_path, monkeypatch):
    root = tmp_path / "soren"
    root.mkdir()
    synthetic_specs = []
    for patch in hotfix.SPECS:
        path = root / patch.relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        old = _synthetic(patch)
        new = hotfix.transform(old.decode(), patch).encode()
        path.write_bytes(old)
        path.chmod(0o755)
        synthetic_specs.append(
            hotfix.PatchSpec(
                patch.relpath,
                hashlib.sha256(old).hexdigest(),
                hashlib.sha256(new).hexdigest(),
                patch.replacements,
            )
        )
    monkeypatch.setattr(hotfix, "SPECS", tuple(synthetic_specs))
    assert hotfix.apply(root) == "applied"
    for patch in synthetic_specs:
        path = root / patch.relpath
        assert hashlib.sha256(path.read_bytes()).hexdigest() == patch.new_sha256
        assert path.stat().st_mode & 0o777 == 0o755
    assert hotfix.apply(root) == "already_applied"


def test_cli_targets_only_reviewed_production_files():
    assert hotfix.SOREN_ROOT == Path("/home/ubuntu/soren")
    assert hotfix.REVIEWED_SOVIET_NOW_COMMIT == "530f7a847c8c09d4e8469a27643d0dcc930c6105"
    assert {p.relpath for p in hotfix.SPECS} == {"core/helpers.sh", "broadcast/radio_corners.sh"}
    assert hotfix.SPECS[0].old_sha256 == "97ff8377bb7685da2a88975f823c48622798e06de9d21f48abc3e9269a7e746a"
    assert hotfix.SPECS[0].new_sha256 == "af9954b97c0e1b09bd7c1161a672aa9909d8190763a61193fed4982a464d7850"
    assert hotfix.SPECS[1].old_sha256 == "80aa0e7d6e99f00706dbac67f54ee81f3cdccb6c53f6294e850f68a4e790032b"
    assert hotfix.SPECS[1].new_sha256 == "846d08043c81617f8ebd1414180cec9a3d51ca32fa297be07383656c940e1fb7"
