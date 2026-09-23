#!/usr/bin/env python3
"""Bounded, read-only CPU profiler for the docich VM (#970 PR1).

Samples ``/proc`` for a fixed, short window and reports where CPU time,
wake-ups (context switches) and process spawns go, aggregated into fixed
component labels. It is a diagnostic snapshot, not a resident monitor:

- read-only: it never signals, renices, kills or writes into the runtime.
  The only write is the optional ``--output`` report file.
- bounded: duration/interval are clamped, the output lists are capped and the
  process never outlives ``--duration`` (+ warm-up).
- sanitized: it reads ``/proc/<pid>/cmdline`` only in-process to classify a
  process. Command lines, environment, cwd and file contents are never emitted.
  Labels are either a fixed executable map, a validated module/subcommand/script
  basename, or a registered worker name from ``runtime_registry.WORKERS``.

Usage::

    python3 ops/vm_actions/profile_cpu.py sample --scenario idle --duration 60
    python3 ops/vm_actions/profile_cpu.py compare before.json after.json

Known limits (reported, not hidden): a process that exits between two samples
loses the CPU ticks accrued since its last sample, and processes that live
shorter than one interval are counted only by the host-wide fork counter
(``host.forks``), not per component. Application-level timings (agent cycle,
screenshot capture/decode, RetroArch FPS, dashboard request rate) are out of
scope for this process-level profiler.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

SCHEMA = "docich.cpu_profile.v1"

MIN_DURATION = 10.0
MAX_DURATION = 600.0
MIN_INTERVAL = 0.5
MAX_INTERVAL = 10.0
MAX_WARMUP = 120.0
MAX_COMPONENTS = 40
MAX_TOP_PROCESSES = 20
MAX_SPAWN_ROWS = 30
MAX_CMDLINE_BYTES = 4096

SCENARIO_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
SUBCOMMAND_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")
SCRIPT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,47}\.(py|sh)$")

# Fixed executable basename -> component label. Only these literal labels are
# emitted for non-script processes; anything else is aggregated as "other".
EXECUTABLES = {
    "retroarch": "retroarch",
    "Xvfb": "x_server",
    "Xorg": "x_server",
    "Xvnc": "x_server",
    "xdotool": "xdotool",
    "xwd": "x_capture_tool",
    "import": "x_capture_tool",
    "xwininfo": "x_query_tool",
    "xprop": "x_query_tool",
    "wmctrl": "x_query_tool",
    "pulseaudio": "audio_server",
    "pipewire": "audio_server",
    "pipewire-pulse": "audio_server",
    "wireplumber": "audio_server",
    "pactl": "audio_cli",
    "pacmd": "audio_cli",
    "paplay": "audio_cli",
    "aplay": "audio_cli",
    "sox": "audio_cli",
    "mpv": "media_player",
    "chromium": "browser",
    "chromium-browser": "browser",
    "chrome": "browser",
    "headless_shell": "browser",
    "obs": "obs",
    "tmux": "tmux",
    "tmux: server": "tmux",
    "node": "node",
    "sshd": "sshd",
    "tailscaled": "tailscale",
    "dockerd": "container_runtime",
    "containerd": "container_runtime",
    "containerd-shim-runc-v2": "container_runtime",
    "nethack": "game:nethack",
    "pacman4console": "game:pacman4console",
    "moon-buggy": "game:moon-buggy",
    "ninvaders": "game:ninvaders",
    "bastet": "game:bastet",
    "nsnake": "game:nsnake",
    "robots": "game:robots",
    "dosbox": "game:dosbox",
    "curl": "curl",
    "git": "git",
    "sleep": "sleep",
    "inotifywait": "inotifywait",
    "jq": "jq",
}
PYTHON_RE = re.compile(r"^python(\d+(\.\d+)?)?$")
SHELLS = {"bash", "sh", "dash"}
GENERIC = ("other", "python", "shell")


def _load_registry_workers():
    path = Path(__file__).with_name("runtime_registry.py")
    try:
        spec = importlib.util.spec_from_file_location("docich_runtime_registry", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return tuple(module.WORKERS)
    except Exception:  # registry is optional context; never fatal
        return ()


# ---------------------------------------------------------------------------
# /proc readers (all tolerate races with exiting processes)


def _read_text(path, limit=65536):
    try:
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", "replace")
    except OSError:
        return None


def read_host_cpu(proc_root):
    """Return (busy_ticks, total_ticks, iowait_ticks, forks, ctxt) or None."""
    text = _read_text(proc_root / "stat")
    if text is None:
        return None
    busy = total = iowait = forks = ctxt = 0
    found = False
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "cpu":
            vals = [int(v) for v in parts[1:9] if v.isdigit()]
            vals += [0] * (8 - len(vals))
            user, nice, system, idle, io, irq, softirq, steal = vals[:8]
            total = user + nice + system + idle + io + irq + softirq + steal
            busy = total - idle - io
            iowait = io
            found = True
        elif parts[0] == "processes" and len(parts) > 1 and parts[1].isdigit():
            forks = int(parts[1])
        elif parts[0] == "ctxt" and len(parts) > 1 and parts[1].isdigit():
            ctxt = int(parts[1])
    return (busy, total, iowait, forks, ctxt) if found else None


def read_loadavg(proc_root):
    text = _read_text(proc_root / "loadavg")
    if not text:
        return None
    parts = text.split()
    try:
        running, _, _total = parts[3].partition("/")
        return float(parts[0]), int(running)
    except (IndexError, ValueError):
        return None


def read_uptime(proc_root):
    text = _read_text(proc_root / "uptime")
    try:
        return float(text.split()[0])
    except (AttributeError, IndexError, ValueError):
        return None


def read_proc(proc_root, pid):
    """Return a per-process sample dict, or None if it vanished/unreadable."""
    base = proc_root / str(pid)
    stat = _read_text(base / "stat")
    if not stat:
        return None
    lpar, rpar = stat.find("("), stat.rfind(")")
    if lpar < 0 or rpar < 0:
        return None
    comm = stat[lpar + 1:rpar]
    fields = stat[rpar + 2:].split()
    # fields[0] is state (field 3); utime=14, stime=15, threads=20, starttime=22
    try:
        ppid = int(fields[1])
        ticks = int(fields[11]) + int(fields[12])
        threads = int(fields[17])
        starttime = int(fields[19])
    except (IndexError, ValueError):
        return None
    vcs = nvcs = rss_kb = 0
    status = _read_text(base / "status")
    if status:
        for line in status.splitlines():
            key, _, value = line.partition(":")
            value = value.strip().split(" ")[0]
            if not value.isdigit():
                continue
            if key == "voluntary_ctxt_switches":
                vcs = int(value)
            elif key == "nonvoluntary_ctxt_switches":
                nvcs = int(value)
            elif key == "VmRSS":
                rss_kb = int(value)
    return {
        "comm": comm,
        "ppid": ppid,
        "ticks": ticks,
        "threads": threads,
        "starttime": starttime,
        "vcs": vcs,
        "nvcs": nvcs,
        "rss_kb": rss_kb,
    }


def read_argv(proc_root, pid):
    try:
        with open(proc_root / str(pid) / "cmdline", "rb") as fh:
            raw = fh.read(MAX_CMDLINE_BYTES)
    except OSError:
        return None
    return [t.decode("utf-8", "replace") for t in raw.split(b"\0") if t]


def list_pids(proc_root):
    try:
        return [int(n) for n in os.listdir(proc_root) if n.isdigit()]
    except OSError:
        return []


# ---------------------------------------------------------------------------
# classification (argv is inspected here and never leaves this module)


def classify(argv, comm):
    """Map a process to a fixed/validated component label."""
    if argv is None:
        return "other"
    if not argv:
        return "kernel"
    exe = os.path.basename(argv[0])
    if exe == "ffmpeg":
        tokens = set(argv[1:])
        if "x11grab" in tokens and ({"-frames:v", "-vframes"} & tokens):
            return "ffmpeg:capture"
        if any(t.startswith(("rtmp://", "rtmps://", "srt://")) for t in argv[1:]):
            return "ffmpeg:stream"
        if "x11grab" in tokens:
            return "ffmpeg:x11grab"
        return "ffmpeg:other"
    if PYTHON_RE.match(exe):
        return _classify_python(argv[1:])
    if exe in SHELLS:
        for token in argv[1:]:
            if token.startswith("-"):
                if token == "-c":
                    return "shell"
                continue
            name = os.path.basename(token)
            return f"sh:{name}" if SCRIPT_RE.match(name) else "shell"
        return "shell"
    if exe == "docich":
        return _docich_subcommand(argv[1:])
    if SCRIPT_RE.match(exe):
        return f"{'py' if exe.endswith('.py') else 'sh'}:{exe}"
    if exe in EXECUTABLES:
        return EXECUTABLES[exe]
    if comm in EXECUTABLES:
        return EXECUTABLES[comm]
    return "other"


def _docich_subcommand(args):
    it = iter(args)
    for token in it:
        if token == "--config":
            next(it, None)
            continue
        if token.startswith("-"):
            continue
        return f"docich:{token}" if SUBCOMMAND_RE.match(token) else "docich"
    return "docich"


def _classify_python(args):
    it = iter(args)
    for token in it:
        if token == "-m":
            module = next(it, "")
            if module == "docich":
                return _docich_subcommand(list(it))
            if MODULE_RE.match(module):
                return f"py:{module}"
            return "python"
        if token == "-c":
            return "python"
        if token.startswith("-"):
            continue
        name = os.path.basename(token)
        if name == "docich":
            return _docich_subcommand(list(it))
        return f"py:{name}" if SCRIPT_RE.match(name) else "python"
    return "python"


def worker_pid_labels(soren_root, workers):
    """Map live worker PIDs (from registered pid files) to ``worker:<name>``."""
    labels = {}
    if soren_root is None:
        return labels
    for name, _required, _cat, relpath, kind in workers:
        rel = relpath or f"tmp/state/{name}.pid"
        text = _read_text(Path(soren_root) / rel, limit=256)
        if not text:
            continue
        tokens = text.split()
        token = tokens[0] if tokens else ""
        if token.isdigit():
            labels[int(token)] = f"worker:{name}"
    return labels


# ---------------------------------------------------------------------------
# sampling


class Tracker:
    def __init__(self, proc_root, worker_labels, self_pid):
        self.proc_root = proc_root
        self.worker_labels = worker_labels
        self.self_pid = self_pid
        self.procs = {}  # (pid, starttime) -> record
        self.sample_index = 0

    def scan(self):
        seen = {}
        for pid in list_pids(self.proc_root):
            snap = read_proc(self.proc_root, pid)
            if snap is None:
                continue
            key = (pid, snap["starttime"])
            seen[pid] = key
            rec = self.procs.get(key)
            if rec is None:
                rec = {
                    "pid": pid,
                    "first_index": self.sample_index,
                    "first": snap,
                    "ppid": snap["ppid"],
                    "own": self._own_label(pid, snap),
                    "rss_max": 0,
                    "threads_max": 0,
                }
                self.procs[key] = rec
            rec["last"] = snap
            rec["rss_max"] = max(rec["rss_max"], snap["rss_kb"])
            rec["threads_max"] = max(rec["threads_max"], snap["threads"])
        self.sample_index += 1
        return len(seen)

    def _own_label(self, pid, snap):
        if pid == self.self_pid:
            return "profiler"
        if pid in self.worker_labels:
            return self.worker_labels[pid]
        return classify(read_argv(self.proc_root, pid), snap["comm"])

    def resolve(self):
        """Assign final components; generic processes inherit a worker ancestor.

        A PID can be reused within the window, so ancestry resolves each ppid to
        the incarnation that was alive when the child started: the latest one
        whose starttime is not after the child's.
        """
        by_pid = {}
        for rec in self.procs.values():
            by_pid.setdefault(rec["pid"], []).append(rec)
        for incarnations in by_pid.values():
            incarnations.sort(key=lambda r: r["first"]["starttime"])

        def parent_of(child):
            candidates = by_pid.get(child["ppid"])
            if not candidates:
                return None
            born = child["first"]["starttime"]
            match = None
            for cand in candidates:
                if cand["first"]["starttime"] <= born:
                    match = cand
            return match

        for rec in self.procs.values():
            spawner = None
            direct = parent = parent_of(rec)
            depth = 0
            while parent is not None and depth < 32:
                if parent["own"].startswith("worker:"):
                    spawner = parent["own"]
                    break
                if spawner is None and parent["own"] not in GENERIC:
                    spawner = parent["own"]
                parent = parent_of(parent)
                depth += 1
            rec["spawner"] = spawner or (direct["own"] if direct else "unknown")
            own = rec["own"]
            rec["component"] = (
                spawner if own in GENERIC and spawner and spawner.startswith("worker:") else own
            )


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * pct // 100))
    return ordered[int(rank) - 1]


def _round(value, digits=2):
    return None if value is None else round(value, digits)


def sample(proc_root, duration, interval, soren_root, scenario, sleep=time.sleep,
           monotonic=time.monotonic):
    clk_tck = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    ncpu = os.cpu_count() or 1
    workers = _load_registry_workers()
    tracker = Tracker(proc_root, worker_pid_labels(soren_root, workers), os.getpid())
    self_start = os.times()

    host_start = read_host_cpu(proc_root)
    uptime_start = read_uptime(proc_root)
    t_start = monotonic()
    tracker.scan()
    interval_busy, loads, runnables, proc_counts = [], [], [], []
    prev = host_start
    deadline = t_start + duration
    while True:
        now = monotonic()
        if now >= deadline:
            break
        sleep(min(interval, deadline - now))
        proc_counts.append(tracker.scan())
        cur = read_host_cpu(proc_root)
        if prev and cur and cur[1] > prev[1]:
            interval_busy.append(100.0 * (cur[0] - prev[0]) / (cur[1] - prev[1]))
        prev = cur or prev
        load = read_loadavg(proc_root)
        if load:
            loads.append(load[0])
            runnables.append(load[1])
    elapsed = max(monotonic() - t_start, 1e-6)
    host_end = prev
    tracker.resolve()

    window_start_ticks = int((uptime_start or 0) * clk_tck)
    components = {}
    spawns = {}
    top = []
    for rec in tracker.procs.values():
        first, last = rec["first"], rec["last"]
        born_in_window = rec["first_index"] > 0 and first["starttime"] >= window_start_ticks
        if born_in_window:
            d_ticks, d_vcs, d_nvcs = last["ticks"], last["vcs"], last["nvcs"]
            key = (rec["own"], rec["spawner"])
            spawns[key] = spawns.get(key, 0) + 1
        else:
            d_ticks = last["ticks"] - first["ticks"]
            d_vcs = last["vcs"] - first["vcs"]
            d_nvcs = last["nvcs"] - first["nvcs"]
        comp = components.setdefault(rec["component"], {
            "ticks": 0, "vcs": 0, "nvcs": 0, "processes": 0, "spawned": 0,
            "rss_kb_max_sum": 0, "threads_max_sum": 0,
        })
        comp["ticks"] += max(d_ticks, 0)
        comp["vcs"] += max(d_vcs, 0)
        comp["nvcs"] += max(d_nvcs, 0)
        comp["processes"] += 1
        comp["spawned"] += int(born_in_window)
        comp["rss_kb_max_sum"] += rec["rss_max"]
        comp["threads_max_sum"] += rec["threads_max"]
        top.append((max(d_ticks, 0), rec))

    total_ticks = sum(c["ticks"] for c in components.values()) or 1
    comp_rows = []
    for name, c in components.items():
        comp_rows.append({
            "component": name,
            "cpu_pct": _round(100.0 * c["ticks"] / clk_tck / elapsed),
            "cpu_share_pct": _round(100.0 * c["ticks"] / total_ticks),
            "processes": c["processes"],
            "spawned": c["spawned"],
            "vcs_per_sec": _round(c["vcs"] / elapsed),
            "nvcs_per_sec": _round(c["nvcs"] / elapsed),
            "rss_kb": c["rss_kb_max_sum"],
            "threads": c["threads_max_sum"],
        })
    comp_rows.sort(key=lambda r: (-r["cpu_pct"], -r["spawned"], r["component"]))
    top.sort(key=lambda item: -item[0])
    top_rows = [{
        "pid": rec["pid"],
        "component": rec["component"],
        "cpu_pct": _round(100.0 * ticks / clk_tck / elapsed),
        "threads": rec["threads_max"],
        "rss_kb": rec["rss_max"],
    } for ticks, rec in top[:MAX_TOP_PROCESSES]]
    spawn_rows = sorted(
        ({"component": own, "spawner": spawner, "count": n}
         for (own, spawner), n in spawns.items()),
        key=lambda r: (-r["count"], r["component"], r["spawner"]),
    )

    host = {}
    if host_start and host_end and host_end[1] > host_start[1]:
        span = host_end[1] - host_start[1]
        host = {
            "cpu_busy_pct": _round(100.0 * (host_end[0] - host_start[0]) / span),
            "cpu_iowait_pct": _round(100.0 * (host_end[2] - host_start[2]) / span),
            "forks": host_end[3] - host_start[3],
            "forks_per_sec": _round((host_end[3] - host_start[3]) / elapsed),
            "ctxt_per_sec": _round((host_end[4] - host_start[4]) / elapsed),
        }
    host.update({
        "cpu_busy_pct_p50": _round(percentile(interval_busy, 50)),
        "cpu_busy_pct_p95": _round(percentile(interval_busy, 95)),
        "load1_p50": _round(percentile(loads, 50)),
        "load1_p95": _round(percentile(loads, 95)),
        "runnable_p50": percentile(runnables, 50),
        "runnable_p95": percentile(runnables, 95),
        "processes_p50": percentile(proc_counts, 50),
        "processes_max": max(proc_counts) if proc_counts else None,
    })
    self_end = os.times()
    return {
        "schema": SCHEMA,
        "meta": {
            "scenario": scenario,
            "generated_at": int(time.time()),
            "elapsed_sec": _round(elapsed),
            "interval_sec": interval,
            "samples": tracker.sample_index,
            "ncpu": ncpu,
            "clk_tck": clk_tck,
            "worker_pidfiles_resolved": len(tracker.worker_labels),
            "profiler_cpu_sec": _round(
                (self_end.user - self_start.user) + (self_end.system - self_start.system), 3),
            "cpu_pct_unit": "100 = one full core",
        },
        "host": host,
        "components": comp_rows[:MAX_COMPONENTS],
        "components_truncated": max(len(comp_rows) - MAX_COMPONENTS, 0),
        "spawns": spawn_rows[:MAX_SPAWN_ROWS],
        "top_processes": top_rows,
    }


# ---------------------------------------------------------------------------
# compare


def compare(before, after):
    """Per-component before/after deltas for two sample reports."""
    def index(report):
        return {r["component"]: r for r in report.get("components", [])}

    b, a = index(before), index(after)
    rows = []
    for name in sorted(set(b) | set(a)):
        rb, ra = b.get(name, {}), a.get(name, {})
        row = {"component": name}
        for field in ("cpu_pct", "spawned", "vcs_per_sec", "nvcs_per_sec"):
            vb, va = rb.get(field) or 0, ra.get(field) or 0
            row[f"{field}_before"] = vb
            row[f"{field}_after"] = va
            row[f"{field}_delta"] = _round(va - vb)
        rows.append(row)
    rows.sort(key=lambda r: (-abs(r["cpu_pct_delta"]), r["component"]))
    hb, ha = before.get("host", {}), after.get("host", {})
    host = {}
    for field in ("cpu_busy_pct", "cpu_busy_pct_p95", "forks_per_sec", "ctxt_per_sec",
                  "load1_p95", "runnable_p95"):
        vb, va = hb.get(field), ha.get(field)
        host[field] = {
            "before": vb, "after": va,
            "delta": _round(va - vb) if isinstance(vb, (int, float)) and isinstance(va, (int, float)) else None,
        }
    return {
        "schema": SCHEMA + ".compare",
        "scenario_before": before.get("meta", {}).get("scenario"),
        "scenario_after": after.get("meta", {}).get("scenario"),
        "host": host,
        "components": rows[:MAX_COMPONENTS],
    }


# ---------------------------------------------------------------------------
# text rendering


def render_text(report):
    meta, host = report["meta"], report["host"]
    lines = [
        f"scenario={meta['scenario']} elapsed={meta['elapsed_sec']}s samples={meta['samples']} "
        f"ncpu={meta['ncpu']} profiler_cpu={meta['profiler_cpu_sec']}s",
        "host: " + " ".join(f"{k}={v}" for k, v in host.items()),
        "",
        f"{'component':<36} {'cpu%':>7} {'share%':>7} {'procs':>5} {'spawn':>5} "
        f"{'vcs/s':>8} {'nvcs/s':>8} {'rssMB':>7} {'thr':>4}",
    ]
    for r in report["components"]:
        lines.append(
            f"{r['component'][:36]:<36} {r['cpu_pct']:>7} {r['cpu_share_pct']:>7} "
            f"{r['processes']:>5} {r['spawned']:>5} {r['vcs_per_sec']:>8} "
            f"{r['nvcs_per_sec']:>8} {r['rss_kb'] // 1024:>7} {r['threads']:>4}"
        )
    if report["spawns"]:
        lines += ["", "spawned in window (component <- spawner):"]
        lines += [f"  {s['count']:>5}  {s['component']} <- {s['spawner']}" for s in report["spawns"]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _default_soren_root():
    candidate = Path("/home/ubuntu/soren")
    return candidate if candidate.is_dir() else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_sample = sub.add_parser("sample", help="sample /proc for a bounded window")
    p_sample.add_argument("--scenario", default="adhoc")
    p_sample.add_argument("--duration", type=float, default=60.0)
    p_sample.add_argument("--interval", type=float, default=1.0)
    p_sample.add_argument("--warmup", type=float, default=0.0)
    p_sample.add_argument("--soren-root", default=None,
                          help="soviet_now root for worker pid files (default: /home/ubuntu/soren if present)")
    p_sample.add_argument("--proc-root", default="/proc", help=argparse.SUPPRESS)
    p_sample.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p_sample.add_argument("--output", default=None, help="also write the JSON report to this path")
    p_cmp = sub.add_parser("compare", help="compare two JSON reports (before, after)")
    p_cmp.add_argument("before")
    p_cmp.add_argument("after")
    args = parser.parse_args(argv)

    if args.cmd == "compare":
        before = json.loads(Path(args.before).read_text(encoding="utf-8"))
        after = json.loads(Path(args.after).read_text(encoding="utf-8"))
        print(json.dumps(compare(before, after), ensure_ascii=False, indent=2))
        return 0

    if not SCENARIO_RE.match(args.scenario):
        parser.error("--scenario must match [a-z0-9][a-z0-9_-]{0,39}")
    duration = _clamp(args.duration, MIN_DURATION, MAX_DURATION)
    interval = _clamp(args.interval, MIN_INTERVAL, MAX_INTERVAL)
    warmup = _clamp(args.warmup, 0.0, MAX_WARMUP)
    soren_root = Path(args.soren_root) if args.soren_root else _default_soren_root()
    if warmup:
        time.sleep(warmup)
    report = sample(Path(args.proc_root), duration, interval, soren_root, args.scenario)
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
