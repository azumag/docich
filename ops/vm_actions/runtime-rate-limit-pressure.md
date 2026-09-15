# Runtime AI rate-limit pressure signal

The VM monitor exposes a fixed `ai_rate_limit_pressure=0|1` signal in the sanitized runtime summary. The signal is observability-only: it does not change provider ordering, retry/backoff behavior, queue limits, worker lifecycle, VM permissions, or runtime severity.

## Threshold

`ai_rate_limit_pressure=1` when all of the following are true in the 15-minute aggregate window:

- `attempts_15m >= 10`
- `rate_limits_15m >= 5`
- `rate_limits_15m / attempts_15m >= 30%`

Otherwise the signal is `0`.

## Rationale

Production observations on 2026-09-14 repeatedly showed windows with 11-20 attempts and 6-10 rate-limit events while worker/queue health could still be otherwise OK. The three-part threshold intentionally requires both a useful sample size and material absolute/relative pressure, so one-off or low-volume 429s do not surface as pressure.

The threshold is intentionally a signal rather than a severity override. Existing queue give-up, all-failed, worker, lock, and other health conditions continue to own WARN/CRITICAL behavior. This avoids alert flapping while providing a stable field that can be correlated across successive monitor runs. If later evidence supports severity promotion or hysteresis, that should be reviewed separately.

## Security and privacy

The classifier reads only existing aggregate counters. It does not publish provider/model identifiers, prompts, paths, raw logs, secrets, or error previews. The owner-only/read-only diagnostics and VM control-plane boundaries remain unchanged.
