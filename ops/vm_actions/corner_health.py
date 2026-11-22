#!/usr/bin/env python3
"""File deduplicated Issues for abnormal corner exits and rotation wedges.

This module is the read-only companion of the corner rotation ledger. It
consumes the already-sanitized diagnostics envelope produced by the owner-only
gateway and keeps two fixed Issues in sync:

* ``[corner] 不正終了アラート`` -- a corner state still records ``failed``, or
  the rotation audit recorded a recent failure it had to release.
* ``[corner rotation] ローテーション異常アラート`` -- the rotation is latched,
  its wait is overdue, its timer is stopped, a released owner cannot be
  retired, a reservation has no owner, or the tick stopped updating.

The module never starts, stops, restarts or mutates a corner and never writes
the production state. Only fixed enums, bounded ids, ages and counts are
published; raw state bodies, exception text, paths, seeds and credentials are
never read out. An unavailable or unreadable diagnostics snapshot leaves both
Issues untouched (fail closed) so a broken probe cannot hide an open alert.

Exit codes: 0 synced or nothing to do, 2 diagnostics/query unavailable (state
untouched), 3 a bounded Issue mutation failed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Callable, Sequence

ABNORMAL_TITLE = "[corner] 不正終了アラート"
ROTATION_TITLE = "[corner rotation] ローテーション異常アラート"
MONITOR_WORKFLOW = "vm-corner-monitor.yml"
DEFAULT_SERVER = "https://github.com"

EXIT_OK = 0
EXIT_UNAVAILABLE = 2
EXIT_PUBLISH_FAILED = 3

Runner = Callable[[Sequence[str]], str]

# Fixed corner state files the collector projects under ``corners``. Keep in
# sync with collect_diagnostics.CORNER_STATE_FILES; unknown entries are never
# published from diagnostics.
CORNER_STATE_NAMES = (
    "retro_corner",
    "paper_corner",
    "paper_corner_manual",
    "nethack_corner",
    "nethack_corner_manual",
    "tsuitate_corner",
    "yoso_corner",
    "doushi_corner",
    "mystery_corner",
    "lawsim_corner",
    "soren91_corner",
    "weather_corner",
)

# Fixed latch taxonomy (docich.corner_rotation.ERROR_KINDS /
# collect_diagnostics.ROTATION_ERROR_KINDS). Unknown values project as
# ``unknown`` instead of free text.
ERROR_KINDS = frozenset({
    "adapter-state",
    "adapter-timestamp",
    "catalog-mismatch",
    "execution-error",
    "execution-unverified",
    "invalid-state",
    "unexpected",
})
FAILED_CORNER_RESOLUTIONS = frozenset({"released-unverified", "recovery-required"})
FAILED_CORNER_REASONS = frozenset({
    "failed-corner-slot-released",
    "execution-or-state-unverified",
    "base-game-unrestored",
    "restore-recovery-exhausted",
    "terminal-owner-absent",
    "stale-owner-admin-released",
})
PENDING_PHASES = frozenset({"selected", "dispatched", "unknown"})
PENDING_OWNERS = frozenset({"absent", "none", "unknown", *CORNER_STATE_NAMES})
ROTATION_STATUSES = frozenset({"ready", "waiting", "running", "recovery_required"})

# Detection thresholds. They are intentionally conservative: the monitor runs
# every ten minutes and a transient failure that the rotation settles in one
# tick must not page the owner.
FAILED_GRACE_SECONDS = 300.0
RECENT_RELEASED_FAILURE_SECONDS = 86400.0
FAILED_STATE_MAX_AGE_SECONDS = 7 * 86400.0
PENDING_UNOWNED_SECONDS = 900.0
MANUAL_PENDING_UNOWNED_SECONDS = 6 * 3600.0
STALE_TICK_SECONDS = 2 * 3600.0
TIMER_INACTIVE_GRACE_SECONDS = 600.0

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class GhError(RuntimeError):
    """A bounded ``gh`` invocation failed."""


class QueryError(RuntimeError):
    """An Issue lookup failed; no alert state may change."""


class PublishError(RuntimeError):
    """An Issue mutation failed."""


def _number(value):
    """Return a finite non-bool number, else ``None``."""
    if isinstance(value, bool) or type(value) not in (int, float):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _epoch(value):
    """Epoch seconds from a projected numeric or ISO-8601 timestamp.

    ``collect_diagnostics._bounded_time`` may pass through a bounded ISO string
    for producer timestamps. Only a parseable value is used; the original text
    is never published.
    """
    number = _number(value)
    if number is not None:
        return number
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def _age(seconds):
    """A non-negative whole-second age.

    The collector uses ``-1`` for an unknown age (for example
    ``pending_age_sec`` when the reservation timestamp is unreadable); that
    must stay unknown, not become a fresh zero-second age.
    """
    value = _number(seconds)
    if value is None or value < 0:
        return None
    return int(value)


def _fixed(value, allowed):
    return value if isinstance(value, str) and value in allowed else "unknown"


def _safe_id(value):
    return value if isinstance(value, str) and _SAFE_ID.fullmatch(value) else None


def _bool(value):
    return value if isinstance(value, bool) else None


def _iso_utc(epoch):
    try:
        return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (OverflowError, OSError, ValueError):
        return "unknown"


def _age_text(seconds):
    if seconds is None:
        return "不明"
    if seconds < 3600:
        return f"{seconds // 60}分 ({seconds}s)"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}時間 ({seconds}s)"
    return f"{seconds / 86400:.1f}日 ({seconds}s)"


def _unavailable(reason):
    return {
        "available": False,
        "reason": reason,
        "abnormal": [],
        "rotation": [],
        "now": None,
    }


def assess(document, *, now=None):
    """Assess one diagnostics document.

    ``document`` may be the gateway envelope (``{"diagnostics": {...}}``) or
    the inner diagnostics object. The result is sanitized: every string is a
    fixed enum, a safe corner id, or a numeric age.
    """
    if not isinstance(document, dict):
        return _unavailable("diagnostics_not_object")
    diagnostics = document.get("diagnostics")
    if not isinstance(diagnostics, dict):
        diagnostics = document
    corners = diagnostics.get("corners")
    if not isinstance(corners, dict):
        return _unavailable("corners_missing")
    rotation = corners.get("corner_rotation")
    if not isinstance(rotation, dict):
        return _unavailable("corner_rotation_missing")
    if rotation.get("present") is not True:
        return _unavailable("corner_rotation_absent")

    if now is None:
        meta = diagnostics.get("meta")
        generated = _epoch(meta.get("generated_at")) if isinstance(meta, dict) else None
        now = generated if generated is not None else time.time()
    now = float(now)

    return {
        "available": True,
        "reason": "ok",
        "abnormal": _abnormal_rows(corners, rotation, now),
        "rotation": _rotation_rows(corners, rotation, now),
        "now": now,
    }


def _abnormal_rows(corners, rotation, now):
    rows = {}
    for name in CORNER_STATE_NAMES:
        entry = corners.get(name)
        if not isinstance(entry, dict) or entry.get("readable") is not True:
            continue
        if entry.get("status") != "failed":
            continue
        at = _epoch(entry.get("completed_at"))
        if at is None:
            at = _epoch(entry.get("started_at"))
        age = _age(now - at) if at is not None and at <= now else None
        if age is not None and age < FAILED_GRACE_SECONDS:
            continue
        recovery_required = _bool(entry.get("recovery_required"))
        if (age is not None and age > FAILED_STATE_MAX_AGE_SECONDS
                and recovery_required is not True):
            continue
        rows[name] = {
            "corner": name,
            "game": _safe_id(entry.get("game")),
            "source": "state",
            "age_sec": age,
            "recovery_required": recovery_required,
            "resolution": "recovery-required" if recovery_required else None,
            "reason_code": None,
            "error_kind": None,
        }

    audit = rotation.get("failed_corners")
    if isinstance(audit, list):
        for row in audit:
            if not isinstance(row, dict):
                continue
            corner = _safe_id(row.get("corner"))
            if corner is None:
                continue
            resolution = _fixed(row.get("resolution"), FAILED_CORNER_RESOLUTIONS)
            at = _epoch(row.get("at"))
            if at is None or at > now:
                continue
            age = _age(now - at)
            if age < FAILED_GRACE_SECONDS:
                continue
            max_age = (
                RECENT_RELEASED_FAILURE_SECONDS
                if resolution == "released-unverified"
                else FAILED_STATE_MAX_AGE_SECONDS
            )
            if age > max_age:
                continue
            current = rows.get(corner)
            if current is None:
                current = {
                    "corner": corner,
                    "game": None,
                    "source": "rotation-audit",
                    "age_sec": age,
                    "recovery_required": resolution == "recovery-required",
                    "resolution": resolution,
                    "reason_code": None,
                    "error_kind": None,
                }
                rows[corner] = current
            if current.get("age_sec") is None or age < current["age_sec"]:
                current["age_sec"] = age
            if current.get("resolution") is None:
                current["resolution"] = resolution
            current["reason_code"] = _fixed(row.get("reason_code"), FAILED_CORNER_REASONS)
            current["error_kind"] = _fixed(row.get("error_kind"), ERROR_KINDS)

    return sorted(
        rows.values(),
        key=lambda row: (row["age_sec"] is None, row["age_sec"] or 0, row["corner"]),
    )


def _rotation_rows(corners, rotation, now):
    rows = []
    if rotation.get("readable") is not True:
        return [{"code": "ledger_unreadable"}]

    raw_status = rotation.get("status")
    valid_status = isinstance(raw_status, str) and raw_status in ROTATION_STATUSES
    status = raw_status if valid_status else "unknown"
    pending = rotation.get("pending") is True
    manual_pending = rotation.get("manual_pending") is True
    last_seen = _epoch(rotation.get("last_seen_at"))
    last_seen_age = _age(now - last_seen) if last_seen is not None and last_seen <= now else None

    invalid_state = (
        not valid_status
        or (status == "running" and not pending and not manual_pending)
        or (pending and manual_pending)
    )
    if invalid_state:
        rows.append({"code": "invalid_state", "status": status})

    if status == "recovery_required":
        rows.append({
            "code": "latched",
            "status": status,
            "error_kind": _fixed(rotation.get("error_kind"), ERROR_KINDS),
            "pending_corner": _safe_id(rotation.get("pending_corner")),
            "pending_phase": _fixed(rotation.get("pending_phase"), PENDING_PHASES),
            "pending_age_sec": _age(rotation.get("pending_age_sec"))
            if _number(rotation.get("pending_age_sec")) is not None else None,
            "pending_owner": _fixed(rotation.get("pending_owner"), PENDING_OWNERS),
        })

    excluded = rotation.get("excluded")
    unretired = []
    if isinstance(excluded, dict):
        for corner, reason in sorted(excluded.items()):
            if reason != "released-owner-unretired":
                continue
            safe = _safe_id(corner)
            if safe is not None:
                unretired.append(safe)
    if unretired:
        rows.append({"code": "released_owner_unretired", "corners": unretired[:8]})

    timer = corners.get("corner_rotation_timer")
    if isinstance(timer, dict):
        active = _bool(timer.get("active"))
        enabled = _bool(timer.get("enabled"))
        if (active is False and enabled is not False
                and (last_seen_age is None
                     or last_seen_age > TIMER_INACTIVE_GRACE_SECONDS)):
            rows.append({
                "code": "timer_inactive",
                "timer_active": active,
                "timer_enabled": enabled,
                "last_seen_age_sec": last_seen_age,
            })

    if (rotation.get("waiting_overdue") is True
            and not pending and not manual_pending):
        rows.append({
            "code": "waiting_overdue",
            "overdue_sec": _age(rotation.get("overdue_sec")),
            "reason_age_sec": _age(rotation.get("reason_age_sec")),
        })

    if (status in {"ready", "waiting"} and not pending and not manual_pending
            and last_seen_age is not None and last_seen_age > STALE_TICK_SECONDS):
        rows.append({
            "code": "stalled",
            "status": status,
            "last_seen_age_sec": last_seen_age,
        })

    if pending:
        owner = _fixed(rotation.get("pending_owner"), PENDING_OWNERS)
        age = _age(rotation.get("pending_age_sec"))
        if owner in {"none", "absent", "unknown"} and age is not None \
                and age >= PENDING_UNOWNED_SECONDS:
            rows.append({
                "code": "pending_unowned",
                "pending_corner": _safe_id(rotation.get("pending_corner")),
                "pending_phase": _fixed(rotation.get("pending_phase"), PENDING_PHASES),
                "pending_owner": owner,
                "pending_age_sec": age,
            })

    if manual_pending:
        owner = _fixed(rotation.get("manual_pending_owner"), PENDING_OWNERS)
        age = _age(rotation.get("manual_pending_age_sec"))
        if owner in {"none", "absent", "unknown"} and age is not None \
                and age >= MANUAL_PENDING_UNOWNED_SECONDS:
            rows.append({
                "code": "manual_pending_unowned",
                "manual_pending_corner": _safe_id(rotation.get("manual_pending_corner")),
                "manual_pending_owner": owner,
                "manual_pending_age_sec": age,
            })

    return rows


def _abnormal_label(row):
    if row.get("source") == "state":
        return "状態ファイルが failed"
    if row.get("resolution") == "released-unverified":
        return "失敗を記録してローテーションを継続"
    if row.get("resolution") == "recovery-required":
        return "要復旧の失敗を記録"
    return "失敗を記録"


def render_abnormal_body(assessment, *, repo, server=DEFAULT_SERVER):
    rows = assessment.get("abnormal") or []
    workflow_url = f"{server.rstrip('/')}/{repo}/actions/workflows/{MONITOR_WORKFLOW}"
    lines = [
        "コーナーの不正終了を自動検知しました。",
        "",
        f"- 検知時刻 (UTC): {_iso_utc(assessment['now'])}",
        f"- 検知件数: {len(rows)}",
        f"- Workflow: {workflow_url}",
        "",
        "### 検知内容",
        "",
    ]
    for row in rows:
        lines.append(f"- `{row['corner']}`")
        if row.get("game"):
            lines.append(f"  - ゲーム: `{row['game']}`")
        lines.append(f"  - 種別: {_abnormal_label(row)}")
        lines.append(f"  - 失敗からの経過: {_age_text(row.get('age_sec'))}")
        recovery = row.get("recovery_required")
        lines.append(
            "  - 要復旧: "
            + ("yes" if recovery is True else "no" if recovery is False else "不明")
        )
        if row.get("resolution"):
            lines.append(f"  - 監査上の扱い: {row['resolution']}")
        if row.get("reason_code"):
            lines.append(f"  - 理由コード: {row['reason_code']}")
        if row.get("error_kind"):
            lines.append(f"  - エラー分類: {row['error_kind']}")
    lines += [
        "",
        "状態ファイルが `failed` のままのコーナーは、異常終了後に回復していないことを示します。",
        "ローテーションの監査に失敗だけが残っている場合は、記録から24時間でこの一覧から外れます。",
        "ローテーションを再開・復旧する操作はこの監視では行いません。",
        "異常が解消すると、このIssueは監視workflowにより自動でクローズされます。",
        "恒久対応が必要なら別Issueで追跡してください。",
        "診断の生データ・例外本文・パス・資格情報は含めていません。",
    ]
    return "\n".join(lines)


def _rotation_label(row):
    return {
        "ledger_unreadable": "ローテーション台帳を読めない",
        "invalid_state": "ローテーション台帳の状態が不正",
        "latched": "ローテーションが recovery_required で停止",
        "released_owner_unretired": "解放済みownerを退役できず対象stateを回避",
        "timer_inactive": "ローテーションタイマーが停止",
        "waiting_overdue": "待機が next_due_at を大きく超過",
        "stalled": "tickが長時間更新されていない",
        "pending_unowned": "予約の所有者がいない",
        "manual_pending_unowned": "手動予約の所有者がいない",
    }.get(row.get("code"), "不明")


def render_rotation_body(assessment, *, repo, server=DEFAULT_SERVER):
    rows = assessment.get("rotation") or []
    workflow_url = f"{server.rstrip('/')}/{repo}/actions/workflows/{MONITOR_WORKFLOW}"
    lines = [
        "コーナーローテーションの異常を自動検知しました。",
        "",
        f"- 検知時刻 (UTC): {_iso_utc(assessment['now'])}",
        f"- 検知件数: {len(rows)}",
        f"- Workflow: {workflow_url}",
        "",
        "### 検知内容",
        "",
    ]
    for row in rows:
        lines.append(f"- {_rotation_label(row)} (`{row['code']}`)")
        if row.get("status"):
            lines.append(f"  - 状態: {row['status']}")
        if row.get("error_kind"):
            lines.append(f"  - エラー分類: {row['error_kind']}")
        if row.get("pending_corner"):
            lines.append(f"  - 予約コーナー: `{row['pending_corner']}`")
        if row.get("pending_phase"):
            lines.append(f"  - 予約フェーズ: {row['pending_phase']}")
        if row.get("pending_age_sec") is not None:
            lines.append(f"  - 予約からの経過: {_age_text(row['pending_age_sec'])}")
        if row.get("pending_owner"):
            lines.append(f"  - 予約owner: {row['pending_owner']}")
        if row.get("manual_pending_corner"):
            lines.append(f"  - 手動予約コーナー: `{row['manual_pending_corner']}`")
        if row.get("manual_pending_age_sec") is not None:
            lines.append(
                f"  - 手動予約からの経過: {_age_text(row['manual_pending_age_sec'])}"
            )
        if row.get("manual_pending_owner"):
            lines.append(f"  - 手動予約owner: {row['manual_pending_owner']}")
        if row.get("corners"):
            lines.append("  - 対象コーナー: " + ", ".join(f"`{c}`" for c in row["corners"]))
        if row.get("overdue_sec") is not None:
            lines.append(f"  - 超過時間: {_age_text(row['overdue_sec'])}")
        if row.get("reason_age_sec") is not None:
            lines.append(f"  - 同じ理由の継続: {_age_text(row['reason_age_sec'])}")
        if row.get("last_seen_age_sec") is not None:
            lines.append(f"  - 最終tickからの経過: {_age_text(row['last_seen_age_sec'])}")
        if row.get("timer_active") is not None:
            lines.append(
                "  - timer active/enabled: "
                f"{row.get('timer_active')}/{row.get('timer_enabled')}"
            )
    lines += [
        "",
        "この監視は読み取り専用です。ローテーションの復旧・再起動・台帳編集は行いません。",
        "異常が解消すると、このIssueは監視workflowにより自動でクローズされます。",
        "恒久対応が必要なら別Issueで追跡してください。",
        "診断の生データ・例外本文・パス・資格情報は含めていません。",
    ]
    return "\n".join(lines)


def _query(runner, args):
    try:
        return runner(args)
    except (GhError, subprocess.SubprocessError, OSError) as exc:
        raise QueryError("gh query failed") from exc


def _publish(runner, args):
    try:
        runner(args)
    except (GhError, subprocess.SubprocessError, OSError) as exc:
        raise PublishError("gh publish failed") from exc


def _open_issues(runner, repo, title):
    out = _query(
        runner,
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            f'"{title}" in:title',
            "--limit",
            "10",
            "--json",
            "number,title,body",
        ],
    )
    try:
        data = json.loads(out or "[]")
    except ValueError as exc:
        raise QueryError("unexpected issue list shape") from exc
    if not isinstance(data, list):
        raise QueryError("unexpected issue list shape")
    issues = []
    for item in data:
        if not isinstance(item, dict) or item.get("title") != title:
            continue
        number = item.get("number")
        if type(number) is not int or number <= 0:
            raise QueryError("unexpected issue number")
        body = item.get("body")
        issues.append({"number": number, "body": body if isinstance(body, str) else ""})
    return issues


def sync_issues(
    runner,
    *,
    repo,
    assessment,
    server=DEFAULT_SERVER,
    dry_run=False,
    log=print,
):
    """Keep the two deduplicated alert Issues consistent with ``assessment``.

    Both Issue lookups happen before any mutation so a failed query leaves the
    alert state untouched. Returns a small summary dict.
    """
    if assessment.get("available") is not True:
        raise QueryError("assessment unavailable")
    titles = (
        ("abnormal", ABNORMAL_TITLE, render_abnormal_body(
            assessment, repo=repo, server=server),
         "コーナーの不正終了状態が解消したことを確認しました。自動クローズします。"
         "恒久対応が必要なら別Issueで追跡してください。"),
        ("rotation", ROTATION_TITLE, render_rotation_body(
            assessment, repo=repo, server=server),
         "コーナーローテーションの異常が解消したことを確認しました。自動クローズします。"
         "恒久対応が必要なら別Issueで追跡してください。"),
    )
    open_issues = {
        key: _open_issues(runner, repo, title)
        for key, title, _body, _comment in titles
    }
    actions = []
    for key, title, body, comment in titles:
        active = bool(assessment.get(key))
        issues = open_issues[key]
        if active:
            if issues:
                for issue in issues:
                    if issue["body"].strip() == body.strip():
                        continue
                    if not dry_run:
                        _publish(
                            runner,
                            ["issue", "edit", str(issue["number"]), "--repo", repo,
                             "--body", body],
                        )
                    actions.append(
                        {"key": key, "action": "edit", "number": issue["number"]}
                    )
            else:
                if not dry_run:
                    _publish(
                        runner,
                        ["issue", "create", "--repo", repo, "--title", title, "--body", body],
                    )
                actions.append({"key": key, "action": "create", "title": title})
        else:
            for issue in issues:
                if not dry_run:
                    _publish(
                        runner,
                        ["issue", "close", str(issue["number"]), "--repo", repo,
                         "--comment", comment],
                    )
                actions.append({"key": key, "action": "close", "number": issue["number"]})
    for action in actions:
        log(f"corner health {action['key']}: {action['action']} {action.get('number', '')}".strip())
    if not actions:
        log("corner health: no alert changes")
    return {"actions": actions, "abnormal": bool(assessment.get("abnormal")),
            "rotation": bool(assessment.get("rotation"))}


def run(
    runner,
    *,
    repo,
    diagnostics_path,
    server=DEFAULT_SERVER,
    now=None,
    dry_run=False,
    log=print,
):
    try:
        raw = Path(diagnostics_path).read_text(encoding="utf-8")
        document = json.loads(raw)
    except (OSError, ValueError) as exc:
        log("corner health unavailable; alert state left unchanged")
        return EXIT_UNAVAILABLE
    assessment = assess(document, now=now)
    if assessment.get("available") is not True:
        log(
            "corner health unavailable ("
            + str(assessment.get("reason") or "unknown")
            + "); alert state left unchanged"
        )
        return EXIT_UNAVAILABLE
    try:
        sync_issues(
            runner,
            repo=repo,
            assessment=assessment,
            server=server,
            dry_run=dry_run,
            log=log,
        )
    except QueryError:
        log("corner health query failed; alert state left unchanged")
        return EXIT_UNAVAILABLE
    except PublishError:
        log("corner health alert could not be published")
        return EXIT_PUBLISH_FAILED
    return EXIT_OK


def gh_runner(args: Sequence[str]) -> str:
    completed = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise GhError("gh failed")
    return completed.stdout


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="corner_health.py")
    parser.add_argument("--diagnostics", required=True)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--server", default=os.environ.get("GITHUB_SERVER_URL", DEFAULT_SERVER))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.repo or "/" not in args.repo:
        print("corner health requires --repo or GITHUB_REPOSITORY", file=sys.stderr)
        return EXIT_UNAVAILABLE
    return run(
        gh_runner,
        repo=args.repo,
        diagnostics_path=args.diagnostics,
        server=args.server,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
