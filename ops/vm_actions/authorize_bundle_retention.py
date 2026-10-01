"""Select only the fixed owner-confirmed bundle age override; no free command."""
import os
import sys


def authorize(env):
    mode = env.get('EMERGENCY_PRUNE', '')
    if mode in ('', 'false'):
        return False
    if mode != 'true':
        raise ValueError('invalid mode')
    expected = {
        'GITHUB_EVENT_NAME': 'workflow_dispatch',
        'GITHUB_REPOSITORY': 'azumag/docich',
        'GITHUB_ACTOR_ID': '9018513',
        'GITHUB_TRIGGERING_ACTOR': 'azumag',
        'GITHUB_REF': 'refs/heads/main',
        'GITHUB_WORKFLOW_REF': 'azumag/docich/.github/workflows/vm-bundle-retention.yml@refs/heads/main',
        'REF_PROTECTED': 'true',
        'EMERGENCY_CONFIRM': 'prune-unreferenced-bundles',
    }
    if any(env.get(key) != value for key, value in expected.items()):
        raise ValueError('unauthorized emergency override')
    return True


def main():
    try:
        emergency = authorize(os.environ)
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write(f'emergency={int(emergency)}\n')
    except (OSError, ValueError, KeyError):
        print('bundle age override refused', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
