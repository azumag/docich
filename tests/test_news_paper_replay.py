import json
from docich.trading.news_paper.replay import load_manifest, InjectedClock, PitInputView


def test_manifest_validates():
    data = {"header": {"schema_version": "1", "run_id": "r", "scenario_id": "s", "versions": {}, "clock": {"fence_at": 10}, "arm": "A", "chunks": [{"seq": 1, "filename": "c1.json", "sha256": "a"*64, "record_count": 1}]}}
    data["header"]["chunks"][0]["records"] = None
    # put records via chunk not header; loader not provided uses cm records; add
    data["header"]["chunks"][0]["records"] = [{"db": "d", "observation_id": 1, "ingest_sequence": 1, "available_at": 5}]
    m = load_manifest(data)
    assert m.header.arm == "A"


def test_clock_pit():
    c = InjectedClock(10)
    v = PitInputView(c)
    assert v.visible({"available_at": 5})
    assert not v.visible({"available_at": 11})
