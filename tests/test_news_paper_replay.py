import hashlib
import json

import pytest

from docich.trading.news_paper.replay import (
    InjectedClock, PitInputView, ReplayError, load_manifest,
    leakage_post_cutoff_doc, leakage_unconfirmed_bar, leakage_forming_bar,
)


def _fixture():
    raw = json.dumps([{"db": "d", "observation_id": 1, "ingest_sequence": 1,
                       "available_at": 5}]).encode()
    versions = {"source": "v1", "mapping": "v1", "instrument": "v1",
                "calendar": "v1", "cost": "v1", "risk": "v1",
                "policy": "v1", "model": "v1"}
    data = {"header": {"schema_version": "1", "run_id": "r", "scenario_id": "s",
            "versions": versions, "clock": {"fence_at": 10}, "arm": "A",
            "chunks": [{"seq": 1, "filename": "c1.json",
                        "sha256": hashlib.sha256(raw).hexdigest(), "record_count": 1}]}}
    return data, raw


def test_manifest_validates_and_is_deterministic():
    data, raw = _fixture()
    loader = lambda filename: raw
    m = load_manifest(data, loader)
    assert m.header.arm == "A"
    assert m.ingest_order == load_manifest(data, loader).ingest_order


def test_manifest_rejects_inline_or_missing_loader():
    data, raw = _fixture()
    with pytest.raises(ReplayError, match="chunk_loader required"):
        load_manifest(data)
    data["header"]["chunks"][0]["records"] = json.loads(raw)
    with pytest.raises(ReplayError, match="UNKNOWN_KEY|inline records"):
        load_manifest(data, lambda filename: raw)


def test_manifest_rejects_digest_mismatch():
    data, raw = _fixture()
    with pytest.raises(ReplayError, match="sha mismatch"):
        load_manifest(data, lambda filename: raw + b" ")


@pytest.mark.parametrize("filename", ["../secret", "../../x", "/etc/passwd",
                                       "nested/chunk.json", r"..\\secret", "single\\backslash", "chunk\x00name", ".."])
def test_manifest_rejects_unsafe_filename_before_loading(filename):
    data, raw = _fixture()
    data["header"]["chunks"][0]["filename"] = filename
    called = []
    def loader(name):
        called.append(name)
        return raw
    with pytest.raises(ReplayError, match="unsafe chunk filename"):
        load_manifest(data, loader)
    assert not called


def test_manifest_rejects_missing_ingest_sequence():
    data, raw = _fixture()
    broken = json.dumps([{"db": "d", "observation_id": 1}]).encode()
    data["header"]["chunks"][0]["sha256"] = hashlib.sha256(broken).hexdigest()
    with pytest.raises(ReplayError, match="invalid ingest record"):
        load_manifest(data, lambda filename: broken)


def test_clock_pit_fail_closed_and_advances():
    c = InjectedClock(10)
    v = PitInputView(c)
    assert v.visible({"available_at": 5})
    assert not v.visible({"available_at": 11})
    assert not v.visible({})
    assert not v.visible(leakage_unconfirmed_bar())
    assert not v.visible(leakage_forming_bar())
    assert not v.visible(leakage_post_cutoff_doc(10))
    c.advance(12)
    assert v.visible({"available_at": 11})
    assert not v.visible({"available_at": "not a timestamp"})


@pytest.mark.parametrize("mutation", [
    lambda d: d.update({"extra": 1}),
    lambda d: d["header"].update({"unexpected": 1}),
    lambda d: d["header"].update({"schema_version": "future"}),
    lambda d: d["header"].update({"arm": "live"}),
    lambda d: d["header"].update({"clock": {}}),
    lambda d: d["header"].update({"clock": {"fence_at": True}}),
    lambda d: d["header"]["chunks"][0].update({"unknown": 1}),
    lambda d: d["header"]["chunks"][0].update({"seq": True}),
    lambda d: d["header"]["chunks"][0].update({"record_count": True}),
    lambda d: d["header"].update({"chunks": "not a list"}),
])
def test_manifest_rejects_invalid_metadata_before_loading(mutation):
    data, raw = _fixture()
    mutation(data)
    loaded = []
    def loader(filename):
        loaded.append(filename)
        return raw
    with pytest.raises(ReplayError):
        load_manifest(data, loader)
    assert not loaded


def test_manifest_strict_versions_required_keys():
    # docich#2644 P3 PR-A: versions must carry every §2.9 required key.
    data, raw = _fixture()
    data["header"]["versions"].pop("model")
    with pytest.raises(ReplayError, match="UNKNOWN_KEY"):
        load_manifest(data, lambda f: raw)


def test_manifest_strict_versions_unknown_key_rejected():
    data, raw = _fixture()
    data["header"]["versions"]["surprise"] = "v9"
    with pytest.raises(ReplayError, match="UNKNOWN_KEY"):
        load_manifest(data, lambda f: raw)


def test_manifest_strict_record_unknown_key_rejected():
    import json as _json, hashlib as _hl
    rec = {"db": "d", "observation_id": 1, "ingest_sequence": 1,
           "available_at": 5, "rogue": True}
    raw = _json.dumps([rec]).encode()
    data, _ = _fixture()
    data["header"]["chunks"][0]["sha256"] = _hl.sha256(raw).hexdigest()
    with pytest.raises(ReplayError, match="UNKNOWN_KEY"):
        load_manifest(data, lambda f: raw)


def test_replay_error_carries_stage_and_code():
    data, raw = _fixture()
    data["header"]["clock"]["extra"] = 1
    with pytest.raises(ReplayError) as ei:
        load_manifest(data, lambda f: raw)
    assert ei.value.code == "UNKNOWN_KEY"
    assert ei.value.stage == "header.clock"


def test_manifest_schema_version_is_pinned():
    data, raw = _fixture()
    data["header"]["schema_version"] = "2"
    with pytest.raises(ReplayError):
        load_manifest(data, lambda f: raw)
