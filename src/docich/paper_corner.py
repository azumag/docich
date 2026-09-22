#!/usr/bin/env python3
"""Content-driven PAPER program: confirmed boundary, sequential narration, restore.

The corner no longer runs for a fixed duration. It generates the next
fact-grounded narration segment one at a time and reads it as soon as it is
ready; the corner ends when the narrator signals there is nothing new to say.
A model/chain failure is recorded as a distinct degraded end, not as material
exhaustion.
"""
import argparse
import datetime as dt
import fcntl
import json
import inspect
import os
import sys
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from .adapters import make_coordinator_adapter
from .adapters.program import PAPER_VIEW_NAME, make_program_view_adapter
from .config import load_global
from .corner_boundary import CornerWaitExpired, program_slot
from .game_switch import GameSwitchStore, atomic_write_json, new_request_id
from .overlay_queue import OVERLAY_BODY_LIMIT
from .procs import user_bus_env
from .tmux import Tmux
from .trading.presentation import write_presentation
from .trading.soren_output import send_overlay, enqueue_speech

# The corner narrates a sequence of fact-grounded segments and ends when the
# narrator has nothing new to say. There is no fixed duration and no explicit
# narration interval: each segment is spoken as soon as it is generated. A
# bounded retry keeps one transient model failure from ending the corner, and a
# lasting failure ends it as a distinct degraded result (not as exhaustion).
NARRATION_AI_RETRIES = 2
# Advisory Soren radio gate. The radio treats a missing/expired flag as
# inactive, so the flag carries a sliding expiry refreshed on every segment.
# If narration makes no progress for this long the radio resumes fail-open.
PAPER_FLAG_TTL_S = 1800
# Soren's durable PAPER delivery names carry this source marker.  The marker
# is intentionally checked on the basename rather than by inspecting speech
# text, which must never become a source-of-truth for lifecycle decisions.
PAPER_AUDIO_SUFFIXES = ("_crypto_paper.txt", "_crypto_paper.playing")
SPEECH_SOURCE_PHASES = frozenset({"waiting", "playing", "retry_wait"})
# Bounded memory of already-spoken topic labels handed back to the narrator so
# the model can avoid repeating itself across a long sequential run.
MAX_COVERED_TOPICS = 24
# After the last segment is generated, wait for the enqueued speech to finish
# playing before handing the display back, so the corner never cuts off its own
# narration. Poll the Soren audio queue (PAPER items + source-specific speaking
# state) and give up after this bound so a wedged audio worker cannot pin the
# corner.
SPEECH_DRAIN_TIMEOUT_S = 1800
SPEECH_DRAIN_POLL_S = 2.0
# Require the queues/playing state to stay empty for several consecutive polls
# so the brief hand-off gap between the comment queue and the say queue cannot
# make the corner end mid-sentence.
SPEECH_DRAIN_STABLE_POLLS = 3


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
                 coordinator=None, spawn=None, stream_game=None, stream_paper=None):
        self.g, self.clock, self.sleep = g, clock, sleep
        self.overlay, self.speech = overlay, speech
        import tomllib
        raw = tomllib.loads(g.config_path.read_text()).get('paper_corner', {})
        self.enabled = raw.get('enabled', False)
        self.hour = raw.get('start_hour', 22)
        self.tz = ZoneInfo(raw.get('timezone', 'Asia/Tokyo'))
        if type(self.enabled) is not bool or type(self.hour) is not int or not 0 <= self.hour <= 23:
            raise ValueError('invalid paper corner schedule')
        # Optional AI narration/improvement delegation. Empty means fallback-only
        # narration or no improvement job (end of corner).
        self.script_agents = self._optional_agents(raw, 'script_agents')
        self.improve_agents = self._optional_agents(raw, 'improve_agents')
        script_timeout = raw.get('script_timeout_s', 180)
        if type(script_timeout) is not int or not 1 <= script_timeout <= 1800:
            raise ValueError('invalid paper corner script timeout')
        self.script_timeout = script_timeout
        self._spawn = spawn or self._default_spawn_improve_proc
        self._stream_game = stream_game or self._default_stream_game
        self._stream_paper = stream_paper or self._default_stream_paper
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
            from .stream_category import commit_hook

            def _factory(spec):
                if spec.game == PAPER_VIEW_NAME:
                    return make_program_view_adapter(g, spec)
                return make_coordinator_adapter(g, spec)

            coordinator = GameSwitchCoordinator(
                self.store, _factory, post_commit=commit_hook(g)
            )
        self.coordinator = coordinator

    def _default_stream_game(self, game: str) -> None:
        from .stream_category import announce_stream_game

        announce_stream_game(self.g, game)

    def _default_stream_paper(self) -> None:
        from .stream_category import announce_stream_paper

        announce_stream_paper(self.g)

    def _announce_stream_game(self, game: str | None) -> None:
        """Best-effort category/title restoration for a real game."""
        if not isinstance(game, str) or not game:
            return
        try:
            self._stream_game(game)
        except Exception as exc:
            print(
                f"[stream-game] status=failed game={game} detail={_safe_detail(exc)}",
                file=sys.stderr,
            )

    def _announce_stream_paper(self) -> None:
        """Best-effort category/title update for the synthetic PAPER view."""
        try:
            self._stream_paper()
        except Exception as exc:
            print(
                f"[stream-game] status=failed game={PAPER_VIEW_NAME} detail={_safe_detail(exc)}",
                file=sys.stderr,
            )

    def _announce_stream_restore(self, previous: object) -> None:
        if previous == PAPER_VIEW_NAME:
            self._announce_stream_paper()
        else:
            self._announce_stream_game(previous)

    @staticmethod
    def _optional_agents(raw, key: str) -> str:
        value = raw.get(key, '')
        if value is None:
            return ''
        if not isinstance(value, str):
            raise ValueError(f'invalid paper corner {key}')
        return value.strip()

    def save(self, state):
        if state.get('status') == 'active':
            from .corner_ownership import bind_runtime
            bind_runtime(self.store, state, PAPER_VIEW_NAME)
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
            # The overlay queue rejects bodies over OVERLAY_BODY_LIMIT, while
            # narration segments (enriched in #424, up to 600-700 chars) are
            # legitimately longer for speech. Truncate only the overlay copy;
            # state and speech keep the full text.
            overlay_body = report['text'][:OVERLAY_BODY_LIMIT]
            self.overlay(self.g, {'ts': int(self.clock()), 'category': 'system', 'level': 'info',
                                 'title': 'PAPER 暗号資産コーナー', 'body': overlay_body, 'source_id': event_id})
            report['overlay'] = True
            self.save(state)
        if not report['speech']:
            self.speech(self.g, report['text'], event_id=event_id)
            report['speech'] = True
            self.save(state)

    def opening_text(self) -> str:
        return ('PAPER・暗号資産の模擬売買コーナーです。ゲーム画面を取引ダッシュボードに'
                '切り替えました。相場・BOTの判断・保有の順で、話すネタが尽きるまでお送りします。'
                + self.summary())

    def deliver(self, state, slot, text):
        """Durably deliver one periodic/end message without repeating the intro."""
        self.announce(state, str(slot), str(text))

    def _paper_flag_path(self):
        from .trading.soren_output import resolve_soren_root

        return resolve_soren_root(self.g) / "tmp" / ".paper_corner_active"

    def _refresh_paper_flag(self, state) -> None:
        """Advertise the program-view window to the Soren radio (advisory only).

        The radio suppresses new generation only while the flag's ``ends_at`` is
        in the future, so the expiry is refreshed on every segment. Failures are
        swallowed: the radio treats a missing/broken flag as inactive, and a
        stalled corner lets the radio resume fail-open instead of staying muted.
        """
        if state.get('status') != 'active':
            return
        try:
            flag = self._paper_flag_path()
            flag.parent.mkdir(parents=True, exist_ok=True)
            now = self.clock()
            atomic_write_json(flag, {
                'date': state.get('date'),
                'started_at': state.get('started_at'),
                'progress_at': state.get('last_progress_at', state.get('started_at')),
                'ends_at': now + PAPER_FLAG_TTL_S,
            })
        except Exception:
            pass

    def _clear_paper_flag(self) -> None:
        try:
            self._paper_flag_path().unlink(missing_ok=True)
        except Exception:
            pass

    @staticmethod
    def _is_paper_audio_path(value: str) -> bool:
        name = Path(str(value or "")).name
        return any(name.endswith(suffix) for suffix in PAPER_AUDIO_SUFFIXES)

    def _current_speech_source(self, root: Path) -> str:
        """Classify Soren's current playback metadata without reading content.

        ``current_source`` is written as ``owner|phase|content|timestamp|label``
        by Soren's ``say_enqueue.sh``.  A well-formed, mutually consistent
        PAPER path/label proves PAPER playback; a well-formed non-PAPER pair
        proves an unrelated source.  Missing or contradictory metadata remains
        unknown so a concurrent ``speaking.json`` state keeps the old
        fail-safe behavior.
        """
        source_file = root / "tmp/.say_queue/current_source"
        try:
            raw = source_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return "missing"
        except OSError:
            return "unknown"
        fields = raw.rstrip("\n").split("|", 4)
        if len(fields) != 5:
            return "unknown"
        owner, phase, content, timestamp, label = fields
        if not owner or phase not in SPEECH_SOURCE_PHASES or not content:
            return "unknown"
        if not timestamp.isdigit():
            return "unknown"
        path_is_paper = self._is_paper_audio_path(content)
        label_is_paper = label == "crypto_paper" or label.startswith("crypto_paper:")
        if path_is_paper != label_is_paper:
            return "unknown"
        return "paper" if path_is_paper else "other"

    def _pending_speech(self) -> bool:
        """True while this corner's audio is still queued or being spoken.

        PAPER's comment queue filenames and Soren's current-source metadata are
        source-specific.  Derived ``.say_queue`` files are not independently
        attributable to PAPER, so they do not block a restore unless the
        current source proves PAPER.  An unreadable/malformed source together
        with ``speaking.json`` remains pending to preserve the old fail-safe;
        unrelated, well-formed playback is allowed to drain independently.
        """
        try:
            from .trading.soren_output import resolve_soren_root

            root = resolve_soren_root(self.g)
            comment_queue = root / "tmp/.comment_queue"
            comment_items = list(comment_queue.glob("comment_*.txt"))
            comment_items.extend(comment_queue.glob("comment_*.playing"))
            if any(self._is_paper_audio_path(str(item)) for item in comment_items):
                return True
            source = self._current_speech_source(root)
            if source == "paper":
                return True
            speaking = (root / "tmp/state/speaking.json").exists()
            if speaking and source != "other":
                return True
            if source == "unknown":
                return True
        except Exception:
            # A source read/parse failure must not weaken the previous
            # speech-drain safety boundary.
            return True
        return False

    def _wait_for_speech(self, state) -> bool:
        """Wait for the enqueued narration to finish playing (bounded).

        Keeps the corner (and the program slot) while speech drains so the
        display is not handed back mid-sentence. Progress is re-advertised so
        the watchdog and radio gate see a live corner; an audio worker that
        never drains is bounded by ``SPEECH_DRAIN_TIMEOUT_S``.
        """
        deadline = self.clock() + SPEECH_DRAIN_TIMEOUT_S
        saw_pending = False
        stable = 0
        while True:
            if self._pending_speech():
                saw_pending = True
                stable = 0
            else:
                if not saw_pending:
                    # Nothing was ever queued (or speech delivery is disabled),
                    # so there is nothing to drain.
                    return True
                stable += 1
                if stable >= SPEECH_DRAIN_STABLE_POLLS:
                    return True
            if self.clock() >= deadline:
                state['speech_drain_timeout'] = True
                self.save(state)
                return False
            # Keep the liveness marker and radio gate fresh while waiting.
            state['last_progress_at'] = self.clock()
            self._refresh_paper_flag(state)
            self.save(state)
            self.sleep(SPEECH_DRAIN_POLL_S)
        return True

    def _end_text(self, state) -> str:
        # Date-stamped so the player-side duplicate suppression (which hashes
        # file content) does not mistake tonight's closing for a replay of a
        # previous corner's identical line and skip it unheard.
        try:
            day = dt.date.fromisoformat(str(state.get('date')))
            return f'{day.month}月{day.day}日のPAPER・暗号資産コーナーを終え、通常の短報に戻ります。'
        except (ValueError, TypeError):
            return 'PAPER・暗号資産コーナーを終え、通常の短報に戻ります。'

    def _ensure_fallback_script(self, state) -> None:
        """Install the finite deterministic narration set once.

        These fact-grounded segments are spoken only when the AI narrator is
        unavailable or has failed; they are the safe, offline base and they are
        finite, so reading them through ends the corner normally.
        """
        existing = state.get('fallback_segments')
        if isinstance(existing, dict) and existing:
            return
        from .trading.corner_script import (
            MAX_SEGMENT_CHARS,
            SEGMENT_KEYS,
            build_facts,
            render_fallback,
        )

        try:
            segments = render_fallback(build_facts(self.trading_dir, now=self.clock()))
        except Exception as exc:
            state['fallback_error'] = _safe_detail(exc)
            segments = render_fallback({})
        prepared = {}
        for index, key in enumerate(SEGMENT_KEYS, start=1):
            text = str(segments.get(key, '')).strip()
            if text:
                prepared[str(index)] = text[:MAX_SEGMENT_CHARS]
        state['fallback_segments'] = prepared
        self.save(state)

    def _ai_narration_enabled(self) -> bool:
        return bool(self.script_agents)

    @staticmethod
    def _covered_topics(state) -> list:
        raw = state.get('covered_topics')
        return [str(item) for item in raw] if isinstance(raw, list) else []

    def _next_narration_item(self, state) -> tuple[str, object]:
        """Return the next narration action as ``(kind, payload)``.

        ``("item", {"key", "text", "topic"})`` speaks one segment,
        ``("done", reason)`` ends after genuine material exhaustion, and
        ``("failed", reason)`` ends a degraded corner whose narrator could not
        produce more output. Failure is never reported as exhaustion.
        """
        reports = state.get('reports') if isinstance(state.get('reports'), dict) else {}
        # Complete a half-delivered generated item first (e.g. overlay committed
        # but the speech sink failed) instead of skipping its missing half.
        pending_seq = int(state.get('narration_seq', 0) or 0)
        if pending_seq:
            report = reports.get(f'ai:{pending_seq}')
            if isinstance(report, dict) and not (report.get('overlay') and report.get('speech')):
                text = str(report.get('text') or '')
                if text:
                    return 'item', {'key': f'ai:{pending_seq}', 'text': text, 'topic': ''}

        if self._ai_narration_enabled() and not state.get('ai_failed'):
            from .trading.corner_script import generate_next_narration

            last_reason = 'generation-failed'
            previous_gate = os.environ.get('DOCICH_ALLOW_REAL_AI')
            os.environ['DOCICH_ALLOW_REAL_AI'] = '1'
            try:
                for _ in range(NARRATION_AI_RETRIES):
                    result = generate_next_narration(
                        self.g,
                        trading_dir=self.trading_dir,
                        agents=self.script_agents,
                        timeout=self.script_timeout,
                        covered=self._covered_topics(state),
                        now=self.clock(),
                    )
                    status = result.get('status') if isinstance(result, dict) else None
                    if status == 'item':
                        seq = int(state.get('narration_seq', 0) or 0) + 1
                        state['narration_seq'] = seq
                        return 'item', {
                            'key': f'ai:{seq}',
                            'text': str(result.get('text', '')),
                            'topic': str(result.get('topic', '')),
                        }
                    if status == 'done':
                        return 'done', 'ai-done'
                    last_reason = str((result or {}).get('reason') or 'generation-failed')[:120]
            finally:
                if previous_gate is None:
                    os.environ.pop('DOCICH_ALLOW_REAL_AI', None)
                else:
                    os.environ['DOCICH_ALLOW_REAL_AI'] = previous_gate
            state['ai_failed'] = True
            state['ai_failure_reason'] = last_reason
            self.save(state)

        segments = state.get('fallback_segments') if isinstance(state.get('fallback_segments'), dict) else {}
        reports = state.get('reports') if isinstance(state.get('reports'), dict) else {}
        for index in sorted(
            segments, key=lambda value: int(value) if str(value).isdigit() else 0
        ):
            report = reports.get(f'fallback:{index}')
            if isinstance(report, dict) and report.get('overlay') and report.get('speech'):
                continue
            text = str(segments[index]).strip()
            if text:
                # A half-delivered item (e.g. overlay committed, speech sink
                # failed) is returned again so announce() completes the pending
                # half without repeating the committed one.
                return 'item', {'key': f'fallback:{index}', 'text': text, 'topic': ''}
        if state.get('ai_failed'):
            return 'failed', str(state.get('ai_failure_reason') or 'generation-failed')
        return 'done', 'fallback-exhausted'

    def _default_spawn_improve_proc(self, argv, log_path) -> None:
        import subprocess

        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        # The parent only spawns when improve_agents is configured; the child
        # needs the explicit real-AI gate (the tick service env lacks it).
        env = dict(os.environ)
        env['DOCICH_ALLOW_REAL_AI'] = '1'
        if sys.platform == 'linux' and os.environ.get('INVOCATION_ID'):
            # setsid does not escape a systemd cgroup: the corner's default
            # KillMode=control-group kills detached children when tick exits.
            # Give improvement its own bounded service, including AI children.
            import uuid

            command = [
                'systemd-run', '--user', '--quiet', '--collect',
                f'--unit=docich-paper-improve-{uuid.uuid4().hex}',
                '--property=Type=exec', '--property=RuntimeMaxSec=1500',
                '--property=TimeoutStopSec=30', '--property=UMask=0077',
                f'--working-directory={self.g.repo_root}',
                f'--property=StandardOutput=append:{Path(log_path).resolve()}',
                '--property=StandardError=inherit',
                '--setenv=DOCICH_ALLOW_REAL_AI=1',
                f'--setenv=PYTHONPATH={self.g.repo_root / "src"}',
            ]
            if env.get('PATH'):
                command.append(f'--setenv=PATH={env["PATH"]}')
            # Do not fall back to the parent's cgroup on submission failure.
            # Do not pass arbitrary inherited credentials on the command line.
            # systemd-run --user resolves the user manager bus from
            # XDG_RUNTIME_DIR; a timer-launched tick unit does not reliably
            # inherit it, so default it here and keep systemd's own stderr in
            # the durable failure record (#947).
            try:
                submitted = subprocess.run(
                    [*command, '--', *argv], check=False, timeout=30,
                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    env=user_bus_env(),
                )
            except subprocess.TimeoutExpired as exc:
                raw = exc.stderr
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8', 'replace')
                detail = _safe_detail((raw or '').strip())
                raise PaperCornerError(
                    '改善ジョブの起動がタイムアウトしました (systemd-run 30s)'
                    + (f': {detail}' if detail else '')
                ) from exc
            if submitted.returncode != 0:
                detail = _safe_detail((submitted.stderr or '').strip())
                raise PaperCornerError(
                    f'改善ジョブの起動に失敗しました (rc={submitted.returncode})'
                    + (f': {detail}' if detail else '')
                )
            return
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
        from .corner_catalog import rotation_enabled
        if rotation_enabled(self.g):
            from .corner_rotation import CornerRotationManager
            return CornerRotationManager(self.g).tick()["status"]
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
        from .corner_catalog import rotation_enabled
        if rotation_enabled(self.g):
            from .corner_rotation import run_manual
            return run_manual(self.g, self, [PAPER_VIEW_NAME])
        return self._with_guard(self._start_locked)

    def _prewarm_script(self, state) -> None:
        """Prepare the finite fallback narration while waiting for a boundary.

        Base implementation is a no-op; FastPaperCornerManager installs the
        deterministic fallback set during the wait so the corner can speak
        immediately after the switch even if the first AI generation is slow.
        """
        return None

    def run_rotation(self, request_id):
        """Identity-preserving common rotation execution, without the daily gate."""
        self._require_outputs()
        self.delivery_scope = f'corner-rotation:{request_id}'
        def run():
            state = self._read_state()
            if state.get('rotation_request_id') == request_id:
                if state.get('status') == 'completed':
                    return 'completed'
                if state.get('status') == 'restoring':
                    return self._restore_locked(state)
                if state.get('status') in ('starting', 'active'):
                    return self._run_locked(state)
                raise PaperCornerError('rotation execution requires recovery')
            if state.get('status', 'idle') not in ('idle', 'completed', 'interrupted'):
                raise PaperCornerError('rotation execution owner mismatch')
            return self._start_locked(rotation_request_id=request_id)
        return self._with_guard(run)

    def _start_locked(self, rotation_request_id=None):
        state = self._read_state()
        if state.get('status') in ('starting', 'active'):
            return 'already-active'
        if state.get('status') in ('failed', 'restoring'):
            previous = state.get('previous_game')
            # A failed run whose display is already back at the previous game
            # has nothing left to restore. An operator/manual start must then
            # actually start instead of no-oping on the stale failure.
            already_home = (
                state.get('status') == 'failed'
                and previous is not None
                and self._active_game() == previous
            )
            if not already_home:
                return self._restore_locked(state)
        state = {
            'status': 'starting',
            'date': dt.datetime.fromtimestamp(self.clock(), self.tz).date().isoformat(),
            'previous_game': self._active_game(),
            'requested_at': self.clock(),
        }
        if rotation_request_id is not None:
            canonical, _ = self.store.canonical.load()
            if canonical.get('phase') not in ('idle', 'ready', 'draining'):
                raise PaperCornerError('rotation canonical requires recovery')
            active = canonical.get('active') or {}
            state['previous_game'] = active.get('game')
            if state['previous_game'] == PAPER_VIEW_NAME:
                raise PaperCornerError('rotation target already owned by another execution')
            state.update(rotation_request_id=rotation_request_id,
                         switch_request_id=rotation_request_id, live_eligible=False)
        self.save(state)
        try:
            # Manual/operator start: prepare the finite fallback before the
            # display switches, so narration can begin immediately. AI segments
            # are generated one at a time after the switch.
            self._ensure_fallback_script(state)
        except Exception:
            pass
        return self._run_locked(state)

    def stop(self):
        from .corner_catalog import rotation_enabled
        if rotation_enabled(self.g):
            from .corner_rotation import stop_manual
            return stop_manual(self.g, self, lambda: self._with_guard(self._stop_locked))
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
        if wait_boundary and state.get('status') == 'waiting':
            # Still waiting on the match boundary: use the idle time to get
            # scripts ready, so the corner narrates from the first minute.
            self._prewarm_script(state)
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

    @staticmethod
    def _invoke_transition(method, target=None, *, request_id=None):
        try:
            parameters = inspect.signature(method).parameters.values()
            accepts_request_id = any(
                parameter.name == "request_id"
                or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            accepts_request_id = True
        kwargs = {"request_id": request_id} if request_id is not None and accepts_request_id else {}
        return method(target, **kwargs) if target is not None else method(**kwargs)

    @staticmethod
    def _is_queued(result) -> bool:
        return getattr(result, "status", None) in {"queued", "in_progress", "busy"}

    def _restore_locked(self, state) -> str:
        """Hand the display back. Never kills a game the view did not displace.

        Returns the terminal result string ('completed' or 'failed').
        """
        if state.get('status') in ('active', 'restoring'):
            from .corner_ownership import verify_runtime
            verify_runtime(self.store, state, PAPER_VIEW_NAME)
        previous = state.get('previous_game')
        current = self._active_game()
        if current is not None and current != PAPER_VIEW_NAME and current != previous:
            # The operator moved on mid-corner; do not yank their game back.
            write_presentation(self.presentation, 'compact', now=self.clock())
            state.update(status='completed', completed_at=self.clock(),
                         last_error=None,
                         detail='operator switched during corner; restore skipped')
            self._spawn_improve_once(state)
            self._clear_paper_flag()
            self.save(state)
            return 'completed'
        if previous is not None and current == previous:
            # Already home (manual recovery or idempotent retry).
            write_presentation(self.presentation, 'compact', now=self.clock())
            self._announce_stream_restore(previous)
            state.update(status='completed', completed_at=self.clock(), last_error=None)
            self._spawn_improve_once(state)
            self._clear_paper_flag()
            self.save(state)
            return 'completed'
        if previous is None and current is not None and current != PAPER_VIEW_NAME:
            # No evidence the view ever started (legacy active state):
            # never stop the user's live game.
            write_presentation(self.presentation, 'compact', now=self.clock())
            state.update(status='completed', completed_at=self.clock(), last_error=None,
                         detail='no view session evidence; live game left running')
            self._clear_paper_flag()
            self.save(state)
            return 'completed'
        # Restoring: hand the display back to the previous game first,
        # then fall back to compact notifications. A failed restore stays
        # failed (never silently completed) so the next tick retries it.
        state['status'] = 'restoring'
        request_id = state.get('switch_request_id')
        if not isinstance(request_id, str):
            request_id = new_request_id()
            state['switch_request_id'] = request_id
        self.save(state)
        try:
            if previous is None:
                result = self._invoke_transition(self.coordinator.stop, request_id=request_id)
                if self._is_queued(result):
                    state['switch_status'] = getattr(result, 'status', 'queued')
                    self.save(state)
                    return 'queued'
                self._require_success(result, 'program view stop')
            elif previous == PAPER_VIEW_NAME:
                pass
            else:
                result = self._invoke_transition(
                    self.coordinator.switch, previous, request_id=request_id
                )
                if self._is_queued(result):
                    state['switch_status'] = getattr(result, 'status', 'queued')
                    self.save(state)
                    return 'queued'
                self._require_success(result, f'program view->{previous} restore')
            self._announce_stream_restore(previous)
        except PaperCornerError as exc:
            state.update(status='failed', completed_at=self.clock(), last_error=str(exc)[:240])
            self._clear_paper_flag()
            self.save(state)
            return 'failed'
        # Restore compact even when completion output is temporarily unavailable.
        write_presentation(self.presentation, 'compact', now=self.clock())
        self.deliver(state, 'end', self._end_text(state))
        state.pop('switch_request_id', None)
        state.pop('switch_status', None)
        state.update(status='completed', completed_at=self.clock(), last_error=None)
        self._spawn_improve_once(state)
        self._clear_paper_flag()
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
            request_id = state.get('switch_request_id')
            if not isinstance(request_id, str):
                request_id = new_request_id()
                state['switch_request_id'] = request_id
                self.save(state)
            if previous is None:
                result = self._invoke_transition(
                    self.coordinator.start, PAPER_VIEW_NAME, request_id=request_id
                )
            elif previous == PAPER_VIEW_NAME:
                result = None
            else:
                # The match runs to its boundary first (repo rule: never
                # kill a match mid-game). Tell viewers the switch is
                # pending so the continuing game is not confusing.
                self.announce(state, 'switch-notice',
                              'まもなくPAPER・暗号資産の模擬売買コーナーのため、試合終了後に画面を切り替えます。')
                result = self._invoke_transition(
                    self.coordinator.switch, PAPER_VIEW_NAME, request_id=request_id
                )
            if result is not None and self._is_queued(result):
                state['switch_status'] = getattr(result, 'status', 'queued')
                self.save(state)
                return 'queued'
            if result is not None:
                self._require_success(result, f'{previous or "(none)"}->program view switch')
            state.pop('switch_request_id', None)
            state.pop('switch_status', None)
            # Post-commit stop verification: the displaced game must be
            # gone from canonical. A mismatch fails (and retries) instead
            # of silently showing the dashboard over a live game.
            committed = self._active_game()
            if committed is not None and committed != PAPER_VIEW_NAME:
                raise PaperCornerError(
                    f"切替後に旧ゲームが残っています: {committed}")
            self._announce_stream_paper()
            self.announce(state, 'opening', self.opening_text())
            write_presentation(self.presentation, 'detailed', now=self.clock())
            started = self.clock()
            state.update(status='active', started_at=started, last_progress_at=started)
            self.save(state)
            try:
                # Narration is an add-on; a fallback-generation failure must not
                # abort the switch. An empty fallback set still ends cleanly.
                self._ensure_fallback_script(state)
            except Exception as exc:
                state['fallback_error'] = _safe_detail(exc)
                state.setdefault('fallback_segments', {})
                self.save(state)
        elif state.get('status') == 'active':
            from .corner_ownership import verify_runtime
            verify_runtime(self.store, state, PAPER_VIEW_NAME)
            write_presentation(self.presentation, 'detailed', now=self.clock())
        if state.get('status') == 'active':
            # Tell the Soren radio a program view is showing (also heals a
            # flag lost to a crash mid-corner).
            self._refresh_paper_flag(state)

        # Sequential narration: generate the next segment and speak it as soon
        # as it is ready. There is no fixed interval and no fixed duration; the
        # loop ends when the narrator has nothing new to say (or a lasting
        # generation failure degrades the corner, recorded separately).
        while True:
            from .corner_rotation import clear_rotation_stop_request, rotation_stop_requested
            if rotation_stop_requested(self.g, self.path):
                result = self._restore_locked(state)
                if result == 'completed':
                    clear_rotation_stop_request(self.g, self.path)
                return result
            kind, payload = self._next_narration_item(state)
            if kind == 'item':
                self.announce(state, payload['key'], payload['text'])
                topic = payload.get('topic')
                if topic:
                    covered = self._covered_topics(state)
                    covered.append(str(topic))
                    state['covered_topics'] = covered[-MAX_COVERED_TOPICS:]
                state['last_progress_at'] = self.clock()
                self._refresh_paper_flag(state)
                self.save(state)
                continue
            if kind == 'failed':
                state['end_reason'] = 'generation-failed'
                state['end_detail'] = str(payload)[:240]
                state['degraded'] = True
            else:
                state['end_reason'] = 'exhausted'
            self.save(state)
            break
        # The narration material is exhausted, but its speech may still be
        # playing. Wait for the audio queue to drain before handing the display
        # back so the corner never cuts off its own closing segments.
        self._wait_for_speech(state)
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
