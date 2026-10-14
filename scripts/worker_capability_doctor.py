#!/usr/bin/env python3
"""soviet_now worker capability manifest の gap report を出す read-only doctor.

docs/adr/0001-worker-capability-manifest.json (以下 manifest) と、実際に到達できる
soviet_now チェックアウト・systemd unit の実態を突き合わせ、乖離 (gap) を stdout に
出力するだけのスクリプト。

**このスクリプトは read-only である:**
  - プロセスを起動・再起動・kill しない (`os.kill(pid, 0)` によるプローブのみ行うが、
    これはシグナル送信ではなく存在確認のための no-op probe であり、プロセスの状態を
    変更しない)。
  - ファイルを一切書き込まない (stdout への出力のみ)。
  - `systemctl start/stop/enable/disable/daemon-reload` 等の変更系操作は一切呼ばない。
    呼ぶのは `systemctl list-units` / `systemctl show -p ...` など読み取り専用の
    サブコマンドのみ。
  - `.env` を読む場合も、credential の値は一切収集・出力しない。変数名 (key) だけを
    見て、値は読み取った直後に破棄する。

Linux (systemd あり) でも macOS (systemd なし) でも例外を出さずに動作する。
systemd が使えない環境では「systemd 情報を取得できない」ことを明示し、
manifest の静的検証とファイルシステム上の実態確認のみ行う。

Usage:
    python3 scripts/worker_capability_doctor.py
    python3 scripts/worker_capability_doctor.py --soviet-now-root /path/to/soviet_now
    python3 scripts/worker_capability_doctor.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = REPO_ROOT / "docs" / "adr" / "0001-worker-capability-manifest.json"

# soren-runtime.service 等、既にリポジトリの deploy/ 配下に unit file がある既知の
# production unit。manifest の proposed_identity.systemd_unit とは別に、
# 「今すでに存在する unit」として突き合わせの基準にする。
KNOWN_EXISTING_UNITS = [
    "soren-runtime.service",
    "soren-rtmp-relay.service",
    "soren-rtmps-bridge.service",
]

HARDENING_PROPERTIES = [
    "User",
    "Group",
    "NoNewPrivileges",
    "ProtectSystem",
    "ProtectHome",
    "PrivateTmp",
    "RestrictAddressFamilies",
]

_ENTRYPOINT_RE = re.compile(r"[\w./-]+\.(?:sh|py|mjs)")
_SHELL_DEFAULT_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}")


# --------------------------------------------------------------------------
# manifest loading / validation (常にできる、ファイルシステム・systemd不要)
# --------------------------------------------------------------------------


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


REQUIRED_WORKER_KEYS = (
    "name",
    "read_paths",
    "write_paths",
    "network_endpoints",
    "credentials_used",
)


def validate_manifest_schema(manifest: dict[str, Any]) -> list[str]:
    """manifest の必須項目が worker ごとに揃っているかを確認する。

    戻り値は問題点のメッセージ一覧 (空なら問題なし)。例外は投げない。
    """
    problems: list[str] = []
    for section in ("workers", "auxiliary_workers"):
        entries = manifest.get(section)
        if not isinstance(entries, list):
            problems.append(f"manifest['{section}'] がリストではない、または存在しない")
            continue
        for i, entry in enumerate(entries):
            name = entry.get("name", f"<{section}[{i}]:name欠落>")
            for key in REQUIRED_WORKER_KEYS:
                if key not in entry:
                    problems.append(f"{section}[{name}] に必須項目 '{key}' が無い")
            if "proposed_identity" not in entry:
                problems.append(f"{section}[{name}] に proposed_identity (owner割当) が無い")
    if not manifest.get("credentials"):
        problems.append("manifest['credentials'] が空、または存在しない")
    else:
        for i, cred in enumerate(manifest["credentials"]):
            for key in ("var", "purpose", "provider", "consumers", "rotation_owner"):
                if key not in cred:
                    problems.append(f"credentials[{i}] に必須項目 '{key}' が無い (var={cred.get('var')})")
    return problems


def all_worker_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return list(manifest.get("workers", [])) + list(manifest.get("auxiliary_workers", []))


# --------------------------------------------------------------------------
# soviet_now root 解決 (read-only, 発見できなくてもエラーにしない)
# --------------------------------------------------------------------------


def resolve_soviet_now_root(override: str | None) -> Path | None:
    """src/docich/webui.py の _resolve_soren_root と同じ発見規則を踏襲する。

    書き込み・作成は一切しない。見つからなければ None を返すだけで、
    static-only (manifestの構造検証) は引き続き実行できる。
    """
    if override:
        p = Path(override).expanduser().resolve()
        return p if (p / "eloop_lib.sh").is_file() else None
    for env_name in ("DOCICH_SOREN_ROOT", "ELOOP_LIB_DIR"):
        env_val = os.environ.get(env_name)
        if env_val:
            p = Path(env_val).expanduser()
            if (p / "eloop_lib.sh").is_file():
                return p.resolve()
    cand = REPO_ROOT / "games" / "soviet_now"
    if (cand / "eloop_lib.sh").is_file():
        return cand.resolve()
    return None


# --------------------------------------------------------------------------
# helper: entrypoint 文字列からファイル候補を抜き出す / pidfile のシェル既定値展開
# --------------------------------------------------------------------------


def extract_entrypoint_files(entrypoint: str) -> list[str]:
    """'./workers/chat_worker.sh ${TWITCH_CHANNEL:-azumagbanjo}' のような文字列や、
    括弧付きの説明文が混じった entrypoint から実ファイルパス候補を抜き出す。
    """
    return _ENTRYPOINT_RE.findall(entrypoint or "")


def expand_shell_default(value: str) -> str | None:
    """'${IMPROVE_DAEMON_PID_FILE:-tmp/state/improve_daemon.pid}' のような
    シェル変数既定値記法を、既定値部分だけに展開する。プレーンなパスならそのまま返す。
    日本語の説明文 (ASCII 以外を含む、または空白を含む) はパスとして扱わず None を返す。
    """
    if not value:
        return None
    m = _SHELL_DEFAULT_RE.search(value)
    candidate = m.group(1) if m else value
    if not candidate:
        return None
    if not all(ord(ch) < 128 for ch in candidate):
        return None
    if " " in candidate or "(" in candidate:
        return None
    return candidate


# --------------------------------------------------------------------------
# .env の「キー名だけ」を見る (値は絶対に保持・出力しない)
# --------------------------------------------------------------------------


def read_env_keys_only(env_path: Path) -> set[str]:
    """.env のキー名だけを集合として返す。値は読み取った直後に破棄し、
    戻り値にも呼び出し元にも一切渡さない。
    """
    keys: set[str] = set()
    try:
        with env_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _sep, _value_discarded = line.partition("=")
                key = key.strip()
                if key.startswith("export "):
                    key = key[len("export "):].strip()
                if key and all(c.isalnum() or c == "_" for c in key):
                    keys.add(key)
                # _value_discarded はここで scope を抜けて破棄される。保持しない。
    except OSError:
        return set()
    return keys


def credential_var_names(manifest: dict[str, Any]) -> set[str]:
    """manifest['credentials'][].var から実際の環境変数名だけを抜き出す。

    "TWITCH_BOT_TOKEN" や "ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL" のような
    実在の変数名はトークン化するが、"(gcloud ADC、環境変数ではなく...)" のように
    var 自体が説明文のプレースホルダ (括弧書き) のエントリは、そもそも env の
    キーとして存在し得ないため対象外にする。
    """
    names: set[str] = set()
    for cred in manifest.get("credentials", []):
        var = cred.get("var", "").strip()
        if not var or var.startswith("("):
            continue
        for token in re.findall(r"[A-Z][A-Z0-9_]{2,}", var):
            names.add(token)
    return names


# --------------------------------------------------------------------------
# systemd (Linux のみ、read-only サブコマンドのみ)
# --------------------------------------------------------------------------


def systemd_available() -> bool:
    return platform.system() == "Linux" and shutil.which("systemctl") is not None


def _run_readonly(cmd: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def list_existing_service_units() -> list[str]:
    """`systemctl list-units --type=service --all` (read-only) の結果から
    unit名一覧を返す。取得できなければ空リスト。
    """
    rc, out, _err = _run_readonly(
        ["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager", "--plain"]
    )
    if rc != 0 or not out:
        return []
    units = []
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0].endswith(".service"):
            units.append(parts[0])
    return units


def show_unit_properties(unit: str) -> dict[str, str]:
    """`systemctl show <unit> -p <props>` (read-only) で hardening 設定を読む。"""
    rc, out, _err = _run_readonly(
        ["systemctl", "show", unit, "-p", ",".join(HARDENING_PROPERTIES), "--no-pager"]
    )
    props: dict[str, str] = {}
    if rc != 0:
        return props
    for line in out.splitlines():
        if "=" in line:
            k, _sep, v = line.partition("=")
            props[k] = v
    return props


# --------------------------------------------------------------------------
# レポート組み立て
# --------------------------------------------------------------------------


def build_report(manifest_path: Path, soviet_now_root_override: str | None) -> dict[str, Any]:
    report: dict[str, Any] = {"manifest_path": str(manifest_path)}

    try:
        manifest = load_manifest(manifest_path)
    except (OSError, json.JSONDecodeError) as exc:
        report["fatal_error"] = f"manifest を読み込めない: {exc}"
        return report

    report["schema_problems"] = validate_manifest_schema(manifest)

    workers = all_worker_entries(manifest)
    report["worker_count"] = len(workers)
    report["credential_count"] = len(manifest.get("credentials", []))

    soviet_now_root = resolve_soviet_now_root(soviet_now_root_override)
    report["soviet_now_root"] = str(soviet_now_root) if soviet_now_root else None

    # --- entrypoint がファイルシステム上に実在するか (soviet_now_root が分かる場合のみ) ---
    missing_entrypoints: list[dict[str, str]] = []
    pid_status: list[dict[str, Any]] = []
    if soviet_now_root is not None:
        for w in workers:
            entrypoint = w.get("entrypoint", "")
            for rel in extract_entrypoint_files(entrypoint):
                rel_clean = rel[2:] if rel.startswith("./") else rel
                if not (soviet_now_root / rel_clean).exists():
                    missing_entrypoints.append({"worker": w.get("name", "?"), "path": rel})

            pc = w.get("process_control", {})
            raw_pidfile = pc.get("pid_file") if isinstance(pc, dict) else None
            pidfile_rel = expand_shell_default(raw_pidfile) if raw_pidfile else None
            if pidfile_rel:
                pid_path = soviet_now_root / pidfile_rel
                entry: dict[str, Any] = {"worker": w.get("name", "?"), "pid_file": pidfile_rel}
                if pid_path.is_file():
                    try:
                        pid_text = pid_path.read_text(encoding="utf-8", errors="replace").strip()
                        pid = int(pid_text.splitlines()[0]) if pid_text else None
                    except (ValueError, OSError):
                        pid = None
                    if pid:
                        try:
                            os.kill(pid, 0)  # read-only existence probe (シグナル0)
                            entry["alive"] = True
                        except ProcessLookupError:
                            entry["alive"] = False
                        except PermissionError:
                            entry["alive"] = "unknown (permission denied)"
                        entry["pid"] = pid
                    else:
                        entry["alive"] = "unknown (pidfile unparsable)"
                else:
                    entry["alive"] = "no pidfile (stopped, or never started here)"
                pid_status.append(entry)

        # --- .env のキー名だけ突き合わせ (値は一切見ない) ---
        env_path = soviet_now_root / ".env"
        if env_path.is_file():
            present_keys = read_env_keys_only(env_path)
            declared = credential_var_names(manifest)
            report["env_credential_gap"] = {
                "env_file": str(env_path),
                "declared_in_manifest_but_absent_from_env": sorted(declared - present_keys),
                "present_in_env_but_not_in_manifest": sorted(
                    k for k in (present_keys - declared) if _looks_like_credential_name(k)
                ),
                "note": "値は一切読み取り・出力していない。キー名のみの突き合わせ。",
            }
        else:
            report["env_credential_gap"] = {"note": f".env が見つからない ({env_path})"}

    report["missing_entrypoints"] = missing_entrypoints
    report["pid_status"] = pid_status

    # --- systemd ---
    if systemd_available():
        existing_units = set(list_existing_service_units())
        proposed_units = sorted(
            {
                w["proposed_identity"]["systemd_unit"]
                for w in workers
                if isinstance(w.get("proposed_identity"), dict) and w["proposed_identity"].get("systemd_unit")
            }
        )
        already_migrated = [u for u in proposed_units if _unit_basename(u) in existing_units]
        not_yet_migrated = [u for u in proposed_units if _unit_basename(u) not in existing_units]
        known_units_present = [u for u in KNOWN_EXISTING_UNITS if u in existing_units]
        known_units_absent = [u for u in KNOWN_EXISTING_UNITS if u not in existing_units]

        hardening_gaps = []
        for unit in known_units_present:
            props = show_unit_properties(unit)
            gaps = []
            for prop in ("NoNewPrivileges", "ProtectSystem", "ProtectHome", "PrivateTmp"):
                val = props.get(prop, "")
                if val in ("", "no", "false", "no-hardening"):
                    gaps.append(f"{prop}={val or '(未設定)'}")
            if props.get("RestrictAddressFamilies", "") in ("", "none"):
                gaps.append("RestrictAddressFamilies=(未設定)")
            hardening_gaps.append({"unit": unit, "properties": props, "gaps": gaps})

        report["systemd"] = {
            "available": True,
            "known_existing_units_present": known_units_present,
            "known_existing_units_absent_unexpectedly": known_units_absent,
            "proposed_units_already_migrated": already_migrated,
            "proposed_units_not_yet_migrated": not_yet_migrated,
            "hardening_gaps_on_existing_units": hardening_gaps,
        }
    else:
        report["systemd"] = {
            "available": False,
            "note": (
                "systemd 情報を取得できない (systemctl が無いか、Linux ではない環境)。"
                " manifest の静的検証とファイルシステム上の実態確認のみ行った。"
            ),
        }

    return report


def _unit_basename(unit: str) -> str:
    # manifest には "soren-strategy-improve.service" のような単純名以外に
    # "systemd-run --uid=... --scope 等の一時スコープ" のような説明文が混じる
    # エントリもあるため、.service で終わるトークンだけ抜き出す。
    m = re.search(r"[\w.-]+\.service", unit)
    return m.group(0) if m else unit


def _looks_like_credential_name(key: str) -> bool:
    return bool(re.search(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|CLIENT_ID)", key))


# --------------------------------------------------------------------------
# 表示
# --------------------------------------------------------------------------


def format_report_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=== soviet_now worker capability doctor (read-only) ===")
    lines.append(f"manifest: {report.get('manifest_path')}")

    if report.get("fatal_error"):
        lines.append(f"FATAL: {report['fatal_error']}")
        return "\n".join(lines)

    lines.append(f"worker数 (primary+auxiliary): {report.get('worker_count')}")
    lines.append(f"credential数: {report.get('credential_count')}")

    problems = report.get("schema_problems") or []
    if problems:
        lines.append(f"\n[manifest schema gap: {len(problems)}件]")
        for p in problems:
            lines.append(f"  - {p}")
    else:
        lines.append("\n[manifest schema gap: なし]")

    root = report.get("soviet_now_root")
    lines.append(f"\nsoviet_now root: {root or '(見つからない。ファイルシステム/pid照合はスキップ)'}")

    if root:
        missing = report.get("missing_entrypoints") or []
        if missing:
            lines.append(f"\n[manifestに記載のentrypointがファイルシステム上に見つからない: {len(missing)}件]")
            for m in missing:
                lines.append(f"  - {m['worker']}: {m['path']}")
        else:
            lines.append("\n[entrypointファイル存在チェック: 全て見つかった]")

        pid_status = report.get("pid_status") or []
        if pid_status:
            lines.append("\n[pidfileベースの生存確認 (read-only probe)]")
            for p in pid_status:
                lines.append(f"  - {p['worker']}: pid_file={p['pid_file']} alive={p.get('alive')}")

        env_gap = report.get("env_credential_gap") or {}
        if env_gap.get("note", "").startswith(".env が見つからない"):
            lines.append(f"\n[.env credential gap] {env_gap['note']}")
        elif "declared_in_manifest_but_absent_from_env" in env_gap:
            missing_env = env_gap["declared_in_manifest_but_absent_from_env"]
            extra_env = env_gap["present_in_env_but_not_in_manifest"]
            lines.append(f"\n[.env credential gap] ({env_gap.get('env_file')}, 値は読んでいない)")
            lines.append(f"  manifestに記載だが.envに無い変数名: {missing_env or 'なし'}")
            lines.append(f"  .envにあるがmanifest未記載のcredential風変数名: {extra_env or 'なし'}")

    systemd = report.get("systemd", {})
    lines.append("\n[systemd]")
    if not systemd.get("available"):
        lines.append(f"  {systemd.get('note')}")
    else:
        lines.append(f"  既存の主要unit (present): {systemd.get('known_existing_units_present')}")
        lines.append(f"  既存の主要unitのうち見つからないもの (要確認): {systemd.get('known_existing_units_absent_unexpectedly')}")
        lines.append(f"  ADR提案unitで既に存在するもの (移行済み候補): {systemd.get('proposed_units_already_migrated')}")
        lines.append(f"  ADR提案unitで未移行のもの: {len(systemd.get('proposed_units_not_yet_migrated', []))}件")
        for gap in systemd.get("hardening_gaps_on_existing_units", []):
            if gap["gaps"]:
                lines.append(f"  hardening gap [{gap['unit']}]: {', '.join(gap['gaps'])}")
            else:
                lines.append(f"  hardening gap [{gap['unit']}]: なし (ADR記載の主要項目は設定済み)")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH), help="manifest JSON のパス")
    parser.add_argument("--soviet-now-root", default=None, help="games/soviet_now チェックアウトのパス (省略時は自動探索)")
    parser.add_argument("--json", action="store_true", help="JSON形式で出力する")
    args = parser.parse_args(argv)

    report = build_report(Path(args.manifest), args.soviet_now_root)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_report_text(report))

    return 2 if report.get("fatal_error") else 0


if __name__ == "__main__":
    sys.exit(main())
