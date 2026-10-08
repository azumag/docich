#!/usr/bin/env bash
set -euo pipefail
# Fixed push-deploy helper. No worker names, shell commands, or env-file input.
# --root is only for repository tests; the workflow sends this file unchanged.
# Exit: 0 replaced/paused/parked/absent, 10 identity refused,
#       11 TERM/exit unconfirmed, 12 replacement unconfirmed,
#       13 effective supervisor lifecycle contract unavailable.
root="/home/ubuntu/soren"
if [[ "${1:-}" == "--root" ]]; then
  [[ $# -eq 2 ]]
  root="$2"
  shift 2
fi
[[ $# -eq 0 ]]

# Keep the implementation inline: the gateway executes this reviewed payload
# over stdin, so importing another deployed helper could mix code generations.
python3 - "$root" <<'PY'
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import sys
import time

PROC_ROOT = Path('/proc')
TERM_WAIT = 10
REPLACEMENT_WAIT = 20
POLL_INTERVAL = 0.25
LIFECYCLE_KEYS = {b'GAME_LIFECYCLE_ENABLED', b'SOREN_GAME_LIFECYCLE_DIR'}


class Refused(Exception):
    def __init__(self, code, reason):
        self.code = code
        self.reason = reason  # Fixed codes only; never runtime data.


def read_regular(path, limit, *, dir_fd=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise Refused(10, 'not_regular')
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise Refused(10, 'oversized_state')
        return data


def owner(root, filename='.soviet_watchdog.lock/owner'):
    try:
        raw = read_regular(root / 'tmp/state' / filename, 32).strip()
    except FileNotFoundError:
        return None
    if not re.fullmatch(rb'[1-9][0-9]{0,9}', raw) or int(raw) <= 1:
        raise Refused(10, 'invalid_owner')
    return int(raw)


def paused(root):
    # Respect even a dangling pause marker. Do not remove or create markers.
    return os.path.lexists(root / 'tmp/state/soviet_watchdog.paused') or os.path.lexists(root / 'tmp/stop')


class Process:
    """Proc directory and pidfd both belong to this one process incarnation."""
    def __init__(self, pid):
        self.pid = pid
        self.pidfd = None
        self.procfd = os.open(PROC_ROOT / str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        if os.fstat(self.procfd).st_uid != os.geteuid():
            self.close()
            raise Refused(10, 'wrong_owner')

    def close(self):
        if self.pidfd is not None:
            os.close(self.pidfd)
            self.pidfd = None
        os.close(self.procfd)

    def fields(self):
        raw = read_regular('stat', 4096, dir_fd=self.procfd)
        fields = raw.rsplit(b')', 1)[1].split()
        if fields[0] in (b'Z', b'X', b'x'):
            raise ProcessLookupError()
        return int(fields[19]), int(fields[1])  # start ticks, parent PID

    def identity(self, root, script, *, supervisor=False):
        before = self.fields()
        argv = read_regular('cmdline', 4096, dir_fd=self.procfd).split(b'\0')
        if argv[-1:] == [b'']:
            argv.pop()
        scripts = {os.fsencode(root / script), os.fsencode('./' + script), os.fsencode(script)}
        valid_args = len(argv) == 2 or (supervisor and len(argv) == 3 and argv[2] == b'--supervisor')
        # The script must occupy Bash's script operand. -c, options, wrappers,
        # data arguments, similar names, and other roots are all refused.
        if (not valid_args or argv[0] not in (b'bash', b'/bin/bash', b'/usr/bin/bash')
                or argv[1] not in scripts
                or os.readlink('cwd', dir_fd=self.procfd) != str(root)
                or os.readlink('exe', dir_fd=self.procfd) not in ('/bin/bash', '/usr/bin/bash')
                or os.readlink('fd/255', dir_fd=self.procfd) not in (str(root / script), str(root / script) + ' (deleted)')):
            raise Refused(10, 'wrong_command')
        if self.fields() != before:
            raise Refused(10, 'identity_changed')
        return before

    def pin(self):
        if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
            raise Refused(10, 'pidfd_unavailable')
        self.pidfd = os.pidfd_open(self.pid, 0)
        # Caller revalidates through the already opened proc directory. If the
        # PID was reused before pidfd_open, the old proc directory cannot turn
        # into the replacement's metadata, so verification fails with no TERM.
        if self.exited():
            raise ProcessLookupError()

    def exited(self):
        poller = select.poll()
        poller.register(self.pidfd, select.POLLIN)
        return bool(poller.poll(0))

    def lifecycle(self, root):
        # A worker exec inherits the supervisor's exported .env configuration.
        # /proc/supervisor/environ is its *initial* environment, before Bash
        # sources .env, and must not be used here. Never source or parse .env.
        # Only these two non-secret values are retained; no env data is logged.
        values = {}
        raw = read_regular('environ', 1024 * 1024, dir_fd=self.procfd)
        for item in raw.split(b'\0'):
            key, sep, value = item.partition(b'=')
            if sep and key in LIFECYCLE_KEYS:
                if key in values:
                    raise Refused(13, 'ambiguous_lifecycle_config')
                values[key] = os.fsdecode(value)
        enabled = values.get(b'GAME_LIFECYCLE_ENABLED') or '1'
        directory = values.get(b'SOREN_GAME_LIFECYCLE_DIR') or str(root / 'tmp/state/game_lifecycle')
        if enabled not in ('0', '1'):
            raise Refused(13, 'invalid_lifecycle_config')
        # start_all's cwd is root, including relative custom directories.
        return enabled, root / directory


def bridge_parked(root, config):
    enabled, directory = config
    if enabled != '1':
        return False
    if not (root / 'lib/game_lifecycle.sh').is_file():
        raise Refused(13, 'lifecycle_predicate_unavailable')
    try:
        request = json.loads(read_regular(directory / 'request.json', 65536))
        ack = json.loads(read_regular(directory / 'ack.json', 65536))
    except (FileNotFoundError, ValueError):
        # Absent/malformed records retain the supervisor's non-parked result.
        return False
    except (OSError, Refused):
        # A valid park may be unreadable under this helper's stricter file
        # policy. Never convert permission/I/O/symlink refusal into permission
        # to TERM a watchdog while the supervisor still withholds respawn.
        raise Refused(13, 'lifecycle_state_unreadable') from None
    # Same reviewed predicate as start_all's game_lifecycle_bridge_parked.
    if not isinstance(request, dict) or not isinstance(ack, dict):
        return False
    if request.get('schema') != 1 or ack.get('schema') != 1:
        return False
    for field in ('request_id', 'game', 'generation', 'deadline_epoch', 'deadline_at'):
        if field not in request or ack.get(field) != request.get(field):
            return False
    if ack.get('status') == 'stopped':
        return True
    if ack.get('status') in ('stop_requested', 'resume_requested', 'stopping'):
        try:
            return float(request.get('deadline_epoch')) > time.time()
        except (TypeError, ValueError):
            return False
    return False


def contract(root, watchdog, identity):
    # Adopted/orphan watchdogs may have a different environment. Require the
    # canonical supervisor to be the actual parent, rather than guessing from
    # the current .env or from the gateway's intentionally scrubbed environment.
    parent = owner(root, 'start_all.pid')
    if parent is None or parent != identity[1]:
        raise Refused(13, 'supervisor_parent_unconfirmed')
    supervisor = None
    try:
        supervisor = Process(parent)
        expected = supervisor.identity(root, 'start_all.sh', supervisor=True)
        supervisor.pin()
        config = watchdog.lifecycle(root)
        if (owner(root, 'start_all.pid') != parent
                or supervisor.identity(root, 'start_all.sh', supervisor=True) != expected
                or watchdog.identity(root, 'soviet_watchdog.sh') != identity
                or supervisor.exited() or watchdog.exited()):
            raise Refused(13, 'supervisor_contract_changed')
        return supervisor, expected, config
    except (OSError, Refused):
        if supervisor is not None:
            supervisor.close()
        raise Refused(13, 'supervisor_contract_unavailable') from None


def restart(root):
    root = root.resolve(strict=True)
    if paused(root):
        return 'skip: intentionally paused'
    pid = owner(root)
    if pid is None:
        return 'skip: no live supervised soviet_watchdog'
    watchdog = supervisor = None
    try:
        try:
            watchdog = Process(pid)
            identity = watchdog.identity(root, 'soviet_watchdog.sh')
            watchdog.pin()
            if owner(root) != pid or watchdog.identity(root, 'soviet_watchdog.sh') != identity:
                raise Refused(10, 'identity_changed')
        except ProcessLookupError:
            return 'skip: no live supervised soviet_watchdog'
        except FileNotFoundError:
            if watchdog is None:
                return 'skip: no live supervised soviet_watchdog'
            raise Refused(10, 'identity_unavailable') from None

        supervisor, supervisor_identity, config = contract(root, watchdog, identity)
        if paused(root):
            return 'skip: intentionally paused'
        if bridge_parked(root, config):
            return 'skip: lifecycle parked'
        # Revalidate ownership, both incarnations, and exported config under
        # stable handles immediately before TERM. No bare-PID signals exist.
        if (owner(root) != pid or owner(root, 'start_all.pid') != supervisor.pid
                or watchdog.identity(root, 'soviet_watchdog.sh') != identity
                or supervisor.identity(root, 'start_all.sh', supervisor=True) != supervisor_identity
                or watchdog.lifecycle(root) != config or watchdog.exited() or supervisor.exited()):
            raise Refused(10, 'identity_changed')
        if paused(root):
            return 'skip: intentionally paused'
        if bridge_parked(root, config):
            return 'skip: lifecycle parked'
        try:
            signal.pidfd_send_signal(watchdog.pidfd, signal.SIGTERM, None, 0)
        except ProcessLookupError:
            pass  # The pinned old incarnation has already exited. Never signal its successor.
        except OSError:
            raise Refused(11, 'term_failed') from None
        deadline = time.monotonic() + TERM_WAIT
        while not watchdog.exited():
            if time.monotonic() >= deadline:
                raise Refused(11, 'old_process_survived')
            time.sleep(POLL_INTERVAL)

        deadline = time.monotonic() + REPLACEMENT_WAIT
        while True:
            new = None
            try:
                new_pid = owner(root)
                if new_pid is not None:
                    new = Process(new_pid)
                    new_identity = new.identity(root, 'soviet_watchdog.sh')
                    new.pin()
                    # The numeric PID can be reused by a valid new incarnation.
                    if ((new_pid, new_identity[0]) != (pid, identity[0])
                            and new_identity[1] == supervisor.pid
                            and owner(root) == new_pid
                            and new.identity(root, 'soviet_watchdog.sh') == new_identity
                            and new.lifecycle(root) == config
                            and owner(root, 'start_all.pid') == supervisor.pid
                            and supervisor.identity(root, 'start_all.sh', supervisor=True) == supervisor_identity
                            and not new.exited() and not supervisor.exited()):
                        return 'soviet_watchdog: replaced reviewed owner'
            except (OSError, Refused):
                pass  # No success without a stable, live, reviewed replacement.
            finally:
                if new is not None:
                    new.close()
            if time.monotonic() >= deadline:
                raise Refused(12, 'replacement_unconfirmed')
            time.sleep(POLL_INTERVAL)
    finally:
        if supervisor is not None:
            supervisor.close()
        if watchdog is not None:
            watchdog.close()


def main(root):
    try:
        print(restart(Path(root)), file=sys.stderr)
        return 0
    except Refused as exc:
        print('soviet_watchdog: ' + exc.reason, file=sys.stderr)
        return exc.code
    except (OSError, ValueError, IndexError):
        print('soviet_watchdog: identity_unavailable', file=sys.stderr)
        return 10


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
PY
