"""Offline #697 runtime: the deterministic communist law simulator (synthetic).

Non-production, offline and passive, like ``new_corner_runtime`` and
``ai_civilization_observer_runtime``.  It stays outside the broadcast overlay
pipeline and does not claim that production wiring, corner rotation
registration, an LLM proposal brain, or a stream-side HTML view exist.  It
implements one pure layer only:

1. load the fixed synthetic session fixture and validate its envelope
   (``load_session_fixture``);
2. validate a session payload with a strict, fail-closed contract and fold it
   through the deterministic law engine (``parse_session`` / ``simulate`` /
   ``session_trace``);
3. render the overlay payload and derive the bounded
   start -> turns -> end progression plus the single status-overlay entry that
   has to stay aligned with it (``render_overlay`` / ``progress_frames`` /
   ``status_overlay_entry`` / ``validate_status``).

There is no network access, no LLM call, no clock and no write outside the
process.  The same session always produces byte-identical trace records
(``trace_signature`` pins that).  All law names, the republic, the metrics and
every event are fictional synthetic content: the engine never claims to model
a real state, a real economy, or real history.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

CORNER_ID = "communist-law-simulator"
ENGINE_ID = "law-sim-v1"
SCHEMA_VERSION = 1

# Bounded session: a session may not run an unbounded number of turns, and a
# title/republic may not smuggle unbounded text into the overlay.
MAX_SESSION_LAWS = 12
MAX_TITLE_CHARS = 80
SESSION_MAX_BYTES = 64 * 1024

# The shared bounded progression budget (new_corner_runtime precedent).
MAX_PROGRESS_FRAMES = 48

METRICS = (
    "production",
    "support",
    "discipline",
    "reserves",
    "blackMarket",
    "planProgress",
)
METRIC_LABELS = {
    "production": "生産",
    "support": "支持",
    "discipline": "党規律",
    "reserves": "外貨",
    "blackMarket": "闇市場",
    "planProgress": "計画",
}
METRIC_MIN = 0
METRIC_MAX = 100

# Fixed deterministic opening state for every session.
INITIAL_METRICS = {
    "production": 40,
    "support": 40,
    "discipline": 60,
    "reserves": 20,
    "blackMarket": 20,
    "planProgress": 0,
}

CATEGORIES = ("economy", "society", "security", "foreign", "culture")

# The plan accelerates with production: after every enactment the plan gains
# production // PLAN_GAIN_DIVISOR points on top of the law's own effect.
PLAN_GAIN_DIVISOR = 10

# Deterministic warning events.  Each fires once per crossing: "up" when the
# metric moves from below the threshold to at/above it, "down" when it moves
# from above to at/below it.  Crossing back re-arms the warning.
WARNING_THRESHOLDS = (
    ("blackMarket", 50, "up", "闇市場が計画物資の半分を流し始めた"),
    ("support", 25, "down", "食料行列とデモが広がり、民衆の不満が高まっている"),
    ("reserves", 10, "down", "外貨準備が底をつき、輸入計画が停止した"),
    ("production", 80, "up", "工場が計画線を超えて操業している"),
    ("discipline", 30, "down", "党内の異論が統制を緩め始めた"),
)

STATUS_TEXT = {
    "in_session": "審議が続行中である",
    "plan_achieved": "五カ年計画を達成した",
    "plan_incomplete": "会期が終了したが計画は未達である",
    "collapse_public_unrest": "民衆の不満が爆発し、政権は崩壊した",
    "collapse_party_split": "党の統制が失われ、指導部は分裂した",
    "collapse_trade_default": "対外支払が停止し、対外経済は破綻した",
    "collapse_black_market": "闇市場が計画経済を侵食し、国家の統制を失った",
}
TERMINAL_STATUSES = frozenset(k for k in STATUS_TEXT if k != "in_session")

# Model-level caveats: retained into the end frame and the status overlay.
CAVEATS = {
    "modelLimits": (
        "国家指標への効果は固定の近似則であり、実在する経済・政治の法則や"
        "特定の国の歴史をモデル化したものではない"
    ),
    "syntheticContent": (
        "共和国名・法令・指標・出来事はすべて架空の合成例であり、"
        "実在の人物・組織・国家・歴史的事件とは関係がない"
    ),
    "nonProduction": (
        "本ランタイムは非本番の offline slice であり、配信 overlay への配線、"
        "corner rotation への登録、LLM による法案提出は行っていない"
    ),
}


class LawSimError(ValueError):
    """Invalid session, unknown law, or an inconsistent overlay frame set."""


@dataclass(frozen=True)
class Law:
    """One fixed law card.  Effects are integer deltas per metric."""

    law_id: str
    name: str
    category: str
    summary: str
    effects: dict
    risks: tuple


LAWS = {
    "grain-procurement-tightening": Law(
        law_id="grain-procurement-tightening",
        name="穀物調達の強化",
        category="economy",
        summary="国家調達価格を引き下げ、穀物の強制的な集中調達を行う",
        effects={"production": 6, "support": -10, "blackMarket": 12},
        risks=("農村の不満が急速に蓄積する", "余剰穀物が闇市場へ流れる"),
    ),
    "science-technology-plan": Law(
        law_id="science-technology-plan",
        name="科学技術五カ年計画",
        category="economy",
        summary="研究都市を建設し、技術導入と機械工業の近代化を進める",
        effects={"production": 6, "planProgress": 12, "reserves": -5},
        risks=("外国技術への支払が外貨を圧迫する",),
    ),
    "five-year-plan-steel": Law(
        law_id="five-year-plan-steel",
        name="鉄鋼生産倍増令",
        category="economy",
        summary="鉄鋼増産を最優先し、労働割当を引き上げる",
        effects={"production": 14, "planProgress": 10, "reserves": -8, "support": -4},
        risks=("消費財が不足し不満が溜まる",),
    ),
    "collective-farm-consolidation": Law(
        law_id="collective-farm-consolidation",
        name="集体農場の統合",
        category="economy",
        summary="小規模農場を大規模集体農場へ統合し、機械化を進める",
        effects={"production": 8, "support": -6, "blackMarket": 4},
        risks=("統合に反対する農民の流出が起きる",),
    ),
    "foreign-trade-opening": Law(
        law_id="foreign-trade-opening",
        name="対外貿易の部分開放",
        category="foreign",
        summary="指定港に限り、外貨獲得のための直接貿易を認める",
        effects={"reserves": 14, "planProgress": 6, "discipline": -5, "blackMarket": 3},
        risks=("国内流通の統制が緩む",),
    ),
    "currency-reform-confiscation": Law(
        law_id="currency-reform-confiscation",
        name="通貨改革と貯蓄引き上げ",
        category="economy",
        summary="旧券を新券へ交換し、上限を超える貯蓄を無効化する",
        effects={"blackMarket": 10, "support": -8, "reserves": 10},
        risks=("貯蓄を失った層の不満が残る", "取引が闇へ逃げる"),
    ),
    "anti-corruption-campaign": Law(
        law_id="anti-corruption-campaign",
        name="汚職一掃キャンペーン",
        category="security",
        summary="検察と保安省に汚職摘発の特別権限を与える",
        effects={"discipline": 8, "blackMarket": -6, "support": 2},
        risks=("告発が党内の相互監視を強める",),
    ),
    "price-control-reform": Law(
        law_id="price-control-reform",
        name="公定価格の見直し",
        category="economy",
        summary="公定価格を実勢に近づけ、闇取りの利ざやを潰す",
        effects={"blackMarket": -10, "support": -3, "reserves": 4},
        risks=("値上げ部分は民衆の不満になる",),
    ),
    "consumer-goods-priority": Law(
        law_id="consumer-goods-priority",
        name="消費財生産の優先",
        category="economy",
        summary="重工業の割当を削り、生活消費財の生産を優先する",
        effects={"support": 12, "blackMarket": -8, "production": -4},
        risks=("重工業の計画が遅れる",),
    ),
    "machinery-import-program": Law(
        law_id="machinery-import-program",
        name="工作機械の集中輸入",
        category="economy",
        summary="外貨を投じて工作機械を一括輸入する",
        effects={"production": 10, "reserves": -10},
        risks=("外貨準備が急減する",),
    ),
    "rural-electrification": Law(
        law_id="rural-electrification",
        name="農村電化計画",
        category="economy",
        summary="集電所と配電線を農村へ延長する",
        effects={"production": 6, "support": 3},
        risks=("建設資材が他部門から奪われる",),
    ),
    "state-security-purge": Law(
        law_id="state-security-purge",
        name="国家保安省の粛清",
        category="security",
        summary="怠慢な幹部を一斉に罷免し、規律を回復する",
        effects={"discipline": 14, "support": -12, "production": 2},
        risks=("恐怖が申し立てを萎縮させる",),
    ),
    "cultural-enlightenment-decree": Law(
        law_id="cultural-enlightenment-decree",
        name="文化啓蒙令",
        category="culture",
        summary="労働学校と巡回図書館を全国に設置する",
        effects={"support": 4, "discipline": 6, "production": -2},
        risks=("勤務時間が短縮される部門がある",),
    ),
    "workers-council-reform": Law(
        law_id="workers-council-reform",
        name="労働者評議会の拡大",
        category="society",
        summary="職場評議会に生産計画への意見権を与える",
        effects={"support": 10, "discipline": -8},
        risks=("現場の異論が計画変更を招く",),
    ),
}
LAW_IDS = tuple(sorted(LAWS))


def status_text(status: str) -> str:
    try:
        return STATUS_TEXT[status]
    except KeyError as exc:
        raise LawSimError(f"unknown law simulator status: {status!r}") from exc


# --- strict parsing ---------------------------------------------------------


def _require(condition, reason):
    if not condition:
        raise LawSimError(reason)


def _json_pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, "duplicate_json_field")
        result[key] = value
    return result


def _json_nonfinite(_):
    raise LawSimError("nonfinite_json_value")


def parse_session_json(data: bytes) -> dict:
    """Strict JSON parse: duplicate keys and non-finite values are rejected."""

    _require(isinstance(data, (bytes, bytearray)), "session_payload_must_be_bytes")
    _require(len(data) <= SESSION_MAX_BYTES, "session_payload_too_large")
    try:
        parsed = json.loads(
            bytes(data).decode("utf-8", "strict"),
            object_pairs_hook=_json_pairs,
            parse_constant=_json_nonfinite,
        )
    except LawSimError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise LawSimError("invalid_session_json") from exc
    _require(isinstance(parsed, dict), "session_payload_must_be_object")
    return parsed


# --- session contract -------------------------------------------------------


@dataclass(frozen=True)
class SessionSpec:
    """A validated session: fixed opening state plus an ordered law program."""

    title: str
    republic: str
    laws: tuple


def _bounded_text(value, field):
    _require(isinstance(value, str), f"session_{field}_must_be_string")
    _require(value.strip() == value, f"session_{field}_must_not_have_padding")
    _require(0 < len(value) <= MAX_TITLE_CHARS, f"session_{field}_length_out_of_bounds")
    return value


def parse_session(data: dict) -> SessionSpec:
    """Validate a session payload; unknown or repeated laws fail closed."""

    _require(isinstance(data, dict), "session_must_be_object")
    _require(data.get("corner") == CORNER_ID, "unknown_corner_id")
    session = data.get("session")
    _require(isinstance(session, dict), "session_object_missing")
    _require(
        set(session) == {"title", "republic", "laws"},
        "session_fields_must_be_exactly_title_republic_laws",
    )
    title = _bounded_text(session["title"], "title")
    republic = _bounded_text(session["republic"], "republic")
    laws_raw = session["laws"]
    _require(isinstance(laws_raw, list), "session_laws_must_be_list")
    _require(1 <= len(laws_raw) <= MAX_SESSION_LAWS, "session_law_count_out_of_bounds")
    laws = []
    for law_id in laws_raw:
        _require(isinstance(law_id, str), "session_law_id_must_be_string")
        _require(law_id in LAWS, f"unknown_law_id:{law_id}")
        _require(law_id not in laws, f"duplicate_law_id:{law_id}")
        laws.append(law_id)
    return SessionSpec(title=title, republic=republic, laws=tuple(laws))


def load_session_fixture(root: Path | None = None) -> dict:
    """Load and validate the fixed synthetic session fixture."""

    root = root or Path(__file__).resolve().parents[2]
    path = root / "tests" / "fixtures" / "communist_law_simulator" / (
        "fixed_law_session_example.json"
    )
    if not path.is_file():
        raise FileNotFoundError(f"missing law simulator fixture: {path}")
    data = parse_session_json(path.read_bytes())
    _require(data.get("schemaVersion") == SCHEMA_VERSION, "fixture_schema_version")
    _require(data.get("corner") == CORNER_ID, "fixture_corner_id")
    _require(data.get("isSyntheticFixture") is True, "fixture_must_be_synthetic")
    scope_note = data.get("scopeNote")
    _require(
        isinstance(scope_note, str) and scope_note.strip(),
        "fixture_scope_note_required",
    )
    fixture_id = data.get("fixtureId")
    _require(isinstance(fixture_id, str) and fixture_id.strip(), "fixture_id_required")
    acceptance = data.get("nonProductionAcceptance")
    _require(isinstance(acceptance, dict), "fixture_acceptance_block_required")
    for flag in (
        "requiresReviewerApproval",
        "mustNotPresentSyntheticAsReal",
        "mustNotClaimRealState",
        "mustKeepSimulationBoundaries",
    ):
        _require(acceptance.get(flag) is True, f"fixture_acceptance_flag:{flag}")
    parse_session(data)
    return data


# --- deterministic engine ---------------------------------------------------


def _clamp(value: int) -> int:
    return max(METRIC_MIN, min(METRIC_MAX, value))


def _check_metrics(metrics: dict) -> None:
    _require(isinstance(metrics, dict), "metrics_must_be_object")
    _require(set(metrics) == set(METRICS), "metrics_keys_must_match_engine_metrics")
    for key in METRICS:
        value = metrics[key]
        _require(type(value) is int, f"metric_must_be_int:{key}")
        _require(METRIC_MIN <= value <= METRIC_MAX, f"metric_out_of_range:{key}")


def _crossing_events(before: dict, after: dict) -> tuple:
    events = []
    for metric, threshold, direction, text in WARNING_THRESHOLDS:
        old, new = before[metric], after[metric]
        if direction == "up" and old < threshold <= new:
            events.append({"metric": metric, "text": text})
        elif direction == "down" and old > threshold >= new:
            events.append({"metric": metric, "text": text})
    return tuple(events)


def _collapse_status(after: dict) -> str | None:
    if after["planProgress"] >= METRIC_MAX:
        return "plan_achieved"
    if after["support"] <= METRIC_MIN:
        return "collapse_public_unrest"
    if after["discipline"] <= METRIC_MIN:
        return "collapse_party_split"
    if after["reserves"] <= METRIC_MIN:
        return "collapse_trade_default"
    if after["blackMarket"] >= METRIC_MAX:
        return "collapse_black_market"
    return None


def apply_turn(metrics: dict, law_id: str) -> dict:
    """Apply one law to an explicit state; pure and deterministic.

    Returns a turn record.  The input ``metrics`` is not modified.
    """

    _check_metrics(metrics)
    law = LAWS.get(law_id)
    if law is None:
        raise LawSimError(f"unknown_law_id:{law_id}")
    before = dict(metrics)
    after = {}
    for key in METRICS:
        after[key] = _clamp(before[key] + int(law.effects.get(key, 0)))
    plan_gain = after["production"] // PLAN_GAIN_DIVISOR
    after["planProgress"] = _clamp(after["planProgress"] + plan_gain)
    return {
        "lawId": law.law_id,
        "lawName": law.name,
        "category": law.category,
        "deltas": {
            key: int(law.effects[key])
            for key in METRICS
            if int(law.effects.get(key, 0)) != 0
        },
        "planGain": plan_gain,
        "events": list(_crossing_events(before, after)),
        "metricsAfter": after,
        "status": _collapse_status(after) or "in_session",
    }


def simulate(spec: SessionSpec) -> dict:
    """Fold a validated session through the engine from the fixed opening."""

    metrics = dict(INITIAL_METRICS)
    turns = []
    for index, law_id in enumerate(spec.laws):
        if turns and turns[-1]["status"] in TERMINAL_STATUSES:
            raise LawSimError(
                f"session_continues_after_terminal:{turns[-1]['status']}"
            )
        record = apply_turn(metrics, law_id)
        record = {"turn": index + 1, **record}
        metrics = dict(record["metricsAfter"])
        turns.append(record)
    final_status = turns[-1]["status"]
    if final_status == "in_session":
        final_status = "plan_incomplete"
    return {
        "corner": CORNER_ID,
        "engine": ENGINE_ID,
        "title": spec.title,
        "republic": spec.republic,
        "initialMetrics": dict(INITIAL_METRICS),
        "finalMetrics": dict(metrics),
        "finalStatus": final_status,
        "turns": turns,
    }


def session_trace(data: dict) -> dict:
    """Parse a payload and simulate it in one step."""

    return simulate(parse_session(data))


def trace_signature(trace: dict) -> str:
    """Stable hash of a trace; equal sessions must produce equal signatures."""

    canonical = json.dumps(
        trace, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- overlay rendering ------------------------------------------------------


def _metric_line(metrics: dict) -> str:
    return " ".join(f"{METRIC_LABELS[key]}{metrics[key]}" for key in METRICS)


def _delta_line(deltas: dict) -> str:
    if not deltas:
        return "効果なし"
    return " ".join(
        f"{METRIC_LABELS[key]}{deltas[key]:+d}" for key in METRICS if key in deltas
    )


def render_overlay(data: dict) -> dict:
    """Render the overlay payload for one session payload."""

    trace = session_trace(data)
    return {
        "corner": CORNER_ID,
        "isSynthetic": True,
        "engine": ENGINE_ID,
        "title": trace["title"],
        "republic": trace["republic"],
        "finalStatus": trace["finalStatus"],
        "finalStatusText": status_text(trace["finalStatus"]),
        "initialMetrics": dict(trace["initialMetrics"]),
        "finalMetrics": dict(trace["finalMetrics"]),
        "turns": [
            {
                "turn": turn["turn"],
                "lawId": turn["lawId"],
                "lawName": turn["lawName"],
                "category": turn["category"],
                "deltas": dict(turn["deltas"]),
                "planGain": turn["planGain"],
                "events": list(turn["events"]),
                "metricsAfter": dict(turn["metricsAfter"]),
                "status": turn["status"],
            }
            for turn in trace["turns"]
        ],
        "warnings": [
            {"turn": turn["turn"], **event}
            for turn in trace["turns"]
            for event in turn["events"]
        ],
        "caveats": dict(CAVEATS),
    }


def progress_frames(data: dict) -> tuple:
    """The bounded start -> turns -> end frame sequence for one session."""

    overlay = render_overlay(data)
    turns = overlay["turns"]
    total = len(turns) + 2
    if total > MAX_PROGRESS_FRAMES:
        raise LawSimError("session progression exceeds the bounded frame budget")
    frames = [
        {
            "index": 0,
            "total": total,
            "kind": "start",
            "key": "start",
            "lawId": None,
            "status": "in_session",
            "finalStatus": overlay["finalStatus"],
            "heading": overlay["title"],
            "text": (
                f"{overlay['republic']}（架空・合成シミュレーション）で"
                "最高会議が開会した。初期状態: "
                f"{_metric_line(overlay['initialMetrics'])}"
            ),
            "caveats": dict(overlay["caveats"]),
        }
    ]
    for turn in turns:
        text = (
            f"{_delta_line(turn['deltas'])}"
            f"（計画 {turn['planGain']:+d}）／ {_metric_line(turn['metricsAfter'])}"
        )
        if turn["events"]:
            text = text + "／ ⚠ " + "／ ⚠ ".join(e["text"] for e in turn["events"])
        frames.append(
            {
                "index": turn["turn"],
                "total": total,
                "kind": "turn",
                "key": turn["lawId"],
                "lawId": turn["lawId"],
                "status": turn["status"],
                "finalStatus": overlay["finalStatus"],
                "heading": f"第{turn['turn']}回会議: {turn['lawName']}",
                "text": text,
            }
        )
    frames.append(
        {
            "index": total - 1,
            "total": total,
            "kind": "end",
            "key": "end",
            "lawId": None,
            "status": overlay["finalStatus"],
            "finalStatus": overlay["finalStatus"],
            "heading": overlay["title"],
            "text": (
                f"{overlay['finalStatusText']}（合成シミュレーション・非本番） "
                f"{_metric_line(overlay['finalMetrics'])}"
            ),
            "caveats": dict(overlay["caveats"]),
        }
    )
    return tuple(frames)


def status_overlay_entry(data: dict) -> dict:
    """The single status-overlay record derived from the same session payload."""

    frames = progress_frames(data)
    overlay = render_overlay(data)
    return {
        "corner": CORNER_ID,
        "title": frames[0]["heading"],
        "isSynthetic": True,
        "totalFrames": len(frames),
        "startIndex": frames[0]["index"],
        "endIndex": frames[-1]["index"],
        "startKind": frames[0]["kind"],
        "endKind": frames[-1]["kind"],
        "finalStatus": overlay["finalStatus"],
        "caveatKeys": sorted(frames[-1]["caveats"]),
    }


def validate_status(data: dict, frames=None) -> dict:
    """Re-check that the frame sequence and the status entry agree.

    ``frames`` is accepted so an integration point can re-validate the exact
    sequence it is about to show instead of trusting a re-derivation.
    """

    entry = status_overlay_entry(data)
    if frames is None:
        frames = progress_frames(data)
    else:
        frames = tuple(frames)
    if not frames:
        raise LawSimError("session progression has no frames")
    if len(frames) > MAX_PROGRESS_FRAMES:
        raise LawSimError("session progression exceeds the bounded frame budget")
    # Structural invariants run first: a mutation of ``progress_frames`` itself
    # (dropped caveats, changed kinds, ...) must fail here even though the
    # canonical re-derivation would reproduce it.
    if [frame.get("index") for frame in frames] != list(range(len(frames))):
        raise LawSimError("session frames are not a contiguous bounded sequence")
    if len(frames) != entry["totalFrames"]:
        raise LawSimError("session frames do not match the status overlay length")
    if frames[0].get("kind") != "start" or frames[-1].get("kind") != "end":
        raise LawSimError("session progression must start and end with its own frames")
    if frames[0]["index"] != entry["startIndex"] or frames[-1]["index"] != entry["endIndex"]:
        raise LawSimError("session start/end indices do not match the status overlay")
    end = frames[-1]
    if not end.get("caveats"):
        raise LawSimError("session end frame must retain the caveats")
    if sorted(end["caveats"]) != entry["caveatKeys"]:
        raise LawSimError("session caveats do not match the status overlay")
    if end.get("finalStatus") != entry["finalStatus"]:
        raise LawSimError("session final status does not match the status overlay")
    if end.get("status") != end.get("finalStatus"):
        raise LawSimError("session end frame status must equal its final status")
    # Then require an exact match against the canonical re-derivation: a
    # mutated caller sequence (swapped turn, altered text, ...) must not be
    # accepted as valid.
    canonical = progress_frames(data)
    if frames != canonical:
        raise LawSimError("session frames do not match the validated progression")
    return entry
