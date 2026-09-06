# Meriken Retro Corner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a once-daily, one-hour CLI-game program slot branded as the Meriken AI retro game corner, with safe transactional switching and restoration.

**Architecture:** `docich` owns a small `retro_corner` orchestrator that reads validated config, serializes runs with a private lock/state file, delegates all game transitions to `GameSwitchCoordinator`, and is triggered hourly by a user systemd timer while Python decides whether the configured local start hour matches. The first production game list is `robots` only because it is the only CLI game with a stable long-running resolver; the configuration and selector are multi-game ready.

**Tech Stack:** Python 3.11 stdlib (`dataclasses`, `zoneinfo`, `fcntl`, `datetime`), existing docich config/game-switch APIs, systemd user units, unittest.

**Spec:** `docs/superpowers/specs/2026-09-06-meriken-retro-corner-design.md`

## Global Constraints

- VM/deployment/scheduling ownership stays in `docich`; `soviet_now` gains no VM control responsibility.
- All game transitions go through `GameSwitchCoordinator`; no direct tmux/process kill path.
- Initial production `retro_corner.games` is exactly `["robots"]`.
- Default timezone is `Asia/Tokyo`, start hour `20`, duration `60` minutes.
- Manual operator game switches during a corner must never be overwritten on restore.
- State files are private and atomic; failures are fail-closed.
- No per-move LLM calls in this initial implementation.

---

### Task 1: Configuration contract

**Files:**
- Modify: `src/docich/config.py`
- Modify: `config/docich.toml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces `RetroCornerConfig(enabled: bool, start_hour: int, duration_minutes: int, timezone: str, games: list[str])`.
- Adds `GlobalConfig.retro_corner: RetroCornerConfig`.

- [ ] **Step 1: Write failing config tests**

Add tests proving defaults/config parsing and rejection of invalid hour, duration, timezone, empty/non-string games, and invalid game names. Example assertions:

```python
g = load_global(root, config)
self.assertTrue(g.retro_corner.enabled)
self.assertEqual(g.retro_corner.start_hour, 20)
self.assertEqual(g.retro_corner.duration_minutes, 60)
self.assertEqual(g.retro_corner.timezone, "Asia/Tokyo")
self.assertEqual(g.retro_corner.games, ["robots"])
```

Invalid timezone must raise `ConfigError` by constructing `ZoneInfo(value)` during load.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_config -v
```

Expected: new retro-corner tests fail because the config field does not exist.

- [ ] **Step 3: Implement config dataclass and validation**

Add:

```python
@dataclass
class RetroCornerConfig:
    enabled: bool = False
    start_hour: int = 20
    duration_minutes: int = 60
    timezone: str = "Asia/Tokyo"
    games: list[str] = field(default_factory=lambda: ["robots"])
```

Validate:

```python
if type(retro_corner.enabled) is not bool: ...
if type(retro_corner.start_hour) is not int or not 0 <= retro_corner.start_hour <= 23: ...
if type(retro_corner.duration_minutes) is not int or not 1 <= retro_corner.duration_minutes <= 720: ...
try: ZoneInfo(retro_corner.timezone)
except (ZoneInfoNotFoundError, TypeError): raise ConfigError(...)
if not isinstance(retro_corner.games, list) or not retro_corner.games or not all(isinstance(x, str) for x in retro_corner.games): ...
retro_corner.games = [validate_game_name(name) for name in retro_corner.games]
```

Wire it into `GlobalConfig` and `load_global`.

Set production config:

```toml
[retro_corner]
enabled = true
start_hour = 20
duration_minutes = 60
timezone = "Asia/Tokyo"
games = ["robots"]
```

- [ ] **Step 4: Run tests and verify GREEN**

```bash
python3 -m unittest tests.test_config -v
```

- [ ] **Step 5: Commit**

```bash
git add src/docich/config.py config/docich.toml tests/test_config.py
git commit -m "feat: add retro corner configuration"
```

---

### Task 2: Retro-corner orchestration and CLI

**Files:**
- Create: `src/docich/retro_corner.py`
- Modify: `src/docich/cli.py`
- Test: `tests/test_retro_corner.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- `select_game(games: list[str], local_date: date) -> str`
- `RetroCornerManager(g: GlobalConfig, *, now: Callable[[], datetime] | None = None, sleep: Callable[[float], None] = time.sleep)`
- `RetroCornerManager.tick() -> CornerResult`
- `RetroCornerManager.start() -> CornerResult`
- `RetroCornerManager.stop() -> CornerResult`
- `RetroCornerManager.status() -> dict[str, object]`
- CLI: `docich retro-corner {tick,start,stop,status} [--json]`

- [ ] **Step 1: Write failing orchestration tests**

Use a temporary `state_dir`, fake clock/sleep, and a fake transition adapter/facade so tests do not invoke tmux. Cover:

```python
self.assertEqual(select_game(["robots", "ninvaders"], date(2026, 9, 6)), expected)
```

and these flows:

1. active `sorengame` -> start -> switch `robots` -> elapsed -> switch back `sorengame` -> state `completed`.
2. idle -> start `robots` -> elapsed -> stop -> idle.
3. operator changes active game to `nethack` before restore -> no restore call, state `interrupted`.
4. `tick` outside start hour -> no-op.
5. same local date already `completed` -> no second run.
6. expired stale `active` state -> reconcile restore before a new attempt.
7. invalid eligible game (`adapter != cli` or agent disabled) -> fail before switching.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python3 -m unittest tests.test_retro_corner tests.test_cli -v
```

Expected: import/parser failures for the not-yet-created command/module.

- [ ] **Step 3: Implement private state/lock helpers**

In `retro_corner.py`, use:

```python
STATE_SCHEMA_VERSION = 1
STATE_FILE = "retro_corner.json"
LOCK_FILE = "locks/retro-corner.lock"
```

Reuse `game_switch.atomic_write_json` for durable 0600 state writes. `fcntl.flock(..., LOCK_EX | LOCK_NB)` rejects concurrent manual/systemd invocations.

Read canonical active game from `GameSwitchStore(g.state_dir).load()` and extract `state["active"]["game"]` only when valid.

- [ ] **Step 4: Implement coordinator-only transitions**

Create the coordinator with existing adapter factory patterns used by CLI and call only:

```python
coordinator.start(target)
coordinator.switch(target)
coordinator.stop()
```

Return or raise on non-success `SwitchResult`; never manipulate tmux/processes directly.

Before start, validate every configured candidate with `load_game(g, name)`, requiring `adapter == "cli"` and `agent.enabled is True`.

- [ ] **Step 5: Implement start/stop/tick semantics**

- `select_game`: `games[local_date.toordinal() % len(games)]`.
- `start`: reconcile expired active state, reject an unexpired active state, remember previous game, persist active intent, transition to target, wait until `ends_at`, then restore.
- `stop`: if current active still equals corner target, restore previous or stop to idle; if not equal, mark `interrupted` without transition.
- `tick`: use `ZoneInfo(config.timezone)` local time; only run when enabled and `now.hour == start_hour`; completed/interrupted for same date is no-op.
- Errors persist sanitized `last_error` and return/raise a user-facing `RetroCornerError`.

- [ ] **Step 6: Add CLI parser/dispatch**

Parser:

```python
p_corner = sub.add_parser("retro-corner", help="メリケンAI レトロゲームコーナーを操作する")
corner_sub = p_corner.add_subparsers(dest="retro_corner_command", required=True)
corner_sub.add_parser("tick")
corner_sub.add_parser("start")
corner_sub.add_parser("stop")
p_corner_status = corner_sub.add_parser("status")
p_corner_status.add_argument("--json", action="store_true")
```

Dispatch calls `retro_corner.cli_retro_corner(g, args)` and includes `RetroCornerError` in user errors.

- [ ] **Step 7: Run focused tests and verify GREEN**

```bash
python3 -m unittest tests.test_retro_corner tests.test_cli -v
```

- [ ] **Step 8: Commit**

```bash
git add src/docich/retro_corner.py src/docich/cli.py tests/test_retro_corner.py tests/test_cli.py
git commit -m "feat: add daily retro corner orchestration"
```

---

### Task 3: systemd scheduling and operations documentation

**Files:**
- Create: `scripts/systemd/docich-retro-corner.service`
- Create: `scripts/systemd/docich-retro-corner.timer`
- Modify: `scripts/systemd/README.md`
- Modify: `README.md`
- Test: `tests/test_retro_corner.py`

**Interfaces:**
- User timer invokes `__DOCICH_ROOT__/bin/docich retro-corner tick` once per hour.

- [ ] **Step 1: Write failing unit-file policy tests**

Tests read the two unit templates and assert:

```python
self.assertIn("ExecStart=__DOCICH_ROOT__/bin/docich retro-corner tick", service)
self.assertIn("OnCalendar=hourly", timer)
self.assertIn("Persistent=false", timer)
```

Also assert README contains `systemctl --user enable --now docich-retro-corner.timer` and explains Python-side timezone gating.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python3 -m unittest tests.test_retro_corner -v
```

- [ ] **Step 3: Add unit templates and docs**

Service:

```ini
[Unit]
Description=docich メリケンAI レトロゲームコーナー日次判定

[Service]
Type=oneshot
WorkingDirectory=__DOCICH_ROOT__
ExecStart=__DOCICH_ROOT__/bin/docich retro-corner tick
```

Timer:

```ini
[Unit]
Description=docich メリケンAI レトロゲームコーナーを毎時判定

[Timer]
OnCalendar=hourly
Persistent=false
Unit=docich-retro-corner.service

[Install]
WantedBy=timers.target
```

Document that the hourly timer is cheap/no-op outside configured local hour and avoids dependency on VM timezone.

- [ ] **Step 4: Run focused tests and full unit suite**

```bash
python3 -m unittest tests.test_retro_corner -v
python3 -m unittest discover -s tests -v
python3 -m py_compile src/docich/config.py src/docich/retro_corner.py src/docich/cli.py
```

- [ ] **Step 5: Commit**

```bash
git add scripts/systemd/docich-retro-corner.service scripts/systemd/docich-retro-corner.timer scripts/systemd/README.md README.md tests/test_retro_corner.py
git commit -m "ops: schedule daily Meriken retro corner"
```

---

### Task 4: PR, protected-main merge, deployment, and VM activation

**Files:** no source changes unless review/CI finds a defect.

**Interfaces:** Uses existing owner-only `vm-operations` auto-deploy on main push.

- [ ] **Step 1: Open PR and verify CI**

Open PR to `main`; require all docich tests plus VM-operations security regression checks to pass. Review changed files for accidental `soviet_now` VM-control additions or secret-bearing text.

- [ ] **Step 2: Merge through the existing owner PR bypass**

Use squash merge only after CI is green and no blocking review comments remain. Do not direct-push main.

- [ ] **Step 3: Verify automatic production deploy**

Confirm the main push triggers `VM operations`, upload/deploy succeeds, and production status is `configured` at the new main SHA.

- [ ] **Step 4: Install/enable the user timer on VM**

Copy templates with `__DOCICH_ROOT__=/home/ubuntu/docich` into `~/.config/systemd/user/`, then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now docich-retro-corner.timer
systemctl --user status docich-retro-corner.timer --no-pager
```

No sudo is required.

- [ ] **Step 5: Safe live smoke test**

Do not wait an hour. First run:

```bash
/home/ubuntu/docich/bin/docich retro-corner status --json
```

Then test scheduling logic with unit tests/CLI status rather than forcing a production game switch unless explicitly desired. Verify the timer is enabled and next trigger is visible.
