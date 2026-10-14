#!/usr/bin/env python3
"""Unmute one owned BGM playback stream and refresh its restore memory (#1518).

Fixed owner-only recovery for the #968 silence: a bridge/BGM ``ffplay``
survives on ``soren_null`` with a normal volume while its sink input carries
``Mute: yes``, and ``module-stream-restore`` re-applies that mute to every new
stream with the same key. The ``exec`` channel is disabled while the
repository is public, so this fixed operation is the only sanctioned unmute
path. It never executes caller-supplied shell: every child process uses a
fixed argv list, and indices taken from daemon output are re-validated before
use.

Two stages, one operation:

1. Unmute the single owned muted target with the fixed argv
   ``pactl set-sink-input-mute <index> 0``, then re-list and require the same
   (index, pid) stream to report ``Mute: no``. The daemon writes that change
   through to the stream-restore entry for the same key.
2. Spawn a short silent probe stream (fixed ``ffplay`` argv, ``-volume 0``,
   same sink) and require the fresh stream to be born unmuted; a muted birth
   is unmuted once more (refreshing ffplay-keyed entries) and re-verified.
   The probe is then terminated.

Fail-closed gates (refuse without any mutation unless all hold):

- the candidate is enumerated from ``pactl list sink-inputs`` only, so
  capture/monitor (source outputs) are structurally out of scope;
- its sink resolves to the fixed ``soren_null`` through ``list short sinks``;
- it reports ``Mute: yes`` (an owned but already-unmuted stream is a no-op
  success, never a reason to spawn a probe);
- ownership is proven per stream: ``application.process.id`` must be a live
  ``ffplay`` whose command line carries the fixed worker tag or whose parent
  chain reaches a fixed docich/bridge owner (``bgm_worker.sh``,
  ``soviet_local.mjs``, ``soren_loop.sh``, ``soviet_watchdog.sh``). An
  application name alone is never enough;
- exactly one owned muted target exists (zero with no owned stream at all is
  a refusal, not a silent success; two or more is ambiguous and refused);
- the target is not corked (a paused stream may carry an intentional mute).

Stdout is one fixed-shape JSON envelope (exit code plus verification
counts). Raw daemon text, command lines, paths, and pids never leave the VM.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/home/ubuntu/docich")

# The only sink this operation ever touches. Delivery, shared, and game sinks
# keep whatever state they have.
TARGET_SINK = "soren_null"

# Fixed command-line fragments identifying the processes that own BGM
# playback. A stream is owned only when its player is one of these owners'
# descendants (or carries the fixed worker tag below); matching an
# application name reported by the daemon is never sufficient.
OWNER_CMD_FRAGMENTS = ("bgm_worker.sh", "soviet_local.mjs",
                       "soren_loop.sh", "soviet_watchdog.sh")
WORKER_TAG = "soren-bgm-loop"
PROBE_TAG = "docich-bgm-probe"

PACTL_TIMEOUT = 5
LISTING_MAX = 256 * 1024
PROBE_SECONDS = 5
PROBE_WAIT_SECONDS = 10.0
PROBE_POLL_SECONDS = 0.5
PROBE_STOP_SECONDS = 3.0

# Fixed refusal vocabulary. Nothing else is ever reported.
REASONS = frozenset({
    "pactl_unavailable", "pactl_failed", "unparsable", "no_target",
    "ownership_unproven", "multiple_targets", "target_corked",
    "probe_unavailable", "probe_missing", "unmute_failed",
    "probe_still_muted", "remuted", "unexpected_failure",
})


class Refused(Exception):
    """Fail-closed refusal carrying one fixed reason code."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _server_argv(uid=None):
    """Point ``pactl`` at the session socket, mirroring the read-only
    projection in ``collect_diagnostics`` (the exec channel scrubs
    ``XDG_RUNTIME_DIR``). Only this derived path is ever used; without a
    socket ``pactl`` falls back to its default and failures stay failures."""
    try:
        uid = os.getuid() if uid is None else uid
    except OSError:
        return []
    sock = Path("/run/user") / str(uid) / "pulse" / "native"
    try:
        if stat.S_ISSOCK(sock.stat().st_mode):
            return ["--server=unix:" + str(sock)]
    except OSError:
        pass
    return []


def _pactl(args, env=None, server=None):
    """Run one fixed ``pactl`` argv list. No shell is ever involved."""
    if server is None:
        server = _server_argv()
    full_env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
                "LC_ALL": "C", "HOME": "/tmp"}
    if env:
        full_env.update(env)
    return subprocess.run(["pactl", *server, *args],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, timeout=PACTL_TIMEOUT,
                          env=full_env, check=False)


def parse_sink_inputs(text):
    """Strict minimal sink-input parser (index, sink, mute, pid, corked).

    This is the same ``LC_ALL=C`` contract as
    ``docich.pulse_volume.parse_sink_inputs``, reduced to what ownership and
    unmute need. Unknown lines are ignored; callers treat nonempty output
    with zero parsed streams as unparsable, never as an empty daemon.
    """
    items, cur = [], None
    for line in text.splitlines():
        match = re.match(r"^Sink Input #(\d+)\Z", line)
        if match:
            cur = {"index": int(match.group(1))}
            items.append(cur)
            continue
        if cur is None:
            continue
        stripped = line.strip()
        if stripped.startswith("Sink:"):
            cur["sink"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Mute:"):
            value = stripped.split(":", 1)[1].strip()
            if value in {"yes", "no"}:
                cur["mute"] = value == "yes"
        elif stripped.startswith("Corked:"):
            # Absent stays absent: "not reported" is never read as paused.
            value = stripped.split(":", 1)[1].strip()
            if value in {"yes", "no"}:
                cur["corked"] = value == "yes"
        else:
            match = re.match(r"application\.process\.id = \"(\d+)\"\Z", stripped)
            if match:
                cur["pid"] = int(match.group(1))
    return items


def sink_names(text):
    """Map ``list short sinks`` index to name."""
    out = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit():
            out[parts[0]] = parts[1]
    return out


def _cmdline(proc, pid):
    try:
        raw = (proc / str(pid) / "cmdline").read_bytes().split(b"\0")
    except OSError:
        return None
    args = [arg for arg in raw if arg]
    return args or None


def _ppid(proc, pid):
    try:
        text = (proc / str(pid) / "stat").read_text(encoding="ascii")
    except OSError:
        return None
    try:
        return int(text.rsplit(")", 1)[1].split()[1])
    except (IndexError, ValueError):
        return None


def owner_roots(proc):
    """PIDs whose command line names a fixed docich/bridge BGM owner."""
    roots = set()
    try:
        entries = list(proc.iterdir())
    except OSError:
        return roots
    for entry in entries:
        if not entry.name.isdigit():
            continue
        args = _cmdline(proc, int(entry.name))
        if not args:
            continue
        joined = " ".join(os.fsdecode(arg) for arg in args)
        if any(fragment in joined for fragment in OWNER_CMD_FRAGMENTS):
            roots.add(int(entry.name))
    return roots


def is_owned(item, roots, proc):
    """True only for a live ffplay proven to belong to a BGM owner."""
    pid = item.get("pid")
    if type(pid) is not int or pid <= 1:
        return False
    args = _cmdline(proc, pid)
    if not args:
        return False
    if os.fsdecode(args[0]).rsplit("/", 1)[-1] != "ffplay":
        return False
    if WORKER_TAG in " ".join(os.fsdecode(arg) for arg in args[1:]):
        return True
    seen, current = {pid}, pid
    while True:
        parent = _ppid(proc, current)
        if parent is None or parent <= 0 or parent in seen:
            return False
        if parent in roots:
            return True
        seen.add(parent)
        current = parent


def select_target(items, names, proc):
    """Return ``(target, owned_open, skipped, unproven)`` or refuse.

    ``target`` is the single owned muted stream on the fixed sink, ``None``
    when every owned stream there is already unmuted. Anything else on the
    daemon (other sinks, unresolvable sinks, foreign players, unknown mute)
    only increments ``skipped`` and is never touched.
    """
    roots = owner_roots(proc)
    muted, owned_open = [], []
    skipped, unproven = 0, False
    for item in items:
        if names.get(item.get("sink", "")) != TARGET_SINK:
            skipped += 1
            continue
        if not is_owned(item, roots, proc):
            skipped += 1
            if item.get("mute") is True and type(item.get("pid")) is not int:
                unproven = True
            continue
        if item.get("mute") is True:
            muted.append(item)
        elif item.get("mute") is False:
            owned_open.append(item)
        else:
            skipped += 1
    if len(muted) > 1:
        raise Refused("multiple_targets")
    if not muted:
        if owned_open:
            return None, owned_open, skipped, unproven
        raise Refused("ownership_unproven" if unproven else "no_target")
    target = muted[0]
    if target.get("corked") is True:
        raise Refused("target_corked")
    return target, owned_open, skipped, unproven


def relist(run, server):
    """One bounded ``list sink-inputs`` plus sink names, or refuse."""
    try:
        listing = run(["list", "sink-inputs"], server=server)
    except (OSError, subprocess.SubprocessError):
        raise Refused("pactl_unavailable") from None
    if listing.returncode != 0:
        raise Refused("pactl_failed")
    if not isinstance(listing.stdout, str) or len(listing.stdout) > LISTING_MAX:
        raise Refused("unparsable")
    items = parse_sink_inputs(listing.stdout)
    if listing.stdout.strip() and not items:
        # Nonempty but unrecognized output must not look like a healthy daemon
        # with nothing muted.
        raise Refused("unparsable")
    try:
        short = run(["list", "short", "sinks"], server=server)
    except (OSError, subprocess.SubprocessError):
        raise Refused("pactl_unavailable") from None
    names = {}
    if short.returncode == 0 and isinstance(short.stdout, str):
        names = sink_names(short.stdout)
    return items, names


def unmute_index(run, server, index):
    """Unmute one validated index with fixed argv, or refuse."""
    if type(index) is not int or index < 0:
        raise Refused("unmute_failed")
    try:
        result = run(["set-sink-input-mute", str(index), "0"], server=server)
    except (OSError, subprocess.SubprocessError):
        raise Refused("pactl_unavailable") from None
    if result.returncode != 0:
        raise Refused("unmute_failed")


def find_stream(items, index, pid):
    """The same (index, pid) stream in a fresh listing, or None."""
    for item in items:
        if item.get("index") == index and item.get("pid") == pid:
            return item
    return None


def probe_argv():
    """Fixed silent probe: same player binary, same sink, no media file."""
    return ["ffplay", "-nodisp", "-hide_banner", "-loglevel", "error",
            "-volume", "0", "-t", str(PROBE_SECONDS),
            "-window_title", PROBE_TAG,
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]


def probe_env():
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
            "LC_ALL": "C", "HOME": "/tmp", "PULSE_SINK": TARGET_SINK}


def _spawn_probe(spawn):
    try:
        return spawn(probe_argv(), env=probe_env())
    except (OSError, subprocess.SubprocessError):
        raise Refused("probe_missing") from None


def _stop_probe(probe):
    try:
        probe.terminate()
        probe.wait(timeout=PROBE_STOP_SECONDS)
    except (OSError, subprocess.SubprocessError):
        pass
    except Exception:
        pass
    try:
        probe.kill()
        probe.wait(timeout=PROBE_STOP_SECONDS)
    except (OSError, subprocess.SubprocessError):
        pass
    except Exception:
        pass


def run_recovery(run=None, proc=None, spawn=None, which=None,
                 server=None, sleep=None):
    """Execute both stages; return the fixed-shape evidence envelope."""
    run = _pactl if run is None else run
    proc = Path("/proc") if proc is None else proc
    spawn = subprocess.Popen if spawn is None else spawn
    which = shutil.which if which is None else which
    sleep = time.sleep if sleep is None else sleep
    server = _server_argv() if server is None else server

    try:
        items, names = relist(run, server)
    except (OSError, subprocess.SubprocessError):
        raise Refused("pactl_unavailable") from None
    target, owned_open, skipped, _ = select_target(items, names, proc)
    if target is None:
        return {"status": "already_unmuted", "reason": None, "target": None,
                "target_unmuted": None,
                "probe": None,
                "counts": {"owned_unmuted": len(owned_open),
                           "skipped": skipped}}
    index, pid = target["index"], target["pid"]
    if which("ffplay") is None:
        # The verification stage cannot run without the fixed probe player.
        # Refuse before any mutation rather than reporting an unverified fix.
        raise Refused("probe_unavailable")

    unmute_index(run, server, index)
    items, _ = relist(run, server)
    fresh = find_stream(items, index, pid)
    if fresh is None or fresh.get("mute") is not False:
        raise Refused("unmute_failed")

    probe = _spawn_probe(spawn)
    try:
        probe_pid = probe.pid
        if type(probe_pid) is not int or probe_pid <= 1:
            raise Refused("probe_missing")
        born, deadline = None, time.monotonic() + PROBE_WAIT_SECONDS
        while time.monotonic() < deadline:
            items, names = relist(run, server)
            for item in items:
                if (item.get("pid") == probe_pid
                        and names.get(item.get("sink", "")) == TARGET_SINK):
                    born = item
                    break
            if born is not None:
                break
            sleep(PROBE_POLL_SECONDS)
        if born is None:
            raise Refused("probe_missing")
        born_muted = born.get("mute") is True
        if born_muted:
            # A muted birth means the restore memory still forces mute onto
            # new streams: unmute once more so the stored entry for
            # player-keyed streams is refreshed, then re-verify.
            unmute_index(run, server, born["index"])
            items, _ = relist(run, server)
            again = find_stream(items, born["index"], probe_pid)
            if again is None or again.get("mute") is not False:
                raise Refused("probe_still_muted")
        verified = True
    finally:
        _stop_probe(probe)
    try:
        items, _ = relist(run, server)
    except Refused:
        items = []
    leftover = any(item.get("pid") == probe_pid for item in items)
    same = find_stream(items, index, pid)
    if same is not None and same.get("mute") is True:
        # The live target was re-muted behind us: the stored memory is still
        # forcing mute. Report it instead of claiming a recovery.
        raise Refused("remuted")
    return {"status": "recovered", "reason": None,
            "target": {"index": index, "sink": TARGET_SINK},
            "target_unmuted": True,
            "probe": {"spawned": True, "born_muted": born_muted,
                      "verified_unmuted": verified, "leftover": leftover},
            "counts": {"owned_unmuted": len(owned_open), "skipped": skipped}}


def _refused_envelope(reason):
    """Fixed-shape refusal evidence. Raw errors never reach stdout."""
    return {"status": "refused", "reason": reason, "target": None,
            "target_unmuted": None, "probe": None,
            "counts": {"owned_unmuted": 0, "skipped": 0}}


def main():
    if len(sys.argv) != 1 or Path.cwd().resolve() != ROOT:
        raise SystemExit("fixed production root and no arguments required")
    try:
        return run_recovery()
    except Refused as exc:
        if exc.reason not in REASONS:
            raise SystemExit("refused") from None
        print(json.dumps(_refused_envelope(exc.reason), sort_keys=True))
        print("owned BGM mute recovery refused: %s" % exc.reason,
              file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    try:
        result = main()
    except Refused as exc:
        # Unreachable: main() converts refusals to envelopes above.
        raise SystemExit("refused") from None
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # Raw errors may carry paths or identifiers; keep them off stdout.
        print(json.dumps(_refused_envelope("unexpected_failure"),
                         sort_keys=True))
        print("owned BGM mute recovery failed unexpectedly",
              file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))
    if result.get("status") not in {"recovered", "already_unmuted"}:
        raise SystemExit(1)
