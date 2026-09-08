"""Daily PAPER program: confirmed boundary, actual start plus duration, durable replay."""
import argparse
import datetime as dt
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from .config import load_global
from .corner_boundary import program_lock, wait_for_boundary
from .game_switch import atomic_write_json
from .trading.presentation import write_presentation
from .trading.soren_output import send_overlay, enqueue_speech


class PaperCornerManager:
    def __init__(self, g, *, clock=time.time, sleep=time.sleep, overlay=send_overlay, speech=enqueue_speech):
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
            stale = self.clock() - float(data['last_cycle_at']) > max(180, self.g.trading.interval_s * 3)
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
        with program_lock(self.g, self.path) as root:
            now = dt.datetime.fromtimestamp(self.clock(), self.tz)
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
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
            # Restore compact even when completion output is temporarily unavailable.
            write_presentation(self.presentation, 'compact', now=self.clock())
            self.deliver(state, 'end', '規定時間を終え、通常の短報に戻ります。')
            state.update(status='completed', completed_at=self.clock())
            self.save(state)
            return 'completed'


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
