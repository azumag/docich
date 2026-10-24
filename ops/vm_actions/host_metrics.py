#!/usr/bin/env python3
"""Bounded host metrics ring sampler for docich#2578 (one sample per run).

A systemd timer starts this once a minute; it takes exactly one sample and
exits. There is no daemon loop, no network access, no shell and no dependency
outside the Python 3.12 standard library.

Contract (``ops/vm_actions/host_metrics_contract.md`` is authoritative):

  - ``INTERVAL_SEC = 60`` (timer ``OnCalendar=*:*:00``, ``AccuracySec=1s``),
    ``MAX_POINTS = 240`` (4 hours of history), ``MAX_BYTES = 256 KiB``,
    ``COMPACT_KEEP = 200``, ``MAX_LINE_BYTES = 4096``.
  - Every numeric source has a ceiling (``MAX_COUNTER = 1e18`` and at most
    24 ASCII digits, uptime ``1e10`` s, load/PSI ``1e6``): a token no healthy
    kernel produces reads as ``null`` instead of a poisoned integer, because
    a giant value would build a record that cannot fit ``MAX_LINE_BYTES``
    (the failure mode of the first production deploy).
  - Ring file ``<home>/.local/state/docich/host-metrics/ring.jsonl``, file mode
    ``0600``, directory mode ``0700``; ``<home>`` is the runtime user's passwd
    entry, never ``$HOME`` (the gateway exec channel may scrub it). One compact,
    sorted-key JSON object per line. The whole append (and any compaction) runs
    under ``flock`` on the state directory, and the ring is opened ``O_RDWR |
    O_APPEND`` so a tail left without its newline by a power cut is repaired
    with one ``\n`` before the new record: a torn write can never corrupt older
    points, nor glue the next one to them. The write itself is a single
    ``O_APPEND`` write followed by ``fsync`` (a frozen guest may never flush the
    page cache). When the ring has more than
    ``MAX_POINTS`` parseable lines or exceeds ``MAX_BYTES``, compaction keeps the
    newest ``COMPACT_KEEP`` lines through a temp file in the same directory plus
    ``os.replace`` (temp file and directory both fsynced); an over-long line is
    skipped, never truncated.
  - Only fixed counters are read: ``/proc/{loadavg,stat,meminfo,uptime,
    diskstats,pressure/*,self/mountinfo}``, ``/sys/kernel/random/boot_id`` and
    ``systemctl is-active`` for the six units in ``UNIT_ORDER`` (fixed argv,
    short timeout). No file content, environment, argv, path, log body or free
    text is ever recorded, and nothing outside the resolved state directory is
    written. Any missing or unreadable source becomes ``null`` (or the
    documented fallback) instead of failing the sample.
  - The ring file is opened with ``O_NOFOLLOW`` and every ring operation is
    relative to an already-open directory descriptor, so a symlink cannot be
    followed and a check cannot be invalidated before the write.

The module is importable without side effects: nothing runs at import time, and
``main`` only acts when the script is executed.
"""

import fcntl
import json
import math
import os
import pwd
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

# --- Published contract constants (regression-tested) ----------------------

SCHEMA_VERSION = 1
INTERVAL_SEC = 60
MAX_POINTS = 240
MAX_BYTES = 256 * 1024
COMPACT_KEEP = 200
MAX_LINE_BYTES = 4096

RING_NAME = "ring.jsonl"
RING_MODE = 0o600
STATE_DIR_MODE = 0o700

# ``systemctl is-active`` is a read-only probe, so the timeout only needs to
# bound a hung user manager; the unit sets TimeoutStartSec above 6 probes.
UNIT_PROBE_TIMEOUT_SEC = 5.0

UNIT_STATE_INACTIVE = 0b00
UNIT_STATE_ACTIVE = 0b01
UNIT_STATE_FAILED = 0b10
# ``activating``/``reloading``/``deactivating``/``unknown`` and a missing unit
# are all "not a running unit", but they are not the same as a unit that is
# installed and stopped. Keeping them distinct stops every boot (and every
# uninstalled scope) from looking like a failed unit to the reader.
UNIT_STATE_UNKNOWN = 0b11

# Fixed order; unit ``i`` occupies bits ``2*i`` and ``2*i+1`` of the ``units``
# mask. Names and scopes are compiled in, never read from configuration.
UNIT_ORDER = (
    "docich-corner-rotation.timer",
    "docich-corner-rotation.service",
    "docich-webui.service",
    "docich-paper-corner.timer",
    "soren-runtime.service",
    "docich-audio-priority.service",
)
UNIT_SCOPES = ("user", "user", "user", "user", "system", "system")

_READ_LIMIT = 128 * 1024
_BOOT_ID_RE = re.compile(r"[0-9a-f]{12,}")
_DEVICE_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")

# ``/proc/meminfo`` source key -> published ``mem_kb`` key.
_MEMINFO_KEYS = (
    ("MemTotal", "total"),
    ("MemAvailable", "available"),
    ("SwapTotal", "swap_total"),
    ("SwapFree", "swap_free"),
    ("Dirty", "dirty"),
    ("Writeback", "writeback"),
)

# State code -> fixed enum label. ``0b11`` is unused by the contract and reads
# as "unknown" so a corrupt mask never invents a healthy unit.
_UNIT_STATE_LABELS = {
    UNIT_STATE_INACTIVE: "inactive",
    UNIT_STATE_ACTIVE: "active",
    UNIT_STATE_FAILED: "failed",
    UNIT_STATE_UNKNOWN: "unknown",
}
_UNIT_STATE_CODES = {label: code for code, label in _UNIT_STATE_LABELS.items()}


class HostMetricsError(Exception):
    """Unusable state directory; the message is fixed and path-free."""


# --- Published helpers ------------------------------------------------------


def _runtime_home():
    """The runtime user's home directory, independent of ``$HOME``.

    The owner-only gateway ``exec`` channel may scrub or replace ``HOME``, while
    ``systemctl --user start`` runs with the login environment. Resolving the
    passwd entry keeps both the timer-driven sample and the deploy hook on one
    fixed path; ``$HOME`` is only used if the passwd lookup itself fails.
    """
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError):
        return Path.home()


#: Ring location: an ordered list of fixed candidates under the runtime user's
#: home. The first is the XDG state home, but on the production VM its parent
#: (``~/.local/state/docich``, and ``~/.local/state`` itself) was created
#: root-owned by a root-time ``install -d`` in the gateway/canary installers,
#: so an unprivileged sampler cannot create anything below it (the incident:
#: every tick exited with "cannot create state dir"). ``~/.local/share`` is the
#: ubuntu-owned tree on that VM, and a plain home directory is the last
#: resort. The writer picks the candidate that already holds the ring, else
#: the first one it can create; the reader checks them in the same order.
STATE_CANDIDATES = (
    ("state", (".local", "state", "docich", "host-metrics")),
    ("share", (".local", "share", "docich", "host-metrics")),
    ("home", ("docich-host-metrics",)),
)


def state_candidates(home=None):
    """The fixed ``(kind, path)`` ring candidates, in the reviewed order."""
    base = Path(home) if home is not None else _runtime_home()
    return [(kind, base.joinpath(*parts)) for kind, parts in STATE_CANDIDATES]


def state_root(home=None, kind="state"):
    """Return one ring state root for ``home`` (default: the first candidate).

    The default does not read ``$HOME``: see ``_runtime_home``.
    """
    for name, path in state_candidates(home):
        if kind is None or name == kind:
            return path
    raise ValueError("unknown state candidate")


def choose_state_dir(home=None):
    """The candidate that already holds the ring, else the first writable one.

    Sticky on an existing ring so a candidate that becomes writable later can
    never split the history across two files.
    """
    candidates = state_candidates(home)
    for _kind, path in candidates:
        try:
            info = os.stat(path / RING_NAME, follow_symlinks=False)
        except OSError:
            continue
        # Pin only on a real ring: a symlink or a directory where the ring
        # belongs can never hold history and must not pin the writer.
        if stat.S_ISREG(info.st_mode):
            return path
    for _kind, path in candidates:
        # Probe with the real open (create + chmod 0700 + open): a directory
        # that merely exists but is owned by someone else must fail here and
        # move on, not pass an ``exist_ok`` makedirs and die at write time.
        try:
            fd = _open_state_dir(path)
        except (HostMetricsError, OSError):
            continue
        os.close(fd)
        return path
    raise HostMetricsError("no writable state dir candidate")


def encode_units(states):
    """Encode the six unit states into the published bit mask.

    ``states`` is one state per entry of ``UNIT_ORDER``, either an enum code
    (``UNIT_STATE_*``) or its fixed label (``"inactive"``/``"active"``/
    ``"failed"``). A short or long sequence is rejected so a misaligned probe
    can never be written as a different unit's state.
    """
    if len(states) != len(UNIT_ORDER):
        raise ValueError("one state per UNIT_ORDER entry is required")
    mask = 0
    for index, state in enumerate(states):
        if isinstance(state, str):
            code = _UNIT_STATE_CODES.get(state.strip().lower())
            if code is None:
                raise ValueError("unknown unit state label")
        elif isinstance(state, int) and not isinstance(state, bool):
            code = state
            if code not in _UNIT_STATE_LABELS:
                raise ValueError("unknown unit state code")
        else:
            raise ValueError("unit states must be enum codes or labels")
        mask |= code << (2 * index)
    return mask


def decode_units(mask):
    """Decode a published ``units`` mask into fixed labels in ``UNIT_ORDER``.

    Returns one of ``"inactive"``, ``"active"``, ``"failed"`` or ``"unknown"``
    (``0b11``) per unit. Bits above the six units are ignored and a non-integer
    mask is rejected, so the projection never guesses at a unit's state.
    """
    if isinstance(mask, bool) or not isinstance(mask, int):
        raise ValueError("units mask must be an integer")
    if mask < 0:
        raise ValueError("units mask must not be negative")
    labels = []
    for index in range(len(UNIT_ORDER)):
        code = (mask >> (2 * index)) & 0b11
        labels.append(_UNIT_STATE_LABELS.get(code, "unknown"))
    return labels


# --- Best-effort source readers --------------------------------------------


def _read_text(path, limit):
    """Read at most ``limit`` bytes of ``path`` as UTF-8, or return None."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(limit)
    except OSError:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


#: Realistic ceilings for the counters this sampler reads. A token beyond them
#: is not a counter any healthy kernel produces (a 25+ digit run in
#: /proc/meminfo, /proc/stat or /proc/diskstats), and projecting it would build
#: a record that cannot fit the ring's bounded line -- the exact failure the
#: first production deploy hit (#2578). Such a source reads as null, never as
#: a poisoned number.
MAX_COUNTER_DIGITS = 24
MAX_COUNTER = 10 ** 18
MAX_UPTIME_SEC = 10 ** 10
MAX_LOAD = 10 ** 6


def _parse_count(text):
    """Parse a non-negative counter, or None when it is implausibly large."""
    if not isinstance(text, str) or not text.isascii() or not text.isdigit():
        return None
    if len(text) > MAX_COUNTER_DIGITS:
        return None
    value = int(text)
    return value if value <= MAX_COUNTER else None


def _parse_float(text, maximum=MAX_LOAD):
    """Parse a finite float within a realistic ceiling, or return None."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or not -maximum <= value <= maximum:
        return None
    return value


def _round2(text):
    value = _parse_float(text)
    return None if value is None else round(value, 2)


def _read_load(proc_root):
    """/proc/loadavg -> [load1, load5, load15] rounded to 2 decimals."""
    text = _read_text(proc_root / "loadavg", _READ_LIMIT)
    if not text:
        return None
    fields = text.split()
    if len(fields) < 3:
        return None
    values = [_round2(field) for field in fields[:3]]
    if any(value is None for value in values):
        return None
    return values


def _read_cpu(proc_root):
    """/proc/stat cpu line -> [total jiffies, idle, iowait] (cumulative)."""
    text = _read_text(proc_root / "stat", _READ_LIMIT)
    if not text:
        return None
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] != "cpu":
            continue
        values = [_parse_count(field) for field in fields[1:]]
        if any(value is None for value in values) or len(values) < 5:
            return None
        total = sum(values)
        # The sum inherits the same ceiling as its parts so a contrived
        # /proc/stat cannot push the aggregate past what the reader accepts.
        if total > MAX_COUNTER:
            return None
        return [total, values[3], values[4]]
    return None


def _read_meminfo(proc_root):
    """The six fixed /proc/meminfo keys; missing keys stay null."""
    result = {name: None for _, name in _MEMINFO_KEYS}
    text = _read_text(proc_root / "meminfo", _READ_LIMIT)
    if not text:
        return result
    wanted = {source: name for source, name in _MEMINFO_KEYS}
    for line in text.splitlines():
        key, separator, rest = line.partition(":")
        if not separator:
            continue
        name = wanted.get(key.strip())
        if name is None:
            continue
        fields = rest.split()
        if fields:
            result[name] = _parse_count(fields[0])
    return result


def _read_psi_signal(path):
    """One /proc/pressure file -> [some avg10, full avg10]."""
    text = _read_text(path, _READ_LIMIT)
    if not text:
        return [None, None]
    values = {"some": None, "full": None}
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] not in values:
            continue
        for field in fields[1:]:
            if field.startswith("avg10="):
                values[fields[0]] = _round2(field[len("avg10="):])
                break
    return [values["some"], values["full"]]


def _read_psi(proc_root):
    """Pressure stall information for cpu/io/memory in the fixed shape."""
    pressure_root = proc_root / "pressure"
    return {
        "cpu": _read_psi_signal(pressure_root / "cpu"),
        "io": _read_psi_signal(pressure_root / "io"),
        "mem": _read_psi_signal(pressure_root / "memory"),
    }


def _read_uptime(proc_root):
    """Whole seconds from /proc/uptime."""
    text = _read_text(proc_root / "uptime", _READ_LIMIT)
    if not text:
        return None
    fields = text.split()
    if not fields:
        return None
    value = _parse_float(fields[0], maximum=MAX_UPTIME_SEC)
    return None if value is None else int(value)


def _read_boot_id(sys_root):
    """First 12 hex characters of /sys/kernel/random/boot_id, never a path."""
    text = _read_text(sys_root / "kernel" / "random" / "boot_id", 128)
    if not text:
        return None
    compact = text.strip().lower().replace("-", "")
    if not _BOOT_ID_RE.fullmatch(compact):
        return None
    return compact[:12]


def _root_device_numbers(proc_root):
    """``(major, minor)`` of the device backing ``/``.

    ``/proc/self/mountinfo`` is authoritative; ``stat("/")`` is only a fallback
    for kernels without mountinfo. No shell, no ``df`` and no caller-supplied
    path is involved.
    """
    text = _read_text(proc_root / "self" / "mountinfo", _READ_LIMIT)
    if text:
        for line in text.splitlines():
            fields = line.split()
            if len(fields) < 5 or fields[4] != "/":
                continue
            major, _, minor = fields[2].partition(":")
            try:
                return int(major), int(minor)
            except ValueError:
                return None
    try:
        info = os.stat("/")
    except OSError:
        return None
    return os.major(info.st_dev), os.minor(info.st_dev)


def _root_block_device(proc_root, sys_root):
    """Resolve the root device name via /sys/dev/block (None if unresolvable)."""
    numbers = _root_device_numbers(proc_root)
    if numbers is None:
        return None
    link = sys_root / "dev" / "block" / ("%d:%d" % numbers)
    try:
        target = os.readlink(link)
    except OSError:
        return None
    name = os.path.basename(target)
    return name if _DEVICE_NAME_RE.fullmatch(name) else None


def _read_disk(proc_root, device):
    """Cumulative root-device counters from /proc/diskstats.

    Returns ``(disk_sectors, disk_ms)`` where sectors are 512-byte units and
    ``disk_ms`` is ``[time doing I/Os, weighted time doing I/Os]``, or
    ``(None, None)`` when the device cannot be resolved or listed.
    """
    if not device:
        return None, None
    text = _read_text(proc_root / "diskstats", _READ_LIMIT)
    if not text:
        return None, None
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[2] != device:
            continue
        values = [_parse_count(field) for field in fields[3:]]
        if any(value is None for value in values) or len(values) < 11:
            return None, None
        return [values[2], values[6]], [values[9], values[10]]
    return None, None


def _systemctl_state(argv, timeout=UNIT_PROBE_TIMEOUT_SEC):
    """Default unit probe: ``systemctl [--user] is-active <unit>``, no shell."""
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip()


def _probe_units(probe):
    """Probe UNIT_ORDER in order with fixed argv and encode the states."""
    states = []
    for name, scope in zip(UNIT_ORDER, UNIT_SCOPES):
        argv = (
            ("systemctl", "--user", "is-active", name)
            if scope == "user"
            else ("systemctl", "is-active", name)
        )
        try:
            answer = probe(argv)
        except Exception:  # noqa: BLE001 - a failed probe must not lose the sample
            answer = ""
        label = (answer or "").strip().lower()
        states.append(_UNIT_STATE_CODES.get(label, UNIT_STATE_UNKNOWN))
    return encode_units(states)


def sample(now=None, proc_root=Path("/proc"), sys_root=Path("/sys"),
           disk_device=None, unit_probe=None):
    """Take one contract-shaped sample and return it as a plain dict.

    ``disk_device`` and ``unit_probe`` exist for tests; production passes
    neither, so the device is resolved from mountinfo/sysfs and the six units
    are probed with the fixed ``systemctl`` argv.
    """
    proc = Path(proc_root)
    sysfs = Path(sys_root)
    device = disk_device if disk_device is not None else _root_block_device(
        proc, sysfs)
    disk_sectors, disk_ms = _read_disk(proc, device)
    return {
        "v": SCHEMA_VERSION,
        "t": int(time.time() if now is None else now),
        "boot": _read_boot_id(sysfs),
        "uptime_s": _read_uptime(proc),
        "load": _read_load(proc),
        "cpu": _read_cpu(proc),
        "mem_kb": _read_meminfo(proc),
        "psi": _read_psi(proc),
        "disk_sectors": disk_sectors,
        "disk_ms": disk_ms,
        "units": _probe_units(_systemctl_state if unit_probe is None
                              else unit_probe),
    }


# --- Ring writer ------------------------------------------------------------


def _encode_record(record):
    """One compact, sorted-key, ASCII JSON line (the only bytes ever written)."""
    text = json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)
    return text.encode("utf-8") + b"\n"


def _parse_record(line):
    """Return the record dict of one line, or None if it is not one."""
    if not line:
        return None
    try:
        record = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    return record if isinstance(record, dict) else None


def _open_state_dir(state_dir):
    """Open the state directory without following a symlink at the leaf."""
    raw = os.fspath(state_dir)
    if not os.path.isabs(raw):
        raise HostMetricsError("state dir must be an absolute path")
    path = os.path.abspath(raw)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        try:
            os.makedirs(path, mode=STATE_DIR_MODE, exist_ok=True)
        except OSError as exc:
            raise HostMetricsError("cannot create state dir") from exc
        info = os.lstat(path)
    except OSError as exc:
        raise HostMetricsError("cannot inspect state dir") from exc
    if stat.S_ISLNK(info.st_mode):
        raise HostMetricsError("state dir must not be a symlink")
    if not stat.S_ISDIR(info.st_mode):
        raise HostMetricsError("state dir must be a directory")
    try:
        # The ring directory is owned by this sampler; keep its mode at the
        # contract value even if a foreign creator left it wider.
        os.chmod(path, STATE_DIR_MODE)
    except OSError as exc:
        raise HostMetricsError("cannot create state dir") from exc
    try:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise HostMetricsError("cannot open state dir") from exc


def _open_ring(dir_fd):
    """Open the ring for appending; a symlink or non-regular file is refused.

    ``O_RDWR`` (not ``O_WRONLY``) so the torn-tail repair below can inspect the
    last byte through the same descriptor; ``O_APPEND`` still makes the write
    land at EOF.
    """
    fd = os.open(
        RING_NAME,
        os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
        RING_MODE,
        dir_fd=dir_fd,
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise HostMetricsError("ring must be a regular file")
        os.fchmod(fd, RING_MODE)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _write_all(fd, payload):
    written = 0
    while written < len(payload):
        chunk = os.write(fd, payload[written:])
        if chunk <= 0:
            raise HostMetricsError("ring write made no progress")
        written += chunk


def _read_ring_tail(dir_fd, limit):
    """Read at most ``limit`` bytes from the end of the ring, never a symlink.

    The window always ends at EOF so compaction keeps the *newest* points even
    when the ring is larger than the read cap; a line cut by the window fails to
    parse and is dropped.
    """
    fd = os.open(RING_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        size = os.fstat(fd).st_size
        if size > limit:
            os.lseek(fd, size - limit, os.SEEK_SET)
        chunks = []
        remaining = limit
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _replace_ring(dir_fd, temporary_name):
    os.replace(temporary_name, RING_NAME, src_dir_fd=dir_fd,
               dst_dir_fd=dir_fd)


def _sync_directory(dir_fd):
    """Persist the rename in the directory, not just the file content.

    A guest hang followed by a power cycle may never flush the page cache, so
    the new entry has to reach the disk explicitly. Some filesystems refuse
    ``fsync`` on a directory; the atomic replace still holds there, only the
    durability of the directory entry is left to the normal flush.
    """
    try:
        os.fsync(dir_fd)
    except OSError:
        pass


def _rewrite_ring(dir_fd, lines):
    """Atomically replace the ring with ``lines`` via a same-dir temp file."""
    temporary_name = ".%s.tmp.%s" % (RING_NAME, os.urandom(8).hex())
    payload = b"".join(line + b"\n" for line in lines)
    fd = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        RING_MODE,
        dir_fd=dir_fd,
    )
    try:
        try:
            os.fchmod(fd, RING_MODE)
            _write_all(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
    except BaseException:
        try:
            os.unlink(temporary_name, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    _replace_ring(dir_fd, temporary_name)
    _sync_directory(dir_fd)


def _enforce_ring(dir_fd, max_points, max_bytes, compact_keep, max_line_bytes):
    """Compact only when the ring is over a bound, keeping the newest lines.

    ``MAX_BYTES`` is enforced after compaction as well: long-but-valid lines can
    still overflow the byte budget at ``COMPACT_KEEP`` lines, so the oldest kept
    lines are trimmed further until the payload fits (the newest line is always
    kept).
    """
    data = _read_ring_tail(dir_fd, max_bytes + max_line_bytes)
    records = [line for line in data.split(b"\n")
               if _parse_record(line) is not None]
    if len(data) <= max_bytes and len(records) <= max_points:
        return
    kept = records[-compact_keep:]
    size = sum(len(line) + 1 for line in kept)
    drop = 0
    while size > max_bytes and len(kept) - drop > 1:
        size -= len(kept[drop]) + 1
        drop += 1
    _rewrite_ring(dir_fd, kept[drop:])


def append_sample(state_dir, record,
                  max_points=MAX_POINTS, max_bytes=MAX_BYTES,
                  compact_keep=COMPACT_KEEP, max_line_bytes=MAX_LINE_BYTES):
    """Append one record to ``<state_dir>/ring.jsonl`` and bound the ring.

    Returns the number of bytes written; ``0`` means the encoded record does not
    fit in ``MAX_LINE_BYTES`` and was skipped (a line is never truncated). A
    relative or symlinked state directory raises ``HostMetricsError``; a
    symlinked ring file fails the ``O_NOFOLLOW`` open.
    """
    line = _encode_record(record)
    if len(line) > max_line_bytes:
        return 0
    dir_fd = _open_state_dir(state_dir)
    try:
        # Serialize with any other sampler/compaction. A compaction replaces the
        # inode, so an unserialized append could land in the unlinked old file
        # and lose the newest point.
        try:
            fcntl.flock(dir_fd, fcntl.LOCK_EX)
        except OSError:
            pass
        try:
            existed = True
            try:
                os.stat(RING_NAME, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                existed = False
            fd = _open_ring(dir_fd)
            try:
                # A power cut can leave the last line without its newline. The
                # next append must start on a fresh line, or the new (first
                # post-reboot) point would be glued to a torn record and lost.
                size = os.fstat(fd).st_size
                if size and os.pread(fd, 1, size - 1) != b"\n":
                    _write_all(fd, b"\n")
                _write_all(fd, line)
                # A frozen guest may never flush the page cache, so the newest
                # point must reach the disk before this process exits.
                os.fsync(fd)
            finally:
                os.close(fd)
            if not existed:
                # The very first sample also has to persist its directory entry.
                _sync_directory(dir_fd)
            _enforce_ring(dir_fd, max_points, max_bytes, compact_keep,
                          max_line_bytes)
        finally:
            try:
                fcntl.flock(dir_fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(dir_fd)
    return len(line)


# --- Entry point ------------------------------------------------------------

_UNSUPPORTED_ARGUMENT = "host_metrics: unsupported argument\n"
_CANNOT_WRITE = "host_metrics: cannot write host metrics ring\n"
_CANNOT_WRITE_TOO_LONG = (
    "host_metrics: cannot write host metrics ring (record_bytes=%d)\n")


def main(argv=None):
    """Run one sample. The production invocation passes no arguments."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    state_dir = None
    emit = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--print" and not emit:
            emit = True
            index += 1
        elif argument == "--state-dir" and state_dir is None:
            if index + 1 >= len(arguments):
                sys.stderr.write(_UNSUPPORTED_ARGUMENT)
                return 2
            state_dir = arguments[index + 1]
            index += 2
        else:
            sys.stderr.write(_UNSUPPORTED_ARGUMENT)
            return 2
    record = sample()
    if emit:
        sys.stdout.write(_encode_record(record).decode("utf-8"))
        sys.stdout.flush()
    try:
        target = choose_state_dir() if state_dir is None else state_dir
        written = append_sample(target, record)
    except (HostMetricsError, OSError):
        sys.stderr.write(_CANNOT_WRITE)
        return 2
    if written == 0:
        sys.stderr.write(_CANNOT_WRITE_TOO_LONG % len(_encode_record(record)))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
