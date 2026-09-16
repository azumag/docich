"""P5 NetHack corner integration: automatic retrospective + concise recap.

This module subclasses the stable scheduled NetHack corner instead of widening
its core lifecycle.  Game switching and run finalization remain unchanged;
P5b adds a best-effort retrospective only after the terminal run record has
been committed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .config import ConfigError, load_global
from .nethack_corner import (
    GAME_NAME,
    NethackCornerManager,
    _build_parser,
    _recover_json,
    _repo_root,
)
from .nethack_retrospective import NethackRetrospectiveEngine, NethackRetrospectiveError
from .nethack_run import NethackRunError
from .retro_corner import CornerResult, RetroCornerError, RetroCornerManager, _safe_detail

_TERMINAL_RUN_STATUSES = frozenset({"dead", "ascended", "ended", "ended_unknown"})


def viewer_retrospective_summary(retrospective: dict[str, object]) -> str:
    """Produce one short, evidence-bounded viewer recap."""
    status = retrospective.get("terminal_status")
    if status == "ended_unknown":
        return "終了理由の記録が不足しているため、原因は推測せず記録系を確認します。"
    if status != "dead":
        return ""

    parts: list[str] = []
    total = retrospective.get("same_death_total_count")
    if type(total) is int and total >= 2:
        parts.append(f"同じ死因パターンは通算{total}回目です。")

    lessons = retrospective.get("candidate_lessons")
    categories = {
        item.get("category")
        for item in lessons
        if isinstance(item, dict) and isinstance(item.get("category"), str)
    } if isinstance(lessons, list) else set()

    if "food_survival" in categories:
        parts.append("次回は空腹危険域を検知した後の食料判断を重点的に検証します。")
    elif "survival_signal" in categories:
        parts.append("危険域の検知はできていたため、次回は検知後の回復・退避を改善候補にします。")
    elif "proposal_drift" in categories:
        parts.append("判断候補の状態ずれが記録されているため、次回は実行前再確認を重点的に見直します。")
    elif "evidence_gap" in categories:
        parts.append("判断ログが不足しているため、次回は観測記録の確認を優先します。")
    elif "repeated_death" in categories:
        parts.append("次回はこの死亡パターンを独立した回帰ケースとして重点的に見直します。")
    return " ".join(parts[:2])


class NethackP5CornerManager(NethackCornerManager):
    """Scheduled corner with fail-open P5 retrospective integration."""

    def __init__(self, *args, retrospective=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._retrospective = retrospective or NethackRetrospectiveEngine(self.g)

    @staticmethod
    def _remember_retrospective(
        state: dict[str, object], retrospective: dict[str, object]
    ) -> None:
        state["retrospective_status"] = "completed"
        state["retrospective_policy_effect"] = retrospective.get("policy_effect", "none")
        state["retrospective_same_death_total_count"] = retrospective.get(
            "same_death_total_count", 0
        )
        lessons = retrospective.get("candidate_lessons")
        categories: list[str] = []
        if isinstance(lessons, list):
            for item in lessons:
                if not isinstance(item, dict):
                    continue
                category = item.get("category")
                if isinstance(category, str) and category not in categories:
                    categories.append(category)
        state["retrospective_lesson_categories"] = categories
        summary = viewer_retrospective_summary(retrospective)
        if summary:
            state["retrospective_summary"] = summary
        else:
            state.pop("retrospective_summary", None)
        state.pop("retrospective_error", None)

    def _end_announcement(self, state: dict[str, object]) -> str:
        base = super()._end_announcement(state)
        summary = state.get("retrospective_summary")
        if isinstance(summary, str) and summary.strip():
            return f"{base} {summary.strip()}"
        return base

    def _finish_locked(self, state: dict[str, object], completed_at) -> CornerResult:
        # Call the stable Retro lifecycle directly so the terminal run can be
        # reconciled before NethackCornerManager's viewer announcement occurs.
        result = RetroCornerManager._finish_locked(self, state, completed_at)
        if self._run_store is not None and result.status == "completed":
            try:
                run = self._run_store.record_finished(
                    now=completed_at,
                    nethack_still_active=self._active_game_reader() == GAME_NAME,
                )
                self._run_history_error = None
                self._remember_run_in_state(state, run)

                status = run.get("status")
                run_id = run.get("run_id")
                if status in _TERMINAL_RUN_STATUSES and isinstance(run_id, str):
                    try:
                        retrospective = self._retrospective.generate(
                            run_id=run_id,
                            now=completed_at,
                        )
                        self._remember_retrospective(state, retrospective)
                    except Exception as exc:
                        # Postmortem is analytics/viewer output only. Never
                        # disturb the already-finalized game/run on failure.
                        state["retrospective_status"] = "error"
                        state["retrospective_error"] = _safe_detail(exc)
                else:
                    state["retrospective_status"] = "not_terminal"
                    state.pop("retrospective_summary", None)
            except Exception as exc:
                # Same fail-open run-history behavior as the stable manager.
                self._run_history_error = _safe_detail(exc)
                state["run_history_error"] = self._run_history_error

        self._announce_end_locked(state)
        try:
            self._write_state(state)
        except Exception:
            pass
        return result


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config_path = Path(args.config) if args.config else None
    try:
        g = load_global(_repo_root(), config_path)
        manager = NethackP5CornerManager(g)
        if args.command == "status":
            state = manager.status()
            if args.json:
                print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
            else:
                print(
                    "nethack-corner: "
                    f"status={state.get('status')} game={state.get('game')} "
                    f"previous={state.get('previous_game')} ends_at={state.get('ends_at')}"
                )
            return 0
        if args.command == "recover":
            result = manager.coordinator.recover()
            print(_recover_json(result))
            return 0
        result = getattr(manager, args.command)()
        print(
            json.dumps(
                {
                    "status": result.status,
                    "game": result.game,
                    "previous_game": result.previous_game,
                    "detail": result.detail,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0
    except (
        ConfigError,
        RetroCornerError,
        NethackRunError,
        NethackRetrospectiveError,
        RuntimeError,
    ) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
