import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ai_civilization_observer" / "observation_v1.json"
MAX_BYTES = 262144
MAX_EVENTS_PER_RUN = 1000


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_exists_and_parses():
    assert FIXTURE.exists()
    data = load_fixture()
    assert isinstance(data, dict)


def test_top_level_schema():
    data = load_fixture()
    required = {"schemaVersion", "source", "scenario", "health", "snapshot", "previous", "events"}
    assert required.issubset(data.keys())
    assert data["schemaVersion"] == 1
    assert isinstance(data["source"], str)
    assert isinstance(data["scenario"], str)


def test_health_schema():
    data = load_fixture()
    health = data["health"]
    assert isinstance(health, dict)
    assert isinstance(health["tick"], int)
    assert isinstance(health["paused"], bool)
    assert isinstance(health["tickMode"], str)


def test_snapshot_required_fields():
    data = load_fixture()
    snap = data["snapshot"]
    assert isinstance(snap, dict)
    for key in ("tick", "regionId", "population", "regionPopulation"):
        assert key in snap, key
    assert isinstance(snap["tick"], int) and snap["tick"] >= 0
    assert isinstance(snap["regionId"], str) and snap["regionId"]
    pop = snap["population"]
    assert isinstance(pop, dict)
    for key in ("living", "births", "deaths"):
        assert key in pop, key
        assert isinstance(pop[key], int)
    assert isinstance(snap["regionPopulation"], dict)


def test_previous_shape():
    data = load_fixture()
    prev = data["previous"]
    assert isinstance(prev, dict)
    assert isinstance(prev["tick"], int)
    metrics = prev["metrics"]
    assert isinstance(metrics, dict)
    assert isinstance(metrics["populationLiving"], int)
    assert isinstance(metrics["regionPopulation"], dict)


def test_events_bounds_and_uniqueness():
    data = load_fixture()
    events = data["events"]
    assert isinstance(events, list)
    assert len(events) <= MAX_EVENTS_PER_RUN
    ids = [e["id"] for e in events]
    assert len(ids) == len(set(ids)), "event ids must be unique"
    for ev in events:
        assert isinstance(ev, dict)
        for key in ("id", "tick", "kind", "message"):
            assert key in ev, key
        assert isinstance(ev["id"], str)
        assert isinstance(ev["tick"], int)
        assert isinstance(ev["kind"], str)
        assert isinstance(ev["message"], str)
        if "agentId" in ev and ev["agentId"] is not None:
            assert isinstance(ev["agentId"], str)


def test_snapshot_byte_bound():
    data = load_fixture()
    payload = json.dumps(data["snapshot"], ensure_ascii=False).encode("utf-8")
    assert len(payload) <= MAX_BYTES


def test_events_byte_bound():
    data = load_fixture()
    payload = json.dumps(data["events"], ensure_ascii=False).encode("utf-8")
    assert len(payload) <= MAX_BYTES