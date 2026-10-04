"""Linux effects for fixed Soren recovery; no freeze, pause or service restart."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import math
import os
from pathlib import Path
import signal
import time

from .game_switch import atomic_write_json
from .soren_round_recovery import RecoveryRefused, read_object

ROOTS = {"soren_loop.sh", "soviet_watchdog.sh", "soviet_local.mjs", "strategy_runner.py"}
COMMON = {"start_all.sh", "direct_stream.py", "direct_stream.sh", "audio_worker.sh", "chat_worker.sh",
          "radio_worker.sh", "improve_daemon.sh", "prediction_worker.sh", "Xvfb", "ffmpeg", "webui.py",
          "watch_soren_overlay.sh", "watch_system_status.sh", "status_dashboard.py",
          "youtube_worker.sh", "kick_worker.sh", "deadline_monitor.sh", "poll_worker.sh", "goal_worker.sh",
          "status_overlay_watch.sh", "show_status_overlay_watch.sh", "soren_overlay_watch.sh",
          "obs_capture_watchdog.sh", "stream_noon_audit.sh", "youtube_broadcast_guard.sh"}
# These are round-local files only. In particular, no lifecycle request/ack,
# control, capability or resource record is copied, removed or rewritten.
FILES = ("game_state.json", "game_history/latest.jsonl", "commands.txt",
         "tmp/state/main_strategy_runner_active.json", "tmp/state/game_observation.json",
         "tmp/markers/.soviet_created", "tmp/markers/.russia_created")
PAUSES = ("soren_loop", "soviet_watchdog")
OUTPUT = "runner_output.txt"


class LinuxRecoveryEffects:
    def __init__(self, root, state_dir, *, proc=Path("/proc"), clock=time.time,
                 sleep=time.sleep, monotonic=time.monotonic):
        self.root, self.state_dir, self.proc = Path(root).resolve(), Path(state_dir), Path(proc)
        self.clock, self.sleep, self.monotonic = clock, sleep, monotonic

    def _rows(self):
        rows = {}
        for entry in self.proc.iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                args = [os.fsdecode(x) for x in (entry / "cmdline").read_bytes().split(b"\0") if x]
                cwd = self._cwd(entry) if args and entry.stat().st_uid == os.getuid() else ""
                rows[int(entry.name)] = dict(pid=int(entry.name), birth=int(fields[19]),
                    ppid=int(fields[1]), state=fields[0], cwd=cwd, args=args)
            except FileNotFoundError:
                continue  # Short-lived children commonly exit during inventory.
            except (OSError, ValueError, IndexError) as exc:
                raise RecoveryRefused("process inventory incomplete") from exc
        return rows

    @staticmethod
    def _cwd(entry):
        try:
            return str((entry / "cwd").resolve(strict=True))
        except PermissionError:
            return ""  # Still attributable as a descendant through ppid.

    @staticmethod
    def _names(row):
        return {Path(arg).name for arg in row["args"]}

    @staticmethod
    def _role(row):
        args = row["args"]
        if len(args) == 2:
            exe, name = Path(args[0]).name, Path(args[1]).name
            if exe == "bash" and name in {"soren_loop.sh", "soviet_watchdog.sh", "start_all.sh"}:
                return name
            if exe in {"node", "nodejs"} and name == "soviet_local.mjs":
                return name
        if (len(args) == 3 and Path(args[0]).name == "python3" and args[1] == "-u"
                and Path(args[2]).name == "strategy_runner.py"):
            return "strategy_runner.py"
        return None

    def _outer(self, rows):
        roles = {pid: self._role(r) for pid, r in rows.items()
                 if r["cwd"] == str(self.root) and self._role(r) is not None and r["state"] != "Z"}
        roots = set()
        for pid, role in roles.items():
            parent, seen = rows[pid]["ppid"], {pid}
            while parent in rows and parent not in seen:
                if roles.get(parent) == role:
                    break  # A subshell/probe fork can have an intermediate parent.
                seen.add(parent)
                parent = rows[parent]["ppid"]
            else:
                roots.add(pid)
        return roots, roles

    def _profile_owned(self, row):
        profile = self.root / "tmp/soviet_local_chromium_profile"
        # Exact path options, not arbitrary substring matches or process names.
        paths = {str(profile), str(profile / "Crashpad")}
        return any(arg.partition("=")[0] in {"--database", "--user-data-dir", "--crash-dumps-dir"}
                   and arg.partition("=")[2] in paths for arg in row["args"])

    def _tree(self, rows):
        outer, roles = self._outer(rows)
        roots = {pid for pid in outer if roles[pid] in ROOTS}
        if any(r["cwd"] == str(self.root) and self._names(r) & ROOTS and pid not in roles
               and r["state"] != "Z" for pid, r in rows.items()):
            raise RecoveryRefused("unattributed game process")
        owned = set(roots)
        while True:
            added = {pid for pid, r in rows.items() if r["ppid"] in owned} - owned
            if not added:
                break
            owned |= added
        for pid, row in rows.items():
            if pid in owned or not self._profile_owned(row) or row["state"] == "Z":
                continue
            if row["ppid"] == 1 and Path(row["args"][0]).name in {"chrome_crashpad_handler", "crashpad_handler"}:
                owned.add(pid)
            else:
                raise RecoveryRefused("unattributed game browser remains")
        if any(self._names(rows[p]) & COMMON for p in owned):
            raise RecoveryRefused("shared worker in game tree")
        return roots, owned

    @staticmethod
    def _identity(row):
        return {k: row[k] for k in ("pid", "birth")}

    def _operator_gates(self):
        for name in PAUSES:
            if (self.root / f"tmp/state/{name}.paused").exists():
                raise RecoveryRefused("existing pause must be preserved")
        if (self.root / "tmp/stop").exists():
            raise RecoveryRefused("existing user stop must be preserved")
        if (self.root / "tmp/improve.lock").exists():
            raise RecoveryRefused("improve lock would hold the loop respawn")

    def preflight(self):
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise RecoveryRefused("Linux pidfd containment unavailable")
        self._operator_gates()
        rows = self._rows()
        roots, owned = self._tree(rows)
        by_name = {name: [rows[p] for p in roots if self._role(rows[p]) == name] for name in ROOTS}
        if any(len(v) != 1 for v in by_name.values()):
            raise RecoveryRefused("game root missing or duplicated")
        outer, roles = self._outer(rows)
        supervisor = [rows[p] for p in outer if roles[p] == "start_all.sh"]
        if len(supervisor) != 1:
            raise RecoveryRefused("existing common supervisor is not unique")
        state_path = self.root / "game_state.json"
        state = read_object(state_path)
        if state.get("state") not in {"STOP", "GAMEOVER"}:
            raise RecoveryRefused("round is not a stopped board")
        if self.clock() - state_path.stat().st_mtime < 600:
            raise RecoveryRefused("stopped board has not been static for ten minutes")
        return dict(roots={name: self._identity(v[0]) for name, v in by_name.items()},
                    processes=[self._identity(rows[p]) for p in sorted(owned)], board=state,
                    common=self._common(rows, owned), supervisor=self._identity(supervisor[0]),
                    runner_output=self._runner_output(by_name["strategy_runner.py"][0]["pid"]))

    def _common(self, rows, owned):
        boot, ticks = self._boot(), os.sysconf("SC_CLK_TCK")
        named = {p: self._names(r) & COMMON for p, r in rows.items() if p not in owned and self._names(r) & COMMON}
        return [self._identity(rows[p]) for p in sorted(named)
                if named.get(rows[p]["ppid"]) != named[p]
                and self.clock() - (boot + rows[p]["birth"] / ticks) >= 120]

    def _boot(self):
        return next(int(x.split()[1]) for x in (self.proc / "stat").read_text().splitlines() if x.startswith("btime "))

    def _runner_output(self, pid):
        try:
            path = Path(os.readlink(self.proc / str(pid) / "fd/1"))
        except OSError:
            return None
        if path.parent == Path("/tmp") and path.name.startswith("eloop_runner.") and path.is_file():
            return str(path)
        return None

    def _archive_dir(self, recovery):
        return self.state_dir / "soren-round-recovery" / recovery["recovery_id"]

    def _evidence(self, recovery):
        found = [(name, self.root / name) for name in FILES]
        output = recovery["inventory"].get("runner_output")
        return found + ([(OUTPUT, Path(output))] if output else [])

    def archive(self, recovery):
        dest = self._archive_dir(recovery)
        dest.mkdir(parents=True, exist_ok=False, mode=0o700)
        manifest = {}
        for name, path in self._evidence(recovery):
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
                raise RecoveryRefused("result evidence unsafe or oversized")
            data = path.read_bytes()
            saved = dest / name
            saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with saved.open("xb") as handle:
                os.chmod(saved, 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            manifest[name] = hashlib.sha256(data).hexdigest()
        if "game_state.json" not in manifest or "game_history/latest.jsonl" not in manifest:
            raise RecoveryRefused("round result/history missing")
        if read_object(dest / "game_state.json") != recovery["inventory"]["board"]:
            raise RecoveryRefused("board changed before archive")
        recovery["files"] = manifest
        atomic_write_json(dest / "result.json", dict(status="interrupted", reason="operator-abandoned",
            active=recovery["active"], started_epoch=recovery["started_epoch"], files=manifest))

    def _pin(self, row, *, missing=False):
        try:
            fd = os.pidfd_open(row["pid"])
        except ProcessLookupError:
            if missing:
                return None
            raise RecoveryRefused("game root disappeared") from None
        try:
            fields = (self.proc / str(row["pid"]) / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[19]) != row["birth"]:
                if missing:
                    os.close(fd)
                    return None
                raise RecoveryRefused("PID birth changed")
        except FileNotFoundError:
            os.close(fd)
            if missing:
                return None
            raise RecoveryRefused("game root disappeared") from None
        except BaseException:
            os.close(fd)
            raise
        return os.fdopen(fd, "rb")

    def _verify_targets(self, recovery):
        self._operator_gates()
        rows = self._rows()
        roots, owned = self._tree(rows)
        current = {self._role(rows[p]): self._identity(rows[p]) for p in roots}
        if current != recovery["inventory"]["roots"] or len(roots) != len(current):
            raise RecoveryRefused("game roots changed before termination")
        return rows, roots, owned

    def _verify_stopped_board(self, recovery, path):
        board = read_object(path)
        if (board != recovery["inventory"]["board"] or board.get("state") not in {"STOP", "GAMEOVER"}
                or self.clock() - path.stat().st_mtime < 600):
            raise RecoveryRefused("live board changed before termination")

    def stop(self, recovery):
        rows, roots, owned = self._verify_targets(recovery)
        # Refresh descendants just before pinning. Vanished short-lived children
        # are harmless; every live target is bound before the first kill.
        recovery["inventory"]["processes"] = [self._identity(rows[p]) for p in sorted(owned)]
        with ExitStack() as pins:
            bound = []
            for pid in sorted(owned, key=lambda p: (p not in roots, p)):
                fd = self._pin(self._identity(rows[pid]), missing=pid not in roots)
                if fd is not None:
                    bound.append(pins.enter_context(fd))
            self._verify_targets(recovery)
            self._verify_stopped_board(recovery, self.root / "game_state.json")
            # Detach round-local names BEFORE killing, so immediate supervisor
            # respawn cannot have fresh files removed by a later cleanup.
            # Saved bytes are evidence. No lifecycle file or user gate is moved.
            detached = []
            try:
                for name, path in self._evidence(recovery):
                    if name == OUTPUT or name not in recovery["files"] or not path.exists():
                        continue
                    if path.is_symlink():
                        raise RecoveryRefused("round evidence path changed")
                    saved = self._archive_dir(recovery) / name
                    if hashlib.sha256(saved.read_bytes()).hexdigest() != recovery["files"][name]:
                        raise RecoveryRefused("saved evidence changed")
                    retired = self._archive_dir(recovery) / "retired" / name
                    retired.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    path.rename(retired)
                    detached.append((path, retired))
                # Check again at the irreversible boundary. A writer may have
                # updated the moved file through an open fd or recreated the
                # live name while the other round-local files were detached.
                self._verify_targets(recovery)
                self._verify_stopped_board(recovery, self._archive_dir(recovery) / "retired/game_state.json")
                if (self.root / "game_state.json").exists():
                    raise RecoveryRefused("live board changed before termination")
            except BaseException:
                for path, retired in reversed(detached):
                    if not path.exists():
                        retired.rename(path)
                raise
            # SIGKILL avoids legacy TERM/EXIT traps that invoke shared cleanup.
            for fd in bound:
                try:
                    signal.pidfd_send_signal(fd.fileno(), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def _old_gone(self, recovery):
        rows = self._rows()
        return all(r["pid"] not in rows or rows[r["pid"]]["birth"] != r["birth"] or rows[r["pid"]]["state"] == "Z"
                   for r in recovery["inventory"]["processes"])

    def _fresh_rows(self, recovery):
        rows = self._rows()
        outer, roles = self._outer(rows)
        fresh = {}
        boot, ticks = self._boot(), os.sysconf("SC_CLK_TCK")
        for name, old in recovery["inventory"]["roots"].items():
            candidates = [rows[p] for p in outer if roles[p] == name]
            if len(candidates) != 1:
                return None
            row = candidates[0]
            if (row["birth"] <= old["birth"] or self._identity(row) == old
                    or boot + row["birth"] / ticks < recovery["started_epoch"] - 1):
                return None
            fresh[name] = row
        return fresh

    def verify_new(self, recovery):
        deadline = self.monotonic() + 120
        first_board = None
        while self.monotonic() < deadline:
            fresh = self._fresh_rows(recovery)
            try:
                state_path = self.root / "game_state.json"
                board = read_object(state_path)
                marker = read_object(self.root / "tmp/state/main_strategy_runner_active.json")
                updated = state_path.stat().st_mtime >= recovery["started_epoch"]
            except (RecoveryRefused, FileNotFoundError):
                board, marker, updated = {}, {}, False
            if fresh and self._old_gone(recovery) and updated and board.get("state") in {"MOVE", "DROP", "WAITING"}:
                birth = self._boot() + fresh["strategy_runner.py"]["birth"] / os.sysconf("SC_CLK_TCK")
                started = marker.get("started_at")
                if (marker.get("pid") == fresh["strategy_runner.py"]["pid"] and type(started) is int
                        and 0 <= started - birth <= 10):
                    score = board.get("score")
                    progress = type(score) in (int, float) and math.isfinite(score) and score > 0
                    pieces = board.get("pieces")
                    if isinstance(pieces, list):
                        if first_board is None:
                            first_board = pieces
                        elif pieces != first_board:
                            progress = True
                    if progress:
                        supervisor = recovery["inventory"]["supervisor"]
                        row = self._rows().get(supervisor["pid"])
                        if row is None or self._identity(row) != supervisor or row["state"] == "Z":
                            raise RecoveryRefused("existing supervisor changed during recovery")
                        return
            self.sleep(0.25)
        raise RecoveryRefused("new game progress not proved within two minutes; no persistent hold")

    def common_changed(self, recovery):
        rows = self._rows()
        return [r["pid"] for r in recovery["inventory"]["common"]
                if r["pid"] not in rows or rows[r["pid"]]["birth"] != r["birth"]]
