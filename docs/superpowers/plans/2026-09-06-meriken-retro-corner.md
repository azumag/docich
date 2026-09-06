# Meriken Retro Corner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a once-daily, one-hour Meriken AI CLI-game slot that overlays Robots on the existing Soren live display and safely reveals Soren again afterward.

**Architecture:** `src/docich/retro_corner.py` owns program-slot state, timing, recovery and `GameSwitchCoordinator` calls. Production uses the existing `config/docich.soren-live.toml` handoff profile (`:99`, external display, no docich FFmpeg/audio ownership), so Soren keeps running underneath while Robots is displayed in the existing viewport. An hourly user timer calls `tick`; Python gates on `Asia/Tokyo` 20:00 and does not prepare tmux/runtime on healthy off-hours.

**Tech Stack:** Python stdlib, existing docich coordinator/CLI runtime setup, systemd user units, unittest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-06-meriken-retro-corner-design.md`

## Global Constraints

- `docich` remains the VM/deployment/scheduling control plane; no new VM responsibility in `soviet_now`.
- Never start/stop/restart `soren-runtime.service` from the corner.
- All game transitions use `GameSwitchCoordinator`.
- Production game list is initially `["robots"]` only.
- Production timezone/start/duration are `Asia/Tokyo`, `20`, `60`.
- Soren live profile owns no Xvfb/audio/FFmpeg; it connects to externally owned `:99` only.
- Manual operator switching wins over automatic restore.
- State/lock files are private; failures are fail-closed.
- No per-move LLM calls in v1.

---

### Task 1: Lock down behavior with focused tests

**Files:**
- Create: `tests/test_retro_corner.py`
- Create: `.github/workflows/ci.yml`

- [x] Add RED tests for config validation, deterministic selection, start/restore, idle->Robots->idle, manual switch precedence, early stop, same-day once-only, stale recovery, private state, CLI routing, live-profile selection, and systemd contract.
- [x] Add a general CI workflow that checks the focused contract first, then the full `tests/` suite and `compileall`.
- [x] Verify the first focused run fails because `docich.retro_corner` does not yet exist.

### Task 2: Implement the program-slot orchestrator

**Files:**
- Create: `src/docich/retro_corner.py`
- Modify: `src/docich/__main__.py`

- [x] Implement `RetroCornerConfig`, validation and deterministic `select_game`.
- [x] Implement atomic `retro_corner.json` plus `locks/retro-corner.lock`.
- [x] Keep the lock only around begin/finish transitions, not the one-hour wait.
- [x] Implement scheduled tick, manual start/stop/status and stale-active recovery.
- [x] Use only `GameSwitchCoordinator.start/switch/stop` for lifecycle changes.
- [x] Route `docich [--config PATH] retro-corner ...` through `src/docich/__main__.py` without expanding the legacy CLI parser.
- [x] Prepare the existing docich runtime only for an actual start, manual stop, or stale recovery; off-hour tick is side-effect free.

### Task 3: Bind production to the existing live-handoff profile

**Files:**
- Modify: `config/docich.toml`
- Modify: `config/docich.soren-live.toml`
- Modify: `docs/games/robots.md`

- [x] Keep the default profile `retro_corner.enabled=false`.
- [x] Enable the corner only in `docich.soren-live.toml` with Robots / 20:00 / 60min / Asia/Tokyo.
- [x] Preserve `managed=false`, `stream.mode="null"`, `audio.enabled=false` and the 960x540 Soren viewport.
- [x] Document that production normally enters as docich canonical idle, overlays Robots, then stops back to idle to reveal the still-running Soren background.

### Task 4: Add systemd scheduling

**Files:**
- Create: `scripts/systemd/docich-retro-corner.service`
- Create: `scripts/systemd/docich-retro-corner.timer`
- Modify: `scripts/systemd/README.md`

- [x] Add hourly `Persistent=false` timer.
- [x] Service explicitly uses `config/docich.soren-live.toml`.
- [x] Do not use hourly `ExecStartPre=docich up`; runtime preparation is conditional inside the program-slot manager.
- [x] Set `TimeoutStartSec=15h` so configured long slots and guarded round-boundary completion are not killed by a short systemd default.
- [x] Document install/status/stop/recovery and Soren ownership boundaries.

### Task 5: Verify, merge and activate on the VM

- [ ] Wait for latest focused tests, full unit suite, `compileall`, and VM security regression workflow to pass.
- [ ] Review PR #77 diff and review threads for blockers.
- [ ] Squash-merge through the existing owner-only PR bypass; never direct-push main.
- [ ] Verify automatic owner-only production deployment reaches the merged main SHA and remains configured/clean.
- [ ] Install the two unit templates to `~/.config/systemd/user/` with `__DOCICH_ROOT__=/home/ubuntu/docich`, run `systemctl --user daemon-reload`, and enable `docich-retro-corner.timer`.
- [ ] Verify `retro-corner status --json`, timer enabled/next trigger, and that no live switch is forced during setup.
- [ ] Optional controlled smoke: manually start/early-stop the corner only if it can be observed safely; otherwise leave the first real run to the scheduled 20:00 JST slot.
