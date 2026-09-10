"""Daily PAPER program: confirmed boundary, actual start plus duration, durable replay."""
import argparse
import datetime as dt
import fcntl
import json
import os
import sys
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from .adapters import make_coordinator_adapter
from .adapters.program import PAPER_VIEW_NAME, make_program_view_adapter
from .config import load_global
from .corner_boundary import CornerWaitExpired, program_slot
from .game_switch import GameSwitchStore, atomic_write_json
from .tmux import Tmux
from .trading.presentation import write_presentation
from .trading.soren_output import send_overlay, enqueue_speech


def ensure_trading_window(g, tmux=None) -> str:
    """Recreate the shared trading window when missing or dead.

    Safety net for Issue #219 (shared docich session/worker loss during
    switches). Creates only; a live pane is never touched. Best-effort: any
    failure returns a reason instead of raising, so the corner tick outcome
    is unaffected. Gated on paper_worker_enabled, mirroring cmd_up.
    Returns 'ok' | 'disabled' | 'created' | 'recreated' | 'unavailable:<why>'.
    """
    try:
        trading = getattr(g, 'trading', None)
        if not getattr(trading, 'paper_worker_enabled', False):
            return 'disabled'
        tm = tmux if tmux is not None else Tmux()
        tm.ensure_session()
        if tm.has_window('trading'):
            try:
                states = tm.pane_states_checked(f'{tm.session}:trading')
            except Exception:
                states = []
            if states and not any(pane.dead for pane in states):
                return 'ok'
            tm.kill_window('trading')
            action = 'recreated'
        else:
            action = 'created'
        bin_path = str(Path(__file__).resolve().parents[2] / 'bin' / 'docich')
        tm.new_window('trading', [bin_path, '--config', str(g.config_path), 'run', 'trading'])
        return action
    except Exception as exc:
        return f'unavailable:{type(exc).__name__}'


class PaperCornerError(RuntimeError):
    """User-facing failure in the daily PAPER corner."""


def _safe_detail(value: BaseException | str) -> str:
    return str(value).replace("\n", " ")[:240]


class PaperCornerManager:
    def __init__(self, g, *, clock=time.time, sleep=time.sleep, overlay=send_overlay, speech=enqueue_speech,
                 coordinator=None, spawn=None):
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
        # Optional AI narration/improvement delegation. Empty means fallback-only
        # (start narration) or no improvement job (end of corner).
        self.script_agents = self._optional_agents(raw, 'script_agents')
        self.improve_agents = self._optional_agents(raw, 'improve_agents')
        script_timeout = raw.get('script_timeout_s', 180)
        if type(script_timeout) is not int or not 1 <= script_timeout <= 1800:
            raise ValueError('invalid paper corner script timeout')
        self.script_timeout = script_timeout
        self._spawn = spawn or self._default_spawn_improve_proc
        self.path = g.state_dir / 'paper_corner.json'
        self.trading_dir = g.state_dir / 'trading'
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

    @staticmethod
    def _optional_agents(raw, key: str) -> str:
        value = raw.get(key, '')
        if value is None:
            return ''
        if not isinstance(value, str):
            raise ValueError(f'invalid paper corner {key}')
        return value.strip()

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

    def announce(self, state, key, text) -> None:
        """Deliver a one-off corner announcement (overlay+speech, durable)."""
        reports = state.setdefault('reports', {})
        key = str(key)
        if key not in reports:
            reports[key] = {'text': str(text), 'overlay': False, 'speech': False}
            self.save(state)
        report = reports[key]
        event_id = f'paper-corner:{state.get("date", "nodate")}:{key}'
        if not report['overlay']:
            self.overlay(self.g, {'ts': int(self.clock()), 'category': 'system', 'level': 'info',
                                 'title': 'PAPER 暗号資産コーナー', 'body': report['text'], 'source_id': event_id})
            report['overlay'] = True
            self.save(state)
        if not report['speech']:
            self.speech(self.g, report['text'], event_id=event_id)
            report['speech'] = True
            self.save(state)

    def opening_text(self) -> str:
        return ('PAPER・暗号資産の模擬売買コーナーです。ゲーム画面を取引ダッシュボードに'
                f'切り替えました。実際の開始から{self.minutes}分間、相場・BOTの判断・保有の順でお送りします。'
                + self.summary())

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

    def _announce_script(self, state) -> None:
        """Speak the 4 fact-grounded narration segments once per corner.

        AI is attempted only when script_agents is set and
        DOCICH_ALLOW_REAL_AI=1; otherwise the deterministic fallback is spoken.
        Durable per-key reports make a retry idempotent and skip regeneration.
        """
        keys = [f'script:{index}' for index in range(1, 5)]
        reports = state.get('reports') if isinstance(state.get('reports'), dict) else {}
        if all(key in reports for key in keys):
            return
        from .trading.corner_script import SEGMENT_KEYS, generate_corner_script

        # Non-empty script_agents is the explicit consent to run real AI (same
        # convention as retro_corner improve_agents). The tick service does not
        # carry DOCICH_ALLOW_REAL_AI, so grant it for this call only; the
        # generator still falls back deterministically on any failure.
        previous_gate = os.environ.get('DOCICH_ALLOW_REAL_AI')
        if self.script_agents:
            os.environ['DOCICH_ALLOW_REAL_AI'] = '1'
        try:
            result = generate_corner_script(
                self.g,
                trading_dir=self.trading_dir,
                agents=self.script_agents,
                timeout=self.script_timeout,
                now=self.clock(),
            )
        finally:
            if self.script_agents:
                if previous_gate is None:
                    os.environ.pop('DOCICH_ALLOW_REAL_AI', None)
                else:
                    os.environ['DOCICH_ALLOW_REAL_AI'] = previous_gate
        segments = result.get('segments') or {}
        state['script_source'] = result.get('source')
        if result.get('reason'):
            state['script_reason'] = str(result.get('reason'))[:120]
        else:
            state.pop('script_reason', None)
        for index, key in enumerate(SEGMENT_KEYS, start=1):
            text = str(segments.get(key, '')).strip()
            if not text:
                continue
            self.announce(state, f'script:{index}', text)

    def _default_spawn_improve_proc(self, argv, log_path) -> None:
        import subprocess

        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        # The parent only spawns when improve_agents is configured; the child
        # needs the explicit real-AI gate (the tick service env lacks it).
        env = dict(os.environ)
        env['DOCICH_ALLOW_REAL_AI'] = '1'
        with open(log_path, 'ab') as log_fh:
            subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=str(self.g.repo_root),
                env=env,
            )

    def _spawn_improve_once(self, state) -> None:
        """Detach the end-of-corner improvement job. Never fails the corner."""
        agents = (self.improve_agents or '').strip()
        date_str = state.get('date')
        if not agents or not isinstance(date_str, str) or not date_str:
            return
        try:
            dt.date.fromisoformat(date_str)
        except ValueError:
            state['improve_job'] = {'spawned': False, 'error': f'日付が不正です: {date_str}'}
            return
        log_path = self.g.state_dir / 'logs' / f'paper-corner-improve-{date_str}.log'
        argv = [
            sys.executable, '-m', 'docich', '--config', str(self.g.config_path),
            'trading', 'paper-improve', '--date', date_str,
        ]
        try:
            self._spawn(argv, log_path)
            state['improve_job'] = {'spawned': True, 'date': date_str, 'log': str(log_path)}
        except Exception as exc:
            state['improve_job'] = {'spawned': False, 'error': _safe_detail(exc)}

    def tick(self):
        if not self.enabled:
            return 'disabled'
        self._require_outputs()
        return self._with_guard(self._tick_guarded)

    def _require_outputs(self):
        if not all((self.g.trading.paper_worker_enabled, self.g.trading.notifications_enabled,
                    self.g.trading.notification_speech_enabled)):
            raise ValueError('paper corner requires enabled paper worker and outputs')

    def _with_guard(self, fn):
        self.tick_guard_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.tick_guard_path.parent, 0o700)
        guard = self.tick_guard_path.open('a+', encoding='utf-8')
        try:
            try:
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 'already-running'
            try:
                return fn()
            finally:
                fcntl.flock(guard.fileno(), fcntl.LOCK_UN)
        finally:
            guard.close()

    def start(self):
        """Start the PAPER view immediately (operator/manual test).

        Bypasses the schedule and the program boundary. The switch still goes
        through the coordinator, so a running match is never cut mid-game.
        """
        self._require_outputs()
        return self._with_guard(self._start_locked)

    def _start_locked(self):
        state = self._read_state()
        if state.get('status') in ('starting', 'active'):
            return 'already-active'
        if state.get('status') in ('failed', 'restoring'):
            return self._restore_locked(state)
        state = {
            'status': 'starting',
            'date': dt.datetime.fromtimestamp(self.clock(), self.tz).date().isoformat(),
            'previous_game': self._active_game(),
            'requested_at': self.clock(),
        }
        self.save(state)
        return self._run_locked(state)

    def stop(self):
        return self._with_guard(self._stop_locked)

    def _stop_locked(self):
        state = self._read_state()
        if state.get('status') not in ('starting', 'active', 'failed', 'restoring'):
            return 'not-active'
        return self._restore_locked(state)

    def _tick_guarded(self):
        now = dt.datetime.fromtimestamp(self.clock(), self.tz)
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
        deadline = end_of_day.timestamp()
        # Record the boundary request outside the program slot so a corner
        # waiting on its boundary never blocks a ready corner.
        state = json.loads(self.path.read_text()) if self.path.exists() else {}
        status = state.get('status')
        requested_at = None
        wait_boundary = False
        if status == 'waiting':
            if state.get('date') != now.date().isoformat():
                # A previous day's request that never reached a boundary;
                # never fire it late.
                self.save({'status': 'idle', 'date': None})
                return 'expired'
            requested_at = state.get('requested_at')
            if isinstance(requested_at, bool) or not isinstance(requested_at, (int, float)):
                requested_at = self.clock()
                state['requested_at'] = requested_at
                self.save(state)
            wait_boundary = True
        elif status not in ('starting', 'active', 'failed', 'restoring'):
            if now.hour < self.hour or state.get('date') == now.date().isoformat():
                return 'not-due'
            requested_at = self.clock()
            state = {'status': 'waiting', 'date': now.date().isoformat(), 'requested_at': requested_at}
            self.save(state)
            wait_boundary = True
        try:
            with program_slot(
                self.g,
                self.path,
                requested_at=requested_at if requested_at is not None else self.clock(),
                wait_deadline_ts=deadline,
                wait_boundary=wait_boundary,
                sleep=self.sleep,
                now=self.clock,
            ) as root:
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
            self._spawn_improve_once(state)
            self.save(state)
            return 'completed'
        if previous is not None and current == previous:
            # Already home (manual recovery or idempotent retry).
            write_presentation(self.presentation, 'compact', now=self.clock())
            state.update(status='completed', completed_at=self.clock(), last_error=None)
            self._spawn_improve_once(state)
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
        self._spawn_improve_once(state)
        self.save(state)
        return 'completed'

    def _read_state(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _tick_locked(self, root, now):
        state = self._read_state()
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
            state['status'] = 'starting'
            self.save(state)
        return self._run_locked(state)

    def _run_locked(self, state):
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
                # The match runs to its boundary first (repo rule: never
                # kill a match mid-game). Tell viewers the switch is
                # pending so the continuing game is not confusing.
                self.announce(state, 'switch-notice',
                              'まもなくPAPER・暗号資産の模擬売買コーナーのため、試合終了後に画面を切り替えます。')
                self._require_success(
                    self.coordinator.switch(PAPER_VIEW_NAME), f'{previous}->program view switch')
            # Post-commit stop verification: the displaced game must be
            # gone from canonical. A mismatch fails (and retries) instead
            # of silently showing the dashboard over a live game.
            committed = self._active_game()
            if committed is not None and committed != PAPER_VIEW_NAME:
                raise PaperCornerError(
                    f"切替後に旧ゲームが残っています: {committed}")
            self.announce(state, 'opening', self.opening_text())
            try:
                self._announce_script(state)
            except Exception as exc:
                # Narration is an add-on; a sink/generation failure must not
                # abort the switch that already committed.
                state['script_error'] = _safe_detail(exc)
                self.save(state)
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
        guard = ensure_trading_window(manager.g)
        if guard in ('created', 'recreated'):
            print(f'trading window {guard}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
