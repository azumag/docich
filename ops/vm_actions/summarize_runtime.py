#!/usr/bin/env python3
"""Render the public runtime-health summary from sanitized VM diagnostics.

The diagnostics payload is already bounded and redacted on the VM. This helper
runs on the GitHub Actions runner and deliberately emits only counts, booleans,
and fixed category names. It never emits provider/model identifiers, paths,
prompt text, dynamic component labels, or error previews.
"""
from collections import Counter
import json
import re
import sys

CAUSES = (
    "rate_limit",
    "timeout",
    "auth_policy",
    "provider_server",
    "model_unavailable",
    "invalid_output",
    "other",
)

COMPONENTS = (
    "radio_prepass",
    "radio_main",
    "news_spam_check",
    "comment",
    "improvement",
    "other",
)

TIMEOUT_BUCKETS = (
    "exact_20s",
    "other_known",
    "unknown",
)

TIMEOUT_ORIGINS = (
    "local_budget",
    "upstream_or_cli",
    "unknown",
)

BACKEND_FAMILIES = (
    "local",
    "codex",
    "opencode",
    "vercel",
    "amd",
    "other",
)

ACTIVE_CORNER_STATUSES = frozenset({"starting", "active", "restoring"})
ACTIVE_PAPER_IMPROVE_STATUSES = frozenset({"queued", "running"})


def _integer(mapping, name):
    value = mapping.get(name, 0) if isinstance(mapping, dict) else 0
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _nlist(mapping, name):
    value = mapping.get(name) if isinstance(mapping, dict) else None
    return len(value) if isinstance(value, list) else 0


def _fixed_status_is(mapping, allowed):
    """Return a boolean for an allowlisted lifecycle state without echoing it."""
    if not isinstance(mapping, dict):
        return False
    value = mapping.get("status")
    return isinstance(value, str) and value in allowed


def _game_switch_busy(corners):
    """Expose only whether the switch is outside its stable ready phase."""
    if not isinstance(corners, dict):
        return False
    switch = corners.get("game_switch")
    if not isinstance(switch, dict) or switch.get("readable") is not True:
        return False
    phase = switch.get("phase")
    return isinstance(phase, str) and phase not in {"", "ready"}


def _failure_cause(event):
    """Map one already-redacted recent failure event to a fixed enum."""
    if not isinstance(event, dict) or event.get("event") != "fail":
        return None
    rc = str(event.get("rc") or "").lower()
    preview = str(event.get("error_preview") or "").lower()
    text = f"{rc} {preview}"

    if rc == "79" or re.search(r"\b429\b|rate[ _-]?limit|quota", text):
        return "rate_limit"
    if re.search(r"timed? out|timeout|deadline exceeded", text):
        return "timeout"
    if re.search(r"\b401\b|\b403\b|unauthori[sz]ed|forbidden|permission denied|restricted.?model", text):
        return "auth_policy"
    if re.search(r"\b50[0-4]\b|server error|internal server|bad gateway|service unavailable|gateway timeout", text):
        return "provider_server"
    if re.search(r"\b404\b|model not found|unknown model|model unavailable|model is not available", text):
        return "model_unavailable"
    if re.search(r"validation|validator|empty output|output empty|too short|provider error text|invalid output", text):
        return "invalid_output"
    return "other"


def _timeout_bucket(event):
    """Classify timeout duration without publishing arbitrary numeric values."""
    if not isinstance(event, dict) or event.get("event") != "fail":
        return None
    preview = str(event.get("error_preview") or "").lower()
    match = re.search(r"(?:timeout|timed? out)\s+(?:after\s+)?(\d{1,4})\s*(?:s|sec(?:ond)?s?)\b", preview)
    if not match:
        return "unknown"
    try:
        seconds = int(match.group(1))
    except (TypeError, ValueError):
        return "unknown"
    return "exact_20s" if seconds == 20 else "other_known"


def _timeout_origin(event):
    """Distinguish our local timeout budget from timeout text reported upstream.

    Soren's local timeout wrappers store a canonical ``timeout after Ns`` error
    preview. Provider/CLI failures retain their own bounded prefix/text instead.
    Publish only this fixed origin enum so the production alert can tell whether
    a 20-second signature came from our configured process budget without
    exposing provider/model identifiers or the preview itself.
    """
    if not isinstance(event, dict) or event.get("event") != "fail":
        return None
    preview = str(event.get("error_preview") or "").strip().lower()
    if re.match(r"^timeout\s+after\s+\d{1,4}\s*(?:s|sec(?:ond)?s?)\b", preview):
        return "local_budget"
    if preview and re.search(r"timed? out|timeout|deadline exceeded", preview):
        return "upstream_or_cli"
    return "unknown"


def _backend_family(event):
    """Collapse the private provider field to a fixed non-model backend family."""
    if not isinstance(event, dict):
        return "other"
    provider = str(event.get("provider") or "").strip().lower()
    if provider == "local":
        return "local"
    if provider == "codex":
        return "codex"
    if provider in {"opencode", "opencode-go"}:
        return "opencode"
    if provider == "vercel":
        return "vercel"
    if provider == "amd":
        return "amd"
    return "other"


def _component_bucket(event):
    """Collapse a private/dynamic component label into a small fixed enum."""
    if not isinstance(event, dict):
        return "other"
    label = str(event.get("component") or "").lower()
    if label.startswith("news:spam_check"):
        return "news_spam_check"
    if label.startswith(("radio", "news", "jiji", "celebration")):
        if "prepass" in label:
            return "radio_prepass"
        return "radio_main"
    if label.startswith("comment"):
        return "comment"
    if label.startswith(("improve", "improvement", "eloop")):
        return "improvement"
    return "other"


def summarize(data):
    if not isinstance(data, dict):
        raise ValueError("diagnostics must be an object")
    severity = data.get("status")
    if severity not in {"ok", "warn", "critical"}:
        raise ValueError("invalid diagnostics severity")

    workers = data.get("workers") or {}
    queues = data.get("queues") or {}
    ai = data.get("ai") or {}
    improvement = data.get("improvement") or {}
    corners = data.get("corners") or {}

    recent = ai.get("recent_events")
    cause_counts = Counter()
    rate_limit_backend_counts = Counter()
    rate_limit_backend_agents = {family: set() for family in BACKEND_FAMILIES}
    timeout_counts = Counter()
    exact_20s_origin_counts = Counter()
    exact_20s_backend_counts = Counter()
    timeout_component_counts = Counter()
    fail_component_counts = Counter()
    queue_giveup_component_counts = Counter()
    all_failed_component_counts = Counter()
    if isinstance(recent, list):
        for event in recent:
            cause = _failure_cause(event)
            if cause is not None:
                cause_counts[cause] += 1
                component = _component_bucket(event)
                fail_component_counts[component] += 1
                if cause == "rate_limit":
                    family = _backend_family(event)
                    rate_limit_backend_counts[family] += 1
                    if isinstance(event, dict):
                        provider = str(event.get("provider") or "").strip().lower()
                        model = str(event.get("model") or "").strip().lower()
                        if provider or model:
                            rate_limit_backend_agents[family].add((provider, model))
                if cause == "timeout":
                    bucket = _timeout_bucket(event)
                    timeout_counts[bucket] += 1
                    timeout_component_counts[(bucket, component)] += 1
                    if bucket == "exact_20s":
                        exact_20s_origin_counts[_timeout_origin(event)] += 1
                        exact_20s_backend_counts[_backend_family(event)] += 1
            if isinstance(event, dict) and event.get("event") == "queue_giveup":
                queue_giveup_component_counts[_component_bucket(event)] += 1
            if isinstance(event, dict) and event.get("event") == "all_failed":
                all_failed_component_counts[_component_bucket(event)] += 1

    sampled = sum(cause_counts.values())
    sampled_queue_giveups = sum(queue_giveup_component_counts.values())
    sampled_all_failed = sum(all_failed_component_counts.values())
    retro = corners.get("retro_corner") if isinstance(corners, dict) else None
    paper = corners.get("paper_corner") if isinstance(corners, dict) else None
    paper_manual = corners.get("paper_corner_manual") if isinstance(corners, dict) else None
    paper_improve = corners.get("paper_improve") if isinstance(corners, dict) else None
    ab = corners.get("ab") if isinstance(corners, dict) else None
    parts = [
        f"required_down={_nlist(workers, 'required_down')}",
        f"required_stale={_nlist(workers, 'required_stale')}",
        f"paused={_nlist(workers, 'paused')}",
        f"duplicates={_nlist(workers, 'duplicates')}",
        f"zombies={_nlist(workers, 'zombies')}",
        f"stale_pid_files={_nlist(workers, 'stale_pid_files')}",
        f"unregistered={_nlist(workers, 'unregistered')}",
        f"stale_locks={_integer(queues, 'stale_locks')}",
        f"queue_giveups_15m={_integer(queues, 'queue_giveups_15m')}",
        f"ai_attempts_15m={_integer(ai, 'attempts_15m')}",
        f"ai_successes_15m={_integer(ai, 'successes')}",
        f"ai_failures_15m={_integer(ai, 'failures_15m')}",
        f"ai_rate_limits_15m={_integer(ai, 'rate_limits_15m')}",
        f"ai_fallbacks_15m={_integer(ai, 'fallbacks_15m')}",
        f"ai_all_failed_15m={_integer(ai, 'all_failed_15m')}",
        f"ai_recent_fail_sampled={sampled}",
    ]
    parts.extend(f"ai_recent_fail_{cause}={cause_counts[cause]}" for cause in CAUSES)
    parts.extend(
        f"ai_recent_rate_limit_backend_{family}={rate_limit_backend_counts[family]}"
        for family in BACKEND_FAMILIES
    )
    parts.extend(
        f"ai_recent_rate_limit_backend_{family}_distinct_agents={len(rate_limit_backend_agents[family])}"
        for family in BACKEND_FAMILIES
    )
    parts.extend(f"ai_recent_timeout_{bucket}={timeout_counts[bucket]}" for bucket in TIMEOUT_BUCKETS)
    parts.extend(
        f"ai_recent_timeout_exact_20s_origin_{origin}={exact_20s_origin_counts[origin]}"
        for origin in TIMEOUT_ORIGINS
    )
    parts.extend(
        f"ai_recent_timeout_exact_20s_backend_{family}={exact_20s_backend_counts[family]}"
        for family in BACKEND_FAMILIES
    )
    parts.extend(
        f"ai_recent_timeout_{bucket}_component_{component}={timeout_component_counts[(bucket, component)]}"
        for bucket in TIMEOUT_BUCKETS
        for component in COMPONENTS
    )
    parts.extend(f"ai_recent_fail_component_{component}={fail_component_counts[component]}" for component in COMPONENTS)
    parts.append(f"ai_recent_queue_giveup_sampled={sampled_queue_giveups}")
    parts.extend(
        f"ai_recent_queue_giveup_component_{component}={queue_giveup_component_counts[component]}"
        for component in COMPONENTS
    )
    parts.append(f"ai_recent_all_failed_sampled={sampled_all_failed}")
    parts.extend(
        f"ai_recent_all_failed_component_{component}={all_failed_component_counts[component]}"
        for component in COMPONENTS
    )
    parts.extend(
        [
            f"improvement_stale={int(improvement.get('stale') is True)}",
            f"retry_pending={int(improvement.get('retry_pending') is True)}",
            f"corner_game_switch_busy={int(_game_switch_busy(corners))}",
            f"corner_retro_active={int(_fixed_status_is(retro, ACTIVE_CORNER_STATUSES))}",
            f"corner_retro_waiting={int(_fixed_status_is(retro, frozenset({'waiting'})))}",
            f"corner_paper_active={int(_fixed_status_is(paper, ACTIVE_CORNER_STATUSES))}",
            f"corner_paper_manual_active={int(_fixed_status_is(paper_manual, ACTIVE_CORNER_STATUSES))}",
            f"corner_paper_improve_running={int(_fixed_status_is(paper_improve, ACTIVE_PAPER_IMPROVE_STATUSES))}",
            f"corner_ab_candidate_pending={int(isinstance(ab, dict) and ab.get('candidate_pending') is True)}",
        ]
    )
    return severity, ",".join(parts)


def main(argv):
    if len(argv) != 2:
        print("usage: summarize_runtime.py <runtime-diagnostics.json>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as handle:
        wrapper = json.load(handle)
    if wrapper.get("status") != "diagnosed" or not isinstance(wrapper.get("diagnostics"), dict):
        raise SystemExit("invalid diagnostics envelope")
    try:
        severity, summary = summarize(wrapper["diagnostics"])
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"severity={severity}")
    print(f"summary={summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))