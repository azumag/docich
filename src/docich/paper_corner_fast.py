"""Non-blocking scheduled PAPER corner with front-loaded narration."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import load_global
from .game_switch import atomic_write_json
from .adapters.program import PAPER_VIEW_NAME
from .paper_corner import (
    SCRIPT_SLOTS,
    PaperCornerError,
    PaperCornerManager,
    _safe_detail,
    ensure_trading_window,
)
from .trading.presentation import write_presentation

MAIN_NARRATION_INTERVAL_S = 75
FOLLOWUP_NARRATION_INTERVAL_S = 90
MAIN_SCRIPT_SLOTS = 8
SCHEDULE_VERSION = 2
SCRIPT_RESULT_SCHEMA = 1


def _script_result_path(g, date_str: str) -> Path:
    return Path(g.state_dir) / "paper-corner-scripts" / f"{date_str}.json"


def _prepare_segments(result: object) -> dict[str, str]:
    from .trading.corner_script import SEGMENT_KEYS

    data = result if isinstance(result, dict) else {}
    raw = data.get("segments") if isinstance(data.get("segments"), dict) else {}
    prepared: dict[str, str] = {}
    for index, key in enumerate(SEGMENT_KEYS, start=1):
        text = str(raw.get(key, "")).strip()
        if text:
            prepared[str(index)] = text[:700]
    return prepared


def _load_paper_corner_config(config_path: Path) -> tuple[str, int]:
    import tomllib

    raw = tomllib.loads(Path(config_path).read_text(encoding="utf-8")).get("paper_corner", {})
    agents = str(raw.get("script_agents") or "").strip()
    timeout = raw.get("script_timeout_s", 180)
    if type(timeout) is not int or not 1 <= timeout <= 1800:
        raise ValueError("invalid paper corner script timeout")
    return agents, timeout


def run_script_worker(config_path: Path, date_str: str) -> int:
    # The date is part of a private state filename, so accept only ISO calendar dates.
    dt.date.fromisoformat(date_str)
    g = load_global(Path(__file__).resolve().parents[2], config_path)
    agents, timeout = _load_paper_corner_config(config_path)
    if not agents:
        return 0

    from .trading.corner_script import generate_corner_script

    os.environ["DOCICH_ALLOW_REAL_AI"] = "1"
    result = generate_corner_script(
        g,
        trading_dir=Path(g.state_dir) / "trading",
        agents=agents,
        timeout=timeout,
        now=time.time(),
    )
    payload = {
        "schema_version": SCRIPT_RESULT_SCHEMA,
        "date": date_str,
        "source": str(result.get("source") or "fallback")[:40],
        "reason": str(result.get("reason") or "")[:120],
        "segments": _prepare_segments(result),
    }
    target = _script_result_path(g, date_str)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    atomic_write_json(target, payload)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return 0


class FastPaperCornerManager(PaperCornerManager):
    """Scheduled PAPER corner that starts immediately and enriches narration asynchronously."""

    def __init__(self, *args, script_spawn=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._script_spawn = script_spawn or self._default_spawn_script_proc

    def _default_spawn_script_proc(self, argv, log_path) -> int:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(Path(log_path).parent, 0o700)
        except OSError:
            pass
        env = dict(os.environ)
        env["DOCICH_ALLOW_REAL_AI"] = "1"
        with open(log_path, "ab") as log_fh:
            try:
                os.chmod(log_path, 0o600)
            except OSError:
                pass
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=str(self.g.repo_root),
                env=env,
            )
        return int(proc.pid)

    def _announce_script(self, state) -> None:
        """Install an immediate fallback script, then enrich it in a detached worker."""
        existing = state.get("script_segments")
        if not isinstance(existing, dict) or not existing:
            from .trading.corner_script import generate_corner_script

            result = generate_corner_script(
                self.g,
                trading_dir=self.trading_dir,
                agents="",
                timeout=1,
                now=self.clock(),
            )
            state["script_segments"] = _prepare_segments(result)
            state["script_source"] = "fallback-initial"
            if result.get("reason"):
                state["script_reason"] = str(result.get("reason"))[:120]
            self.save(state)

        date_str = state.get("date")
        if not self.script_agents or not isinstance(date_str, str) or not date_str:
            return
        try:
            dt.date.fromisoformat(date_str)
        except ValueError:
            return

        job = state.get("script_job") if isinstance(state.get("script_job"), dict) else {}
        if job.get("date") == date_str and job.get("status") in {"running", "completed"}:
            return

        result_path = _script_result_path(self.g, date_str)
        try:
            result_path.unlink(missing_ok=True)
        except OSError:
            pass
        log_path = Path(self.g.state_dir) / "logs" / f"paper-corner-script-{date_str}.log"
        argv = [
            sys.executable,
            "-m",
            "docich.paper_corner_fast",
            "--config",
            str(self.g.config_path),
            "script-worker",
            "--date",
            date_str,
        ]
        try:
            pid = self._script_spawn(argv, log_path)
            state["script_job"] = {"date": date_str, "status": "running", "pid": int(pid)}
        except Exception as exc:
            state["script_job"] = {"date": date_str, "status": "error", "error": _safe_detail(exc)}
        self.save(state)

    def _refresh_ai_script(self, state) -> None:
        job = state.get("script_job") if isinstance(state.get("script_job"), dict) else {}
        date_str = state.get("date")
        if not isinstance(date_str, str) or job.get("date") != date_str or job.get("status") == "completed":
            return
        target = _script_result_path(self.g, date_str)
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(data, dict) or data.get("schema_version") != SCRIPT_RESULT_SCHEMA or data.get("date") != date_str:
            return
        segments = data.get("segments")
        if not isinstance(segments, dict) or not segments:
            job["status"] = "error"
            job["error"] = "script_result_invalid"
            self.save(state)
            return

        source = str(data.get("source") or "fallback")[:40]
        if source in {"ai", "ai-partial"}:
            merged = dict(state.get("script_segments") or {})
            for key, value in segments.items():
                if str(key).isdigit() and isinstance(value, str) and value.strip():
                    merged[str(key)] = value.strip()[:700]
            state["script_segments"] = merged
        state["script_source"] = source
        reason = str(data.get("reason") or "")[:120]
        if reason:
            state["script_reason"] = reason
        else:
            state.pop("script_reason", None)
        job["status"] = "completed"
        job.pop("pid", None)
        self.save(state)

    @staticmethod
    def _due_seconds(slot: int) -> int:
        if slot <= MAIN_SCRIPT_SLOTS:
            return slot * MAIN_NARRATION_INTERVAL_S
        return (
            MAIN_SCRIPT_SLOTS * MAIN_NARRATION_INTERVAL_S
            + (slot - MAIN_SCRIPT_SLOTS) * FOLLOWUP_NARRATION_INTERVAL_S
        )

    @staticmethod
    def _report_done(report: object) -> bool:
        return isinstance(report, dict) and bool(report.get("overlay")) and bool(report.get("speech"))

    def _slot_delivered(self, state, slot: int) -> bool:
        reports = state.get("reports") if isinstance(state.get("reports"), dict) else {}
        script_index = SCRIPT_SLOTS.get(slot)
        if script_index is not None and self._report_done(reports.get(f"script:{script_index}")):
            return True
        return self._report_done(reports.get(f"chatter:{slot}"))

    def _next_slot(self, state) -> tuple[int, int] | None:
        duration_s = max(1, int(float(state["ends_at"]) - float(state["started_at"])))
        for slot in range(1, 128):
            due = self._due_seconds(slot)
            if due >= duration_s:
                return None
            if not self._slot_delivered(state, slot):
                return slot, due
        return None

    def _run_locked(self, state):
        if state["status"] == "active" and state.get("schedule_version") != SCHEDULE_VERSION:
            # Do not reinterpret the timing of a corner that already started under
            # the legacy scheduler. New starts use the non-blocking schedule.
            return super()._run_locked(state)

        if state["status"] == "starting":
            if "previous_game" not in state:
                previous = self._active_game()
                state["previous_game"] = previous
                self.save(state)
            else:
                previous = state.get("previous_game")
            if previous is None:
                self._require_success(self.coordinator.start(PAPER_VIEW_NAME), "program view start")
            elif previous == PAPER_VIEW_NAME:
                pass
            else:
                self.announce(
                    state,
                    "switch-notice",
                    "まもなくPAPER・暗号資産の模擬売買コーナーのため、試合終了後に画面を切り替えます。",
                )
                self._require_success(
                    self.coordinator.switch(PAPER_VIEW_NAME), f"{previous}->program view switch"
                )
            committed = self._active_game()
            if committed is not None and committed != PAPER_VIEW_NAME:
                raise PaperCornerError(f"切替後に旧ゲームが残っています: {committed}")

            self.announce(state, "opening", self.opening_text())
            write_presentation(self.presentation, "detailed", now=self.clock())
            started = self.clock()
            state.update(
                status="active",
                started_at=started,
                ends_at=started + self.minutes * 60,
                schedule_version=SCHEDULE_VERSION,
            )
            self.save(state)

            # Important: the corner is active before any model/network work.
            # Fallback content is local and immediate; AI enrichment is detached.
            try:
                self._announce_script(state)
            except Exception as exc:
                state["script_error"] = _safe_detail(exc)
                state.setdefault("script_segments", {})
                self.save(state)
        elif self.clock() < state["ends_at"]:
            write_presentation(self.presentation, "detailed", now=self.clock())
            if "script_segments" not in state:
                try:
                    self._announce_script(state)
                except Exception as exc:
                    state["script_error"] = _safe_detail(exc)
                    state["script_segments"] = {}
                    self.save(state)

        while self.clock() < state["ends_at"]:
            self._refresh_ai_script(state)
            elapsed = max(0.0, self.clock() - state["started_at"])
            next_item = self._next_slot(state)
            if next_item is None:
                self.sleep(max(0.0, state["ends_at"] - self.clock()))
                break
            slot, due = next_item
            if elapsed >= due:
                self._scheduled_narration(state, slot)
                continue
            remaining = state["ends_at"] - self.clock()
            self.sleep(min(max(0.0, due - elapsed), remaining))
        return self._restore_locked(state)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("command", choices=["tick", "status", "script-worker"])
    parser.add_argument("--date")
    args = parser.parse_args(argv)

    if args.command == "script-worker":
        if args.config is None or not args.date:
            parser.error("script-worker requires --config and --date")
        return run_script_worker(args.config, args.date)

    manager = FastPaperCornerManager(
        load_global(Path(__file__).resolve().parents[2], args.config)
    )
    if args.command == "status":
        print(manager.path.read_text() if manager.path.exists() else "{}")
    else:
        print(manager.tick())
        guard = ensure_trading_window(manager.g)
        if guard in ("created", "recreated"):
            print(f"trading window {guard}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
