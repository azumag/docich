import ast
import hashlib
import json
from pathlib import Path

import pytest

from docich.trading.news_paper import replay
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


def _chunk(records, *, seq=1, filename="c1.json", record_count=None):
    raw = json.dumps(records).encode()
    return raw, {
        "seq": seq,
        "filename": filename,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "record_count": len(records) if record_count is None else record_count,
    }


def _manifest(chunk_metas):
    data, _ = _fixture()
    data["header"]["chunks"] = chunk_metas
    return data



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


# -- PR-C: #780 precondition 3 negative branches ---------------------------
# The branches below are deliberately kept in this file: DSH note 1-1 assigns
# the fence negatives to PR-B and leaves record/count/size/guard tests to PR-C.


def test_manifest_rejects_record_count_mismatch():
    raw, meta = _chunk(
        [{"db": "d", "observation_id": 1, "ingest_sequence": 1}],
        record_count=2,
    )
    with pytest.raises(ReplayError, match="record_count mismatch"):
        load_manifest(_manifest([meta]), lambda filename: raw)


@pytest.mark.parametrize("record_count", [0, replay.MAX_RECORDS_PER_CHUNK + 1])
def test_manifest_rejects_chunk_meta_record_count_out_of_range(record_count):
    raw, meta = _chunk(
        [{"db": "d", "observation_id": 1, "ingest_sequence": 1}],
        record_count=record_count,
    )
    loaded = []
    def loader(filename):
        loaded.append(filename)
        return raw
    with pytest.raises(ReplayError) as ei:
        load_manifest(_manifest([meta]), loader)
    assert ei.value.code == "PAYLOAD_LIMIT"
    assert ei.value.stage == "chunk_meta"
    assert not loaded


@pytest.mark.parametrize("seqs", [(2, 1), (1, 1)])
def test_manifest_rejects_non_increasing_or_duplicate_chunk_seq(seqs):
    raw1, meta1 = _chunk(
        [{"db": "d", "observation_id": 1, "ingest_sequence": 1}],
        seq=seqs[0], filename="c1.json",
    )
    raw2, meta2 = _chunk(
        [{"db": "d", "observation_id": 2, "ingest_sequence": 2}],
        seq=seqs[1], filename="c2.json",
    )
    raw_by_name = {"c1.json": raw1, "c2.json": raw2}
    loaded = []
    def loader(filename):
        loaded.append(filename)
        return raw_by_name[filename]
    with pytest.raises(ReplayError, match="seq not strictly increasing unique"):
        load_manifest(_manifest([meta1, meta2]), loader)
    assert not loaded


@pytest.mark.parametrize("across_chunks", [False, True])
def test_manifest_rejects_duplicate_db_observation_id(across_chunks):
    first = {"db": "d", "observation_id": 1, "ingest_sequence": 1}
    second = {"db": "d", "observation_id": 1, "ingest_sequence": 2}
    if across_chunks:
        raw1, meta1 = _chunk([first], seq=1, filename="c1.json")
        raw2, meta2 = _chunk([second], seq=2, filename="c2.json")
        metas = [meta1, meta2]
        raw_by_name = {"c1.json": raw1, "c2.json": raw2}
    else:
        raw, meta = _chunk([first, second])
        metas = [meta]
        raw_by_name = {"c1.json": raw}
    with pytest.raises(ReplayError, match="duplicate ingest_order"):
        load_manifest(_manifest(metas), lambda filename: raw_by_name[filename])


@pytest.mark.parametrize("sequences", [(2, 2), (2, 1)])
def test_manifest_rejects_non_increasing_ingest_sequence(sequences):
    raw, meta = _chunk([
        {"db": "d", "observation_id": 1, "ingest_sequence": sequences[0]},
        {"db": "d", "observation_id": 2, "ingest_sequence": sequences[1]},
    ])
    with pytest.raises(ReplayError, match="ingest_sequence not increasing"):
        load_manifest(_manifest([meta]), lambda filename: raw)


def test_manifest_ingest_sequence_is_continuous_across_chunks():
    raw1, meta1 = _chunk(
        [{"db": "d", "observation_id": 1, "ingest_sequence": 5}],
        seq=1, filename="c1.json",
    )
    raw2, meta2 = _chunk(
        [{"db": "d", "observation_id": 2, "ingest_sequence": 4}],
        seq=2, filename="c2.json",
    )
    raw_by_name = {"c1.json": raw1, "c2.json": raw2}
    with pytest.raises(ReplayError, match="ingest_sequence not increasing"):
        load_manifest(_manifest([meta1, meta2]), lambda filename: raw_by_name[filename])


def test_manifest_rejects_oversized_header_before_loading():
    data, raw = _fixture()
    data["header"]["run_id"] = "r" * (replay.MAX_HEADER_BYTES + 1)
    loaded = []
    def loader(filename):
        loaded.append(filename)
        return raw
    with pytest.raises(ReplayError) as ei:
        load_manifest(data, loader)
    assert ei.value.code == "PAYLOAD_LIMIT"
    assert ei.value.stage == "header"
    assert not loaded


def test_manifest_rejects_chunk_count_over_limit():
    data, raw = _fixture()
    data["header"]["chunks"] = [
        dict(data["header"]["chunks"][0])
        for _ in range(replay.MAX_CHUNKS + 1)
    ]
    loaded = []
    def loader(filename):
        loaded.append(filename)
        return raw
    with pytest.raises(ReplayError) as ei:
        load_manifest(data, loader)
    assert ei.value.code == "PAYLOAD_LIMIT"
    assert ei.value.stage == "header"
    assert not loaded


def test_manifest_rejects_oversized_chunk_bytes():
    data, raw = _fixture()
    oversized = b"x" * (replay.MAX_CHUNK_BYTES + 1)
    data["header"]["chunks"][0]["sha256"] = hashlib.sha256(oversized).hexdigest()
    with pytest.raises(ReplayError) as ei:
        load_manifest(data, lambda filename: oversized)
    assert ei.value.code == "PAYLOAD_LIMIT"
    assert ei.value.stage == "chunk"


def test_manifest_rejects_chunk_record_count_over_limit():
    records = [
        {"db": "d", "observation_id": i, "ingest_sequence": i}
        for i in range(1, replay.MAX_RECORDS_PER_CHUNK + 2)
    ]
    raw, meta = _chunk(records, record_count=1)
    with pytest.raises(ReplayError) as ei:
        load_manifest(_manifest([meta]), lambda filename: raw)
    assert ei.value.code == "PAYLOAD_LIMIT"
    assert ei.value.stage == "chunk"


def test_manifest_rejects_total_record_limit(monkeypatch):
    # MAX_CHUNKS * MAX_RECORDS_PER_CHUNK currently equals MAX_TOTAL_RECORDS, so
    # the branch is unreachable with the shipped limits; lower the limit to pin
    # the fail-closed behavior against a future limit change.
    monkeypatch.setattr(replay, "MAX_TOTAL_RECORDS", 1)
    raw, meta = _chunk([
        {"db": "d", "observation_id": 1, "ingest_sequence": 1},
        {"db": "d", "observation_id": 2, "ingest_sequence": 2},
    ])
    with pytest.raises(ReplayError) as ei:
        load_manifest(_manifest([meta]), lambda filename: raw)
    assert ei.value.code == "PAYLOAD_LIMIT"
    assert ei.value.stage == "chunk"


def test_manifest_rejects_oversized_record():
    raw, meta = _chunk([
        {"db": "d", "observation_id": 1, "ingest_sequence": 1,
         "title": "x" * (replay.MAX_RECORD_BYTES + 1)},
    ])
    with pytest.raises(ReplayError) as ei:
        load_manifest(_manifest([meta]), lambda filename: raw)
    assert ei.value.code == "PAYLOAD_LIMIT"
    assert ei.value.stage == "record"


def test_manifest_rejects_record_depth_over_limit():
    nested = []
    cursor = nested
    for _ in range(replay.MAX_DEPTH + 2):
        child = []
        cursor.append(child)
        cursor = child
    raw, meta = _chunk([
        {"db": "d", "observation_id": 1, "ingest_sequence": 1, "bids": nested},
    ])
    with pytest.raises(ReplayError) as ei:
        load_manifest(_manifest([meta]), lambda filename: raw)
    assert ei.value.code == "PAYLOAD_LIMIT"
    assert ei.value.stage == "record"


FORBIDDEN_IMPORT_ROOTS = frozenset({
    "socket", "ssl", "http", "urllib", "requests", "httpx", "aiohttp",
    "websockets", "ftplib", "smtplib", "telnetlib",
    "openai", "anthropic", "litellm", "ollama", "cohere", "transformers",
    "time", "datetime", "random", "secrets",
})


def test_replay_module_refuses_network_llm_and_wall_clock_imports():
    source = Path(replay.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    roots = {name.split(".")[0] for name in imported}
    assert not roots & FORBIDDEN_IMPORT_ROOTS
    assert "importlib" not in roots
    forbidden_calls = {
        "utcnow", "today", "time", "monotonic", "perf_counter",
        "urlopen", "create_connection",
    }
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in forbidden_calls
    }
    # ``InjectedClock.now`` is the injected logical clock and is allowed; a
    # wall-clock ``datetime.now`` / ``date.today`` is not.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "now"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"datetime", "date", "time"}):
            calls.add("wall-clock-now")
    assert not calls


def test_manifest_loading_uses_injected_loader_only(monkeypatch):
    data, raw = _fixture()
    def forbidden(*args, **kwargs):
        raise AssertionError("manifest loading must not read files or network")
    monkeypatch.setattr("builtins.open", forbidden)
    assert load_manifest(data, lambda filename: raw).chunks



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


def test_visibility_boundary_is_inclusive_fill_fence_is_separate():
    # PR #3888 re-review "境界規則の解釈" (docich#2644 P3 PR-B): deterministic
    # replay visibility uses the inclusive ts <= now(); the strict
    # available_at > fence_at + 2 * max_error_ms rule belongs to N05/N06 fills.
    c = InjectedClock(10)
    v = PitInputView(c)
    assert v.visible({"available_at": 10})
    assert not v.visible({"available_at": 10.000001})


def test_manifest_clock_rejects_max_error_ms_key():
    # The §3.5 tolerance is a contract constant, never a manifest key.
    data, raw = _fixture()
    data["header"]["clock"]["max_error_ms"] = 1000
    with pytest.raises(ReplayError) as ei:
        load_manifest(data, lambda filename: raw)
    assert ei.value.code == "UNKNOWN_KEY"
    assert ei.value.stage == "header.clock"


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
