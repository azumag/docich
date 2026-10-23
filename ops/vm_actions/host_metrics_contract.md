# host metrics ring — implementation contract (docich#2578)

This file is the single source of truth shared by the sampler
(`ops/vm_actions/host_metrics.py`) and the diagnostics projection
(`ops/vm_actions/collect_diagnostics.py`). The user-facing contract lives in
`docs/operations/host-metrics-ring.md`; keep both consistent.

## Purpose

After a guest-OS hang (docich#2278) the VM is rebooted and diagnostics cannot
reconstruct the host state from before the hang. The sampler writes a small,
fixed-size, fixed-cadence ring of host counters onto the persistent root disk so
the owner-only `diagnostics` operation can still show the last hour before the
hang *and* after the reboot.

Scope exclusions from the issue: no automatic reboot, no new cloud
permissions, no root cause claim for 2026-10-15.

## Paths

- State root: `home/.local/state/docich/host-metrics/` where `home` is the
  runtime user's home directory (`/home/ubuntu` in production).
- Ring file: `<state root>/ring.jsonl`, mode `0600`, directory mode `0700`.
- Never under the program `state_dir`, never on tmpfs, never under
  `soren/tmp` (both are cleaned and are not reboot-persistent evidence).

## Cadence and bounds (fixed constants, regression-tested)

```
INTERVAL_SEC     = 60     # systemd timer OnCalendar=*:*:00, AccuracySec=1s
MAX_POINTS       = 240    # 4 hours of history
MAX_BYTES        = 256 * 1024
COMPACT_KEEP     = 200    # lines kept by one compaction
MAX_LINE_BYTES   = 4096   # one over-long line is dropped, never truncated
```

Worst case steady state: 240 points x ~400 B ≈ 96 KiB, hard-capped at 256 KiB,
one append per minute. Compaction rewrites at most 200 lines and only when the
ring exceeds `MAX_POINTS` or `MAX_BYTES` (~every 40 minutes's worth of growth
per compaction at a 240-point ring: compaction keeps 200, so it triggers every
40 new points). Writes are `O_APPEND` single writes; compaction is
temp-file + `os.replace`, so a torn write can never corrupt the ring.

## Record format (one compact JSON object per line, sorted keys)

```json
{"boot":"a1b2c3d4e5f6","cpu":[123456,98765,4321],
 "disk_ms":[123456,78901],"disk_sectors":[1111,2222],
 "load":[0.42,0.5,0.6],
 "mem_kb":{"available":111,"dirty":1,"swap_free":222,"swap_total":333,
           "total":444,"writeback":0},
 "psi":{"cpu":[1,2],"io":[3,4],"mem":[5,6]},
 "t":1761000000,"units":63,"uptime_s":123456,"v":1}
```

- A point is **valid** iff `v == 1` and `t` is an integer in
  `0 .. now+86400`. Every other field is nullable: a missing/unreadable source
  is recorded as `null` and the point is still evidence (degraded VM). The
  reader projects null and never guesses 0/healthy.
- `v`: schema version, currently `1`. Unknown versions are ignored by the
  reader (never guessed at).
- `t`: epoch seconds at sample time (integer).
- `boot`: first 12 hex chars of `/sys/kernel/random/boot_id`; only a fixed
  hash prefix, never a path.
- `uptime_s`: `/proc/uptime` seconds (integer).
- `load`: `[load1, load5, load15]` rounded to 2 decimals.
- `cpu`: cumulative `/proc/stat` `cpu` jiffies `[total, idle, iowait]`.
  `total` is the sum of every field; deltas between two consecutive points in
  the same boot give per-interval percentages.
- `mem_kb`: fixed six keys from `/proc/meminfo` (`MemTotal`, `MemAvailable`,
  `SwapTotal`, `SwapFree`, `Dirty`, `Writeback`). Missing keys are `null`,
  never invented.
- `psi`: `/proc/pressure/{cpu,io,memory}` `[some avg10, full avg10]`, rounded
  to 2 decimals; `[null, null]` when the kernel has no PSI.
- `disk_sectors`: `[sectors read, sectors written]` for the root block device
  from `/proc/diskstats` (cumulative, 512-byte sectors).
- `disk_ms`: `[io ms, weighted io ms]` for the same device (cumulative).
- `units`: 2 bits per entry of `UNIT_ORDER`, little-endian per unit:
  `0b00` inactive/unknown, `0b01` active, `0b10` failed. Unit `i` occupies
  bits `2*i` and `2*i+1`. `UNIT_ORDER` is fixed in code and published once by
  diagnostics; no unit name is ever read from configuration.

`UNIT_ORDER` (fixed tuple, both scopes probed with fixed argv only):

```
0 docich-corner-rotation.timer  (user)
1 docich-corner-rotation.service(user)
2 docich-webui.service          (user)
3 docich-paper-corner.timer     (user)
4 soren-runtime.service         (system)
5 docich-audio-priority.service (system)
```

No file content, no environment, no argv, no paths and no free text are ever
recorded. The sampler reads only `/proc`, `/sys/kernel/random/boot_id` and
`systemctl is-active` output for those six fixed units.

## Diagnostics projection (`host_metrics` key)

Read-only, bounded, fixed shape. Reads the newest at most `MAX_POINTS` parseable
records, keeps the newest `SAMPLES_MAX = 8` for output **plus the last point of
up to `HOST_METRICS_BOUNDARY_BOOTS = 2` older boots** (a post-reboot run must
never hide the last point before the hang), and computes per-interval deltas only
between consecutive records of the same boot (first record of a boot has `null`
deltas). Output cap: `(SAMPLES_MAX + 2)` x ~250 B plus ~700 B metadata, kept
under 6 KiB by a regression test.

```json
"host_metrics": {
  "status": "ok|absent|unreadable|invalid|stale",
  "retention": {"interval_sec": 60, "max_points": 240, "max_bytes": 262144},
  "unit_order": ["docich-corner-rotation.timer", "..."],
  "ring_bytes": 54800,
  "points_available": 137,
  "points": 8,
  "samples_omitted": 0,
  "oldest_age_sec": 420,
  "newest_age_sec": 18,
  "boots": 2,
  "current_boot": "a1b2c3d4e5f6",
  "spans_reboot": true,
  "samples": [
    {"age_sec": 18, "boot": "a1b2c3d4e5f6", "t": 1761000000, "uptime_s": 123456,
     "load1": 0.42, "load5": 0.5, "load15": 0.6,
     "cpu_busy_pct": 12.5, "mem_available_kb": 111, "mem_total_kb": 444,
     "swap_free_kb": 222, "swap_total_kb": 333,
     "dirty_kb": 1, "writeback_kb": 0,
     "psi_cpu_some10": 1.0, "psi_io_some10": 3.0, "psi_mem_some10": 5.0,
     "disk_read_sectors": 2048, "disk_write_sectors": 4096, "disk_io_ms": 30,
     "units": 63,
     "down_units": ["docich-paper-corner.timer"]}
  ]
}
```

- `status`: `absent` (no file), `unreadable` (permission/IO/not a regular
  file/symlink), `invalid` (no parseable v1 record, or a file over `MAX_BYTES`
  because compaction did not run), `stale` (newest record older than
  `STALE_SEC = 3 * INTERVAL_SEC`), else `ok`. A transient sampler failure
  therefore shows as `stale`, never as an invented healthy value.
- `ring_bytes`: bytes actually read from the ring (0 when absent/refused,
  `<= max_bytes` otherwise), so the fixed on-disk size is observable.
- `points` is the projected count (`<= SAMPLES_MAX`), `points_available` the
  retained count (`<= MAX_POINTS`), `samples_omitted` the count dropped by the
  soft budget.
- `spans_reboot`: true when the newest `points_available` records contain more
  than one `boot` value — this is the acceptance check for "指標が再起動をまたいで
  読める".
- `down_units`: fixed unit names from `UNIT_ORDER` for the newest sample
  (state != active); names only, never paths.
- `cpu_busy_pct`: `100 * (d_total - d_idle - d_iowait) / d_total`, clamped
  `0..100`, `null` for the first record of a boot.
- Delta fields are `null` for the first record of a boot, otherwise cumulative
  deltas (may be 0).
- The projection is dropped by the collector's hard-ceiling fallback only as the
  last resort (see `_HARD_DROP_ORDER`); under the softer `MAX_JSON_BYTES`
  budget the samples list is reduced to the newest 4 before any whole-section
  drop.
