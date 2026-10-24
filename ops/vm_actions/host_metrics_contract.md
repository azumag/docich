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

- Ring candidates, in the reviewed order: `home/.local/state/docich/host-metrics/`,
  `home/.local/share/docich/host-metrics/`, then `home/docich-host-metrics/`,
  where `home` is the runtime user's home directory (`/home/ubuntu` in
  production). The writer uses the candidate that already holds the ring, else
  the first one it can create; the reader checks them in the same order and
  reports which one it read as `ring_path_kind` (`state`/`share`/`home`).
  Why: on the production VM, `~/.local/state` and its `docich` child are
  root-owned (created by a root-time `install -d` in the gateway/canary
  installers), so the unprivileged sampler cannot create anything below the
  first candidate and falls back to the ubuntu-owned `~/.local/share` tree.
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
40 new points). Writes are `O_APPEND` single writes under `flock` on the state
directory; the ring is opened `O_RDWR | O_APPEND` so a tail left without its
newline by a power cut is repaired with one `\n` before the new record, and
compaction is temp-file + `os.replace` (fsynced). A torn write can therefore
never corrupt the ring, nor glue the next record to a partial line.

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
- Numeric sources have ceilings (`MAX_COUNTER` = 1e18, uptime 1e10 s,
  load/PSI 1e6): a token no healthy kernel produces reads as `null` instead of
  a poisoned integer, because a giant value would build a record that cannot
  fit the bounded line -- the failure mode of the first production deploy. On
  such a refusal the sampler exits 1 and writes
  `cannot write host metrics ring (record_bytes=N)` to the journal; the
  collector projects the parsed `N` as `journal.refused_record_bytes`.
- `v`: schema version, currently `1`. Unknown versions are ignored by the
  reader (never guessed at).
- `t`: epoch seconds at sample time (integer).
- `boot`: first 12 hex chars of the kernel boot id, read from
  `/proc/sys/kernel/random/boot_id` first and `/sys/kernel/random/boot_id`
  as the fallback; only a fixed hash prefix, never a path. The proc path is
  first because on the production VM /sys reads fail in the sampler's
  user-unit context while every /proc source works (proven by the collector's
  own boot_id read in the same diagnostics run).
- `uptime_s`: `/proc/uptime` seconds (integer).
- `load`: `[load1, load5, load15]` rounded to 2 decimals.
- `cpu`: cumulative `/proc/stat` `cpu` jiffies `[total, idle, iowait]`.
  `total` is the sum of every field; deltas between two consecutive points in
  the same boot give per-interval percentages.
- `mem_kb`: fixed six keys from `/proc/meminfo` (`MemTotal`, `MemAvailable`,
  `SwapTotal`, `SwapFree`, `Dirty`, `Writeback`). Missing keys are `null`,
  never invented.
- `psi`: `/proc/pressure/{cpu,io,memory}` `[some avg10, full avg10]`, rounded
  to 2 decimals; `[null, null]` when the kernel has no PSI. Both halves are
  projected (`psi_*_some10` / `psi_*_full10`); `full` is the "everything is
  stalled" signal for a guest-OS hang.
- `disk_sectors`: `[sectors read, sectors written]` for the root block device
  from `/proc/diskstats` (cumulative, 512-byte sectors). The root device's
  name is resolved from `/proc/partitions` first (same /proc-first rationale)
  and the `/sys/dev/block` readlink second.
- `disk_ms`: `[io ms, weighted io ms]` for the same device (cumulative).
- `units`: 2 bits per entry of `UNIT_ORDER`, little-endian per unit:
  `0b00` inactive, `0b01` active, `0b10` failed, `0b11` unknown
  (activating/reloading/deactivating, a scope without the unit, probe failure). Unit `i` occupies
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
deltas). Output cap: `(SAMPLES_MAX + 2)` x ~600 B plus ~700 B metadata, kept
under 8 KiB by a regression test.

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

- `runtime`: fixed-shape read-only health of the sampler units themselves
  (`systemctl --user is-active`/`is-enabled`/`show` enums plus a bounded
  `journalctl --user-unit` tail classified to one fixed signature). This is the
  sanctioned read-back for a deploy-hook failure whose VM-side output is
  withheld: a stopped, failing, timed-out or OOM-killed sampler is visible
  here even while the ring reads `absent`. Journal text and paths never leave
  the collector.
- `status`: `absent` (no file), `unreadable` (permission/IO/not a regular
  file/symlink), `invalid` (no parseable v1 record, or a file over `MAX_BYTES`
  because compaction did not run), `stale` (newest record older than
  `STALE_SEC = 3 * INTERVAL_SEC`), else `ok`. A transient sampler failure
  therefore shows as `stale`, never as an invented healthy value.
- `ring_path_kind`: which fixed candidate was read (`state`/`share`/`home`,
  null when no ring exists anywhere); the path itself is never published.
- `ring_bytes`: bytes actually read from the ring (0 when absent/refused,
  `<= max_bytes` otherwise), so the fixed on-disk size is observable.
- `points` is the projected count (`<= SAMPLES_MAX + 2` because of the boot
  boundary), `points_available` the retained count (`<= MAX_POINTS`),
  `samples_omitted` the count dropped by the hard-ceiling trim.
- `spans_reboot`: true when the newest `points_available` records contain more
  than one `boot` value — this is the acceptance check for "指標が再起動をまたいで
  読める".
- `down_units`: fixed unit names from `UNIT_ORDER` for the newest sample whose
  2-bit state is inactive (`00`) or failed (`10`). `unknown` (`11`:
  activating/reloading/deactivating, a scope without the unit, or a failed
  probe) is not "down"; names only, never paths.
- `cpu_busy_pct`: `100 * (d_total - d_idle - d_iowait) / d_total`, clamped
  `0..100`, `null` for the first record of a boot.
- Delta fields are `null` for the first record of a boot, otherwise cumulative
  deltas (may be 0).
- Trimming happens only against `HARD_JSON_BYTES`, and only the oldest samples
  are removed (the newest point and the previous-boot boundary always stay),
  before any whole section is dropped. The floor is the newest point plus the
  retained previous-boot boundary points (up to `HOST_METRICS_BOUNDARY_BOOTS`
  of them, so three boots keep three points). When even the floor cannot fit
  under the ceiling, the trim is skipped entirely: a section is dropped either
  way, and the boundary must not be burned for nothing. The softer `MAX_JSON_BYTES` target never
  trims the ring: the production envelope already exceeds it, and trimming there
  would trade the entire `corners` block for a few hundred bytes (#2533). Whole
  sections are dropped only after the trim cannot make the envelope fit, and
  `host_metrics` is last in `_HARD_DROP_ORDER`.
