import gzip
import hashlib
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "hotfixes" / "apply_soren_news_spam_queue_wait_hotfix.py"
spec = importlib.util.spec_from_file_location("hotfix", SCRIPT)
hotfix = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(hotfix)


def _write_fixture(root, payload_dir, patch, *, wrong_target=False):
    target = root / patch.relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    old = (f"old:{patch.relpath}\n").encode()
    new = (f"new:{patch.relpath}\n").encode()
    target.write_bytes(b"drift\n" if wrong_target else old)
    target.chmod(0o755 if patch.relpath.endswith("radio_news.sh") else 0o644)
    payload = payload_dir / patch.payload_name
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(gzip.compress(new, mtime=0))
    return hotfix.PatchSpec(
        patch.relpath,
        hashlib.sha256(old).hexdigest(),
        hashlib.sha256(new).hexdigest(),
        patch.payload_name,
    )


def test_payloads_are_exact_reviewed_soviet_now_postimages():
    for patch in hotfix.SPECS:
        payload = hotfix.PAYLOAD_DIR / patch.payload_name
        assert payload.is_file() and not payload.is_symlink()
        assert hashlib.sha256(gzip.decompress(payload.read_bytes())).hexdigest() == patch.new_sha256
    ai = gzip.decompress((hotfix.PAYLOAD_DIR / "lib_ai_generate.sh.gz").read_bytes()).decode()
    news = gzip.decompress((hotfix.PAYLOAD_DIR / "broadcast_radio_news.sh.gz").read_bytes()).decode()
    assert "AI_QUEUE_GIVEUP_RC=92" in ai
    assert "AI_GENERATION_QUEUE_GUARD_FD" in ai
    assert "generation slot wait exceeded" in ai
    assert "NEWS_SPAM_CHECK_QUEUE_WAIT_MAX_SEC" in news


def test_apply_preflights_all_targets_before_mutation(tmp_path, monkeypatch):
    root = tmp_path / "soren"
    payload_dir = tmp_path / "payloads"
    specs = []
    for index, patch in enumerate(hotfix.SPECS):
        specs.append(_write_fixture(root, payload_dir, patch, wrong_target=(index == 1)))
    monkeypatch.setattr(hotfix, "SPECS", tuple(specs))
    first = root / specs[0].relpath
    before = first.read_bytes()
    try:
        hotfix.apply(root, payload_dir)
    except ValueError as exc:
        assert "refusing unreviewed live drift" in str(exc)
    else:
        raise AssertionError("unknown live drift must be refused")
    assert first.read_bytes() == before


def test_apply_updates_both_files_idempotently_and_preserves_modes(tmp_path, monkeypatch):
    root = tmp_path / "soren"
    payload_dir = tmp_path / "payloads"
    specs = tuple(_write_fixture(root, payload_dir, patch) for patch in hotfix.SPECS)
    monkeypatch.setattr(hotfix, "SPECS", specs)
    modes = {p.relpath: (root / p.relpath).stat().st_mode & 0o777 for p in specs}
    assert hotfix.apply(root, payload_dir) == "applied"
    for patch in specs:
        path = root / patch.relpath
        assert hashlib.sha256(path.read_bytes()).hexdigest() == patch.new_sha256
        assert path.stat().st_mode & 0o777 == modes[patch.relpath]
    assert hotfix.apply(root, payload_dir) == "already_applied"


def test_apply_refuses_bad_payload_before_mutation(tmp_path, monkeypatch):
    root = tmp_path / "soren"
    payload_dir = tmp_path / "payloads"
    specs = tuple(_write_fixture(root, payload_dir, patch) for patch in hotfix.SPECS)
    monkeypatch.setattr(hotfix, "SPECS", specs)
    bad = payload_dir / specs[1].payload_name
    bad.write_bytes(b"tampered payload\n")
    first = root / specs[0].relpath
    before = first.read_bytes()
    try:
        hotfix.apply(root, payload_dir)
    except ValueError as exc:
        assert "reviewed payload" in str(exc)
    else:
        raise AssertionError("tampered payload must be refused")
    assert first.read_bytes() == before


def test_apply_refuses_symlink_target(tmp_path, monkeypatch):
    root = tmp_path / "soren"
    payload_dir = tmp_path / "payloads"
    specs = tuple(_write_fixture(root, payload_dir, patch) for patch in hotfix.SPECS)
    monkeypatch.setattr(hotfix, "SPECS", specs)
    target = root / specs[0].relpath
    data = target.read_bytes()
    target.unlink()
    real = root / "real-ai.sh"
    real.write_bytes(data)
    target.symlink_to(real)
    try:
        hotfix.apply(root, payload_dir)
    except ValueError as exc:
        assert "regular non-symlink" in str(exc)
    else:
        raise AssertionError("symlink target must be refused")


def test_contract_pins_observed_production_preimages_and_source_commits():
    assert hotfix.SOREN_ROOT == Path("/home/ubuntu/soren")
    assert hotfix.QUEUE_OWNER_FIX_COMMIT == "74ecf1da7309db065b1ce49ac1f56ab6b6c661f4"
    assert hotfix.NEWS_QUEUE_FIX_COMMIT == "6a59ea7bb7c7affb44937fa2d0ac58af99fc932c"
    by_path = {p.relpath: p for p in hotfix.SPECS}
    assert by_path["lib/ai_generate.sh"].old_sha256 == "8b43d7ddc305d5ec80e8c78aa1c35ade5f392979078564a06611b0bceb2fbd4a"
    assert by_path["lib/ai_generate.sh"].new_sha256 == "d417e583ad0dcb7afe5faae37de1c07e66d9342172749fa66aea8646e76cc9b1"
    assert by_path["broadcast/radio_news.sh"].old_sha256 == "3614091c7c2bd2ddd089ec5c72220fc47d3769a329ff37a084514233c6f52520"
    assert by_path["broadcast/radio_news.sh"].new_sha256 == "c3faf52e8b8f6920d804b28f8bd3f539a93210f6d86c466297fa8993c0e70cf3"
