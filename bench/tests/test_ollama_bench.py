"""Offline regressions for PR #1970; never contact an inference service."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench" / "tools"))
import run_ollama_bench as runner
import reaggregate_ollama_results as offline

jb = runner.jb
SUITE = ROOT / "bench" / "jev_eval_v1"
RESULTS = ROOT / "bench" / "results" / "2026-10-09_rtx3060_jev"
PAYLOAD = {"messages": [{"role": "user", "content": "synthetic comment"}]}


def case(case_id, category, tags=()):
    return {"case_id": case_id, "input": {"comment": "synthetic comment"},
            "expected": {"category": category, "screen_need": None, "intent_family": "game"},
            "tags": list(tags)}


def record(run, gold, choice, *, warmup=False):
    return {"run": run, "case_id": gold["case_id"], "warmup": warmup,
            "choice": choice, "parse_ok": choice is not None, "error": None,
            "tags": gold["tags"], "ttft_ms": 10, "total_ms": 20,
            "tokens_per_second": 50}


def summarize(records, cases):
    return jb.summarize(records, cases, model="synthetic", quantization="none",
                        backend="ollama", args=argparse.Namespace(**jb.GENERATION_DEFAULTS),
                        peak_vram=None, suite_dir=SUITE, run_dir="/private/synthetic/run",
                        started_at="synthetic", runs=len({r["run"] for r in records}))


class TestPooledScoring(unittest.TestCase):
    def test_trials_keep_predictions_across_runs_and_orders(self):
        a = case("a", "game_question", ["live-log"])
        b = case("b", "raid", ["notification"])
        warm = case("warm", "other")
        cases = [a, b, warm]
        rows = [record(1, a, "game_question"), record(1, b, "other"),
                record(2, b, "raid"), record(2, a, None),
                record(3, a, "game_question"), record(3, b, "raid")]
        rows += [record(r, warm, "other", warmup=True) for r in (1, 2, 3)]
        report = summarize(rows, cases)
        for ordered in (rows, list(reversed(rows)), rows[3:] + rows[:3]):
            actual = summarize(ordered, cases)
            for name in ("all", "no_warmup", "non_notification", "live_log"):
                self.assertEqual(actual[name], report[name])
                block = actual[name]
                self.assertEqual(sum(block["confusion"].values()), block["n"])
                self.assertEqual(sum(v["support"] for v in block["per_label"].values()), block["n"])
        self.assertEqual(report["all"]["n"], 6)
        self.assertAlmostEqual(report["all"]["coverage"], 5 / 6)
        self.assertAlmostEqual(report["all"]["accuracy"], 4 / 5)
        self.assertAlmostEqual(report["all"]["correct_fraction_all"], 4 / 6)
        self.assertEqual(report["all"]["parse_failures"], 1)
        self.assertEqual(report["non_notification"]["n"], 3)
        self.assertEqual(report["all"]["per_label"]["raid"]["support"], 3)
        self.assertAlmostEqual(report["all"]["per_label"]["raid"]["recall"], 2 / 3)


class TestNativeStream(unittest.TestCase):
    def generate(self, events):
        stream = io.BytesIO(b"".join(
            event if isinstance(event, bytes) else (json.dumps(event) + "\n").encode()
            for event in events))
        with patch.object(runner.urllib.request, "urlopen", return_value=stream) as urlopen:
            result = runner.OllamaBackend("http://synthetic.invalid", "mock", 8192).generate(PAYLOAD)
            request = urlopen.call_args.args[0]
            body = json.loads(request.data)
            self.assertEqual(body["options"]["num_ctx"], 8192)
            self.assertEqual(body["options"]["num_predict"], 64)
            return result

    def test_eof_never_becomes_a_successful_classification(self):
        valid = {"message": {"content": '{"choice":"game_question","confidence":0.9}'}}
        for events in ([], [b'{"message":'], [valid], [valid, {"done": False}],
                       [valid, {"done": "true"}]):
            with self.subTest(events=events):
                with self.assertRaises((RuntimeError, json.JSONDecodeError)):
                    self.generate(events)
                with patch.object(runner.urllib.request, "urlopen", return_value=io.BytesIO(
                    b"".join(e if isinstance(e, bytes) else (json.dumps(e) + "\n").encode()
                             for e in events))):
                    rows = jb.run_once(runner.OllamaBackend("http://synthetic.invalid", "mock", 8192),
                                       [case("a", "game_question")], system="synthetic",
                                       prompt_format="chat", chat_template=None,
                                       vram=jb.VramSampler("none"), run_index=1)
                self.assertIsNotNone(rows[0]["error"])
                self.assertFalse(rows[0]["parse_ok"])
                self.assertFalse(rows[0]["correct"])

    def test_done_with_real_usage(self):
        text, meta = self.generate([
            {"message": {"content": '{"choice":"other"}'}},
            {"done": True, "done_reason": "stop", "eval_count": 8,
             "prompt_eval_count": 20, "eval_duration": 200_000_000}])
        self.assertEqual(jb.parse_answer(text)[0], "other")
        self.assertEqual(meta["output_tokens"], 8)
        self.assertEqual(meta["usage"], {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28})
        self.assertEqual(meta["tokens_per_second"], 40)
        self.assertEqual(meta["finish_reason"], "stop")

    def test_done_without_usage_does_not_invent_tokens_or_speed(self):
        for count in (None, True, -1, "8"):
            with self.subTest(count=count):
                _, meta = self.generate([
                    {"message": {"content": '{"choice":"other"}'}},
                    {"done": True, "eval_count": count, "eval_duration": 200_000_000}])
                self.assertIsNone(meta["output_tokens"])
                self.assertIsNone(meta["usage"]["output_tokens"])
                self.assertNotIn("total_tokens", meta["usage"])
                self.assertIsNone(meta["tokens_per_second"])
        _, zero = self.generate([{"done": True, "eval_count": 0, "eval_duration": 200_000_000}])
        self.assertEqual(zero["output_tokens"], 0)
        self.assertEqual(zero["tokens_per_second"], 0)


class TestPublicArtifacts(unittest.TestCase):
    def test_projection_leaves_local_records_untouched(self):
        for private in ("/Users/synthetic/work/private", "/home/synthetic/private",
                        r"C:\Users\synthetic\private"):
            with self.subTest(private=private):
                local = {"suite": private + "/suite", "run_dir": private + "/run", "model": "mock"}
                before = copy.deepcopy(local)
                public = runner.public_report(local, suite_reference="bench/jev_eval_v1",
                                              run_reference="results:synthetic/mock")
                self.assertEqual(local, before)
                self.assertNotIn(private, json.dumps(public))
                metadata = {"suite": local["suite"], "command": private + "/run.py --out " + private,
                            "generation": jb.GENERATION_DEFAULTS, "num_ctx": 8192,
                            "runs": 3, "warmup": 3, "models": ["mock"]}
                before_meta = copy.deepcopy(metadata)
                projected = runner.public_metadata(metadata, suite_reference="bench/jev_eval_v1")
                self.assertEqual(metadata, before_meta)
                self.assertNotIn(private, json.dumps(projected))
        self.assertEqual(runner.public_suite_reference(SUITE, "sha256:synthetic"), "bench/jev_eval_v1")
        self.assertEqual(runner.public_suite_reference("/private/synthetic", "sha256:synthetic"),
                         "suite:sha256:synthetic")
        self.assertEqual(runner.public_suite_reference(r"C:\Users\synthetic\private", "sha256:synthetic"),
                         "suite:sha256:synthetic")

    def test_saved_raw_reaggregation_is_offline_idempotent_and_preserves_medians(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / RESULTS.name
            shutil.copytree(RESULTS, target)
            raw_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (target / "raw").glob("*.jsonl")}
            originals = {p.name: json.loads(p.read_text()) for p in target.glob("*.report.json")}
            expected = {c["case_id"]: c["expected"]["category"] for c in jb.load_suite(SUITE)["cases"]}
            with patch.object(runner, "http_json", side_effect=AssertionError("no API")), \
                 patch.object(runner.urllib.request, "urlopen", side_effect=AssertionError("no network")), \
                 patch.object(runner, "collect_metadata", side_effect=AssertionError("no metadata API")):
                offline.regenerate(target, SUITE)
                once = {p.name: p.read_bytes() for p in target.glob("*") if p.is_file()}
                offline.regenerate(target, SUITE)
            self.assertEqual(once, {p.name: p.read_bytes() for p in target.glob("*") if p.is_file()})
            self.assertEqual(raw_before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in (target / "raw").glob("*.jsonl")})
            for path in target.glob("*.report.json"):
                report = json.loads(path.read_text())
                self.assertEqual(report["all"]["n"], 315)
                self.assertEqual(sum(report["all"]["confusion"].values()), 315)
                self.assertEqual(sum(v["support"] for v in report["all"]["per_label"].values()), 315)
                raw_path = target / "raw" / path.name.replace(".report.json", ".raw.jsonl")
                trials = [json.loads(line) for line in raw_path.read_text().splitlines()]
                trials = [r for r in trials if not r["warmup"]]
                confusion = Counter(f"{expected[r['case_id']]}->{r['choice'] or 'unavailable'}"
                                    for r in trials)
                self.assertEqual(report["all"]["confusion"], dict(confusion))
                hits = sum(r["choice"] == expected[r["case_id"]] for r in trials)
                self.assertAlmostEqual(report["all"]["correct_fraction_all"], hits / len(trials))
                for a, b in zip(report["per_run"], originals[path.name]["per_run"]):
                    self.assertEqual({k: a[k] for k in b}, b)
                for value in ("suite", "run_dir"):
                    self.assertFalse(report[value].startswith("/"))
                if path.name.startswith("schroneko-"):
                    self.assertAlmostEqual(report["all"]["accuracy"], 157 / 315)
                    self.assertAlmostEqual(report["all"]["macro_f1"], 0.6576904727)
                if path.name.startswith("llama3.1-"):
                    from docich.comment_classifier import jev
                    notification = jev.NOTIFICATIONS
                    fp = sum(n for pair, n in report["all"]["confusion"].items()
                             if pair.split("->")[0] not in notification
                             and pair.split("->")[1] in notification)
                    self.assertEqual(fp, 6)
                    self.assertAlmostEqual(100 * fp / report["all"]["n"], 1.9047619048)
            with (target / "summary.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 5)
            for row in rows:
                self.assertEqual(row["cases_per_run"], "105")
                self.assertEqual(row["pooled_n"], "315")
            swallow = next(r for r in rows if r["model"].startswith("schroneko/"))
            self.assertAlmostEqual(float(swallow["accuracy_median"]), 0.4952380952)
            self.assertAlmostEqual(float(swallow["macro_f1_median"]), 0.6555766816)


if __name__ == "__main__":
    unittest.main()
