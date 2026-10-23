"""Bounded, auditable scenario generation for the participatory TRPG corner.

Untrusted model output never becomes game state directly. Every candidate is
normalized by :func:`trpg_corner.validate_scenario` and then checked as a
finite scene graph. Generation is staged by the corner manager *before* it
asks GameSwitch to disturb the currently displayed runtime; materialization
only consumes that immutable staged scenario.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import tempfile
from typing import Any, Mapping

from . import trpg_corner as core
from .ai_generate import AiError, run_prompt

GENERATION_LABEL = "RADIO:trpg-scenario"
GENERATION_TIMEOUT_S = 15
GENERATION_OVERALL_TIMEOUT_S = 20.0
MAX_PATH_SCENES = 4
SCENE_SECONDS = 3 + core.MAX_VOTE_WINDOW_DEFAULT + 3
REVEAL_SECONDS = 10
RESTORE_RESERVE_SECONDS = 30
HISTORY_FILE = "trpg_scenario_history.jsonl"
HISTORY_LOCK = ".trpg_scenario_history.lock"
HISTORY_LIMIT = 100
STAGE_DIR = "staged_scenarios"
MAX_STAGE_FILES = 32
PROGRAM_STATE_FILENAME = "trpg_corner_state.json"
MAX_GENERATED_OUTPUT_BYTES = core.MAX_BYTES
MAX_STAGE_BYTES = core.MAX_BYTES + 64 * 1024
MAX_HISTORY_BYTES = 2 * 1024 * 1024


def generation_agents(settings: Mapping[str, Any], env: Mapping[str, str] | None = None) -> str:
    """Resolve the existing operator model chain without inventing providers."""

    env = os.environ if env is None else env
    if "generation_agents" in settings:
        value = settings.get("generation_agents")
        if not isinstance(value, str):
            raise core.TrpgCornerError("trpg generation_agents must be a string")
        return value.strip()
    return (env.get("TRPG_GENERATION_AGENTS", "").strip()
            or env.get("RADIO_AGENTS", "").strip()
            or env.get("AI_COMMON_AGENTS", "").strip())


def _seed(request_id: str) -> str:
    return hashlib.sha256(request_id.encode("utf-8")).hexdigest()


def _canonical_bytes(story: Mapping[str, Any]) -> bytes:
    return json.dumps(story, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def scenario_hash(story: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(story)).hexdigest()


def _reject_controls(value: Any) -> None:
    if isinstance(value, str):
        if any(ord(ch) < 0x20 and ch not in "\n\t" for ch in value):
            raise core.TrpgCornerError("generated trpg scenario contains control characters")
    elif isinstance(value, list):
        for item in value:
            _reject_controls(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_controls(key)
            _reject_controls(item)


def validate_generated_scenario(raw: Any, *, duration_minutes: int = 8) -> dict:
    """Validate shape plus graph termination for an untrusted generated story."""

    story = core.validate_scenario(raw)
    if story["duration_minutes"] != duration_minutes:
        raise core.TrpgCornerError("generated trpg duration does not match the slot")
    if story.get("climax") is None:
        raise core.TrpgCornerError("generated trpg scenario requires a climax")
    _reject_controls(story)

    by_id = {scene["id"]: scene for scene in story["scenes"]}
    reachable: set[str] = set()
    visiting: set[str] = set()
    memo: dict[str, int] = {}

    def visit(scene_id: str) -> int:
        if scene_id in visiting:
            raise core.TrpgCornerError("generated trpg scenario contains a scene cycle")
        if scene_id in memo:
            return memo[scene_id]
        visiting.add(scene_id)
        reachable.add(scene_id)
        child_depths = []
        for target in by_id[scene_id]["next"].values():
            if target is not None:
                child_depths.append(visit(target))
        visiting.remove(scene_id)
        depth = 1 + (max(child_depths) if child_depths else 0)
        memo[scene_id] = depth
        return depth

    longest = visit(story["first_scene"])
    if reachable != set(by_id):
        raise core.TrpgCornerError("generated trpg scenario contains unreachable scenes")
    if longest > MAX_PATH_SCENES:
        raise core.TrpgCornerError("generated trpg scenario path is too long")

    climax_seconds = int(story["climax"]["window_seconds"])
    estimated = longest * SCENE_SECONDS + climax_seconds + REVEAL_SECONDS
    if estimated > duration_minutes * 60 - RESTORE_RESERVE_SECONDS:
        raise core.TrpgCornerError("generated trpg scenario cannot finish inside the slot budget")
    return story


def deterministic_scenario(seed: str, *, duration_minutes: int = 8) -> dict:
    """Create a varied but fully deterministic fallback from a request seed."""

    rng = random.Random(int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16))
    cases = [
        ("閉鎖研究棟の停電", ["主任研究員", "警備責任者", "設備技師"], "非常電源の時刻記録"),
        ("夜行列車の消えた荷物", ["車掌", "旅行作家", "行商人"], "連結部の通過記録"),
        ("山荘の壊れた無線", ["管理人", "登山家", "気象観測員"], "予備電池の交換記録"),
        ("博物館の消えた展示品", ["学芸員", "修復師", "夜間警備員"], "収蔵庫の温度ログ"),
    ]
    title, suspects, trick = rng.choice(cases)
    answer = rng.choice(suspects)
    scene_templates = [
        ("s1", f"{title}。現場には複数の説明が食い違う記録が残っている。最初に何を確かめる？",
         ["時刻の記録を照合する", "現場の痕跡を確認する"], {"1": "s2", "2": "s2"}),
        ("s2", f"調査を進めると「{trick}」が重要だと分かった。次はどちらから詰める？",
         ["関係者の証言を比較する", "物証と移動経路を照合する"], {"1": "s3", "2": "s3"}),
        ("s3", "証言と記録を並べると、ひとつだけ両立しない説明がある。最後の確認を選んで推理へ進む。",
         ["矛盾した時刻を確認する", "矛盾した行動経路を確認する"], {"1": None, "2": None}),
    ]
    raw = {
        "id": f"auto-{seed[:16]}",
        "title": title,
        "genre": "both",
        "duration_minutes": duration_minutes,
        "scenes": [
            {"id": sid, "text": text, "choices": choices, "next": nxt}
            for sid, text, choices, nxt in scene_templates
        ],
        "climax": {
            "suspects": suspects,
            "answer": answer,
            "trick": trick,
            "window_seconds": core.MAX_ACCUSE_WINDOW_DEFAULT,
        },
    }
    return validate_generated_scenario(raw, duration_minutes=duration_minutes)


def preview_scenario(settings: Mapping[str, Any], *, duration_minutes: int = 8,
                     env: Mapping[str, str] | None = None) -> dict:
    """Cheap eligibility/preflight proof; never invokes a model."""

    generation_agents(settings, env)
    return deterministic_scenario("eligibility-preview", duration_minutes=duration_minutes)


def _prompt(seed: str, duration_minutes: int) -> str:
    return f"""あなたは配信向け参加型TRPG/短編ミステリーのシナリオ生成器です。
次の制約をすべて満たすJSONオブジェクトだけを返してください。Markdownや説明文は禁止です。

- seed: {seed}
- duration_minutes: {duration_minutes}
- 日本語。実在人物・個人情報・差別扇動・露骨な性表現・過度な残虐表現は使わない。
- id, title, genre, duration_minutes, scenes, climax を持つ。
- genre は \"trpg\", \"murder_mystery\", \"both\" のいずれか。
- scenes は2〜4個。各sceneは id, text, choices, next を持つ。
- choices は2〜4個。nextは選択肢番号の文字列をキーに、後続scene idまたはnullを値にする。
- scene graphは循環禁止、全sceneが先頭sceneから到達可能、全分岐が4scene以内に終了する。
- climaxは suspects(2〜6個), answer, trick, window_seconds を持つ。answerはsuspectsの一つ。
- window_secondsは60〜120。
- 視聴者が選択肢投票と最後の推理を楽しめるが、正解を途中のscene textで明言しない。
- duration_minutes は必ず {duration_minutes}。
"""


def _private_dir(g) -> Path:
    path = Path(g.state_dir) / "trpg"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _stage_dir(g) -> Path:
    path = _private_dir(g) / STAGE_DIR
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _stage_path(g, request_id: str) -> Path:
    if not isinstance(request_id, str) or not request_id or any(ch not in "0123456789abcdef-" for ch in request_id):
        raise core.TrpgCornerError("trpg scenario request id is invalid")
    return _stage_dir(g) / f"{request_id}.json"


def _atomic_private_bytes(path: Path, encoded: bytes, *, max_bytes: int) -> None:
    if len(encoded) > max_bytes:
        raise core.TrpgCornerError("private trpg scenario data is too large")
    fd, tmp_name = tempfile.mkstemp(prefix=".trpg-", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _atomic_private_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    _atomic_private_bytes(path, encoded, max_bytes=MAX_STAGE_BYTES)


def _record_history(g, metadata: Mapping[str, Any], story: Mapping[str, Any]) -> None:
    directory = _private_dir(g)
    history = directory / HISTORY_FILE
    lock_path = directory / HISTORY_LOCK
    row = {**metadata, "scenario": story}
    encoded_row = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        with lock_path.open("a", encoding="utf-8") as lock:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            lines = []
            if history.exists():
                if history.stat().st_size > MAX_HISTORY_BYTES:
                    raise core.TrpgCornerError("trpg scenario audit is too large")
                lines = history.read_text(encoding="utf-8").splitlines()[-(HISTORY_LIMIT - 1):]
            lines.append(encoded_row)
            encoded = ("\n".join(lines) + "\n").encode("utf-8")
            _atomic_private_bytes(history, encoded, max_bytes=MAX_HISTORY_BYTES)
    except OSError as exc:
        raise core.TrpgCornerError("trpg scenario audit write failed") from exc


def _gc_stages(g, *, keep_request_id: str) -> None:
    files = sorted(_stage_dir(g).glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    kept = 0
    for path in files:
        if path.name == f"{keep_request_id}.json" or kept < MAX_STAGE_FILES - 1:
            kept += 1
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _load_stage(g, request_id: str, *, duration_minutes: int) -> tuple[dict, dict]:
    path = _stage_path(g, request_id)
    if path.stat().st_size > MAX_STAGE_BYTES:
        raise core.TrpgCornerError("staged trpg scenario is too large")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(raw, dict) or raw.get("schema") != 1
            or raw.get("request_id") != request_id
            or raw.get("duration_minutes") != duration_minutes
            or not isinstance(raw.get("metadata"), dict)):
        raise core.TrpgCornerError("staged trpg scenario metadata is invalid")
    story = validate_generated_scenario(raw.get("scenario"), duration_minutes=duration_minutes)
    metadata = dict(raw["metadata"])
    if metadata.get("scenario_sha256") != scenario_hash(story):
        raise core.TrpgCornerError("staged trpg scenario hash is invalid")
    return story, metadata


def generate_scenario(g, request_id: str, settings: Mapping[str, Any], *, duration_minutes: int = 8,
                      generator=run_prompt, env: Mapping[str, str] | None = None) -> tuple[dict, dict]:
    """Generate one scenario without touching GameSwitch or a runtime."""

    seed = _seed(request_id)
    agents = generation_agents(settings, env)
    story = None
    source = "procedural-fallback"
    status = "fallback"
    failure = "no-agents" if not agents else ""

    if agents:
        effective_env = dict(os.environ if env is None else env)
        effective_env["DOCICH_ALLOW_REAL_AI"] = "1"
        try:
            result = generator(
                g,
                label=GENERATION_LABEL,
                agents=agents,
                prompt_text=_prompt(seed, duration_minutes),
                timeout=GENERATION_TIMEOUT_S,
                timeout_sec=GENERATION_OVERALL_TIMEOUT_S,
                env=effective_env,
            )
            if not result.ok:
                raise AiError(result.failure_kind or result.detail or "trpg scenario generation failed")
            if (not isinstance(result.output, str)
                    or len(result.output.encode("utf-8")) > MAX_GENERATED_OUTPUT_BYTES):
                raise core.TrpgCornerError("generated trpg scenario output is too large")
            candidate = json.loads(result.output)
            story = validate_generated_scenario(candidate, duration_minutes=duration_minutes)
            source = result.last_agent or "ai"
            status = "generated"
            failure = ""
        except (AiError, ValueError, TypeError, RuntimeError, OSError) as exc:
            failure = type(exc).__name__

    if story is None:
        story = deterministic_scenario(seed, duration_minutes=duration_minutes)

    metadata = {
        "schema": 1,
        "seed": seed,
        "request_id": request_id,
        "source": source,
        "status": status,
        "failure": failure,
        "scenario_sha256": scenario_hash(story),
    }
    return story, metadata


def ensure_staged_scenario(g, request_id: str, settings: Mapping[str, Any], *, duration_minutes: int = 8,
                           generator=run_prompt, env: Mapping[str, str] | None = None) -> tuple[dict, dict]:
    """Idempotently stage a scenario before the GameSwitch request starts."""

    try:
        return _load_stage(g, request_id, duration_minutes=duration_minutes)
    except FileNotFoundError:
        pass
    story, metadata = generate_scenario(
        g, request_id, settings, duration_minutes=duration_minutes,
        generator=generator, env=env,
    )
    _record_history(g, metadata, story)
    stage = {
        "schema": 1,
        "request_id": request_id,
        "duration_minutes": duration_minutes,
        "metadata": metadata,
        "scenario": story,
    }
    _atomic_private_json(_stage_path(g, request_id), stage)
    _gc_stages(g, keep_request_id=request_id)
    return story, metadata


def staged_scenario_for_current_request(g, *, duration_minutes: int = 8) -> tuple[dict, dict]:
    """Load the manager-staged scenario for the currently starting request."""

    state_path = Path(g.state_dir) / PROGRAM_STATE_FILENAME
    if state_path.stat().st_size > MAX_STAGE_BYTES:
        raise core.TrpgCornerError("trpg program state is too large")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    request_id = state.get("rotation_request_id") if isinstance(state, dict) else None
    if (not isinstance(request_id, str)
            or state.get("status") not in {"starting", "active", "restoring"}
            or state.get("duration_minutes") != duration_minutes):
        raise core.TrpgCornerError("trpg program has no staged scenario owner")
    return _load_stage(g, request_id, duration_minutes=duration_minutes)
