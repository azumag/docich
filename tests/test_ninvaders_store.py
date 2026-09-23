"""昇格版のバージョン管理: 固定版(ハッシュ)・現行ポインタ・破損時のフォールバック・ロールバック。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.ninvaders.sandbox import policy_sha  # noqa: E402
from docich.ninvaders.store import PolicyStore  # noqa: E402

BASE = "def decide(obs, state):\n    return ['Space']\n"
V1 = "# CHANGE: one\ndef decide(obs, state):\n    return ['Left', 'Space']\n"
V2 = "# CHANGE: two\ndef decide(obs, state):\n    return ['Right', 'Space']\n"


def make(tmp_path):
    baseline = tmp_path / "baseline.py"
    baseline.write_text(BASE, encoding="utf-8")
    return PolicyStore(tmp_path / "pd", baseline)


def test_nothing_promoted_means_the_tracked_baseline_plays(tmp_path):
    cur = make(tmp_path).current()
    assert cur["origin"] == "baseline" and cur["sha"] == policy_sha(BASE)
    assert Path(cur["path"]).read_text(encoding="utf-8") == BASE


def test_promote_stores_an_immutable_hash_named_version_and_flips_current(tmp_path):
    store = make(tmp_path)
    cur = store.promote(V1, {"origin": "llm", "change": "one", "candidate_mean": 6000, "incumbent_mean": 5000, "p_value": 0.01})
    sha = policy_sha(V1)
    assert cur["origin"] == "promoted" and cur["sha"] == sha and cur["parent"] == policy_sha(BASE)
    assert (store.versions_dir / f"{sha}.py").read_text(encoding="utf-8") == V1
    meta = json.loads((store.versions_dir / f"{sha}.json").read_text(encoding="utf-8"))
    assert meta["parent"] == policy_sha(BASE) and meta["origin"] == "llm"
    assert store.recent_attempts(1)[0]["event"] == "promoted"


def test_tampered_version_file_falls_back_to_baseline(tmp_path):
    store = make(tmp_path)
    sha = store.promote(V1, {})["sha"]
    (store.versions_dir / f"{sha}.py").write_text("def decide(o, s):\n    return []\n", encoding="utf-8")
    assert store.current()["origin"] == "baseline"


def test_corrupt_or_missing_pointer_falls_back_to_baseline(tmp_path):
    store = make(tmp_path)
    store.promote(V1, {})
    (store.dir / "current.json").write_text("{not json", encoding="utf-8")
    assert store.current()["origin"] == "baseline"
    (store.dir / "current.json").unlink()
    assert store.current()["origin"] == "baseline"


def test_malformed_sha_pointer_cannot_escape_the_versions_directory(tmp_path):
    store = make(tmp_path)
    store.dir.mkdir(parents=True)
    (store.dir / "current.json").write_text(
        json.dumps({"sha": "../../baseline"}), encoding="utf-8"
    )
    assert store.current()["origin"] == "baseline"
    with pytest.raises(ValueError, match="invalid policy SHA"):
        store.source_of("../../baseline")


def test_rollback_returns_to_the_parent_and_is_logged(tmp_path):
    store = make(tmp_path)
    store.promote(V1, {})
    store.promote(V2, {})
    assert store.current()["sha"] == policy_sha(V2)
    assert store.rollback()["sha"] == policy_sha(V1)
    assert store.rollback()["origin"] == "baseline"  # V1's parent is the baseline seed
    assert store.rollback()["origin"] == "baseline"  # nothing left to roll back
    events = [e["event"] for e in store.recent_attempts(10)]
    assert events.count("rollback") == 2


def test_source_of_reads_baseline_and_promoted_versions(tmp_path):
    store = make(tmp_path)
    store.promote(V1, {})
    assert store.source_of(policy_sha(BASE)) == BASE
    assert store.source_of(policy_sha(V1)) == V1


def test_recent_attempts_is_bounded_and_skips_garbage_lines(tmp_path):
    store = make(tmp_path)
    for i in range(7):
        store.log({"event": "kept", "n": i})
    with (store.dir / "history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("garbage\n")
    got = store.recent_attempts(3)
    assert [e.get("n") for e in got if "n" in e] == [5, 6]
