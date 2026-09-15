#!/usr/bin/env python3
from pathlib import Path


path = Path("ops/vm_actions/collect_diagnostics.py")
text = path.read_text(encoding="utf-8")

old = 'RATE_LIMIT_RC = "79"\nTMP_SO_ROOT = Path("/tmp")\n'
new = '''RATE_LIMIT_RC = "79"
CHAIN_SUMMARY_MAX_COUNT = 1000
CHAIN_SUMMARY_RE = re.compile(
    r"\\Avrl=(0|[1-9][0-9]{0,3});vda=(0|[1-9][0-9]{0,3});nfs=([01]);"
    r"term=(winner|all_failed|queue_giveup|gate_giveup)\\Z"
)
TMP_SO_ROOT = Path("/tmp")
'''
assert text.count(old) == 1, "rate-limit anchor drifted"
text = text.replace(old, new, 1)

old = "def _collect_ai(soren, now):\n"
new = '''def _parse_chain_summary(value):
    """Parse the fixed Soren chain_summary payload without exposing free text."""
    if not isinstance(value, str):
        return None
    match = CHAIN_SUMMARY_RE.fullmatch(value)
    if match is None:
        return None
    vrl, vda, nfs, terminal = match.groups()
    vrl = int(vrl)
    vda = int(vda)
    if vrl > CHAIN_SUMMARY_MAX_COUNT or vda > CHAIN_SUMMARY_MAX_COUNT or vda > vrl:
        return None
    return vrl, vda, int(nfs), terminal


def _collect_ai(soren, now):
'''
assert text.count(old) == 1, "collect-ai anchor drifted"
text = text.replace(old, new, 1)

old = '''    attempts = successes = failures = rate_limits = winners = 0
    all_failed = queue_giveups = gate_giveups = 0
'''
new = '''    attempts = successes = failures = rate_limits = winners = 0
    all_failed = queue_giveups = gate_giveups = 0
    chain_summary_sampled = 0
    multi_vercel_429_chains = 0
    multi_vercel_429_non_vercel_recovered = 0
    multi_vercel_429_all_failed = 0
'''
assert text.count(old) == 1, "ai counter anchor drifted"
text = text.replace(old, new, 1)

old = '''        rc = str(event.get("rc") or "")
        entry = by_label.setdefault(label, {"fail": 0, "winner": 0, "agents": set(), "all_failed": 0})
'''
new = '''        rc = str(event.get("rc") or "")
        if kind == "chain_summary":
            # The producer intentionally stores only this fixed aggregate in
            # the error field. Reject anything outside that exact grammar and
            # never add chain summaries to recent_events, where arbitrary
            # labels/models/errors could become public or evict fail evidence.
            chain = _parse_chain_summary(event.get("error"))
            if chain is not None:
                vrl, vda, non_vercel_success, terminal = chain
                chain_summary_sampled += 1
                if vrl >= 2 and vda >= 2:
                    multi_vercel_429_chains += 1
                    if non_vercel_success == 1 and terminal == "winner":
                        multi_vercel_429_non_vercel_recovered += 1
                    if terminal == "all_failed":
                        multi_vercel_429_all_failed += 1
            continue
        entry = by_label.setdefault(label, {"fail": 0, "winner": 0, "agents": set(), "all_failed": 0})
'''
assert text.count(old) == 1, "ai event anchor drifted"
text = text.replace(old, new, 1)

old = '''        "gate_giveups": gate_giveups,
        "malformed_lines": malformed,
'''
new = '''        "gate_giveups": gate_giveups,
        "chain_summary_sampled": chain_summary_sampled,
        "multi_vercel_429_chains": multi_vercel_429_chains,
        "multi_vercel_429_non_vercel_recovered": multi_vercel_429_non_vercel_recovered,
        "multi_vercel_429_all_failed": multi_vercel_429_all_failed,
        "malformed_lines": malformed,
'''
assert text.count(old) == 1, "ai return anchor drifted"
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

path = Path("ops/vm_actions/summarize_runtime_queue_attribution.py")
text = path.read_text(encoding="utf-8")
old = 'ATTRIBUTION_COMPONENTS = COMPONENTS + ("unknown",)\n'
new = '''ATTRIBUTION_COMPONENTS = COMPONENTS + ("unknown",)
CHAIN_SUMMARY_KEYS = (
    "chain_summary_sampled",
    "multi_vercel_429_chains",
    "multi_vercel_429_non_vercel_recovered",
    "multi_vercel_429_all_failed",
)
'''
assert text.count(old) == 1, "summary constants anchor drifted"
text = text.replace(old, new, 1)
old = '''    summary = f"{summary},ai_rate_limit_pressure={rate_limit_pressure(ai)}"
    counts, consistent, exact = attribute_queue_giveups(data)
'''
new = '''    summary = f"{summary},ai_rate_limit_pressure={rate_limit_pressure(ai)}"
    summary += "," + ",".join(
        f"ai_{name}={_integer(ai or {}, name)}" for name in CHAIN_SUMMARY_KEYS
    )
    counts, consistent, exact = attribute_queue_giveups(data)
'''
assert text.count(old) == 1, "summary render anchor drifted"
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

Path("ops/vm_actions/tests/test_ai_chain_summary_diagnostics.py").write_text(
    '''import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VMOPS = ROOT / "ops" / "vm_actions"
sys.path.insert(0, str(VMOPS))

import summarize_runtime_queue_attribution as attribution  # noqa: E402


def load_collector():
    spec = importlib.util.spec_from_file_location(
        "chain_summary_collect_diagnostics", VMOPS / "collect_diagnostics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ChainSummaryDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.soren = Path(self.tmp.name) / "soren"
        self.now = int(time.time())
        self.stats = self.soren / "tmp" / "state" / "ai_stats"
        self.stats.mkdir(parents=True)
        self.collector = load_collector()

    def write_events(self, events):
        day = time.strftime("%Y%m%d", time.localtime(self.now))
        with open(self.stats / f"{day}.jsonl", "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\\n")

    def chain(self, value, **extra):
        event = {
            "ts": self.now,
            "event": "chain_summary",
            "label": "RADIO:SUPERSECRET_LABEL:prepass",
            "agent": "vercel:SUPERSECRET_MODEL",
            "rc": "79",
            "error": value,
        }
        event.update(extra)
        return event

    def test_fixed_chain_counters_ignore_malformed_and_hide_dynamic_fields(self):
        self.write_events(
            [
                self.chain("vrl=2;vda=2;nfs=1;term=winner"),
                self.chain("vrl=1;vda=1;nfs=0;term=winner"),
                self.chain("vrl=2;vda=2;nfs=0;term=all_failed"),
                self.chain("vrl=-1;vda=1;nfs=0;term=winner"),
                self.chain("vrl=999999999;vda=2;nfs=0;term=winner"),
                self.chain("vrl=2;vda=2;nfs=0;term=unknown"),
                self.chain("vrl=1;vda=2;nfs=0;term=winner"),
                self.chain("vrl=2;vda=2;nfs=1;term=winner;token=SUPERSECRET_TOKEN"),
                {
                    "ts": self.now,
                    "event": "fail",
                    "label": "RADIO:public:main",
                    "agent": "vercel:m1",
                    "rc": "79",
                    "error": "429",
                },
            ]
        )
        ai = self.collector._collect_ai(self.soren, self.now)
        self.assertEqual(ai["chain_summary_sampled"], 3)
        self.assertEqual(ai["multi_vercel_429_chains"], 2)
        self.assertEqual(ai["multi_vercel_429_non_vercel_recovered"], 1)
        self.assertEqual(ai["multi_vercel_429_all_failed"], 1)
        self.assertEqual([event["event"] for event in ai["recent_events"]], ["fail"])
        rendered = json.dumps(ai)
        self.assertNotIn("SUPERSECRET_LABEL", rendered)
        self.assertNotIn("SUPERSECRET_MODEL", rendered)
        self.assertNotIn("SUPERSECRET_TOKEN", rendered)

    def test_chain_summaries_do_not_consume_recent_event_budget(self):
        events = []
        for index in range(self.collector.MAX_RECENT_EVENTS + 5):
            events.append(self.chain("vrl=2;vda=2;nfs=0;term=queue_giveup"))
            events.append(
                {
                    "ts": self.now,
                    "event": "fail",
                    "label": "RADIO:public:main",
                    "agent": f"vercel:m{index}",
                    "rc": "1",
                    "error": "failure",
                }
            )
        self.write_events(events)
        ai = self.collector._collect_ai(self.soren, self.now)
        self.assertEqual(ai["chain_summary_sampled"], self.collector.MAX_RECENT_EVENTS + 5)
        self.assertEqual(len(ai["recent_events"]), self.collector.MAX_RECENT_EVENTS)
        self.assertTrue(all(event["event"] == "fail" for event in ai["recent_events"]))

    def test_public_runtime_summary_includes_fixed_chain_counters(self):
        severity, summary = attribution.render(
            {
                "status": "warn",
                "workers": {},
                "queues": {"queue_giveups_15m": 0},
                "ai": {
                    "chain_summary_sampled": 5,
                    "multi_vercel_429_chains": 2,
                    "multi_vercel_429_non_vercel_recovered": 1,
                    "multi_vercel_429_all_failed": 1,
                    "recent_events": [],
                },
                "improvement": {},
                "corners": {},
                "storage_artifacts": {},
                "bundle_storage": {},
            }
        )
        self.assertEqual(severity, "warn")
        self.assertIn("ai_chain_summary_sampled=5", summary)
        self.assertIn("ai_multi_vercel_429_chains=2", summary)
        self.assertIn("ai_multi_vercel_429_non_vercel_recovered=1", summary)
        self.assertIn("ai_multi_vercel_429_all_failed=1", summary)
        self.assertNotIn("label=", summary)
        self.assertNotIn("model=", summary)


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)
