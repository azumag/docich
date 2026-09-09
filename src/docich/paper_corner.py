"""Daily PAPER program: confirmed boundary, actual start plus duration, durable replay."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from .adapters import make_coordinator_adapter
from .adapters.program import PAPER_VIEW_NAME, make_program_view_adapter
from .config import load_global
from .corner_boundary import CornerWaitExpired, program_lock, wait_for_boundary
from .game_switch import GameSwitchStore, atomic_write_json
from .trading.presentation import write_presentation
from .trading.soren_output import send_overlay, enqueue_speech


class PaperCornerError(RuntimeError):
    """User-facing failure in the daily PAPER corner."""


class PaperCornerManager:
    def __init__(self, g, *, clock=time.time, sleep=time.sleep, overlay=send_overlay, speech=enqueue_speech,
                 coordinator=None):
        self.g, self.clock, self.sleep = g, clock, sleep
        self.overlay, self.speech = overlay, speech
        import tomllib
        raw = tomllib.loads(g.config_path.read_text()).get('paper_corner', {})
        self.enabled = raw.get('enabled', False)
        self.hour = raw.get('start_hour', 22)
        self.minutes = raw.get('duration_minutes', 30)
        self.tz = ZoneInfo(raw.get('timezone', 'Asia/Tokyo'))
        if type(self.enabled) is not bool or type(self.hour) is not int or not 0 <= self.hour <= 23:
            raise ValueError('invalid paper corner schedule')
        if type(self.minutes) is not int or not 1 <= self.minutes <= 720:
            raise ValueError('invalid paper corner duration')
        self.path = g.state_dir / 'paper_corner.json'
        self.presentation = g.state_dir / 'trading/presentation.json'
        self.tick_guard_path = g.state_dir / 'locks' / 'paper-corner-tick.lock'
        self.store = GameSwitchStore(g.state_dir)
        if coordinator is None:
            from .game_switch import GameSwitchCoordinator

            def _factory(spec):
                if spec.game == PAPER_VIEW_NAME:
                    return make_program_view_adapter(g, spec)
                return make_coordinator_adapter(g, spec)

            coordinator = GameSwitchCoordinator(self.store, _factory)
        self.coordinator = coordinator

    def save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, state)

    def summary(self):
        try:
            data = json.loads((self.g.state_dir / 'trading/status.json').read_text())
            from decimal import Decimal
            capital = Decimal(data['capital_reference'])
            deployed = Decimal(data['deployed_reference'])
            if not capital.is_finite() or not deployed.is_finite():
                raise ValueError('nonfinite money')
            positions = len(data['open_positions'])
            # Data age (not the worker heartbeat): a freshly started but
            # failing cycle must not look fresh. Older files without snapshot
            # fields fall back to the cycle timestamp.
            import math
            data_as_of = data.get('snapshot_generated_at')
            if data_as_of is None:
                worker_summary = data.get('worker_summary')
                if isinstance(worker_summary, dict):
                    data_as_of = worker_summary.get('last_success_at', data.get('last_cycle_at'))
                else:
                    data_as_of = data.get('last_cycle_at')
            # Non-finite snapshot ages fail safe to stale, never fresh.
            age_base = float(data_as_of)
            stale = (not math.isfinite(age_base)
                     or self.clock() - age_base > max(180, self.g.trading.interval_s * 3))
            text = f'模擬資金{capital:,.0f}円、投入額{deployed:,.0f}円、保有は{positions}銘柄です。'
            if stale:
                text += '集計が古いため、最新状況は確認待ちです。'
            return text
        except (OSError, ValueError, TypeError, KeyError, ArithmeticError):
            return '現在の模擬売買集計は確認待ちです。'

    def deliver(self, state, slot, prefix):
        reports = state.setdefault('reports', {})
        key = str(slot)
        if key not in reports:
            reports[key] = {'text': 'PAPER・暗号資産の模擬売買コーナーです。' + prefix + self.summary(),
                            'overlay': False, 'speech': False}
            self.save(state)
        report = reports[key]
        event_id = f'paper-corner:{state["date"]}:{key}'
        if not report['overlay']:
            self.overlay(self.g, {'ts': int(self.clock()), 'category': 'system', 'level': 'info',
                                 'title': 'PAPER 暗号資産コーナー', 'body': report['text'], 'source_id': event_id})
            report['overlay'] = True
            self.save(state)
        if not report['speech']:
            self.speech(self.g, report['text'], event_id=event_id)
            report['speech'] = True
            self.save(state)

    def tick(self):
        if not self.enabled:
            return 'disabled'
        if not all((self.g.trading.paper_worker_enabled, self.g.trading.notifications_enabled,
                    self.g.trading.notification_speech_enabled)):
            raise ValueError('paper corner requires enabled paper worker and outputs')
        self.tick_guard_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.tick_guard_path.parent, 0o700)
        guard = self.tick_guard_path.open('a+', encoding='utf-8')
        try:
            try:
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 'already-running'
            try:
                return self._tick_guarded()
            finally:
                fcntl.flock(guard.fileno(), fcntl.LOCK_UN)
        finally:
            guard.close()

    def _tick_guarded(self):
        now = dt.datetime.fromtimestamp(self.clock(), self.tz)
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
        try:
            with program_lock(self.g, self.path,
                              wait_deadline_ts=end_of_day.timestamp()) as root:
                return self._tick_locked(root, now)
        except CornerWaitExpired:
            return 'expired'

    def _active_game(self) -> str | None:
        from .game_switch import GameSwitchError

        try:
            state, _missing = self.store.canonical.load()
        except (OSError, ValueError, GameSwitchError) as exc:
            # Corrupt/unreadable canonical fails closed downstream; record
            # the cause where the next save can see it instead of silencing.
            self._active_game_error = str(exc)[:120]
            return None
        if state.get("phase") != "ready":
            return None
        active = state.get("active")
        if not isinstance(active, dict) or not isinstance(active.get("game"), str):
            return None
        return active["game"]

    @staticmethod
    def _require_success(result, action: str) -> None:
        if getattr(result, "status", None) != "succeeded":
            detail = (
                getattr(result, "detail", None)
                or getattr(result, "error_code", None)
                or "unknown"
            )
            raise PaperCornerError(f"{action} に失敗しました: {detail}")

    def _restore_locked(self, state) -> str:
        """Hand the display back. Never kills a game the view did not displace.

        Returns the terminal result string ('completed' or 'failed').
        """
        previous = state.get('previous_game')
        current = self._active_game()
        if current is not None and current != PAPER_VIEW_NAME and current != previous:
            # The operator moved on mid-corner; do not yank their game back.
            write_presentation(self.presentation, 'compact', now=self.clock())
            state.update(status='completed', completed_at=self.clock(),
                         last_error=None,
                         detail='operator switched during corner; restore skipped')
            self.save(state)
            return 'completed'
        if previous is not None and current == previous:
            # Already home (manual recovery or idempotent retry).
            write_presentation(self.presentation, 'compact', now=self.clock())
            state.update(status='completed', completed_at=self.clock(), last_error=None)
            self.save(state)
            return 'completed'
        if previous is None and current is not None and current != PAPER_VIEW_NAME:
            # No evidence the view ever started (legacy active state):
            # never stop the user's live game.
            write_presentation(self.presentation, 'compact', now=self.clock())
            state.update(status='completed', completed_at=self.clock(), last_error=None,
                         detail='no view session evidence; live game left running')
            self.save(state)
            return 'completed'
        # Restoring: hand the display back to the previous game first,
        # then fall back to compact notifications. A failed restore stays
        # failed (never silently completed) so the next tick retries it.
        state['status'] = 'restoring'
        self.save(state)
        try:
            if previous is None:
                self._require_success(self.coordinator.stop(), 'program view stop')
            elif previous == PAPER_VIEW_NAME:
                pass
            else:
                self._require_success(
                    self.coordinator.switch(previous), f'program view->{previous} restore')
        except PaperCornerError as exc:
            state.update(status='failed', completed_at=self.clock(), last_error=str(exc)[:240])
            self.save(state)
            return 'failed'
        # Restore compact even when completion output is temporarily unavailable.
        write_presentation(self.presentation, 'compact', now=self.clock())
        self.deliver(state, 'end', '規定時間を終え、通常の短報に戻ります。')
        state.update(status='completed', completed_at=self.clock(), last_error=None)
        self.save(state)
        return 'completed'

    def _tick_locked(self, root, now):
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
            if state.get('status') in ('failed', 'restoring'):
                # A failed restore (or a crash inside one) retries on the
                # next tick instead of going quiet for the rest of the day.
                return self._restore_locked(state)
            if state.get('status') not in ('waiting', 'starting', 'active'):
                if now.hour < self.hour or state.get('date') == now.date().isoformat():
                    return 'not-due'
                state = {'status': 'waiting', 'date': now.date().isoformat(), 'requested_at': self.clock()}
                self.save(state)
            if state['status'] == 'waiting':
                wait_for_boundary(root, state['requested_at'], sleep=self.sleep)
                state['status'] = 'starting'
                self.save(state)
            if state['status'] == 'starting':
                # M1: record the return target once. Re-entering starting
                # after a crash must not overwrite it with the view itself.
                if 'previous_game' not in state:
                    previous = self._active_game()
                    state['previous_game'] = previous
                    self.save(state)
                else:
                    previous = state.get('previous_game')
                if previous is None:
                    self._require_success(self.coordinator.start(PAPER_VIEW_NAME), 'program view start')
                elif previous == PAPER_VIEW_NAME:
                    pass
                else:
                    self._require_success(
                        self.coordinator.switch(PAPER_VIEW_NAME), f'{previous}->program view switch')
                write_presentation(self.presentation, 'detailed', now=self.clock())
                started = self.clock()
                state.update(status='active', started_at=started, ends_at=started + self.minutes * 60)
                self.save(state)
            elif self.clock() < state['ends_at']:
                write_presentation(self.presentation, 'detailed', now=self.clock())
            while self.clock() < state['ends_at']:
                slot = int((self.clock() - state['started_at']) // 300)
                self.deliver(state, slot, f'実際の開始から{self.minutes}分間お送りします。' if slot == 0 else '')
                remaining = state['ends_at'] - self.clock()
                if remaining > 0:
                    self.sleep(min(300 - ((self.clock() - state['started_at']) % 300), remaining))
            return self._restore_locked(state)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path)
    parser.add_argument('command', choices=['tick', 'status'])
    args = parser.parse_args(argv)
    manager = PaperCornerManager(load_global(Path(__file__).resolve().parents[2], args.config))
    if args.command == 'status':
        print(manager.path.read_text() if manager.path.exists() else '{}')
    else:
        print(manager.tick())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
