# Crypto Trading Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bridge new paper-trading events into the existing Soren overlay/audio queues with deterministic compact/detailed PAPER narration, durable replay suppression, and no change to trading execution authority.

**Architecture:** Extract the Soren overlay-event queue primitives from Web UI into a shared module while keeping compatibility wrappers. Add presentation/rendering and notification-delivery modules under `docich.trading`; the continuous paper worker calls the delivery function after each cycle, while a `notify-once` CLI uses the same path. Delivery state tracks overlay/speech acknowledgements independently and defaults to bootstrap-without-replay.

**Tech Stack:** Python 3.11+, JSON/JSONL, atomic file replacement, existing Soren overlay generator/comment audio queue, existing docich trading worker/CLI, pytest.

**Spec:** `docs/superpowers/specs/2026-09-08-crypto-notifications-design.md`

## Global Constraints

- Notification configuration defaults off.
- `notifications_enabled` and `notification_speech_enabled` are strict booleans.
- No bitbank API key/private/order endpoint, leverage, withdrawal, live execution, or exit strategy.
- Every viewer-facing trading notice prominently says `PAPER`.
- Presentation mode changes wording only, never strategy/capital/settlement behavior.
- First notification enablement bootstraps current event history without replay.
- Overlay and speech acknowledgements are independent; one destination failure must not roll back the other.
- Corrupt trading event or delivery state fails closed.
- Public notification output contains no raw exception text or credentials.
- Full Web UI compatibility tests and full docich regression are required.

---

## File Structure

- Create `src/docich/overlay_queue.py`: shared overlay event validation/load/append/atomic lock/regeneration helpers.
- Modify `src/docich/webui.py`: compatibility wrappers delegate overlay queue operations to `overlay_queue`.
- Create `src/docich/trading/presentation.py`: persistent compact/detailed mode plus deterministic PAPER wording.
- Create `src/docich/trading/notifications.py`: delivery state, source event reading, per-destination ACK/retry, bootstrap behavior.
- Create `src/docich/trading/soren_output.py`: narrow Soren-root / overlay / audio adapters.
- Modify `src/docich/trading/events.py`: public strict journal reader.
- Modify `src/docich/config.py`: notification enablement booleans.
- Modify `src/docich/trading/cli.py`: presentation + notify-once commands and safe output.
- Modify `src/docich/trading/worker.py`: isolated post-cycle notification delivery.
- Modify `config/docich.toml`, `config/docich.soren-live.toml`, `.github/workflows/ci.yml`, `README.md`.
- Create `tests/test_overlay_queue.py`, `tests/test_trading_presentation.py`, `tests/test_trading_notifications.py`.
- Modify `tests/test_webui.py`, `tests/test_trading_worker.py`, `tests/test_config.py`, `tests/test_trading_cli.py` as needed.

### Task 1: Shared Soren overlay queue

**Files:**
- Create: `src/docich/overlay_queue.py`
- Modify: `src/docich/webui.py`
- Create: `tests/test_overlay_queue.py`
- Modify: `tests/test_webui.py`

**Interfaces:**
- Produces: `validate_event(event) -> dict`, `load_events(soren_root, *, keep=None, strict=False) -> list[dict]`, `append_event(soren_root, event, *, keep=None, strict=True) -> bool`, `regenerate(soren_root) -> bool`.
- Preserves: Web UI private helper behavior through wrappers.

- [ ] **Step 1: Write failing shared-queue tests**

```python
def test_append_is_bounded_atomic_and_exact_dedup(tmp_path):
    root = tmp_path / "soren"
    event = {"ts": 100, "category": "worker", "title": "PAPER", "body": "x", "level": "info"}
    assert append_event(root, event, keep=2, strict=True) is True
    assert append_event(root, event, keep=2, strict=True) is False
    assert len(load_events(root, strict=True)) == 1


def test_strict_append_refuses_corrupt_existing_queue(tmp_path):
    root = tmp_path / "soren"
    p = root / "tmp/state/overlay_events.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(OverlayQueueError):
        append_event(root, valid_event(), strict=True)
    assert p.read_text(encoding="utf-8") == "not-json\n"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m pytest -q tests/test_overlay_queue.py`

Expected: import failure because `docich.overlay_queue` does not exist.

- [ ] **Step 3: Implement shared queue primitives**

Implement existing Web UI contracts in `overlay_queue.py`: category/title/body validation, event/env path resolution, optional strict JSONL parsing, `.webui_overlay.lock`, atomic rewrite, keep trimming, exact normalized-event dedupe, and `generate_event_overlay.py` regeneration. Keep file mode `0644` because the overlay is an existing Soren runtime artifact.

- [ ] **Step 4: Delegate Web UI helpers without changing endpoints**

Replace Web UI helper bodies with calls to the shared module while keeping the existing private function names and return shapes used by handlers.

- [ ] **Step 5: Run shared + Web UI tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_overlay_queue.py tests/test_webui.py`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/docich/overlay_queue.py src/docich/webui.py tests/test_overlay_queue.py tests/test_webui.py
git commit -m "refactor: share Soren overlay event queue"
```

### Task 2: Presentation state and deterministic PAPER wording

**Files:**
- Create: `src/docich/trading/presentation.py`
- Modify: `src/docich/config.py`
- Modify: `config/docich.toml`
- Modify: `config/docich.soren-live.toml`
- Create: `tests/test_trading_presentation.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `PresentationState(mode: str, updated_at: float | None)`, `read_presentation(path)`, `write_presentation(path, mode, now)`, `render_notification(event, *, mode, status=None) -> RenderedNotification`.
- `RenderedNotification` contains normalized overlay event data and speech text only.

- [ ] **Step 1: Write failing config/presentation tests**

```python
def test_notification_config_defaults_off(repo_root):
    g = load_global(repo_root)
    assert g.trading.notifications_enabled is False
    assert g.trading.notification_speech_enabled is False


def test_missing_presentation_defaults_compact(tmp_path):
    state = read_presentation(tmp_path / "presentation.json")
    assert state.mode == "compact"


def test_fill_text_is_paper_labeled_and_detailed_uses_reason():
    rendered = render_notification(fill_event(), mode="detailed", status=safe_status())
    assert "PAPER" in rendered.overlay_event["title"]
    assert "モメンタム" in rendered.speech_text
    assert "30%" not in rendered.speech_text  # do not invent policy prose
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m pytest -q tests/test_trading_presentation.py tests/test_config.py`

Expected: missing module/config fields.

- [ ] **Step 3: Add strict config fields and presentation persistence**

Add `notifications_enabled: bool = False` and `notification_speech_enabled: bool = False` to `TradingConfig`; validate with `type(value) is bool`. Add explicit false values to both global config files. Implement atomic private presentation JSON with only `compact|detailed`.

- [ ] **Step 4: Implement deterministic rendering**

Map known reason codes to fixed Japanese phrases, format paper fills/settlements from allowlisted source values, cap overlay title/body and speech lengths, set overlay category `worker`, and use `warn` only for incomplete settlement simulations.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_trading_presentation.py tests/test_config.py`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/docich/trading/presentation.py src/docich/config.py config/docich.toml config/docich.soren-live.toml tests/test_trading_presentation.py tests/test_config.py
git commit -m "feat: add crypto notification presentation modes"
```

### Task 3: Durable notification delivery and Soren adapters

**Files:**
- Create: `src/docich/trading/notifications.py`
- Create: `src/docich/trading/soren_output.py`
- Modify: `src/docich/trading/events.py`
- Create: `tests/test_trading_notifications.py`

**Interfaces:**
- Produces: `read_public_events(path) -> list[dict]` strict source reader.
- Produces: `deliver_pending_notifications(g, *, overlay_sender=None, speech_sender=None, now=None) -> NotificationDeliveryResult`.
- Produces: `resolve_soren_root(g)`, `send_overlay(g, event) -> bool`, `enqueue_speech(g, text) -> bool`.

- [ ] **Step 1: Write failing delivery tests**

```python
def test_first_enable_bootstraps_without_replay(tmp_path):
    write_source_events(tmp_path, [fill_event("a")])
    result = deliver_pending_notifications(g(tmp_path), overlay_sender=overlay, speech_sender=speech, now=100)
    assert result.bootstrapped is True
    assert overlay.calls == []
    assert speech.calls == []


def test_destinations_ack_independently(tmp_path):
    bootstrap_then_add_event(tmp_path, "b")
    overlay.fail = True
    result = deliver_pending_notifications(g(tmp_path), overlay_sender=overlay, speech_sender=speech, now=101)
    assert result.overlay_pending == 1
    assert result.speech_pending == 0
    overlay.fail = False
    deliver_pending_notifications(g(tmp_path), overlay_sender=overlay, speech_sender=speech, now=102)
    assert len(overlay.calls) == 2  # failed attempt + retry
    assert len(speech.calls) == 1  # no duplicate speech
```

- [ ] **Step 2: Run delivery tests and verify RED**

Run: `python3 -m pytest -q tests/test_trading_notifications.py`

Expected: missing notification module/functions.

- [ ] **Step 3: Expose strict public-event reader and delivery state**

Refactor existing private trading journal loader into public `read_public_events`. Implement `notification_delivery.json` schema with bounded overlay/speech delivered ID lists, private atomic persistence, corrupt-state failure, and bootstrap-to-current behavior.

- [ ] **Step 4: Implement Soren output adapters**

Resolve Soren root from `g.webui.soren_root` or `games/soviet_now`. Overlay adapter calls shared `overlay_queue.append_event(..., strict=True)` then regenerates. Speech adapter lazily calls the existing Web UI/Soren audio enqueue implementation with source `crypto_paper`; a dedup response counts as delivered.

- [ ] **Step 5: Implement per-destination delivery**

Render each new source event using current presentation mode. Persist an ACK immediately after each successful destination. If speech is disabled, record speech suppression so later enablement does not replay old events. Catch output exceptions into stable notification error codes; never include raw error text in result/state.

- [ ] **Step 6: Run delivery tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_trading_notifications.py tests/test_trading_events.py tests/test_overlay_queue.py`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/docich/trading/notifications.py src/docich/trading/soren_output.py src/docich/trading/events.py tests/test_trading_notifications.py
git commit -m "feat: deliver paper trading notifications"
```

### Task 4: CLI/worker integration, docs, review, and verification

**Files:**
- Modify: `src/docich/trading/cli.py`
- Modify: `src/docich/trading/worker.py`
- Modify: `tests/test_trading_cli.py`
- Modify: `tests/test_trading_worker.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`

**Interfaces:**
- Adds CLI: `docich trading presentation status|compact|detailed` and `docich trading notify-once`.
- Worker calls the same `deliver_pending_notifications` after a cycle only when notifications are enabled.

- [ ] **Step 1: Write failing CLI/worker isolation tests**

```python
def test_notify_once_does_not_poll_exchange(...):
    with patch("docich.trading.cli.BitbankPublicGateway") as gateway:
        rc = main(["trading", "notify-once"])
    assert rc == 0
    gateway.assert_not_called()


def test_notification_failure_does_not_fail_market_cycle(...):
    with patch("docich.trading.worker.deliver_pending_notifications", side_effect=RuntimeError("sink")):
        run_paper_worker(g, max_cycles=2, ...)
    assert market_cycle_count == 2
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m pytest -q tests/test_trading_cli.py tests/test_trading_worker.py`

Expected: parser/integration failures.

- [ ] **Step 3: Add CLI and isolated worker hook**

Wire presentation state and notify-once without constructing a market gateway. In the continuous worker, call notifier after each completed cycle inside a separate try/except. Disabled notification config produces no Soren import/write.

- [ ] **Step 4: Add CI contract and README operator docs**

Include the new notification/presentation/overlay queue tests in Crypto trading contracts. Document both config gates, first-enable no-replay behavior, compact/detailed commands, notify-once, output files, and explicit PAPER-only scope.

- [ ] **Step 5: Run complete verification**

Run:

```bash
python3 -m pytest -q tests/test_trading_*.py tests/test_overlay_queue.py
python3 -m pytest -q tests/test_webui.py
python3 -m compileall -q src tests
git diff --check origin/main...HEAD
python3 -m pytest -q tests
```

Expected: all pass.

- [ ] **Step 6: Run safety/source scans and local notification smoke**

Verify no new order/private/API-key paths. With temporary trading/Soren roots, bootstrap one existing event, append one new event, run notifier twice, and confirm one overlay event, at most one audio queue item, no duplicates on replay, `PAPER` label, and no credential markers.

- [ ] **Step 7: External code review and fixes**

Run current app-bundled Codex review against latest main. Reproduce each actionable finding with a test before fixing it; rerun full verification after any fix.

- [ ] **Step 8: Commit docs/CI, push Draft PR, confirm GitHub CI**

Create a Draft PR directly to `main` if parent PR #150 is merged; otherwise stack it on #150. Confirm mergeability, unresolved review threads, and latest-head CI/VM operations CI.


### 2026-09-09 continuation

User approved shared native/docich flock implementation and Draft PRs. The user's
no-agent instruction supersedes the external-review step above: primary agent
performs review directly. Audio producer process-crash recovery was implemented
and verified first. Shared-lock RED tests reproduced missing native events,
ignoring held locks, Web UI delete overwriting a native append, and lost 409
semantics. Changes address all four; local cross-writer, custom-path, owner-death,
full regression, and latest-head CI are the completion gates. No main merge or VM
deployment; production paper worker/notifications/speech stay disabled.
