#!/usr/bin/env python3
from pathlib import Path
import textwrap


def block(source, indent=""):
    value = textwrap.dedent(source).lstrip("\n")
    return textwrap.indent(value, indent) if indent else value


def insert_before(text, marker, insertion, name):
    count = text.count(marker)
    if count != 1:
        raise SystemExit(f"{name}: expected one marker, got {count}")
    return text.replace(marker, insertion + marker, 1)


def replace_block(text, start_marker, end_marker, replacement, name):
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise SystemExit(f"{name}: block marker missing")
    return text[:start] + replacement + text[end:]


collector = Path("ops/vm_actions/collect_diagnostics.py")
text = collector.read_text(encoding="utf-8")
text = insert_before(
    text,
    "AI_COMPONENTS = (\n",
    block(r'''
    BUDGET_EXHAUSTED_DETAIL_COMPONENTS = ("radio_prepass", "radio_main")
    BUDGET_EXHAUSTED_DETAIL_MAX_COUNT = 99
    BUDGET_EXHAUSTED_DETAIL_MAX_LAST_BUDGET_SEC = 240
    BUDGET_EXHAUSTED_DETAIL_RE = re.compile(
        r"\Aexec=(0|[1-9][0-9]?);skip=(0|[1-9][0-9]?);"
        r"last_budget=(0|[1-9][0-9]{0,2});rem=0\Z"
    )
    '''),
    "collector constants",
)
text = insert_before(
    text,
    "def _ai_component_bucket(label):\n",
    block('''
    def _parse_budget_exhausted_detail(value):
        """Parse Soren's fixed RADIO budget-exhaustion detail, fail-closed."""
        if not isinstance(value, str):
            return None
        match = BUDGET_EXHAUSTED_DETAIL_RE.fullmatch(value)
        if match is None:
            return None
        executed, skipped, last_budget = (int(item) for item in match.groups())
        if (
            executed > BUDGET_EXHAUSTED_DETAIL_MAX_COUNT
            or skipped > BUDGET_EXHAUSTED_DETAIL_MAX_COUNT
            or last_budget > BUDGET_EXHAUSTED_DETAIL_MAX_LAST_BUDGET_SEC
        ):
            return None
        return executed, skipped, last_budget


    '''),
    "collector parser",
)
init_marker = "    budget_exhausted_components = {component: 0 for component in AI_COMPONENTS}\n"
init_insert = block('''
    budget_exhausted_detail_sampled = 0
    budget_exhausted_detail_malformed = 0
    budget_exhausted_detail_missing = 0
    budget_exhausted_detail_components = {
        component: {
            "sampled": 0,
            "exec_sum": 0,
            "skip_sum": 0,
            "last_budget_min_sec": 0,
            "last_budget_max_sec": 0,
        }
        for component in BUDGET_EXHAUSTED_DETAIL_COMPONENTS
    }
    ''', "    ")
if text.count(init_marker) != 1:
    raise SystemExit("collector init marker mismatch")
text = text.replace(init_marker, init_marker + init_insert, 1)
text = replace_block(
    text,
    '        if kind == "budget_exhausted":\n',
    '        if kind == "chain_summary":\n',
    block('''
    if kind == "budget_exhausted":
        # Observability-only fixed counters. Never publish this event's
        # agent/provider/model/error or dynamic label into recent_events.
        component = _ai_component_bucket(label)
        budget_exhausted += 1
        budget_exhausted_components[component] += 1
        if component in BUDGET_EXHAUSTED_DETAIL_COMPONENTS:
            raw_detail = event.get("error")
            if raw_detail is None:
                # Older events did not carry dispatch detail. Keep them
                # compatible and distinguish absence from malformed input.
                budget_exhausted_detail_missing += 1
            else:
                detail = _parse_budget_exhausted_detail(raw_detail)
                if detail is None:
                    budget_exhausted_detail_malformed += 1
                else:
                    executed, skipped, last_budget = detail
                    budget_exhausted_detail_sampled += 1
                    row = budget_exhausted_detail_components[component]
                    row["sampled"] += 1
                    row["exec_sum"] += executed
                    row["skip_sum"] += skipped
                    if row["sampled"] == 1:
                        row["last_budget_min_sec"] = last_budget
                    else:
                        row["last_budget_min_sec"] = min(row["last_budget_min_sec"], last_budget)
                    row["last_budget_max_sec"] = max(row["last_budget_max_sec"], last_budget)
        continue
    ''', "        "),
    "collector budget handler",
)
return_marker = '        "budget_exhausted_components": budget_exhausted_components,\n'
return_insert = block('''
    "budget_exhausted_detail_sampled": budget_exhausted_detail_sampled,
    "budget_exhausted_detail_malformed": budget_exhausted_detail_malformed,
    "budget_exhausted_detail_missing": budget_exhausted_detail_missing,
    "budget_exhausted_detail_components": budget_exhausted_detail_components,
    ''', "        ")
if text.count(return_marker) != 1:
    raise SystemExit("collector return marker mismatch")
text = text.replace(return_marker, return_marker + return_insert, 1)
collector.write_text(text, encoding="utf-8")


summary = Path("ops/vm_actions/summarize_runtime_queue_attribution.py")
text = summary.read_text(encoding="utf-8")
text = insert_before(
    text,
    "IMPROVEMENT_BLOCKERS = (\n",
    block('''
    BUDGET_EXHAUSTED_DETAIL_COMPONENTS = ("radio_prepass", "radio_main")
    BUDGET_EXHAUSTED_DETAIL_MAX_COUNT = 99
    BUDGET_EXHAUSTED_DETAIL_MAX_LAST_BUDGET_SEC = 240
    '''),
    "summary constants",
)
text = insert_before(
    text,
    "def invalid_output_component_metrics(data):\n",
    block('''
    def budget_exhausted_detail_metrics(data):
        """Project validated RADIO dispatch detail to bounded fixed counters."""
        ai = data.get("ai") if isinstance(data, dict) else None
        ai = ai if isinstance(ai, dict) else {}

        def strict_nonnegative(mapping, name):
            value = mapping.get(name) if isinstance(mapping, dict) else None
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            return value

        sampled_raw = strict_nonnegative(ai, "budget_exhausted_detail_sampled")
        malformed_raw = strict_nonnegative(ai, "budget_exhausted_detail_malformed")
        missing_raw = strict_nonnegative(ai, "budget_exhausted_detail_missing")
        sampled = sampled_raw if sampled_raw is not None else 0
        malformed = malformed_raw if malformed_raw is not None else 0
        missing = missing_raw if missing_raw is not None else 0
        consistent = all(value is not None for value in (sampled_raw, malformed_raw, missing_raw))

        raw_components = ai.get("budget_exhausted_detail_components")
        raw_components = raw_components if isinstance(raw_components, dict) else {}
        rows = {}
        component_samples = 0
        for component in BUDGET_EXHAUSTED_DETAIL_COMPONENTS:
            raw = raw_components.get(component)
            values = {
                name: strict_nonnegative(raw, name)
                for name in (
                    "sampled",
                    "exec_sum",
                    "skip_sum",
                    "last_budget_min_sec",
                    "last_budget_max_sec",
                )
            }
            if any(value is None for value in values.values()):
                consistent = False
                values = {name: 0 for name in values}
            row_sampled = values["sampled"]
            component_samples += row_sampled
            if (
                values["exec_sum"] > row_sampled * BUDGET_EXHAUSTED_DETAIL_MAX_COUNT
                or values["skip_sum"] > row_sampled * BUDGET_EXHAUSTED_DETAIL_MAX_COUNT
                or values["last_budget_min_sec"] > BUDGET_EXHAUSTED_DETAIL_MAX_LAST_BUDGET_SEC
                or values["last_budget_max_sec"] > BUDGET_EXHAUSTED_DETAIL_MAX_LAST_BUDGET_SEC
                or values["last_budget_min_sec"] > values["last_budget_max_sec"]
                or (
                    row_sampled == 0
                    and any(values[name] != 0 for name in values if name != "sampled")
                )
            ):
                consistent = False
            rows[component] = values
        if component_samples != sampled:
            consistent = False
        if not consistent:
            rows = {
                component: {
                    "sampled": 0,
                    "exec_sum": 0,
                    "skip_sum": 0,
                    "last_budget_min_sec": 0,
                    "last_budget_max_sec": 0,
                }
                for component in BUDGET_EXHAUSTED_DETAIL_COMPONENTS
            }

        raw_budget_counts = ai.get("budget_exhausted_components")
        raw_budget_counts = raw_budget_counts if isinstance(raw_budget_counts, dict) else {}
        radio_total = sum(
            _integer(raw_budget_counts, component)
            for component in BUDGET_EXHAUSTED_DETAIL_COMPONENTS
        )
        coverage_exact = consistent and sampled + malformed + missing == radio_total
        return sampled, malformed, missing, rows, consistent, coverage_exact


    '''),
    "summary helper",
)
render_marker = '    summary += "," + ",".join(_improvement_metrics(data))\n'
render_insert = block('''
    (
        detail_sampled,
        detail_malformed,
        detail_missing,
        detail_rows,
        detail_consistent,
        detail_coverage_exact,
    ) = budget_exhausted_detail_metrics(data)
    summary += f",ai_budget_exhausted_detail_sampled={detail_sampled}"
    summary += f",ai_budget_exhausted_detail_malformed={detail_malformed}"
    summary += f",ai_budget_exhausted_detail_missing={detail_missing}"
    summary += f",ai_budget_exhausted_detail_attribution_consistent={int(detail_consistent)}"
    summary += f",ai_budget_exhausted_detail_coverage_exact={int(detail_coverage_exact)}"
    for component in BUDGET_EXHAUSTED_DETAIL_COMPONENTS:
        row = detail_rows[component]
        summary += f",ai_budget_exhausted_detail_{component}_sampled={row['sampled']}"
        summary += f",ai_budget_exhausted_detail_{component}_exec_sum={row['exec_sum']}"
        summary += f",ai_budget_exhausted_detail_{component}_skip_sum={row['skip_sum']}"
        summary += (
            f",ai_budget_exhausted_detail_{component}_last_budget_min_sec="
            f"{row['last_budget_min_sec']}"
        )
        summary += (
            f",ai_budget_exhausted_detail_{component}_last_budget_max_sec="
            f"{row['last_budget_max_sec']}"
        )
    ''', "    ")
if text.count(render_marker) != 1:
    raise SystemExit("summary render marker mismatch")
text = text.replace(render_marker, render_insert + render_marker, 1)
summary.write_text(text, encoding="utf-8")
