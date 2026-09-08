# Crypto Trading Notifications Design

**Date:** 2026-09-08
**Status:** Approved by the user’s 2026-09-08 「やってください」 continuation of the previously proposed trading-event narration slice.
**Parent work:** Continuous paper worker merged by PR #150.

## Goal

Consume the paper-only `run/trading/events.jsonl` journal and bridge new trading events into docich’s existing Soren overlay/audio surfaces without changing strategy, capital allocation, or exchange execution behavior. Ordinary gameplay receives concise PAPER-labeled alerts; an operator-selectable Bitcoin-corner presentation mode receives more detailed PAPER narration.

## Non-goals

- No bitbank API key, private endpoint, order endpoint, live execution, leverage, withdrawal, or exit strategy.
- No LLM-generated trading explanation or numerical invention.
- No automatic scheduling of the Bitcoin corner in this slice; presentation mode is explicit operator state.
- No new audio playback implementation. Reuse Soren’s existing comment audio queue.
- No separate overlay renderer. Reuse the existing Soren event overlay queue/generator.

## Configuration and enablement

Extend `TradingConfig` with:

- `notifications_enabled: bool = false`
- `notification_speech_enabled: bool = false`

Both are strict booleans. Deploying or enabling the paper worker alone therefore cannot create viewer notifications or audio. When `notifications_enabled=false`, the notification consumer performs no Soren writes. Speech is a second independent gate and cannot enable delivery while notifications are disabled.

## Presentation mode

Persist `run/trading/presentation.json` with schema version 1 and mode `compact` or `detailed`. Missing state means `compact`. Invalid/corrupt state fails closed for mutation/notification delivery rather than guessing a mode.

Expose:

- `docich trading presentation status`
- `docich trading presentation compact`
- `docich trading presentation detailed`

Changing presentation mode changes wording only. It never changes strategy selection, capital policy, settlement simulation, or worker lifecycle.

## Notification wording

All viewer-facing trading notices must prominently contain `PAPER` so simulated activity cannot be mistaken for live execution.

`paper_fill` compact notice includes event type, symbol, side, approximate paper notional, and strategy label. Detailed mode additionally includes the stable reason-code interpretation and current paper deployed/capital values read from allowlisted `status.json` when available.

`multileg_settlement` compact notice states PAPER arbitrage observation, route, probe amount, and whether the constrained simulation completed. Detailed mode additionally includes effective edge when present and stable failure reason when incomplete. The text must call edge a paper/simulated edge, never guaranteed profit.

Unknown reason codes are shown as sanitized codes rather than passed to an LLM. Numeric values come only from the allowlisted event/status records.

## Shared Soren output bridge

The existing Soren `overlay_events.jsonl` and comment audio queue remain the canonical viewer-output surfaces.

Create a small shared overlay-queue module containing the existing event validation, event path resolution, queue loading, lock-compatible atomic replacement, bounded append, and overlay regeneration behavior. Web UI compatibility wrappers delegate to this shared module so manual Web UI notifications and trading notifications use the same schema and `.webui_overlay.lock` protocol.

Trading output resolution uses `webui.soren_root` when configured, otherwise the repository `games/soviet_now` runtime tree, matching existing docich behavior. The audio path reuses the existing Soren comment audio enqueue implementation through a narrow adapter; no direct VOICEVOX/playback process is started by trading code.

Overlay delivery uses category `worker`. Normal successful paper events use `info`; failed constrained settlements use `warn`.

## Delivery state and replay behavior

Persist private `run/trading/notification_delivery.json` (0600 in the existing 0700 trading directory) with schema version 1 and bounded delivered-ID sets for overlay and speech.

The first run after notifications are enabled bootstraps to the current end of `events.jsonl` and emits nothing. This prevents a historical notification/audio flood when the feature is first turned on.

After bootstrap, each new event is considered independently for overlay and speech:

1. If overlay delivery succeeds, record the event ID in `overlay_delivered_ids` immediately.
2. If speech is enabled and enqueue succeeds, record the event ID in `speech_delivered_ids` immediately.
3. If speech is disabled, mark the event ID as speech-suppressed/delivered so enabling speech later does not replay historical notices.
4. Failure of one output does not roll back an already successful other output.
5. Failed destinations remain pending for the next cycle.

Delivered-ID lists are reduced to IDs still present in the bounded source journal, keeping state bounded. Corrupt source journal or delivery state fails closed: do not advance delivery state and do not overwrite Soren output.

The Soren overlay append path also suppresses an exact duplicate normalized overlay event, reducing duplicate display if a process dies after the Soren write but before the local ACK write. The existing Soren audio-text dedupe plus delivery ACK provides the equivalent normal replay suppression for speech.

## Worker integration

At the end of each successful or degraded paper worker cycle, after trading events have been durably appended, call notification delivery only when `notifications_enabled=true`. Notification exceptions are caught separately from trading-cycle exceptions. A broken Soren overlay/audio path must not affect candidate selection, paper fills, settlement recording, or the next market-data cycle.

Also expose `docich trading notify-once` for operator testing/recovery. It uses the exact same delivery function as the worker and does not poll bitbank or generate new trading events.

## Public/operator status

Expose a safe notification result from `notify-once` and add `notification_summary` to trading public status only if needed for worker observability. At minimum include presentation mode, pending overlay count, pending speech count, and stable notification error codes. Never include raw exception strings, Soren `.env` contents, queue file contents, or credentials.

## Security boundary

- Trading notification input is only the strict allowlisted paper event journal and safe trading status.
- Viewer text is deterministic formatting, not LLM output.
- No viewer/chat text can flow back into strategy or execution.
- No bitbank credential/private/order capability is added.
- Soren writes are confined to the pre-existing overlay/audio queue paths and use their existing validation/containment contracts.
- Notification configuration defaults off.

## Testing requirements

TDD must cover strict config validation; compact/detailed deterministic wording; PAPER labeling; presentation state persistence and corrupt-state failure; first-enable bootstrap without replay; per-destination ACK/retry; speech-disabled historical suppression; bounded delivery state; exact overlay duplicate suppression; overlay/audio failure isolation from trading cycle; `notify-once`; Web UI overlay compatibility; safe status sanitization; file permissions; and absence of live/private exchange methods.

Run focused trading/output tests, the existing Web UI tests, full docich regression, compile/diff checks, credential-free local notification smoke, and GitHub CI before review completion.
