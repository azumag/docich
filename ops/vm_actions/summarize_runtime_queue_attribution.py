#!/usr/bin/env python3
"""Extend the public runtime summary with bounded operational attribution.

The VM diagnostics already expose the 15-minute aggregate queue-giveup count
plus a bounded, redacted recent-event sample.  A queue_giveup can therefore age
out of the sample while remaining present in the aggregate.  This helper keeps
the existing runtime summary unchanged and adds fixed-category attribution
where sample evidence exists; any aggregate remainder is assigned to
``unknown`` rather than being silently reported as zero for every component.

It is the single runtime-summary entry point used by the VM monitor, so it also
composes the fixed ``ai_rate_limit_pressure`` signal and bounded improvement
retry state.  That keeps observability additions in one ``severity``/``summary``
step-output pair instead of competing for the same GitHub Actions output key.

Only fixed counts/booleans and capped ages are emitted. Dynamic component
labels, providers, models, errors, paths, prompts, credentials and free-form
blocker values are never printed.
"""
import json
import sys

from summarize_runtime import COMPONENTS, _component_bucket, summarize
from summarize_runtime_pressure import rate_limit_pressure


ATTRIBUTION_COMPONENTS = COMPONENTS + ("unknown",)
CHAIN_SUMMARY_KEYS = (
    "chain_summary_sampled",
    "multi_vercel_429_chains",
    "multi_vercel_429_non_vercel_recovered",
    "multi_vercel_429_all_failed",
)
IMPROVEMENT_BLOCKERS = (
    "rate_limit_backoff",
    "peak_hour_defer",
    "ab_pending",
    "daemon_paused",
    "unknown",
)
IMPROVEMENT_AGE_CAP_SEC = 86400


def _integer(mapping, name):
    value = mapping.get(name, 0) if isinstance(mapping, dict) else 0
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _bounded_age(mapping, name):
    """Return a non-negative age capped at one day plus a capped flag."""
    value = mapping.get(name) if isinstance(mapping, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0, False
    return min(value, IMPROVEMENT_AGE_CAP_SEC), value > IMPROVEMENT_AGE_CAP_SEC


def _improvement_metrics(data):
    """Project retry state to fixed booleans/capped counters only.

    ``blocked_by`` is private runtime state even though the collector already
    restricts it to stable values.  Re-check the allowlist here and emit only
    one boolean per known category so an unexpected/free-form value can never
    enter the public GitHub issue body.
    """
    improvement = data.get("improvement") if isinstance(data, dict) else None
    improvement = improvement if isinstance(improvement, dict) else {}
    retry_age, retry_age_capped = _bounded_age(improvement, "retry_age_sec")
    backoff_age, backoff_age_capped = _bounded_age(improvement, "backoff_age_sec")
    raw_blockers = improvement.get("blocked_by")
    blockers = {
        item for item in raw_blockers
        if isinstance(raw_blockers, list) and isinstance(item, str) and item in IMPROVEMENT_BLOCKERS
    } if isinstance(raw_blockers, list) else set()
    metrics = [
        f"improvement_running={int(improvement.get('running') is True)}",
        f"improvement_backing_off={int(improvement.get('backing_off') is True)}",
        f"improvement_retry_age_sec={retry_age}",
        f"improvement_retry_age_capped={int(retry_age_capped)}",
        f"improvement_backoff_age_sec={backoff_age}",
        f"improvement_backoff_age_capped={int(backoff_age_capped)}",
    ]
    metrics.extend(
        f"improvement_blocked_by_{blocker}={int(blocker in blockers)}"
        for blocker in IMPROVEMENT_BLOCKERS
    )
    return metrics


def attribute_queue_giveups(data):
    """Return fixed full-window buckets, failing closed to ``unknown``.

    ``consistent`` means the bounded recent sample does not contain more
    queue-giveup events than the full-window aggregate. ``exact`` means every
    aggregate event is still present in the recent sample, so no unknown
    remainder was required.
    """
    queues = data.get("queues") if isinstance(data, dict) else None
    ai = data.get("ai") if isinstance(data, dict) else None
    aggregate = _integer(queues or {}, "queue_giveups_15m")
    recent = (ai or {}).get("recent_events") if isinstance(ai, dict) else None

    counts = {component: 0 for component in ATTRIBUTION_COMPONENTS}
    if not isinstance(recent, list):
        counts["unknown"] = aggregate
        return counts, False, False

    sampled = 0
    for event in recent:
        if not isinstance(event, dict) or event.get("event") != "queue_giveup":
            continue
        sampled += 1
        counts[_component_bucket(event)] += 1

    if sampled > aggregate:
        # The payload is internally inconsistent. Do not publish partial fixed
        # buckets as though they covered the aggregate.
        counts = {component: 0 for component in ATTRIBUTION_COMPONENTS}
        counts["unknown"] = aggregate
        return counts, False, False

    counts["unknown"] = aggregate - sampled
    return counts, True, counts["unknown"] == 0


def render(data):
    severity, summary = summarize(data)
    ai = data.get("ai") if isinstance(data, dict) else None
    summary = f"{summary},ai_rate_limit_pressure={rate_limit_pressure(ai)}"
    summary += "," + ",".join(
        f"ai_{name}={_integer(ai or {}, name)}" for name in CHAIN_SUMMARY_KEYS
    )
    summary += "," + ",".join(_improvement_metrics(data))
    counts, consistent, exact = attribute_queue_giveups(data)
    extra = [
        f"ai_queue_giveup_attribution_consistent={int(consistent)}",
        f"ai_queue_giveup_attribution_exact={int(exact)}",
    ]
    extra.extend(
        f"ai_queue_giveup_component_{component}={counts[component]}"
        for component in ATTRIBUTION_COMPONENTS
    )
    return severity, summary + "," + ",".join(extra)


def main(argv):
    if len(argv) != 2:
        print("usage: summarize_runtime_queue_attribution.py <runtime-diagnostics.json>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as handle:
        wrapper = json.load(handle)
    if wrapper.get("status") != "diagnosed" or not isinstance(wrapper.get("diagnostics"), dict):
        raise SystemExit("invalid diagnostics envelope")
    try:
        severity, summary = render(wrapper["diagnostics"])
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"severity={severity}")
    print(f"summary={summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
