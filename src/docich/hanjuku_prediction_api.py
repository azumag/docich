"""Small fixed-host Helix client for the Hanjuku corner, never the game bot.

No shell execution, token logging, automatic retries, arbitrary endpoints or
redirects. Existing Soren OAuth settings are read afresh on each controller tick.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .trading.soren_output import resolve_soren_root

KEYS = frozenset({'TWITCH_PREDICTIONS_ENABLED', 'TWITCH_PREDICTIONS_TOKEN',
                  'TWITCH_CLIENT_ID', 'TWITCH_BROADCASTER_ID', 'EXPLORE_MODE'})
STATUSES = frozenset({'ACTIVE', 'LOCKED', 'RESOLVED', 'CANCELED'})
MAX_BYTES = 256 * 1024
ENDPOINT = 'https://api.twitch.tv/helix/predictions'


class APIError(RuntimeError):
    def __init__(self, code='transport', *, rejected=False):
        self.code, self.rejected = code, rejected
        super().__init__(code)  # fixed code only, never response bodies/tokens


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def settings(g):
    values = {k: os.environ.get(k, '') for k in KEYS}
    path = resolve_soren_root(g) / '.env'
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise APIError('configuration')
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line.startswith('export '):
                line = line[7:].lstrip()
            key, sep, value = line.partition('=')
            key = key.strip()
            if sep and key in KEYS:
                value = value.strip()
                # Only literal values; never eval/source a shell environment.
                match = re.fullmatch(r'''(['"])(.*?)\1(?:\s+#.*)?''', value)
                if match:
                    value = match[2]
                else:
                    value = value.split(' #', 1)[0].strip()
                values[key] = value
    return values


def configured_client(g):
    cfg = settings(g)
    if cfg['TWITCH_PREDICTIONS_ENABLED'] != '1':
        return None, 'disabled'
    if cfg['EXPLORE_MODE'] == '1':
        return None, 'explore'
    token = cfg['TWITCH_PREDICTIONS_TOKEN'].removeprefix('oauth:')
    client, broadcaster = cfg['TWITCH_CLIENT_ID'], cfg['TWITCH_BROADCASTER_ID']
    if not all((token, client, broadcaster)):
        return None, 'unconfigured'
    if any(re.search(r'\s|[$`]', s) for s in (token, client, broadcaster)):
        raise APIError('configuration')
    # The legacy Soren wrapper recovered *any* live remote prediction and
    # could later resolve our three-way wager as a Soren result. Require the
    # companion ownership-aware wrapper before enabling creation/settlement.
    wrapper = resolve_soren_root(g) / 'twitch_predictions.sh'
    if (not wrapper.is_file() or wrapper.is_symlink() or wrapper.stat().st_size > MAX_BYTES
            or '# SOREN_REMOTE_PREDICTION_OWNERSHIP_V1:' not in wrapper.read_text(encoding='utf-8')):
        return None, 'incompatible_soren'
    return Helix(token, client, broadcaster), None


class Helix:
    def __init__(self, token, client, broadcaster, *, opener=None):
        self._token, self._client = token, client
        self.broadcaster = broadcaster
        self._opener = opener or urllib.request.build_opener(NoRedirect())

    def request(self, method, payload=None, **query):
        query = {'broadcaster_id': self.broadcaster, **query}
        url = ENDPOINT + ('?' + urllib.parse.urlencode(query) if method == 'GET' else '')
        if payload is not None:
            payload = {'broadcaster_id': self.broadcaster, **payload}
        request = urllib.request.Request(
            url, method=method,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
            headers={'Authorization': f'Bearer {self._token}', 'Client-Id': self._client,
                     'Content-Type': 'application/json'})
        try:
            with self._opener.open(request, timeout=5) as response:
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise APIError('invalid_response')
            body = json.loads(data)
            rows = body.get('data') if isinstance(body, dict) else None
            if not isinstance(rows, list) or len(rows) > 100:
                raise APIError('invalid_response')
            for row in rows:
                if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                        or not 1 <= len(row['id']) <= 128 or row.get('status') not in STATUSES
                        or not isinstance(row.get('title'), str)
                        or not isinstance(row.get('outcomes'), list)):
                    raise APIError('invalid_response')
                for outcome in row['outcomes']:
                    if (not isinstance(outcome, dict) or not isinstance(outcome.get('id'), str)
                            or not 1 <= len(outcome['id']) <= 128
                            or not isinstance(outcome.get('title'), str)):
                        raise APIError('invalid_response')
            return rows
        except urllib.error.HTTPError as exc:
            code = ('auth' if exc.code in (401, 403) else 'rate_limited' if exc.code == 429
                    else 'rejected' if 400 <= exc.code < 500 else 'transport')
            raise APIError(code, rejected=400 <= exc.code < 500) from None
        except (OSError, ValueError, TypeError, urllib.error.URLError):
            raise APIError('transport') from None

    def list(self, prediction_id=None):
        return self.request('GET', **({'id': prediction_id} if prediction_id else {'first': 100}))

    def create(self, title, outcomes, window):
        return self.request('POST', {'title': title, 'outcomes': [{'title': o} for o in outcomes],
                                     'prediction_window': window})

    def end(self, prediction_id, status, outcome_id=None):
        body = {'id': prediction_id, 'status': status}
        if outcome_id is not None:
            body['winning_outcome_id'] = outcome_id
        return self.request('PATCH', body)
