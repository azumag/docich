import json
from pathlib import Path
from unittest import TestCase

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ai_civilization_observer" / "observation_v1.json"
MAX_BYTES = 262144
MAX_EVENTS_PER_RUN = 1000
MAX_TRACKED_AGENT_IDS = 10000

WORLD_STATE_REQUIRED = {
    "schemaVersion", "worldId", "regionId", "revision", "tick",
    "seed", "rngState", "width", "height", "tiles", "factions",
    "agents", "structures", "events", "processedCommandIds",
}


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def assert_previous_population_consistent(previous, region_id):
    """Validate the complete bounded ID set required for the fixture's exact diff."""
    ids = previous["livingAgentIds"]
    assert len(ids) == len(set(ids)), "previous living IDs must be unique"
    assert len(ids) <= MAX_TRACKED_AGENT_IDS, "incomplete/over-cap ID sets cannot support exact MVP diffs"
    metrics = previous["metrics"]
    assert metrics["populationLiving"] == len(ids), "previous population must match its complete living-ID set"
    assert metrics["regionPopulation"] == {region_id: len(ids)}, "single-region population must match the same observation"


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
    assert "upstream-snapshot" in data["source"]
    assert isinstance(data["scenario"], str)


def test_health_schema():
    data = load_fixture()
    health = data["health"]
    assert isinstance(health, dict)
    assert isinstance(health["tick"], int)
    assert isinstance(health["paused"], bool)
    assert isinstance(health["tickMode"], str)


def test_snapshot_is_worldstate_shape():
    data = load_fixture()
    snap = data["snapshot"]
    assert isinstance(snap, dict)
    assert WORLD_STATE_REQUIRED.issubset(snap.keys())
    assert isinstance(snap["tick"], int) and snap["tick"] >= 0
    assert isinstance(snap["regionId"], str) and snap["regionId"]
    assert isinstance(snap["agents"], list)
    # living は観測側で導出（agents.filter(a => a.hp > 0)）
    derived_living = sum(1 for a in snap["agents"] if isinstance(a, dict) and (a.get("hp") or 0) > 0)
    exp = data.get("derivedExpectations", {})
    if "living" in exp:
        assert derived_living == exp["living"], (derived_living, exp["living"])
    # snapshot 自体に観測側導出値を含めない（raw upstream layer）
    for forbidden in ("population", "regionPopulation", "pairCount", "maxGeneration", "familyCount"):
        assert forbidden not in snap, forbidden


def test_previous_shape():
    data = load_fixture()
    prev = data["previous"]
    assert isinstance(prev, dict)
    assert isinstance(prev["tick"], int)
    assert isinstance(prev.get("livingAgentIds"), list)
    assert all(isinstance(x, str) and x for x in prev["livingAgentIds"])
    metrics = prev["metrics"]
    assert isinstance(metrics, dict)
    # 保存スキーマ (ai_civ_observer_last.json) に合わせる
    assert isinstance(metrics["populationLiving"], int)
    assert isinstance(metrics["regionPopulation"], dict)
    for optional in ("births", "deaths"):
        if optional in metrics:
            assert isinstance(metrics[optional], int), optional
    assert_previous_population_consistent(prev, data["snapshot"]["regionId"])


def test_previous_population_mismatch_is_rejected():
    data = load_fixture()
    previous = data["previous"]
    previous["metrics"]["populationLiving"] = 100
    previous["metrics"]["regionPopulation"] = {data["snapshot"]["regionId"]: 100}
    with TestCase().assertRaises(AssertionError):
        assert_previous_population_consistent(previous, data["snapshot"]["regionId"])


def test_previous_duplicate_ids_are_rejected():
    data = load_fixture()
    previous = data["previous"]
    previous["livingAgentIds"] = ["agent-0", "agent-0", "agent-1"]
    with TestCase().assertRaises(AssertionError):
        assert_previous_population_consistent(previous, data["snapshot"]["regionId"])


def test_previous_id_cap_requires_complete_bounded_set():
    region_id = "garden-1"
    ids = [f"agent-{i}" for i in range(MAX_TRACKED_AGENT_IDS)]
    previous = {"livingAgentIds": ids, "metrics": {"populationLiving": len(ids), "regionPopulation": {region_id: len(ids)}}}
    assert_previous_population_consistent(previous, region_id)
    # An unchanged population over the cap cannot be accepted as an exact
    # previous record; FIFO deletion would create a false birth next time.
    over_cap = ids + ["agent-over-cap"]
    previous = {"livingAgentIds": over_cap, "metrics": {"populationLiving": len(over_cap), "regionPopulation": {region_id: len(over_cap)}}}
    with TestCase().assertRaises(AssertionError):
        assert_previous_population_consistent(previous, region_id)
    assert set(over_cap) - set(over_cap) == set()
    assert set(over_cap) - set(over_cap[1:]) == {"agent-0"}


def test_window_births_deaths_derived():
    data = load_fixture()
    snap = data["snapshot"]
    prev = data["previous"]
    current_alive = {a["id"] for a in snap["agents"] if isinstance(a, dict) and (a.get("hp") or 0) > 0}
    previous_living = set(prev["livingAgentIds"])
    births = len(current_alive - previous_living)
    deaths = len(previous_living - current_alive)
    assert len(current_alive) == prev["metrics"]["populationLiving"] + births - deaths
    exp = data.get("derivedExpectations", {})
    if "windowBirths" in exp:
        assert births == exp["windowBirths"], (births, exp["windowBirths"])
    if "windowDeaths" in exp:
        assert deaths == exp["windowDeaths"], (deaths, exp["windowDeaths"])


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