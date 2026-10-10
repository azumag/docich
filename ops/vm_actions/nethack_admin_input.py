"""Fixed private inputs from the event file, never workflow expression expansion."""
import json
import os
import re
from pathlib import Path


def read_expires(env):
    try:
        with Path(env['GITHUB_EVENT_PATH']).open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=unique)['inputs'].get('approval_expires_at', '')
        if not isinstance(value, str) or (value and not re.fullmatch('[1-9][0-9]{9,11}', value)):
            raise ValueError()
        return value
    except Exception:
        raise ValueError('approval input unavailable') from None


if __name__ == '__main__':
    try:
        print(read_expires(os.environ))
    except ValueError:
        raise SystemExit('approval input unavailable')
