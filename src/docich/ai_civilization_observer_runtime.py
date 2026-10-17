"""Non-production runtime for the AI civilization observer corner (issue #1705).

This module implements the follow-up cards that
``docs/features/ai-civ-observer-corner.md`` explicitly split out of the input
contract: the bounded snapshot I/O, the previous/current diff, duplicate-event
suppression, importance-based topic selection, the structured diary draft, the
corner lifecycle frames, and the overlay payload.

Safety boundary (unchanged from the contract):

* read-only / passive: there is no HTTP client here at all, so no simulation
  activity can be triggered.  Fetching stays behind the contract's allowlisted
  GET endpoints and is a separate work unit.
* bounded: every upstream value is checked against the contract limits
  (256 KiB responses, 200 events/page, 1000 events/run, 1 region, 10000 tracked
  agent ids, 500 seen event ids, 3 topics).
* deterministic and offline: no clock, no randomness.  ``observed_at`` is an
  explicit caller input so a test (or a replay) gets byte-identical output.
* fail closed: an observation that fails validation produces no overlay and no
  stored record instead of partially publishing stale data.

The LLM narration call is intentionally *not* here; this module produces the
structured draft and the bounded topic list that a later narration step would
consume.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


OBSERVATION_MAX_BYTES = 262144
OBSERVATION_MAX_EVENTS_PER_PAGE = 200
OBSERVATION_MAX_EVENTS_PER_RUN = 1000
OBSERVATION_MAX_REGIONS = 1
OBSERVATION_HTTP_TIMEOUT_S = 4
OBSERVATION_TOTAL_FETCH_BUDGET_S = 45
OBSERVATION_SNAPSHOT_STALE_AFTER_S = 900
OBSERVER_MAX_TRACKED_AGENT_IDS = 10000
OBSERVER_SEEN_EVENT_CAP = 500
OBSERVER_TOPIC_COUNT = 3

CORNER_ID = "ai-civilization-observer"
RECORD_FILENAME = "ai_civ_observer_last.json"
RECORD_SCHEMA_VERSION = 1

# Maximum start + topics + end frames (3 topics is the contract cap).
MAX_PROGRESS_FRAMES = OBSERVER_TOPIC_COUNT + 2

WORLD_STATE_REQUIRED = (
    "schemaVersion",
    "worldId",
    "regionId",
    "revision",
    "tick",
    "seed",
    "rngState",
    "width",
    "height",
    "tiles",
    "factions",
    "agents",
    "structures",
    "events",
    "processedCommandIds",
)

OBSERVATION_REQUIRED = (
    "schemaVersion",
    "source",
    "scenario",
    "health",
    "snapshot",
    "previous",
    "events",
)

# Where a topic came from.  Births/deaths are derived from the agent-id set
# difference because MoYoGarden's EventKind has no birth/death kind.
DERIVED_TOPIC_KINDS = ("births", "deaths", "population_delta")

# This slice's importance policy for upstream event kinds (protocol.ts
# EventKind).  Higher is more newsworthy for the corner.  Unknown or future
# kinds fall back to DEFAULT_EVENT_WEIGHT so the selection stays total.
EVENT_KIND_WEIGHTS = {
    "world_started": 60,
    "construction_completed": 40,
    "trade_completed": 35,
    "trade_failed": 30,
    "resource_depleted": 30,
    "command_rejected": 25,
    "agent_conversation": 20,
    "construction_started": 20,
    "autonomy_changed": 15,
    "goal_changed": 15,
    "resources_deposited": 10,
    "resource_gathered": 10,
    "construction_progress": 5,
    "agent_moved": 5,
    "command_accepted": 5,
}
DEFAULT_EVENT_WEIGHT = 20
DERIVED_TOPIC_WEIGHTS = {"births": 50, "deaths": 50, "population_delta": 30}


class ObserverRuntimeError(ValueError):
    """Invalid observation, unavailable diff basis, or an inconsistent frame set."""


def _as_int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _bounded_bytes(payload) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def validate_previous(previous: dict, region_id: str) -> None:
    """A previous record must carry a complete, bounded living-id set.

    An incomplete set (duplicates or over the tracking cap) cannot support an
    exact diff: FIFO deletion would fabricate a birth on the next observation.
    """

    if not isinstance(previous, dict):
        raise ObserverRuntimeError("previous record must be an object")
    ids = previous.get("livingAgentIds")
    if not isinstance(ids, list) or any(not isinstance(x, str) or not x for x in ids):
        raise ObserverRuntimeError("previous record must retain living agent ids")
    if len(ids) != len(set(ids)):
        raise ObserverRuntimeError("previous living agent ids must be unique")
    if len(ids) > OBSERVER_MAX_TRACKED_AGENT_IDS:
        raise ObserverRuntimeError("previous living agent ids exceed the tracking cap")
    metrics = previous.get("metrics")
    if not isinstance(metrics, dict):
        raise ObserverRuntimeError("previous record must retain metrics")
    if _as_int(metrics.get("populationLiving")) != len(ids):
        raise ObserverRuntimeError("previous population must match its living id set")
    if metrics.get("regionPopulation") != {region_id: len(ids)}:
        raise ObserverRuntimeError(
            "previous single-region population must match the same observation"
        )


def validate_observation(data: dict) -> dict:
    """Validate one bounded observation, fail closed."""

    if not isinstance(data, dict):
        raise ObserverRuntimeError("observation must be a JSON object")
    missing = [key for key in OBSERVATION_REQUIRED if key not in data]
    if missing:
        raise ObserverRuntimeError(f"observation is missing required keys: {missing}")
    if data.get("schemaVersion") != 1:
        raise ObserverRuntimeError("unsupported observation schema version")
    if not isinstance(data.get("source"), str) or not data["source"]:
        raise ObserverRuntimeError("observation must name its source")
    if not isinstance(data.get("scenario"), str) or not data["scenario"]:
        raise ObserverRuntimeError("observation must name its scenario")

    snapshot = data["snapshot"]
    if not isinstance(snapshot, dict):
        raise ObserverRuntimeError("observation must contain a snapshot object")
    if _bounded_bytes(snapshot) > OBSERVATION_MAX_BYTES:
        raise ObserverRuntimeError("snapshot exceeds the response byte bound")
    missing = [key for key in WORLD_STATE_REQUIRED if key not in snapshot]
    if missing:
        raise ObserverRuntimeError(f"snapshot is missing WorldState keys: {missing}")
    if not isinstance(snapshot.get("agents"), list):
        raise ObserverRuntimeError("snapshot agents must be a list")
    tick = snapshot.get("tick")
    if _as_int(tick) is None or tick < 0:
        raise ObserverRuntimeError("snapshot tick must be a non-negative integer")
    region_id = snapshot.get("regionId")
    if not isinstance(region_id, str) or not region_id:
        raise ObserverRuntimeError("snapshot must name its region")

    health = data["health"]
    if not isinstance(health, dict) or _as_int(health.get("tick")) is None:
        raise ObserverRuntimeError("observation health must carry a tick")
    if not isinstance(health.get("paused"), bool):
        raise ObserverRuntimeError("observation health must carry a paused flag")
    if not isinstance(health.get("tickMode"), str) or not health["tickMode"]:
        raise ObserverRuntimeError("observation health must carry a tick mode")

    events = data["events"]
    if not isinstance(events, list):
        raise ObserverRuntimeError("observation events must be a list")
    if len(events) > OBSERVATION_MAX_EVENTS_PER_RUN:
        raise ObserverRuntimeError("observation exceeds the events-per-run bound")
    if _bounded_bytes(events) > OBSERVATION_MAX_BYTES:
        raise ObserverRuntimeError("events exceed the response byte bound")
    ids = []
    for event in events:
        if not isinstance(event, dict):
            raise ObserverRuntimeError("observation events must be objects")
        for key in ("id", "tick", "kind", "message"):
            if key not in event:
                raise ObserverRuntimeError(f"observation event is missing {key}")
        if not isinstance(event["id"], str) or not event["id"]:
            raise ObserverRuntimeError("observation event ids must be non-empty strings")
        ids.append(event["id"])
    if len(ids) != len(set(ids)):
        raise ObserverRuntimeError("observation event ids must be unique")

    previous = data["previous"]
    if previous is not None:
        validate_previous(previous, region_id)
    return data


def load_observation(path: Path) -> dict:
    # A valid observation can carry a 256 KiB snapshot plus a 256 KiB event
    # page, so the file-level guard is intentionally looser than one response;
    # the per-part bounds are enforced by validate_observation.
    raw = Path(path).read_bytes()
    if len(raw) > OBSERVATION_MAX_BYTES * 4:
        raise ObserverRuntimeError("observation file exceeds the bounded read size")
    return validate_observation(json.loads(raw.decode("utf-8")))


def is_synthetic(observation: dict) -> bool:
    """A fixture-sourced observation must never be presented as real."""

    return str(observation.get("source", "")).startswith("fixture:")


def living_agent_ids(snapshot: dict) -> tuple:
    """Living agents are derived observer-side: agents with hp > 0."""

    ids = []
    for agent in snapshot.get("agents", []):
        if not isinstance(agent, dict):
            continue
        agent_id = agent.get("id")
        hp = agent.get("hp")
        if isinstance(agent_id, str) and agent_id and _as_int(hp) is not None and hp > 0:
            ids.append(agent_id)
    return tuple(sorted(ids))


def normalize_observation(observation: dict) -> dict:
    """The observer-side record extracted from one bounded snapshot."""

    snapshot = observation["snapshot"]
    region_id = snapshot["regionId"]
    living = living_agent_ids(snapshot)
    if len(living) > OBSERVER_MAX_TRACKED_AGENT_IDS:
        raise ObserverRuntimeError("living agent id set exceeds the tracking cap")
    return {
        "tick": _as_int(snapshot.get("tick")),
        "regionId": region_id,
        "livingAgentIds": living,
        "metrics": {
            "populationLiving": len(living),
            "regionPopulation": {region_id: len(living)},
        },
        "events": tuple(observation["events"]),
        "health": dict(observation["health"]),
    }


def diff_observation(
    observation: dict,
    previous: dict | None = None,
    seen_event_ids=(),
) -> dict:
    """Window diff against the previous stored record.

    ``births`` / ``deaths`` are window increments from the agent-id set
    difference (migration included).  They are ``None`` when no complete
    previous id set exists, which the caller must surface instead of guessing.
    """

    current = normalize_observation(observation)
    seen = set(seen_event_ids)
    unseen = tuple(event for event in current["events"] if event["id"] not in seen)

    basis = previous is not None
    if basis:
        validate_previous(previous, current["regionId"])

    if basis:
        previous_living = set(previous["livingAgentIds"])
        current_living = set(current["livingAgentIds"])
        births = len(current_living - previous_living)
        deaths = len(previous_living - current_living)
        previous_tick = _as_int(previous.get("tick"))
        population_delta = current["metrics"]["populationLiving"] - _as_int(
            previous["metrics"].get("populationLiving")
        )
    else:
        births = None
        deaths = None
        previous_tick = None
        population_delta = None

    return {
        "tick": current["tick"],
        "previousTick": previous_tick,
        "regionId": current["regionId"],
        "populationLiving": current["metrics"]["populationLiving"],
        "populationDelta": population_delta,
        "births": births,
        "deaths": deaths,
        "hasDiffBasis": basis,
        "unseenEvents": unseen,
        "seenEventIds": tuple(seen_event_ids),
    }


def _topic_sort_key(topic: dict):
    return (-topic["score"], topic["tick"], topic["eventId"] or "")


def select_topics(diff: dict, limit: int = OBSERVER_TOPIC_COUNT) -> tuple:
    """Deterministic importance ranking: score desc, tick asc, eventId asc."""

    if limit < 0 or limit > OBSERVER_TOPIC_COUNT:
        raise ObserverRuntimeError("topic limit must stay within the corner bound")
    tick = diff["tick"] if _as_int(diff["tick"]) is not None else 0
    candidates = []

    if diff["births"]:
        candidates.append(
            {
                "kind": "births",
                "eventId": None,
                "score": DERIVED_TOPIC_WEIGHTS["births"],
                "tick": tick,
                "title": f"観測窓で新たに {diff['births']} 体が現れた（出生または移住）",
            }
        )
    if diff["deaths"]:
        candidates.append(
            {
                "kind": "deaths",
                "eventId": None,
                "score": DERIVED_TOPIC_WEIGHTS["deaths"],
                "tick": tick,
                "title": f"観測窓で {diff['deaths']} 体が観測から消えた（死亡または移住）",
            }
        )
    if diff["populationDelta"]:
        sign = "増加" if diff["populationDelta"] > 0 else "減少"
        candidates.append(
            {
                "kind": "population_delta",
                "eventId": None,
                "score": DERIVED_TOPIC_WEIGHTS["population_delta"],
                "tick": tick,
                "title": (
                    f"{diff['regionId']} の人口が前回比 "
                    f"{abs(diff['populationDelta'])} 体{sign}"
                ),
            }
        )
    for event in diff["unseenEvents"]:
        kind = event["kind"]
        candidates.append(
            {
                "kind": kind,
                "eventId": event["id"],
                "score": EVENT_KIND_WEIGHTS.get(kind, DEFAULT_EVENT_WEIGHT),
                "tick": _as_int(event.get("tick")) or 0,
                "title": event["message"],
            }
        )

    candidates.sort(key=_topic_sort_key)
    return tuple(candidates[:limit])


def update_seen_event_ids(
    seen_event_ids, topics, cap: int = OBSERVER_SEEN_EVENT_CAP
) -> tuple:
    """Add the topics introduced this run; drop the oldest past the cap."""

    if cap <= 0:
        raise ObserverRuntimeError("seen event cap must be positive")
    ordered = [event_id for event_id in seen_event_ids if isinstance(event_id, str) and event_id]
    for topic in topics:
        event_id = topic.get("eventId")
        if isinstance(event_id, str) and event_id and event_id not in ordered:
            ordered.append(event_id)
    return tuple(ordered[-cap:])


def topic_seeds(topics) -> tuple:
    return tuple(
        {
            "eventId": topic["eventId"],
            "kind": topic["kind"],
            "title": topic["title"],
            "firstSeenTick": topic["tick"],
        }
        for topic in topics
        if topic.get("eventId")
    )


def has_changes(diff: dict) -> bool:
    if diff["births"] or diff["deaths"] or diff["populationDelta"]:
        return True
    return bool(diff["unseenEvents"])


def next_focus(diff: dict, topics=()) -> tuple:
    """1-3 concrete things to watch next time (0 when nothing changed)."""

    if not has_changes(diff):
        return ()
    focus = []
    if diff["births"]:
        focus.append("出生・移入した個体が定着するか")
    if diff["deaths"]:
        focus.append("消えた個体が死亡か移住かの切り分け")
    if diff["populationDelta"]:
        focus.append(f"{diff['regionId']} の人口推移")
    shown = {topic["title"] for topic in topics}
    for event in diff["unseenEvents"]:
        if event["message"] in shown:
            continue
        focus.append(f"{event['kind']} の続報")
    return tuple(focus[:OBSERVER_TOPIC_COUNT])


def build_diary_draft(observation: dict, diff: dict, topics=(), focus=()) -> dict:
    """The structured diary draft a later (separate) LLM step would narrate."""

    current = normalize_observation(observation)
    lines = []
    if diff["hasDiffBasis"]:
        lines.append(
            f"tick {diff['previousTick']} から {diff['tick']} までの観測窓の差分。"
        )
        lines.append(
            f"人口は {diff['populationLiving']} 体（前回比 {diff['populationDelta']:+d}）、"
            f"新規 {diff['births']} 体 / 消失 {diff['deaths']} 体。"
        )
    else:
        lines.append(
            f"tick {diff['tick']} の初回観測。前回の完全な living agent id 集合が無いため、"
            "出生・死亡の窓増分は計算不能。"
        )
    if topics:
        lines.extend(f"・{topic['title']}" for topic in topics)
    else:
        lines.append("・今回は紹介すべき変化なし。")

    return {
        "corner": CORNER_ID,
        "isSynthetic": is_synthetic(observation),
        "source": observation["source"],
        "scenario": observation["scenario"],
        "regionId": current["regionId"],
        "tick": current["tick"],
        "metrics": current["metrics"],
        "previousTick": diff["previousTick"],
        "populationDelta": diff["populationDelta"],
        "births": diff["births"],
        "deaths": diff["deaths"],
        "topics": [dict(topic) for topic in topics],
        "nextFocus": list(focus),
        "lines": lines,
    }


def build_record(
    observation: dict,
    *,
    observed_at: str,
    source_base_url: str,
    diff: dict,
    topics=(),
    seen_event_ids=(),
) -> dict:
    """The persisted diff record (doc: ai_civ_observer_last.json)."""

    current = normalize_observation(observation)
    metrics = dict(current["metrics"])
    # births/deaths stay absent (not null) when no complete previous id set
    # exists: the stored record schema only carries integers for them.
    if diff["births"] is not None and diff["deaths"] is not None:
        metrics["births"] = diff["births"]
        metrics["deaths"] = diff["deaths"]
    return {
        "schemaVersion": RECORD_SCHEMA_VERSION,
        "observedAt": observed_at,
        "sourceBaseUrl": source_base_url,
        "regionId": current["regionId"],
        "tick": current["tick"],
        "metrics": metrics,
        "livingAgentIds": list(current["livingAgentIds"]),
        "topicSeeds": list(topic_seeds(topics)),
        "seenEventIds": list(update_seen_event_ids(seen_event_ids, topics)),
    }


def should_persist(observation: dict, previous: dict | None) -> bool:
    """Same tick with the same living set is idempotent: keep the old record."""

    if previous is None:
        return True
    current = normalize_observation(observation)
    if _as_int(previous.get("tick")) != current["tick"]:
        return True
    if previous.get("regionId") != current["regionId"]:
        return True
    return list(previous.get("livingAgentIds") or ()) != list(current["livingAgentIds"])


def save_record(state_dir: Path, record: dict) -> Path:
    """Atomically persist one region's record inside the configured state dir."""

    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / RECORD_FILENAME
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(f".{RECORD_FILENAME}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_record(state_dir: Path) -> dict | None:
    path = Path(state_dir) / RECORD_FILENAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def render_overlay(draft: dict) -> dict:
    """Minimal overlay payload for the observer corner."""

    return {
        "corner": CORNER_ID,
        "title": "AI文明観察日記",
        "isSynthetic": draft["isSynthetic"],
        "source": draft["source"],
        "regionId": draft["regionId"],
        "tick": draft["tick"],
        "metrics": dict(draft["metrics"]),
        "previousTick": draft["previousTick"],
        "populationDelta": draft["populationDelta"],
        "topics": [dict(topic) for topic in draft["topics"]],
        "nextFocus": list(draft["nextFocus"]),
    }


def progress_frames(overlay: dict) -> tuple:
    """Bounded start -> topic -> end frames; an unchanged run is start/end only."""

    topics = overlay["topics"]
    total = len(topics) + 2
    if total > MAX_PROGRESS_FRAMES:
        raise ObserverRuntimeError("observer progression exceeds the bounded frame budget")
    frames = [
        {
            "index": 0,
            "total": total,
            "kind": "start",
            "key": "start",
            "heading": overlay["title"],
            "text": f"tick {overlay['tick']} / region {overlay['regionId']}",
        }
    ]
    for order, topic in enumerate(topics, start=1):
        frames.append(
            {
                "index": order,
                "total": total,
                "kind": "topic",
                "key": topic["kind"],
                "heading": topic["title"],
                "text": topic["kind"],
            }
        )
    changed = bool(topics)
    frames.append(
        {
            "index": total - 1,
            "total": total,
            "kind": "end",
            "key": "end",
            "heading": overlay["title"],
            "text": (
                " / ".join(overlay["nextFocus"]) if changed else "今回は紹介すべき変化なし"
            ),
        }
    )
    return tuple(frames)


def status_overlay_entry(overlay: dict, frames=None) -> dict:
    if frames is None:
        frames = progress_frames(overlay)
    frames = tuple(frames)
    if not frames:
        raise ObserverRuntimeError("observer progression has no frames")
    return {
        "corner": CORNER_ID,
        "title": overlay["title"],
        "isSynthetic": overlay["isSynthetic"],
        "tick": overlay["tick"],
        "totalFrames": len(frames),
        "startIndex": frames[0]["index"],
        "endIndex": frames[-1]["index"],
        "startKind": frames[0]["kind"],
        "endKind": frames[-1]["kind"],
    }


def validate_progression(overlay: dict, frames=None) -> dict:
    """Re-check that the shown frames and the status entry still agree."""

    frames = tuple(progress_frames(overlay) if frames is None else frames)
    if not frames:
        raise ObserverRuntimeError("observer progression has no frames")
    entry = status_overlay_entry(overlay, frames)
    if len(frames) > MAX_PROGRESS_FRAMES:
        raise ObserverRuntimeError("observer progression exceeds the bounded frame budget")
    if [frame.get("index") for frame in frames] != list(range(len(frames))):
        raise ObserverRuntimeError("observer frames are not a contiguous bounded sequence")
    if frames[0].get("kind") != "start" or frames[-1].get("kind") != "end":
        raise ObserverRuntimeError("observer progression must open and close with its own frames")
    if len(frames) != entry["totalFrames"]:
        raise ObserverRuntimeError("observer frames do not match the status overlay length")
    if frames[0]["index"] != entry["startIndex"] or frames[-1]["index"] != entry["endIndex"]:
        raise ObserverRuntimeError("observer start/end indices do not match the status overlay")
    topic_frames = [frame for frame in frames[1:-1] if frame.get("kind") == "topic"]
    if len(topic_frames) != len(overlay["topics"]):
        raise ObserverRuntimeError("observer topic frames do not match the overlay topics")
    return entry
