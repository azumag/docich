"""Controlled NetHack baseline/candidate canary experiments (P5g).

The candidate may operate only inside a dedicated canary arena managed by an
external worker.  Production NetHack state is snapshotted before every episode
and must remain byte/metadata stable.  Results are descriptive evidence only;
this module never changes production config, agent selection, or policy.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import ConfigError, GlobalConfig, load_global
from .game_switch import atomic_write_json
from .nethack_candidate_eval import CandidateManifest, load_candidate_manifest
from .nethack_candidate_shadow import NethackCandidateShadowError, _offline_gate
from .nethack_retrospective import normalize_death_signature
from .nethack_run import NethackPersistenceSettings, load_nethack_persistence_settings

PLAN_SCHEMA_VERSION = 1
WORKER_REQUEST_SCHEMA_VERSION = 1
WORKER_RESULT_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
MAX_PLAN_BYTES = 128 * 1024
MAX_WORKER_RESPONSE_BYTES = 512 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_ALLOWED_TERMINAL = frozenset({"dead", "ascended", "ended", "ended_unknown", "timeout", "error"})
_ALLOWED_ISOLATION = frozenset({"container", "vm", "namespace"})
Arm = Literal["baseline", "candidate"]


class NethackCanaryError(RuntimeError):
    """Controlled canary evidence cannot be created or trusted safely."""


@dataclass(frozen=True)
class NethackCanaryPlan:
    experiment_id: str
    candidate_manifest: str
    worker_command: str | tuple[str, ...]
    episodes_per_arm: int = 4
    min_completed_per_arm: int = 2
    episode_timeout_s: float = 900.0
    max_turns: int = 20_000
    seed_base: int | None = None
    require_seed_control: bool = False
    required_isolation_mode: str = "container"

    @property
    def worker_command_hash(self) -> str:
        raw = self.worker_command if isinstance(self.worker_command, str) else list(self.worker_command)
        return hashlib.sha256(
            json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _exact_keys(raw: dict[str, object], allowed: set[str], where: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise NethackCanaryError(f"{where} contains unknown fields: {unknown}")


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int, name: str) -> int:
    if value is None:
        return default
    if type(value) is not int or not minimum <= value <= maximum:
        raise NethackCanaryError(f"{name} must be {minimum}-{maximum}")
    return value


def _bounded_float(value: object, *, default: float, minimum: float, maximum: float, name: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NethackCanaryError(f"{name} must be numeric")
    result = float(value)
    if not minimum <= result <= maximum:
        raise NethackCanaryError(f"{name} must be {minimum}-{maximum}")
    return result


def load_canary_plan(path: Path) -> NethackCanaryPlan:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise NethackCanaryError("canary planを読めません") from exc
    if size > MAX_PLAN_BYTES:
        raise NethackCanaryError("canary plan size limit超過")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NethackCanaryError("canary plan JSONが不正です") from exc
    if not isinstance(raw, dict):
        raise NethackCanaryError("canary plan root must be object")
    _exact_keys(
        raw,
        {
            "schema_version", "experiment_id", "candidate_manifest", "worker_command",
            "episodes_per_arm", "min_completed_per_arm", "episode_timeout_s", "max_turns",
            "seed_base", "require_seed_control", "required_isolation_mode",
        },
        "canary plan",
    )
    if raw.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise NethackCanaryError("canary plan schema_versionが不正です")
    experiment_id = raw.get("experiment_id")
    if not isinstance(experiment_id, str) or _ID_RE.fullmatch(experiment_id) is None:
        raise NethackCanaryError("experiment_idが不正です")
    manifest = raw.get("candidate_manifest")
    if not isinstance(manifest, str) or not manifest.strip() or len(manifest) > 4096:
        raise NethackCanaryError("candidate_manifestが不正です")
    command_raw = raw.get("worker_command")
    command: str | tuple[str, ...]
    if isinstance(command_raw, str) and command_raw.strip() and len(command_raw) <= 4096:
        command = command_raw
    elif (
        isinstance(command_raw, list)
        and 1 <= len(command_raw) <= 32
        and all(isinstance(item, str) and item and len(item) <= 2048 for item in command_raw)
    ):
        command = tuple(command_raw)
    else:
        raise NethackCanaryError("worker_command must be non-empty string/string array")
    episodes = _bounded_int(raw.get("episodes_per_arm"), default=4, minimum=1, maximum=100, name="episodes_per_arm")
    minimum = _bounded_int(raw.get("min_completed_per_arm"), default=2, minimum=1, maximum=100, name="min_completed_per_arm")
    if minimum > episodes:
        raise NethackCanaryError("min_completed_per_arm may not exceed episodes_per_arm")
    seed_base = raw.get("seed_base")
    if seed_base is not None and (type(seed_base) is not int or seed_base < 0 or seed_base > 2**63 - 1):
        raise NethackCanaryError("seed_baseが不正です")
    require_seed = raw.get("require_seed_control", False)
    if type(require_seed) is not bool:
        raise NethackCanaryError("require_seed_control must be boolean")
    isolation = raw.get("required_isolation_mode", "container")
    if not isinstance(isolation, str) or isolation not in _ALLOWED_ISOLATION:
        raise NethackCanaryError("required_isolation_modeが不正です")
    return NethackCanaryPlan(
        experiment_id=experiment_id,
        candidate_manifest=manifest.strip(),
        worker_command=command,
        episodes_per_arm=episodes,
        min_completed_per_arm=minimum,
        episode_timeout_s=_bounded_float(raw.get("episode_timeout_s"), default=900.0, minimum=1.0, maximum=7200.0, name="episode_timeout_s"),
        max_turns=_bounded_int(raw.get("max_turns"), default=20_000, minimum=100, maximum=10_000_000, name="max_turns"),
        seed_base=seed_base,
        require_seed_control=require_seed,
        required_isolation_mode=isolation,
    )


def _resolve_path(g: GlobalConfig, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(g.repo_root) / path
    return path.resolve()


def _under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _production_live(state_dir: Path) -> bool:
    try:
        switch = json.loads((state_dir / "game_switch.json").read_text(encoding="utf-8"))
        if isinstance(switch, dict):
            active = switch.get("active")
            if switch.get("phase") == "ready" and isinstance(active, dict) and active.get("game") == "nethack":
                return True
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    root = state_dir / "nethack"
    try:
        current = json.loads((root / "current.json").read_text(encoding="utf-8"))
        run_id = current.get("run_id") if isinstance(current, dict) else None
        if isinstance(run_id, str):
            run = json.loads((root / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
            if isinstance(run, dict) and run.get("status") == "active":
                return True
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return False


def _path_fingerprint(path: Path) -> object:
    if not path.exists():
        return {"exists": False}
    try:
        if path.is_file():
            stat = path.stat()
            return {"exists": True, "kind": "file", "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        if path.is_dir():
            entries: list[tuple[str, int, int]] = []
            for item in sorted(path.iterdir(), key=lambda p: p.name):
                try:
                    stat = item.stat()
                except OSError:
                    entries.append((item.name, -1, -1))
                    continue
                entries.append((item.name, stat.st_size, stat.st_mtime_ns))
            return {"exists": True, "kind": "dir", "entries": entries}
    except OSError as exc:
        raise NethackCanaryError(f"production path fingerprint failed: {path}") from exc
    return {"exists": True, "kind": "other"}


def _production_fingerprint(settings: NethackPersistenceSettings) -> dict[str, object]:
    return {
        "save_dir": _path_fingerprint(settings.save_dir),
        "xlogfile": _path_fingerprint(settings.xlogfile),
        "dump_dir": _path_fingerprint(settings.dump_dir),
    }


def _worker_command(plan: NethackCanaryPlan) -> str | list[str]:
    return plan.worker_command if isinstance(plan.worker_command, str) else list(plan.worker_command)


def _episode_paths(root: Path, arm: Arm, index: int) -> dict[str, Path]:
    episode = root / arm / f"episode-{index:03d}"
    return {
        "episode_root": episode,
        "playground_dir": episode / "playground",
        "save_dir": episode / "playground" / "save",
        "xlogfile": episode / "playground" / "xlogfile",
        "dump_dir": episode / "playground" / "dumps",
    }


def _prepare_episode_dirs(paths: dict[str, Path]) -> None:
    for key in ("episode_root", "playground_dir", "save_dir", "dump_dir"):
        paths[key].mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(paths[key], 0o700)


def _player_name(experiment_id: str, arm: Arm, index: int) -> str:
    digest = hashlib.sha256(experiment_id.encode("utf-8")).hexdigest()[:6]
    prefix = "b" if arm == "baseline" else "c"
    return f"canary_{prefix}_{digest}_{index:03d}"[:31]


def _request(
    plan: NethackCanaryPlan,
    manifest_path: Path,
    manifest: CandidateManifest,
    root: Path,
    arm: Arm,
    index: int,
) -> tuple[dict[str, object], dict[str, Path]]:
    paths = _episode_paths(root, arm, index)
    _prepare_episode_dirs(paths)
    seed = plan.seed_base + index if plan.seed_base is not None else None
    controller: dict[str, object]
    if arm == "baseline":
        controller = {"kind": "baseline_p3b"}
    else:
        controller = {
            "kind": "candidate_strategist",
            "manifest_path": str(manifest_path),
            "candidate_id": manifest.candidate_id,
            "candidate_version": manifest.version,
            "candidate_fingerprint": manifest.fingerprint,
            "command_sha256": manifest.command_hash,
        }
    payload = {
        "schema_version": WORKER_REQUEST_SCHEMA_VERSION,
        "experiment_id": plan.experiment_id,
        "episode_id": f"{index:03d}",
        "arm": arm,
        "arena": {key: str(value) for key, value in paths.items()},
        "player_name": _player_name(plan.experiment_id, arm, index),
        "max_turns": plan.max_turns,
        "seed": seed,
        "controller": controller,
        "requirements": {
            "isolation_mode": plan.required_isolation_mode,
            "production_state_must_remain_untouched": True,
            "wizard_mode": False,
            "explore_mode": False,
        },
    }
    return payload, paths


def _run_worker(
    g: GlobalConfig,
    plan: NethackCanaryPlan,
    request: dict[str, object],
) -> dict[str, object]:
    command = _worker_command(plan)
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            text=True,
            capture_output=True,
            timeout=plan.episode_timeout_s,
            cwd=str(g.repo_root),
            shell=isinstance(command, str),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "schema_version": WORKER_RESULT_SCHEMA_VERSION,
            "worker_status": "timeout",
            "error": "worker timeout",
        }
    except OSError as exc:
        return {
            "schema_version": WORKER_RESULT_SCHEMA_VERSION,
            "worker_status": "error",
            "error": str(exc).replace("\n", " ")[:240],
        }
    if len(completed.stdout.encode("utf-8", errors="replace")) > MAX_WORKER_RESPONSE_BYTES:
        return {"schema_version": WORKER_RESULT_SCHEMA_VERSION, "worker_status": "error", "error": "worker response too large"}
    if completed.returncode != 0:
        return {
            "schema_version": WORKER_RESULT_SCHEMA_VERSION,
            "worker_status": "error",
            "error": f"worker exit {completed.returncode}: {completed.stderr.strip()[:200]}",
        }
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"schema_version": WORKER_RESULT_SCHEMA_VERSION, "worker_status": "error", "error": "worker returned invalid JSON"}
    if not isinstance(raw, dict):
        return {"schema_version": WORKER_RESULT_SCHEMA_VERSION, "worker_status": "error", "error": "worker result must be object"}
    return raw


def _validate_worker_result(
    raw: dict[str, object],
    *,
    plan: NethackCanaryPlan,
    request: dict[str, object],
    paths: dict[str, Path],
    production: NethackPersistenceSettings,
) -> dict[str, object]:
    worker_status = raw.get("worker_status")
    if worker_status in {"timeout", "error"}:
        return {
            "worker_status": worker_status,
            "terminal_status": worker_status,
            "error": str(raw.get("error", "worker failure"))[:240],
            "integrity_ok": True,
            "seed_applied": False,
        }
    allowed = {
        "schema_version", "worker_status", "episode_id", "arm", "isolation_mode", "arena",
        "player_name", "seed", "seed_applied", "controller_kind", "terminal_status", "score",
        "turns", "max_depth", "death_reason", "got_amulet", "exit_reason",
        "candidate_action_source", "production_state_touched",
    }
    _exact_keys(raw, allowed, "canary worker result")
    if raw.get("schema_version") != WORKER_RESULT_SCHEMA_VERSION or worker_status != "completed":
        raise NethackCanaryError("canary worker result schema/status invalid")
    if raw.get("episode_id") != request.get("episode_id") or raw.get("arm") != request.get("arm"):
        raise NethackCanaryError("canary worker result episode identity mismatch")
    if raw.get("isolation_mode") != plan.required_isolation_mode:
        raise NethackCanaryError("canary worker isolation mode mismatch")
    if raw.get("player_name") != request.get("player_name"):
        raise NethackCanaryError("canary player identity mismatch")
    if raw.get("production_state_touched") is not False:
        raise NethackCanaryError("canary worker reports production state access")
    terminal = raw.get("terminal_status")
    if not isinstance(terminal, str) or terminal not in _ALLOWED_TERMINAL:
        raise NethackCanaryError("canary terminal_status invalid")
    controller_expected = "baseline_p3b" if request.get("arm") == "baseline" else "candidate_strategist"
    if raw.get("controller_kind") != controller_expected:
        raise NethackCanaryError("canary controller_kind mismatch")
    action_source = raw.get("candidate_action_source")
    if request.get("arm") == "baseline":
        if action_source not in {None, "baseline_p3b"}:
            raise NethackCanaryError("baseline canary used unexpected action source")
    elif action_source != "candidate_strategist":
        raise NethackCanaryError("candidate canary did not use candidate strategist action source")
    arena = raw.get("arena")
    if not isinstance(arena, dict):
        raise NethackCanaryError("canary arena attestation missing")
    expected_arena = {key: str(value) for key, value in paths.items()}
    if arena != expected_arena:
        raise NethackCanaryError("canary arena attestation mismatch")
    production_paths = (production.save_dir.resolve(), production.xlogfile.resolve(), production.dump_dir.resolve())
    episode_root = paths["episode_root"].resolve()
    for path in paths.values():
        resolved = path.resolve()
        if not _under(resolved, episode_root) and resolved != episode_root:
            raise NethackCanaryError("canary path escapes episode root")
        for prod in production_paths:
            if resolved == prod or _under(resolved, prod) or _under(prod, resolved):
                raise NethackCanaryError("canary path overlaps production NetHack path")
    seed_applied = raw.get("seed_applied")
    if type(seed_applied) is not bool:
        raise NethackCanaryError("seed_applied must be boolean")
    requested_seed = request.get("seed")
    if requested_seed is not None and raw.get("seed") != requested_seed:
        raise NethackCanaryError("canary seed attestation mismatch")
    if plan.require_seed_control and not seed_applied:
        raise NethackCanaryError("worker did not apply required deterministic seed")
    for key in ("score", "turns", "max_depth"):
        value = raw.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise NethackCanaryError(f"canary {key} invalid")
    death = raw.get("death_reason")
    if death is not None and (not isinstance(death, str) or len(death) > 1000):
        raise NethackCanaryError("canary death_reason invalid")
    if type(raw.get("got_amulet")) is not bool:
        raise NethackCanaryError("canary got_amulet must be boolean")
    sanitized = dict(raw)
    sanitized["arena"] = {
        key: str(Path(value).resolve().relative_to(episode_root.parent.parent.resolve()))
        for key, value in arena.items()
    }
    sanitized["integrity_ok"] = True
    return sanitized


def _arm_summary(results: list[dict[str, object]]) -> dict[str, object]:
    terminal = [item for item in results if item.get("worker_status") == "completed"]
    completed = [item for item in terminal if item.get("terminal_status") in {"dead", "ascended", "ended", "ended_unknown"}]
    scores = [int(item["score"]) for item in completed if type(item.get("score")) is int]
    turns = [int(item["turns"]) for item in completed if type(item.get("turns")) is int]
    depths = [int(item["max_depth"]) for item in completed if type(item.get("max_depth")) is int]
    deaths = [item for item in completed if item.get("terminal_status") == "dead"]
    signatures: dict[str, int] = {}
    for item in deaths:
        signature = normalize_death_signature(item.get("death_reason"))
        if signature is not None:
            signatures[signature] = signatures.get(signature, 0) + 1
    def med(values: list[int]) -> float | None:
        return float(statistics.median(values)) if values else None
    return {
        "episodes_attempted": len(results),
        "worker_completed": len(terminal),
        "terminal_completed": len(completed),
        "dead": len(deaths),
        "ascended": sum(1 for item in completed if item.get("terminal_status") == "ascended"),
        "ended": sum(1 for item in completed if item.get("terminal_status") in {"ended", "ended_unknown"}),
        "timeouts": sum(1 for item in results if item.get("worker_status") == "timeout"),
        "errors": sum(1 for item in results if item.get("worker_status") == "error"),
        "got_amulet": sum(1 for item in completed if item.get("got_amulet") is True),
        "death_rate": (len(deaths) / len(completed)) if completed else None,
        "median_score": med(scores),
        "median_turns": med(turns),
        "median_max_depth": med(depths),
        "death_signatures": dict(sorted(signatures.items())),
        "seed_applied_count": sum(1 for item in terminal if item.get("seed_applied") is True),
    }


def _difference(candidate: object, baseline: object) -> float | None:
    if isinstance(candidate, bool) or isinstance(baseline, bool):
        return None
    if not isinstance(candidate, (int, float)) or not isinstance(baseline, (int, float)):
        return None
    return float(candidate) - float(baseline)


def run_canary(
    g: GlobalConfig,
    plan: NethackCanaryPlan,
    *,
    plan_path: Path | None = None,
    now: dt.datetime | None = None,
) -> dict[str, object]:
    timestamp = now or dt.datetime.now(dt.timezone.utc)
    if timestamp.tzinfo is None:
        raise NethackCanaryError("now must be timezone-aware")
    if _production_live(Path(g.state_dir)):
        raise NethackCanaryError("production NetHack is active; canary is blocked")
    persistence = load_nethack_persistence_settings(g)
    if persistence is None:
        raise NethackCanaryError("production NetHack persistence settings are required")
    manifest_path = _resolve_path(g, plan.candidate_manifest)
    manifest = load_candidate_manifest(manifest_path)
    suite_id, offline_report = _offline_gate(g, manifest)
    if offline_report.get("candidate_safety_contract_passed") is not True:
        raise NethackCanaryError("candidate did not pass offline safety gate")

    root = (Path(g.state_dir) / "nethack" / "canary" / plan.experiment_id).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    production_paths = (persistence.save_dir.resolve(), persistence.xlogfile.resolve(), persistence.dump_dir.resolve())
    for prod in production_paths:
        if root == prod or _under(root, prod) or _under(prod, root):
            raise NethackCanaryError("canary root overlaps production NetHack path")

    before_experiment = _production_fingerprint(persistence)
    results: dict[Arm, list[dict[str, object]]] = {"baseline": [], "candidate": []}
    safety_violations: list[str] = []
    aborted = False
    for index in range(plan.episodes_per_arm):
        order: tuple[Arm, Arm] = ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
        for arm in order:
            if aborted:
                break
            request, paths = _request(plan, manifest_path, manifest, root, arm, index)
            before = _production_fingerprint(persistence)
            raw = _run_worker(g, plan, request)
            after = _production_fingerprint(persistence)
            if before != after:
                safety_violations.append(f"production_fingerprint_changed:{arm}:{index:03d}")
                aborted = True
                break
            try:
                result = _validate_worker_result(
                    raw,
                    plan=plan,
                    request=request,
                    paths=paths,
                    production=persistence,
                )
            except NethackCanaryError as exc:
                result = {
                    "worker_status": "error",
                    "terminal_status": "error",
                    "error": str(exc)[:240],
                    "integrity_ok": False,
                    "seed_applied": False,
                }
                safety_violations.append(f"worker_integrity:{arm}:{index:03d}")
                aborted = True
            result["episode_id"] = f"{index:03d}"
            result["arm"] = arm
            results[arm].append(result)
        if aborted:
            break

    after_experiment = _production_fingerprint(persistence)
    if before_experiment != after_experiment and not safety_violations:
        safety_violations.append("production_fingerprint_changed:experiment")
        aborted = True

    baseline = _arm_summary(results["baseline"])
    candidate = _arm_summary(results["candidate"])
    enough = (
        baseline["terminal_completed"] >= plan.min_completed_per_arm
        and candidate["terminal_completed"] >= plan.min_completed_per_arm
    )
    seed_pair_count = 0
    for index in range(plan.episodes_per_arm):
        b = next((item for item in results["baseline"] if item.get("episode_id") == f"{index:03d}"), None)
        c = next((item for item in results["candidate"] if item.get("episode_id") == f"{index:03d}"), None)
        if b and c and b.get("seed_applied") is True and c.get("seed_applied") is True:
            seed_pair_count += 1

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "experiment_id": plan.experiment_id,
        "evaluated_at": timestamp.isoformat(),
        "candidate_id": manifest.candidate_id,
        "candidate_version": manifest.version,
        "candidate_fingerprint": manifest.fingerprint,
        "candidate_command_sha256": manifest.command_hash,
        "worker_command_sha256": plan.worker_command_hash,
        "suite_id": suite_id,
        "required_isolation_mode": plan.required_isolation_mode,
        "episodes_per_arm_planned": plan.episodes_per_arm,
        "min_completed_per_arm": plan.min_completed_per_arm,
        "max_turns": plan.max_turns,
        "seed_control_required": plan.require_seed_control,
        "paired_seed_count": seed_pair_count,
        "aborted": aborted,
        "safety_integrity_passed": not safety_violations,
        "safety_violations": safety_violations,
        "baseline": baseline,
        "candidate": candidate,
        "descriptive_differences": {
            "death_rate_candidate_minus_baseline": _difference(candidate.get("death_rate"), baseline.get("death_rate")),
            "median_score_candidate_minus_baseline": _difference(candidate.get("median_score"), baseline.get("median_score")),
            "median_turns_candidate_minus_baseline": _difference(candidate.get("median_turns"), baseline.get("median_turns")),
            "median_max_depth_candidate_minus_baseline": _difference(candidate.get("median_max_depth"), baseline.get("median_max_depth")),
        },
        "performance_evidence_available": bool(enough and not safety_violations),
        "comparison_is_descriptive_only": True,
        "statistical_significance_assessed": False,
        "eligible_for_promotion_review": False,
        "automatic_promotion": False,
        "policy_effect": "none",
        "production_config_changed": False,
        "results": results,
    }
    atomic_write_json(root / "report.json", report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-nethack-canary")
    parser.add_argument("--config", metavar="PATH")
    parser.add_argument("--plan", required=True, metavar="PATH")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        g = load_global(_repo_root(), Path(args.config) if args.config else None)
        plan_path = Path(args.plan)
        plan = load_canary_plan(plan_path)
        report = run_canary(g, plan, plan_path=plan_path)
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        return 0 if report.get("safety_integrity_passed") is True else 3
    except (ConfigError, NethackCandidateShadowError, NethackCanaryError, ValueError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
