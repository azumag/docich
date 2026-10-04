"""Linux effects for owned-round recovery; no pattern kill or service restart."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time

from .game_switch import atomic_write_json
from .naming import runtime_names
from .soren_round_recovery import RecoveryRefused, read_object

ROOTS = {"soren_loop.sh", "soviet_watchdog.sh", "soviet_local.mjs", "strategy_runner.py"}
COMMON = {"start_all.sh", "direct_stream.py", "audio_worker.sh", "chat_worker.sh",
          "radio_worker.sh", "improve_daemon.sh", "prediction_worker.sh", "Xvfb",
          "watch_soren_overlay.sh", "watch_system_status.sh", "ffmpeg", "webui.py"}
FILES = ("game_state.json", "game_history/latest.jsonl", "commands.txt",
         "tmp/state/main_strategy_runner_active.json", "tmp/state/game_observation.json",
         "tmp/markers/.soviet_created", "tmp/markers/.russia_created",
         *("tmp/state/game_lifecycle/" + name + ".json" for name in
           ("request", "ack", "control", "game_resource", "player_capabilities")))
PAUSES = ("soren_loop", "soviet_watchdog")
OUTPUT = "runner_output.txt"


class LinuxRecoveryEffects:
    def __init__(self, root, state_dir, *, proc=Path("/proc"), clock=time.time, sleep=time.sleep):
        self.root, self.state_dir, self.proc = Path(root).resolve(), Path(state_dir), Path(proc)
        self.clock, self.sleep = clock, sleep

    def _rows(self):
        rows = {}
        for entry in self.proc.iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                args = [os.fsdecode(x) for x in (entry / "cmdline").read_bytes().split(b"\0") if x]
                # Game/agent processes are launched as this uid. Reading an
                # unrelated root daemon's cwd requires privileges we do not
                # have and must not acquire. Its argv/birth can still protect
                # known common workers without treating it as a game root.
                cwd = self._cwd(entry) if args and entry.stat().st_uid == os.getuid() else ""
                rows[int(entry.name)] = dict(pid=int(entry.name), birth=int(fields[19]),
                    ppid=int(fields[1]), state=fields[0], cwd=cwd, args=args)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, IndexError) as exc:
                raise RecoveryRefused("process inventory incomplete") from exc
        return rows

    @staticmethod
    def _cwd(entry):
        # A non-dumpable process (e.g. a sandboxed browser child) hides its cwd
        # even from the same uid. It cannot be a game root; descendants are
        # still attributed through ppid.
        try:
            return str((entry / "cwd").resolve(strict=True))
        except PermissionError:
            return ""

    @staticmethod
    def _names(row):
        return {Path(arg).name for arg in row["args"]}

    @staticmethod
    def _role(row):
        args = row["args"]
        if len(args) == 2 and Path(args[0]).name in {"bash", "node", "nodejs"}:
            name = Path(args[1]).name
            if name in {"soren_loop.sh", "soviet_watchdog.sh"} and Path(args[0]).name == "bash":
                return name
            if name == "soviet_local.mjs" and Path(args[0]).name in {"node", "nodejs"}:
                return name
        if len(args) == 3 and Path(args[0]).name == "python3" and args[1] == "-u" and Path(args[2]).name == "strategy_runner.py":
            return "strategy_runner.py"
        return None

    def _outer(self, rows):
        """Outermost game-root pids and every role-matching pid.

        bash forks subshells that keep the parent's argv, so a role can match
        several pids; only the one whose parent is not the same role is the
        root. The forks are descendants and are frozen/stopped with it.
        """
        roles = {pid: self._role(r) for pid, r in rows.items()
                 if r["cwd"] == str(self.root) and self._role(r) is not None}
        roots = {pid for pid, role in roles.items() if roles.get(rows[pid]["ppid"]) != role}
        return roots, roles

    def _tree(self, rows):
        roots, roles = self._outer(rows)
        if any(r["cwd"] == str(self.root) and self._names(r) & ROOTS and pid not in roles
               for pid, r in rows.items()):
            raise RecoveryRefused("unattributed game process")
        owned = set(roots)
        while True:
            added = {pid for pid, r in rows.items() if r["ppid"] in owned} - owned
            if not added:
                break
            owned |= added
        # A shared worker descendant is an ambiguous topology, never a reason
        # to include common workers in a game termination.
        if any(self._names(rows[p]) & (COMMON - {"ffmpeg"}) for p in owned):
            raise RecoveryRefused("shared worker in game tree")
        return roots, owned

    def target_released(self, generation, target):
        rows = self._rows()
        runtime_base = self.state_dir / "runtimes"
        # Inspect process attribution as well as tmux. Missing windows alone
        # cannot prove that a reparented game/agent child released resources.
        for r in rows.values():
            tokens = [r["cwd"], *r["args"]]
            if any(str(runtime_base / f"g{generation}-") in x for x in tokens):
                raise RecoveryRefused("target runtime process remains")
            if any(Path(x).name in {"retroarch", "hanjuku_bot.py", "hanjuku-bot"} for x in r["args"]):
                raise RecoveryRefused("unattributed retro process remains")
        # Failure/query errors are unknown, not an empty list.
        p = subprocess.run(["tmux", "list-windows", "-a", "-F", "#{window_name}"],
                           capture_output=True, text=True, timeout=5, check=False)
        if p.returncode != 0:
            raise RecoveryRefused("tmux resource inventory unavailable")
        names = runtime_names(generation)
        if {names.game_window, names.agent_window} & set(p.stdout.splitlines()):
            raise RecoveryRefused("target runtime window remains")

    def _public(self, row):
        return {k: row[k] for k in ("pid", "birth")}

    def preflight(self):
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise RecoveryRefused("Linux pidfd containment unavailable")
        for name in PAUSES:
            if (self.root / f"tmp/state/{name}.paused").exists():
                raise RecoveryRefused("existing pause must be preserved")
        if (self.root / "tmp/stop").exists():
            raise RecoveryRefused("existing user stop must be preserved")
        if (self.root / "tmp/improve.lock").exists():
            raise RecoveryRefused("improve lock would hold the loop respawn")
        player = self.root / "tmp/state/game_lifecycle/player_state.json"
        if player.exists() and read_object(player).get("policy") != "existing":
            raise RecoveryRefused("player policy is not existing")
        rows = self._rows()
        roots, owned = self._tree(rows)
        if any(p not in owned and r["cwd"] == str(self.root)
               and "game_lifecycle.py" in self._names(r) for p, r in rows.items()):
            raise RecoveryRefused("independent lifecycle writer/probe in flight")
        by_name = {name: [rows[p] for p in roots if name == self._role(rows[p])] for name in ROOTS}
        if any(len(v) != 1 for v in by_name.values()):
            raise RecoveryRefused("game root missing or duplicated")
        runner = read_object(self.root / "tmp/state/main_strategy_runner_active.json")
        if (type(runner.get("pid")) is not int
                or runner["pid"] != by_name["strategy_runner.py"][0]["pid"]
                or type(runner.get("game")) is not int or runner["game"] < 1
                or type(runner.get("started_at")) is not int
                or not 0 <= self.clock() - runner["started_at"] <= 86400):
            raise RecoveryRefused("runner marker identity mismatch")
        boot = self._boot()
        birth_epoch = boot + by_name["strategy_runner.py"][0]["birth"] / os.sysconf("SC_CLK_TCK")
        if not 0 <= runner["started_at"] - birth_epoch <= 10:
            raise RecoveryRefused("runner marker birth mismatch")
        state = read_object(self.root / "game_state.json")
        count = state.get("makeSorenCount")
        if (state.get("state") not in {"STOP", "GAMEOVER"} or type(count) not in (int, float)
                or not math.isfinite(count) or not 0 <= count <= 2**53 - 1 or count != int(count)):
            raise RecoveryRefused("round is not a valid stopped board")
        if not 60 <= self.clock() - (self.root / "game_state.json").stat().st_mtime <= 86400:
            raise RecoveryRefused("board is recent, future or too stale")
        # Orphaned Chromium with the game's profile is not a pinned descendant.
        for pid, r in rows.items():
            if pid not in owned and any("soviet_local_chromium_profile" in x for x in r["args"]):
                raise RecoveryRefused("unattributed game browser remains")
        supervisor = [r for r in rows.values() if r["cwd"] == str(self.root)
                      and "start_all.sh" in self._names(r)]
        if len(supervisor) != 1:
            raise RecoveryRefused("existing common supervisor is not unique")
        try:
            observed = read_object(self.root / "tmp/state/game_observation.json").get("game_id")
        except RecoveryRefused:
            observed = None
        return dict(roots={name: self._public(v[0]) for name, v in by_name.items()},
                    processes=[self._public(rows[p]) for p in sorted(owned)],
                    common=self._common(rows, owned), board=state, runner=runner,
                    observation_game_id=observed,
                    runner_output=self._runner_output(runner["pid"]))

    def _common(self, rows, owned):
        # Pin only durable outermost shared workers. Short-lived children and
        # subshell forks may legitimately exit and must not fail the proof.
        boot = self._boot()
        ticks = os.sysconf("SC_CLK_TCK")
        named = {p: self._names(r) & COMMON for p, r in rows.items()
                 if p not in owned and self._names(r) & COMMON}
        # A parent with the same worker name is a subshell fork, not a worker.
        return [self._public(rows[p]) for p in sorted(named)
                if named.get(rows[p]["ppid"]) != named[p]
                and self.clock() - (boot + rows[p]["birth"] / ticks) >= 120]

    def _boot(self):
        return next(int(x.split()[1]) for x in (self.proc / "stat").read_text().splitlines()
                    if x.startswith("btime "))

    def _runner_output(self, pid):
        # The eloop runner's stdout temp file holds the round's printed result.
        try:
            path = Path(os.readlink(self.proc / str(pid) / "fd/1"))
        except OSError:
            return None
        if path.parent == Path("/tmp") and path.name.startswith("eloop_runner.") and path.is_file():
            return str(path)
        return None

    def _signal(self, row, sig, *, missing=False):
        entry = self.proc / str(row["pid"])
        try:
            with os.fdopen(os.pidfd_open(row["pid"]), "rb") as fd:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[19]) != row["birth"]:
                    if missing:
                        return
                    raise RecoveryRefused("PID birth changed")
                signal.pidfd_send_signal(fd.fileno(), sig)
        except ProcessLookupError:
            if not missing:
                raise RecoveryRefused("owned process disappeared")

    def pause(self, j):
        for name in PAUSES:
            path = self.root / f"tmp/state/{name}.paused"
            token = "round-recovery:" + j["request_id"]
            try:
                with path.open("x") as h:
                    h.write(token)
                    h.flush()
                    os.fsync(h.fileno())
            except FileExistsError:
                if path.read_text() != token:
                    raise RecoveryRefused("pause owner changed")

    def freeze(self, j):
        for r in j["inventory"]["roots"].values():
            self._signal(r, signal.SIGSTOP)
        # Roots can no longer fork. Repeatedly freeze descendants; a child
        # that forks before its stop is included on the next scan.
        for _ in range(20):
            rows = self._rows()
            _, owned = self._tree(rows)
            for pid in owned:
                self._signal(self._public(rows[pid]), signal.SIGSTOP)
            later = self._rows()
            _, after = self._tree(later)
            if after == owned and all(later[p]["state"] in {"T", "t", "Z"} for p in after):
                if read_object(self.root / "game_state.json") != j["inventory"]["board"]:
                    raise RecoveryRefused("board changed before freeze; retain hold")
                j["inventory"]["processes"] = [self._public(later[p]) for p in sorted(after)]
                return
        raise RecoveryRefused("game tree did not freeze")

    def _archive_dir(self, j):
        return self.state_dir / "soren-round-recovery" / j["request_id"]

    def _evidence(self, j):
        """(archive name, source) for root files plus the runner's printed output."""
        found = [(name, self.root / name) for name in FILES]
        output = (j.get("inventory") or {}).get("runner_output")
        return found + ([(OUTPUT, Path(output))] if output else [])

    def archive(self, j):
        dest = self._archive_dir(j)
        dest.mkdir(parents=True, exist_ok=True, mode=0o700)
        manifest = {}
        for name, path in self._evidence(j):
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
                raise RecoveryRefused("result evidence unsafe or oversized")
            data = path.read_bytes()
            saved = dest / name
            saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if saved.exists() and saved.read_bytes() != data:
                raise RecoveryRefused("saved result changed")
            if not saved.exists():
                with saved.open("xb") as h:
                    os.chmod(saved, 0o600)
                    h.write(data)
                    h.flush()
                    os.fsync(h.fileno())
            manifest[name] = hashlib.sha256(data).hexdigest()
        if "game_state.json" not in manifest or "game_history/latest.jsonl" not in manifest:
            raise RecoveryRefused("round result/history missing")
        atomic_write_json(dest / "result.json", dict(status="interrupted", reason="operator-abandoned",
            active=j["active"], original_completed_at=j["completed_at"], files=manifest))
        j["archive_sha256"] = hashlib.sha256((dest / "result.json").read_bytes()).hexdigest()

    def verify_archive(self, j):
        if j["stage"] in {"prepared", "paused", "frozen"}:
            return
        dest = self._archive_dir(j)
        result = read_object(dest / "result.json")
        if hashlib.sha256((dest / "result.json").read_bytes()).hexdigest() != j.get("archive_sha256"):
            raise RecoveryRefused("archive receipt changed")
        for name, digest in result["files"].items():
            if name not in (*FILES, OUTPUT) or hashlib.sha256((dest / name).read_bytes()).hexdigest() != digest:
                raise RecoveryRefused("saved evidence changed")

    def stop(self, j):
        # SIGKILL after saving a frozen tree avoids legacy loop exit traps
        # calling shared cleanup_all. pidfds limit this to the bound tree.
        for r in j["inventory"]["processes"]:
            self._signal(r, signal.SIGKILL, missing=True)
        self._wait(lambda: self._old_gone(j), 15)

    def _old_gone(self, j):
        rows = self._rows()
        return all(r["pid"] not in rows or rows[r["pid"]]["birth"] != r["birth"]
                   or rows[r["pid"]]["state"] == "Z" for r in j["inventory"]["processes"])

    def clear_old(self, j):
        if not self._old_gone(j):
            raise RecoveryRefused("old writer survives")
        self.verify_archive(j)
        for name, path in self._evidence(j):
            if not path.exists():
                continue
            archived = self._archive_dir(j) / name
            if not archived.exists() or path.read_bytes() != archived.read_bytes():
                raise RecoveryRefused("late writer changed archived evidence")
            path.unlink()

    def _unpause(self, j, name):
        path = self.root / f"tmp/state/{name}.paused"
        if not path.exists():
            return  # launch intent is durable; adopt the one fresh process.
        if path.read_text() != "round-recovery:" + j["request_id"]:
            raise RecoveryRefused("pause owner changed")
        path.unlink()

    def _fresh(self, name, j):
        return self._fresh_row(name, j) is not None

    def _fresh_row(self, name, j):
        all_rows = self._rows()
        roots, roles = self._outer(all_rows)
        rows = [all_rows[p] for p in roots if roles[p] == name and all_rows[p]["state"] != "Z"]
        old = j["inventory"]["roots"][name]
        if len(rows) == 1 and rows[0]["birth"] > old["birth"] and self._public(rows[0]) != old:
            return rows[0]
        return None

    def _wait(self, check, seconds=150):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if check():
                return
            self.sleep(0.25)
        raise RecoveryRefused("game-only recovery readiness timeout")

    def start_bridge(self, j):
        if not self._old_gone(j):
            raise RecoveryRefused("old writer survives")
        self._unpause(j, "soviet_watchdog")
        self._wait(lambda: self._fresh("soviet_watchdog.sh", j) and self._fresh("soviet_local.mjs", j)
                   and self._new_board(j))

    def _new_board(self, j):
        try:
            state = read_object(self.root / "game_state.json")
            observation = read_object(self.root / "tmp/state/game_observation.json")
        except RecoveryRefused:
            return False
        board, game_id = observation.get("board"), observation.get("game_id")
        zero = lambda v: type(v) in (int, float) and v == 0
        playing = lambda v: isinstance(v, str) and v not in {"STOP", "GAMEOVER"}
        # The old observation file was archived and removed, so a heartbeat with
        # a different bridge nonce proves a new bridge saw an empty live board.
        return (playing(state.get("state")) and zero(state.get("makeSorenCount")) and zero(state.get("score"))
                and isinstance(board, dict) and playing(board.get("state"))
                and isinstance(game_id, str) and bool(game_id)
                and game_id != j["inventory"].get("observation_game_id")
                and type(observation.get("observed_epoch")) in (int, float)
                and 0 <= self.clock() - observation["observed_epoch"] <= 3
                and (self.root / "game_state.json").stat().st_mtime >= j["started_epoch"])

    def start_runner(self, j):
        self._unpause(j, "soren_loop")
        self._wait(lambda: self._fresh("soren_loop.sh", j) and self._fresh("strategy_runner.py", j))

    def verify_new(self, j):
        fresh = {name: self._fresh_row(name, j) for name in ROOTS}
        if not self._old_gone(j) or not all(fresh.values()):
            raise RecoveryRefused("new game process identity unproved")
        # The old marker was archived and removed, so this one was written by
        # the new loop for exactly the new runner process.
        runner = read_object(self.root / "tmp/state/main_strategy_runner_active.json")
        if (runner.get("pid") != fresh["strategy_runner.py"]["pid"]
                or type(runner.get("started_at")) is not int
                or runner["started_at"] < int(j["started_epoch"])):
            raise RecoveryRefused("runner marker does not identify the new runner")

    def common_changed(self, j):
        rows = self._rows()
        return [r["pid"] for r in j["inventory"]["common"]
                if r["pid"] not in rows or rows[r["pid"]]["birth"] != r["birth"]]

    def common_unchanged(self, j):
        if self.common_changed(j):
            raise RecoveryRefused("common worker identity changed")
