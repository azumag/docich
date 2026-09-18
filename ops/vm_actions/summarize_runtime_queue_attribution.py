#!/usr/bin/env python3
"""Extend the public runtime summary with bounded operational attribution.

The VM diagnostics already expose the 15-minute aggregate queue-giveup count
plus a bounded, redacted recent-event sample.  A queue_giveup can therefore age
out of the sample while remaining present in the aggregate.  This helper keeps
the existing runtime summary unchanged and adds fixed-category caller
attribution where sample evidence exists; any aggregate remainder is assigned
to ``unknown`` rather than being silently reported as zero for every
component.

Soren also emits a dedicated ``queue_giveup_detail`` event with only a bounded
wait and a fixed holder category.  The collector validates that strict grammar
before placing fixed counters in diagnostics; this helper projects those
counters separately so caller attribution can never be confused with the lane
holder that caused the wait.

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
QUEUE_GIVEUP_HOLDERS = (
    "radio_prepass",
    "radio_main",
    "news",
    "jiji",
    "celebration",
    "other",
    "unknown",
)
QUEUE_GIVEUP_WAIT_CAP_SEC = 86400
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
PAPER_IMPROVE_ACTIVE_STATUSES = frozenset({"queued", "running"})
PAPER_IMPROVE_STALE_SEC = 1800


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


def _paper_improve_stale(data):
    """Return a fixed stale flag for PAPER improve metadata only.

    The collector already reduces PAPER improve state to fixed lifecycle fields
    and a file age.  Treat queued/running metadata older than the conservative
    30-minute bound as stale, while failing closed to zero for malformed shapes.
    This is observability-only: it does not change runtime severity or mutate
    production state.
    """
    corners = data.get("corners") if isinstance(data, dict) else None
    paper = corners.get("paper_improve") if isinstance(corners, dict) else None
    if not isinstance(paper, dict) or paper.get("status") not in PAPER_IMPROVE_ACTIVE_STATUSES:
        return 0
    age = paper.get("age_sec")
    if isinstance(age, bool) or not isinstance(age, int) or age < 0:
        return 0
    return int(age > PAPER_IMPROVE_STALE_SEC)


def attribute_queue_giveups(data):
    """Return fixed caller buckets, failing closed to ``unknown``.

    ``consistent`` means the bounded recent sample does not contain more
    queue-giveup events than the full-window aggregate. ``exact`` means every
    aggregate event is still present in the recent sample, so no unknown
    remainder was required. These are caller metrics only; holder metrics are
    projected independently by :func:`attribute_queue_giveup_holders`.
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


def attribute_queue_giveup_holders(data):
    """Return fixed holder counters from validated detail telemetry.

    The collector is the trust boundary for the detail grammar, but re-check
    every shape here before publishing it to a GitHub issue.  An inconsistent
    holder map fails closed to zeroed holder buckets. ``exact`` additionally
    requires one valid detail record for every aggregate queue giveup and no
    malformed detail records in the window.
    """
    queues = data.get("queues") if isinstance(data, dict) else None
    ai = data.get("ai") if isinstance(data, dict) else None
    ai = ai if isinstance(ai, dict) else {}
    aggregate = _integer(queues or {}, "queue_giveups_15m")
    sampled = _integer(ai, "queue_giveup_detail_sampled")
    malformed = _integer(ai, "queue_giveup_detail_malformed")
    wait_max = min(_integer(ai, "queue_giveup_detail_wait_max_sec"), QUEUE_GIVEUP_WAIT_CAP_SEC)
    raw_counts = ai.get("queue_giveup_detail_holders")
    raw_counts = raw_counts if isinstance(raw_counts, dict) else {}
    counts = {holder: _integer(raw_counts, holder) for holder in QUEUE_GIVEUP_HOLDERS}
    consistent = sum(counts.values()) == sampled
    if not consistent:
        counts = {holder: 0 for holder in QUEUE_GIVEUP_HOLDERS}
        wait_max = 0
    exact = consistent and malformed == 0 and sampled == aggregate
    return counts, sampled, malformed, wait_max, consistent, exact


def render(data):
    severity, summary = summarize(data)
    ai = data.get("ai") if isinstance(data, dict) else None
    summary = f"{summary},ai_rate_limit_pressure={rate_limit_pressure(ai)}"
    summary += "," + ",".join(
        f"ai_{name}={_integer(ai or {}, name)}" for name in CHAIN_SUMMARY_KEYS
    )
    summary += "," + ",".join(_improvement_metrics(data))
    summary += f",corner_paper_improve_stale={_paper_improve_stale(data)}"
    counts, consistent, exact = attribute_queue_giveups(data)
    extra = [
        # Keep the historical names for dashboards while adding explicit
        # caller-prefixed aliases so they cannot be mistaken for holder data.
        f"ai_queue_giveup_attribution_consistent={int(consistent)}",
        f"ai_queue_giveup_attribution_exact={int(exact)}",
        f"ai_queue_giveup_caller_attribution_consistent={int(consistent)}",
        f"ai_queue_giveup_caller_attribution_exact={int(exact)}",
    ]
    extra.extend(
        f"ai_queue_giveup_component_{component}={counts[component]}"
        for component in ATTRIBUTION_COMPONENTS
    )
    (
        holder_counts,
        detail_sampled,
        detail_malformed,
        holder_wait_max,
        holder_consistent,
        holder_exact,
    ) = attribute_queue_giveup_holders(data)
    extra.extend(
        [
            f"ai_queue_giveup_holder_detail_sampled={detail_sampled}",
            f"ai_queue_giveup_holder_detail_malformed={detail_malformed}",
            f"ai_queue_giveup_holder_wait_max_sec={holder_wait_max}",
            f"ai_queue_giveup_holder_attribution_consistent={int(holder_consistent)}",
            f"ai_queue_giveup_holder_coverage_exact={int(holder_exact)}",
        ]
    )
    extra.extend(
        f"ai_queue_giveup_holder_{holder}={holder_counts[holder]}"
        for holder in QUEUE_GIVEUP_HOLDERS
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
