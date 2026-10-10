"""実観測の短い実況を記録し、設定済みの音声キューへ入力を待たせず送る。"""
from __future__ import annotations
import json, os, time, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

class AudioClient:
    def __init__(self, base_url, token=''):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('audio URL must be an HTTP base without embedded credentials')
        self.base = base_url.rstrip('/')
        self.token = token

    def _request(self, path, payload=None, csrf=''):
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if csrf:
            headers['X-CSRF-Token'] = csrf
        data = None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = Request(self.base + path, data=data, headers=headers)
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    def send(self, text):
        csrf = self._request('/api/csrf').get('csrf_token')
        if not csrf:
            raise RuntimeError('audio CSRF response missing')
        result = self._request('/api/audio/enqueue', {'text': text, 'source': 'flyhome_commentary'}, csrf)
        if result.get('ok') is not True:
            raise RuntimeError('audio enqueue was not acknowledged')
        return {'ok': True, 'dedup': bool(result.get('dedup'))}

class SpeechJournal:
    def __init__(self, path, client=None):
        self.path = Path(path)
        self.client = client
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='flyhome-narration')
        self.futures = []
        self.published = self.accepted = self.failed = 0
        self.delivery = 'local_pending'

    @classmethod
    def from_env(cls):
        url = os.environ.get('FLYHOME_AUDIO_URL', '')
        client = AudioClient(url, os.environ.get('FLYHOME_AUDIO_TOKEN', '')) if url else None
        return cls(os.environ.get('FLYHOME_NARRATION_FILE', 'run/flyhome/narration.jsonl'), client)

    def publish(self, text, kind, level=None, attempt=None, ttl_s=180):
        if not text or len(text) > 240 or '\n' in text:
            raise ValueError('speech must be a single sentence group of at most 240 characters')
        self._collect()
        now = time.time()
        event = {'id': str(uuid.uuid4()), 'created_at': now, 'expires_at': now + ttl_s,
                 'kind': kind, 'level': level, 'attempt': attempt, 'text': text,
                 'source': 'flyhome_commentary'}
        self.published += 1
        self.futures.append(self.executor.submit(self._write_send, event))
        return event['id']

    def _write_send(self, event):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        if self.client is None:
            return
        receipt = {'id': event['id'], 'at': time.time(), 'status': 'expired'}
        if receipt['at'] <= event['expires_at']:
            try:
                receipt.update(self.client.send(event['text']), status='accepted')
                self.accepted += 1
            except Exception as exc:
                # URL、認証ヘッダ、サーバーの応答本文をログへ複製しない。
                receipt.update(status='pending', error=type(exc).__name__)
                self.failed += 1
        self.delivery = receipt['status']
        with self.path.with_suffix('.receipts.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(receipt) + '\n')

    def _collect(self):
        while self.futures and self.futures[0].done():
            self.futures.pop(0).result()

    def metrics(self):
        self._collect()
        return {'audio_delivery': self.delivery, 'audio_published': self.published,
                'audio_accepted': self.accepted, 'audio_failed': self.failed}

    def close(self):
        self.executor.shutdown(wait=True)
        for future in self.futures:
            future.result()
        self.futures.clear()

class LiveNarrator:
    def __init__(self, journal, level):
        self.journal, self.level = journal, level
        self.last_spoken = -1e30
        self.attempt_started = None
        self.last_attempt = None
        self.terminal = set()
        self.journal.publish(f'{level}面を始めます。家に帰るまで実機で挑戦します。', 'level_start', level)

    def step(self, state):
        if state.attempt != self.last_attempt:
            self.last_attempt = state.attempt
            self.attempt_started = state.t
        key = (state.attempt, state.phase)
        if state.phase == 'cleared' and key not in self.terminal:
            self.terminal.add(key)
            self.journal.publish(f'{self.level}面でクリア画面が出ました。次の面へ進みます。',
                                 'clear_screen', self.level, state.attempt, 300)
            self.last_spoken = state.t
        elif state.phase == 'dead' and key not in self.terminal:
            self.terminal.add(key)
            if state.t - self.attempt_started >= 1.2 and state.t - self.last_spoken >= 10:
                self.journal.publish(f'{self.level}面、試行{state.attempt}は失敗しました。もう一度挑戦します。',
                                     'failure', self.level, state.attempt)
                self.last_spoken = state.t
        elif state.phase == 'playing' and state.x is not None and state.t - self.last_spoken >= 15:
            vertical = '上' if state.y < 60 else '下' if state.y > 120 else '中央'
            horizontal = '左' if state.x < 100 else '右' if state.x > 220 else '中央'
            self.journal.publish(f'{self.level}面、試行{state.attempt}。画面の{vertical}、{horizontal}側で家への進路を試しています。',
                                 'progress', self.level, state.attempt)
            self.last_spoken = state.t
