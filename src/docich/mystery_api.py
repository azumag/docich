"""Small fixed-host Helix clients for the mystery corner (polls + predictions).

Same trust boundary as ``hanjuku_prediction_api``: no shell execution, no
token logging, no retries, no redirects, no arbitrary endpoints. Credentials
are read afresh from the environment and the Soren ``.env`` on each tick and
are never copied into state or diagnostics.

- Polls: the participatory action votes. Gated by ``TWITCH_POLLS_ENABLED=1``
  with the ``TWITCH_POLLS_TOKEN`` fallback to ``TWITCH_PREDICTIONS_TOKEN``
  (the same keys soviet_now's ``twitch_polls.sh`` uses, so a channel that
  opted into the survey corner needs no new configuration).
- Predictions: the final culprit wager. Reuses the reviewed shared Helix
  prediction client (``hanjuku_prediction_api``): its channel-level opt-in
  and the Soren ownership-wrapper requirement protect any docich-created
  prediction from being settled as a Soren round, which is exactly the hazard
  this corner would otherwise add.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .hanjuku_prediction_api import APIError, NoRedirect  # noqa: F401  (re-exported)
from .hanjuku_prediction_api import configured_client as configured_prediction_client
from .trading.soren_output import resolve_soren_root

__all__ = [
    "APIError",
    "NoRedirect",
    "POLL_STATUSES",
    "PollHelix",
    "configured_poll_client",
    "configured_prediction_client",
    "poll_choice_votes",
]

POLL_KEYS = frozenset({'TWITCH_POLLS_ENABLED', 'TWITCH_POLLS_TOKEN',
                       'TWITCH_PREDICTIONS_TOKEN', 'TWITCH_CLIENT_ID',
                       'TWITCH_BROADCASTER_ID', 'EXPLORE_MODE'})
POLL_STATUSES = frozenset({'ACTIVE', 'COMPLETED', 'TERMINATED', 'ARCHIVED', 'MODERATED'})
POLL_MAX_BYTES = 256 * 1024
POLLS_ENDPOINT = 'https://api.twitch.tv/helix/polls'
#: Helix Create Poll bounds (soviet_now twitch_polls.sh applies the same).
POLL_MIN_CHOICES, POLL_MAX_CHOICES = 2, 5
POLL_MIN_DURATION_S, POLL_MAX_DURATION_S = 15, 1800


def _dotenv_value(root, key, current):
    # Tokens are rotated in .env by docich.twitch_oauth, so an inherited copy
    # may already be expired: for *_TOKEN keys the file's value wins.
    if current and not key.endswith('_TOKEN'):
        return current
    path = root / '.env'
    try:
        if not path.exists() or path.is_symlink() or not path.is_file() \
                or path.stat().st_size > POLL_MAX_BYTES:
            return current
    except OSError:
        return current
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].lstrip()
        name, sep, value = line.partition('=')
        if sep and name.strip() == key:
            value = value.strip()
            match = re.fullmatch(r'''(['"])(.*?)\1(?:\s+#.*)?''', value)
            value = match[2] if match else value.split(' #', 1)[0].strip()
            if value:
                return value
    return current


def poll_settings(g):
    """Bounded credential read for polls; never logs or stores values."""
    values = {k: os.environ.get(k, '') for k in POLL_KEYS}
    try:
        root = resolve_soren_root(g)
    except Exception:
        return values
    for key in POLL_KEYS:
        values[key] = _dotenv_value(root, key, values[key])
    return values


def configured_poll_client(g):
    """A :class:`PollHelix` client, or ``(None, reason)`` when not enabled."""
    cfg = poll_settings(g)
    if cfg['TWITCH_POLLS_ENABLED'] != '1':
        return None, 'disabled'
    if cfg['EXPLORE_MODE'] == '1':
        return None, 'explore'
    token = cfg['TWITCH_POLLS_TOKEN'] or cfg['TWITCH_PREDICTIONS_TOKEN']
    token = token.removeprefix('oauth:')
    client, broadcaster = cfg['TWITCH_CLIENT_ID'], cfg['TWITCH_BROADCASTER_ID']
    if not all((token, client, broadcaster)):
        return None, 'unconfigured'
    if any(re.search(r'\s|[$`]', s) for s in (token, client, broadcaster)):
        raise APIError('configuration')
    return PollHelix(token, client, broadcaster), None


class PollHelix:
    """Strict /helix/polls client: bounded responses, fixed error codes."""

    def __init__(self, token, client, broadcaster, *, opener=None):
        self._token, self._client = token, client
        self.broadcaster = broadcaster
        self._opener = opener or urllib.request.build_opener(NoRedirect())

    def request(self, method, payload=None, **query):
        query = {'broadcaster_id': self.broadcaster, **query}
        url = POLLS_ENDPOINT + ('?' + urllib.parse.urlencode(query) if method == 'GET' else '')
        if payload is not None:
            payload = {'broadcaster_id': self.broadcaster, **payload}
        request = urllib.request.Request(
            url, method=method,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
            headers={'Authorization': f'Bearer {self._token}', 'Client-Id': self._client,
                     'Content-Type': 'application/json'})
        try:
            with self._opener.open(request, timeout=5) as response:
                data = response.read(POLL_MAX_BYTES + 1)
            if len(data) > POLL_MAX_BYTES:
                raise APIError('invalid_response')
            body = json.loads(data)
            rows = body.get('data') if isinstance(body, dict) else None
            if not isinstance(rows, list) or len(rows) > 100:
                raise APIError('invalid_response')
            for row in rows:
                _validate_poll_row(row)
            return rows
        except urllib.error.HTTPError as exc:
            code = ('auth' if exc.code in (401, 403) else 'rate_limited' if exc.code == 429
                    else 'rejected' if 400 <= exc.code < 500 else 'transport')
            raise APIError(code, rejected=400 <= exc.code < 500) from None
        except (OSError, ValueError, TypeError, urllib.error.URLError):
            raise APIError('transport') from None

    def list(self, poll_id=None):
        # Helix returns newest first; only one poll can be live at once.
        return self.request('GET', **({'id': poll_id} if poll_id else {'first': 1}))

    def create(self, title, choices, duration_s):
        if (type(duration_s) is not int
                or not POLL_MIN_DURATION_S <= duration_s <= POLL_MAX_DURATION_S):
            raise APIError('rejected')
        if not POLL_MIN_CHOICES <= len(choices) <= POLL_MAX_CHOICES:
            raise APIError('rejected')
        return self.request('POST', {
            'title': title,
            'choices': [{'title': choice} for choice in choices],
            'duration': duration_s,
        })


def _validate_poll_row(row):
    if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
            or not 1 <= len(row['id']) <= 128 or row.get('status') not in POLL_STATUSES
            or not isinstance(row.get('title'), str) or not isinstance(row.get('choices'), list)):
        raise APIError('invalid_response')
    choices = row['choices']
    if not POLL_MIN_CHOICES <= len(choices) <= POLL_MAX_CHOICES:
        raise APIError('invalid_response')
    for choice in choices:
        if (not isinstance(choice, dict) or not isinstance(choice.get('id'), str)
                or not 1 <= len(choice['id']) <= 128
                or not isinstance(choice.get('title'), str)):
            raise APIError('invalid_response')


def poll_choice_votes(row):
    """Bounded ``[votes, ...]`` per choice, or ``None`` when unreadable.

    A poll the channel-wide worker created is also visible here, so callers
    must only interpret rows whose id they recorded at create time. The
    winner selection is the canonical trpg-core tie-break in
    ``mystery_participation.winner`` (plurality, ties to the lowest index).
    """
    choices = row.get('choices') if isinstance(row, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    votes = []
    for choice in choices:
        value = choice.get('votes') if isinstance(choice, dict) else None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        votes.append(value)
    return votes
