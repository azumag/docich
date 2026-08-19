"""Referencing-run wrapper for soviet_now overlay generators (common_parts_overlay.md).

docich does not copy or own the overlay HTML implementation.  This module builds
a fixed bash wrapper that sources ``eloop_lib.sh`` (same source order as
production) and calls one allowlisted overlay script in ``once`` mode.  The
output HTML is redirected to a private temp dir by default so production
``tmp/state/*.html`` is never touched.  OBS ``ensure-obs`` modes are not exposed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from shlex import quote

from .config import ConfigError, GlobalConfig
from .procs import run
from .tts import TtsError, game_submodule


ALLOWED_OVERLAY_KINDS = {
    "status": "generate_status_overlay.sh",
    "show_status": "generate_show_status_overlay.sh",
    "improve": "generate_improve_overlay.sh",
    "event": "generate_event_overlay.py",
    "notify": "overlay_notify.sh",
}

WRAPPER = """\
#!/usr/bin/env bash
# docich reference-run wrapper for soviet_now overlay generators.
set -uo pipefail
ROOT="$1"; shift
cd "$ROOT" || exit 2
export ELOOP_LIB_DIR="$ROOT"
[ -f "eloop_lib.sh" ] || exit 2
source ./eloop_lib.sh
"$@"
"""


class OverlayError(TtsError):
    """User-facing overlay reference error."""


@dataclass(frozen=True)
class OverlayInvocation:
    script_path: Path
    cwd: Path
    argv: list[str]
    env: dict[str, str]
    kind: str
    rcs_note: str = ""

    def repro(self) -> str:
        parts = [f"cwd={self.cwd}"]
        for key, value in sorted(self.env.items()):
            parts.append(f"{key}={value}")
        parts.append(f"kind={self.kind}")
        parts.append("argv=" + " ".join(quote(str(part)) for part in self.argv))
        return " ".join(parts)


def _overlay_root(g: GlobalConfig, game_name: str) -> Path:
    root = game_submodule(g, game_name)
    if not (root / "eloop_lib.sh").is_file():
        raise OverlayError(f"eloop_lib.sh が見つかりません: {root / 'eloop_lib.sh'}")
    return root


def _env_for(g: GlobalConfig, output: Path) -> dict[str, str]:
    env = {
        "SAY_CONTEXT_LABEL": "docich",
        "DOCICH_CC_ENABLED": "0",
        # OBS 連動 (ensure-obs) は PoC では無効化し、HTML 生成のみ行う。
        "STATUS_OVERLAY_HTML_FILE": str(output / "status_overlay.html"),
        "SHOW_STATUS_OVERLAY_HTML_FILE": str(output / "show_status_overlay.html"),
        "IMPROVE_OVERLAY_HTML_FILE": str(output / "improve_overlay.html"),
        "EVENT_OVERLAY_HTML_FILE": str(output / "event_overlay.html"),
        "EVENT_OVERLAY_EVENTS_FILE": str(output / "overlay_events.jsonl"),
    }
    if g.audio.enabled:
        env["PULSE_SINK"] = g.audio.sink_name
        env["SAY_AUDIO_DEVICE"] = g.audio.sink_name
    return env


def _write_wrapper(tmp_dir: Path) -> Path:
    wrapper = tmp_dir / "overlay_ref.sh"
    wrapper.write_text(WRAPPER, encoding="utf-8")
    wrapper.chmod(0o700)
    return wrapper


def build_overlay_invocation(
    g: GlobalConfig,
    *,
    game_name: str,
    kind: str,
    output: Path | None = None,
) -> OverlayInvocation:
    if kind not in ALLOWED_OVERLAY_KINDS:
        raise OverlayError(
            f"overlay kind は {sorted(ALLOWED_OVERLAY_KINDS)} に限定されます: {kind}"
        )
    script_name = ALLOWED_OVERLAY_KINDS[kind]
    root = _overlay_root(g, game_name)
    script = root / script_name
    if not script.is_file():
        raise OverlayError(f"オーバーレイスクリプトが見つかりません: {script}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="docich-overlay-"))
    output_dir = (output or tmp_dir / "out").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    wrapper = _write_wrapper(tmp_dir)

    argv = ["bash", str(wrapper), str(root), script_name, "once"]
    env = _env_for(g, output_dir)
    return OverlayInvocation(
        script_path=script,
        cwd=root,
        argv=argv,
        env=env,
        kind=kind,
        rcs_note="rc 0=生成完了/1=失敗/2=引数エラー",
    )


def run_overlay(
    g: GlobalConfig,
    *,
    game_name: str,
    kind: str,
    output: Path | None = None,
    dry_run: bool = False,
    timeout: float | None = None,
) -> tuple[int, str]:
    inv = build_overlay_invocation(g, game_name=game_name, kind=kind, output=output)
    if dry_run:
        return 0, inv.repro()
    if os.environ.get("DOCICH_ALLOW_REAL_OVERLAY") != "1":
        raise OverlayError(
            "実実行は既定で無効です。--dry-run で確認するか、DOCICH_ALLOW_REAL_OVERLAY=1 で"
            "明示許可してください (HTML 生成・OBS 連動を含むため)"
        )
    result = run(
        inv.argv,
        cwd=str(inv.cwd),
        env_extra=inv.env,
        timeout=timeout,
        capture=True,
    )
    return result.returncode, (result.stderr or "")


def cli_overlay(args) -> int:
    from .cli import _load_global

    g = _load_global(args)
    try:
        rc, detail = run_overlay(
            g,
            game_name=args.game,
            kind=args.kind,
            output=Path(args.output) if args.output else None,
            dry_run=args.dry_run,
        )
    except (ConfigError, OverlayError, OSError) as exc:
        raise OverlayError(str(exc)) from exc
    if args.dry_run:
        print(f"docich: overlay dry-run: {detail}")
        return 0
    if rc != 0:
        print(f"docich: overlay 参照実行がエラー終了しました (rc={rc})", flush=True)
        return rc
    print("docich: overlay 完了")
    return 0
