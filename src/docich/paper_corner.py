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

# Spread narration across the whole 30-minute corner instead of front-loading
# four long segments and then going quiet. 170s gives ten narration slots in a
# 30-minute run (plus opening/end), while legacy active states keep their old
# 5-minute replay cadence for safe crash recovery.
NARRATION_INTERVAL_S = 170
LEGACY_INTERVAL_S = 300
SCRIPT_SLOTS = {1: 1, 4: 2, 7: 3, 10: 4}


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
        # narration or no improvement job (end of corner).
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
        # Delivery/dedupe namespace for announcements. The scheduled corner
        # delivers each announcement once per day; out-of-band runners (manual
        # tests) override this so they neither replay nor consume the daily
        # corner's audio deliveries.
        self.delivery_scope = 'paper-corner'
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

    def _event_id(self, state, key) -> str:
        """Delivery/dedupe key for one announcement (never expires)."""
        return f'{self.delivery_scope}:{state.get("date", "nodate")}:{key}'

    def announce(self, state, key, text) -> None:
        """Deliver a one-off corner announcement (overlay+speech, durable)."""
        reports = state.setdefault('reports', {})
        key = str(key)
        if key not in reports:
            reports[key] = {'text': str(text), 'overlay': False, 'speech': False}
            self.save(state)
        report = reports[key]
        event_id = self._event_id(state, key)
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

    def deliver(self, state, slot, text):
        """Durably deliver one periodic/end message without repeating the intro."""
        self.announce(state, str(slot), str(text))

    def _announce_script(self, state) -> None:
        """Prepare four fact-grounded narration segments for later delivery.

        Earlier versions spoke all four immediately at corner start, which made
        the first few minutes dense and the rest of a 30-minute corner empty.
        The cleaned segments are now stored in bounded state and distributed by
        `_scheduled_narration`; raw model output is still never persisted.
        """
        existing = state.get('script_segments')
        if isinstance(existing, dict) and existing:
            return
        from .trading.corner_script import SEGMENT_KEYS, generate_corner_script

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
        prepared = {}
        for index, key in enumerate(SEGMENT_KEYS, start=1):
            text = str(segments.get(key, '')).strip()
            if text:
                prepared[str(index)] = text[:600]
        state['script_segments'] = prepared
        state['script_source'] = result.get('source')
        if result.get('reason'):
            state['script_reason'] = str(result.get('reason'))[:120]
        else:
            state.pop('script_reason', None)
        self.save(state)

    @staticmethod
    def _fmt_money(value) -> str:
        try:
            return f'{float(value):,.0f}円'
        except (TypeError, ValueError, OverflowError):
            return '確認待ち'

    def _casual_text(self, slot: int) -> str:
        """Fresh, factual filler between the four longer AI segments."""
        try:
            from .trading.corner_script import build_facts
            facts = build_facts(self.trading_dir, now=self.clock())
        except Exception:
            return ('相場が静かな時間も、BOTにとっては立派な判断材料です。'
                    '無理に売買回数を増やさず、次に条件がそろうまで値動きと見送り理由を眺めていきます。')

        focus = facts.get('focus') if isinstance(facts.get('focus'), dict) else {}
        perf = facts.get('performance') if isinstance(facts.get('performance'), dict) else {}
        policy = facts.get('policy') if isinstance(facts.get('policy'), dict) else {}
        fills = facts.get('recent_fills') if isinstance(facts.get('recent_fills'), list) else []
        skipped = facts.get('skipped_decisions') if isinstance(facts.get('skipped_decisions'), list) else []
        variant = int(slot) % 7

        if variant == 0:
            symbol = str(focus.get('symbol') or '注目銘柄')
            change = focus.get('change_pct')
            change_text = f'{float(change):+.2f}%' if isinstance(change, (int, float)) else '値動きを観測中'
            return (f'BOT側ではいま{symbol}を注目していて、保存足ベースでは{change_text}です。'
                    '画面のローソクは見やすさのため活発な銘柄へ一時退避することがありますが、売買判断そのものは別です。')
        if variant == 1:
            count = int(facts.get('candidate_count', 0) or 0)
            reasons = [str(x) for x in (facts.get('candidate_reasons') or []) if str(x).strip()]
            why = f'主な理由は「{reasons[0]}」です。' if reasons else 'まだ条件の決め手がありません。'
            return (f'いま売買候補は{count}件です。{why}'
                    '候補ゼロも故障ではなく、手数料やスリッページを払ってまで入る価値がないなら待つ、というのも戦略です。')
        if variant == 2:
            return (f'資金配分を見てみると、模擬資金は{self._fmt_money(facts.get("capital_jpy"))}、'
                    f'投入は{self._fmt_money(facts.get("deployed_jpy"))}、保有は{int(facts.get("position_count", 0) or 0)}銘柄です。'
                    '余力を残している時間は地味ですが、急な値動きに反応できる余白でもあります。')
        if variant == 3:
            cumulative = self._fmt_money(perf.get('cumulative_pnl_jpy'))
            today = self._fmt_money(perf.get('today_realized_pnl_jpy'))
            unrealized = self._fmt_money(perf.get('unrealized_pnl_jpy'))
            return (f'損益も途中経過を確認します。累積は{cumulative}、今日の確定分は{today}、含みは{unrealized}です。'
                    '短い区間の勝ち負けだけで作戦の良し悪しを決めず、コスト込みで積み上がるかを見ます。')
        if variant == 4:
            lookback = policy.get('momentum_lookback', '?')
            threshold = policy.get('momentum_threshold_bps', '?')
            z = policy.get('mean_reversion_z', '?')
            return (f'作戦の中身にも少し触れると、勢いは直近{lookback}本を見て、基準上限は{threshold}bpsです。'
                    f'平均回帰側はz={z}あたりを見ています。勢い側は相場の実現ボラで必要幅を調整するので、BTCのような低ボラ時間も拾いやすくしています。')
        if variant == 5 and fills:
            fill = fills[0] if isinstance(fills[0], dict) else {}
            side = '買い' if str(fill.get('side')).lower() == 'buy' else '売り'
            return (f'直近の模擬約定は{fill.get("symbol", "銘柄不明")}の{side}です。'
                    '表示される約定価格にはPAPERでも手数料とスリッページを乗せているので、都合のいい理想価格だけで勝ったことにはしません。')
        if variant == 6 and skipped:
            item = skipped[0] if isinstance(skipped[0], dict) else {}
            symbol = str(item.get('symbol') or '候補')
            reason = str(item.get('reason') or '条件未達')
            return (f'見送り側を見ると、{symbol}は「{reason}」で止まっています。'
                    '売買した話だけでなく、なぜ見送ったかを眺めるとBOTの癖が分かるので、この時間もちゃんと観察対象です。')
        return ('暗号資産はずっと派手に動くわけではありません。こういう無風の時間は、'
                'ローソクの形、候補の増減、保有の偏りをのんびり見ながら、次の変化を待ちます。')

    def _scheduled_narration(self, state, slot: int) -> None:
        if slot <= 0:
            return
        script_index = SCRIPT_SLOTS.get(int(slot))
        segments = state.get('script_segments') if isinstance(state.get('script_segments'), dict) else {}
        if script_index is not None:
            text = str(segments.get(str(script_index), '')).strip()
            if text:
                self.announce(state, f'script:{script_index}', text)
                return
        self.announce(state, f'chatter:{int(slot)}', self._casual_text(int(slot)))

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
                # Narration is an add-on; generation failure must not abort the
                # switch. An empty prepared set still enables fresh casual talk.
                state['script_error'] = _safe_detail(exc)
                state['script_segments'] = {}
                self.save(state)
            write_presentation(self.presentation, 'detailed', now=self.clock())
            started = self.clock()
            state.update(status='active', started_at=started, ends_at=started + self.minutes * 60)
            self.save(state)
        elif self.clock() < state['ends_at']:
            write_presentation(self.presentation, 'detailed', now=self.clock())

        # States created by older code have no script_segments. Keep their old
        # cadence for crash-safe replay; newly started corners use the denser,
        # distributed narration schedule.
        modern = 'script_segments' in state
        interval = NARRATION_INTERVAL_S if modern else LEGACY_INTERVAL_S
        while self.clock() < state['ends_at']:
            elapsed = max(0.0, self.clock() - state['started_at'])
            slot = int(elapsed // interval)
            if modern:
                self._scheduled_narration(state, slot)
            else:
                self.deliver(state, slot, self._casual_text(slot))
            remaining = state['ends_at'] - self.clock()
            if remaining > 0:
                sleep_for = interval - (elapsed % interval)
                self.sleep(min(sleep_for, remaining))
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
