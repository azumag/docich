#!/usr/bin/env python3
"""Extend the public runtime summary with full-window queue-giveup attribution.

The VM diagnostics already expose the 15-minute aggregate count plus a bounded,
redacted recent-event sample.  A queue_giveup can therefore age out of the
sample while remaining present in the aggregate.  This helper keeps the
existing runtime summary unchanged and adds fixed-category attribution where
sample evidence exists; any aggregate remainder is assigned to ``unknown``
rather than being silently reported as zero for every component.

Only fixed counts/booleans are emitted.  Dynamic component labels, providers,
models, errors, paths, prompts and credentials are never printed.
"""
import json
import sys

from summarize_runtime import COMPONENTS, _component_bucket, summarize


ATTRIBUTION_COMPONENTS = COMPONENTS + ("unknown",)


def _integer(mapping, name):
    value = mapping.get(name, 0) if isinstance(mapping, dict) else 0
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


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
