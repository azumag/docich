"""Tests for the Jev local-LLM benchmark harness (#1263).

Run with the repo's normal test command::

    env -u PYTHONPATH python3 -m pytest -q bench/tests/test_jev_bench.py

They are hermetic: the dummy backend needs no GPU, and the OpenAI backend is
exercised against a local SSE mock so the streaming/TTFT path is covered without
a real model server.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

SUITE = REPO_ROOT / "bench" / "jev_eval_v1"

_spec = importlib.util.spec_from_file_location("jev_bench", REPO_ROOT / "bench" / "jev_bench.py")
jev_bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev_bench)


class TestSuite(unittest.TestCase):
    def setUp(self):
        self.loaded = jev_bench.load_suite(SUITE)

    def test_counts_and_all_labels_present(self):
        cases = self.loaded["cases"]
        self.assertGreaterEqual(len(cases), 50)
        self.assertLessEqual(len(cases), 200)
        labels = {case["expected"]["category"] for case in cases}
        expected = {"card_gacha", "raid", "subscription", "stream_goal", "bits",
                    "sing_request", "game_question", "game_status", "general_question",
                    "strategy_advice", "comment_advice", "stream_bug_report", "chitchat", "other"}
        self.assertEqual(labels, expected)

    def test_no_handle_or_private_path_leaks(self):
        for name in ("public_cases.jsonl", "critical_cases.jsonl"):
            for line in (SUITE / name).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                case = json.loads(line)
                comment = case["input"]["comment"]
                self.assertIsNone(re.search(r"@[^\s@]{1,32}", comment), comment)
                for banned in ("/home/", "/Users/", "gho_", "sk-"):
                    self.assertNotIn(banned, comment)

    def test_every_case_is_a_valid_eval_case(self):
        from docich.eval import contracts
        for case in self.loaded["cases"]:
            contracts.validate_case(case)
            contracts.assert_public_safe(case)


class TestPromptAndParsing(unittest.TestCase):
    def test_system_prompt_contains_the_live_rubric(self):
        text = jev_bench.build_system_prompt()
        from docich.comment_classifier import jev
        for label in jev.CRITERIA:
            self.assertIn(label, text)
        self.assertIn('"choice"', text)

    def test_parse_answer(self):
        self.assertEqual(jev_bench.parse_answer('{"choice": "chitchat", "confidence": 0.8}')[0],
                         "chitchat")
        self.assertEqual(jev_bench.parse_answer('noise {"choice": "other"} tail')[0], "other")
        choice, _conf, ok = jev_bench.parse_answer('{"choice": "not_a_label"}')
        self.assertIsNone(choice)
        self.assertFalse(ok)
        self.assertEqual(jev_bench.parse_answer("")[2], False)

    def test_template_prompt_format_uses_placeholders(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("<|sys|>{{ system }}<|user|>{{ user }}<|bot|>")
            path = handle.name
        payload = jev_bench.render_prompt("SYS", "USER", prompt_format="template",
                                          chat_template=path)
        self.assertEqual(payload["prompt"], "<|sys|>SYS<|user|>USER<|bot|>")


class _StreamingHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in ('{"choice":', ' "chitchat",', ' "confidence": 0.7}'):
            self.wfile.write(f'data: {json.dumps({"choices": [{"delta": {"content": piece}}]})}\n\n'
                             .encode())
            self.wfile.flush()
        self.wfile.write(b'data: {"choices": [], "usage": {"completion_tokens": 3}}\n\n')
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *args):
        pass


class TestBackends(unittest.TestCase):
    def test_dummy_backend_is_deterministic(self):
        backend = jev_bench.DummyBackend("dummy", seed=7)
        payload = {"messages": [{"role": "system", "content": "s"},
                                {"role": "user", "content": "u\n\ncomment"}],
                   "_expected": "chitchat"}
        first = backend.generate(payload)[0]
        second = backend.generate(payload)[0]
        self.assertEqual(first, second)

    def test_openai_backend_streaming_measures_ttft(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _StreamingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            backend = jev_bench.OpenAIBackend(
                f"http://127.0.0.1:{server.server_address[1]}/v1", "mock")
            text, meta = backend.generate({"messages": [
                {"role": "system", "content": "s"}, {"role": "user", "content": "u"}]})
        finally:
            server.shutdown()
        self.assertIn('"chitchat"', text)
        self.assertEqual(jev_bench.parse_answer(text)[0], "chitchat")
        self.assertGreaterEqual(meta["ttft_ms"], 0)
        self.assertGreaterEqual(meta["total_ms"], meta["ttft_ms"])
        self.assertEqual(meta["usage"]["completion_tokens"], 3)


class TestEndToEnd(unittest.TestCase):
    def test_dummy_run_writes_csv_and_report(self):
        with tempfile.TemporaryDirectory() as out:
            code = jev_bench.main(["--suite", str(SUITE), "--backend", "dummy",
                                   "--model", "dummy-unit", "--quantization", "none",
                                   "--runs", "1", "--warmup", "1", "--out", out])
            self.assertEqual(code, 0)
            run_dirs = list(Path(out).glob("*/*"))
            self.assertEqual(len(run_dirs), 1)
            run_dir = run_dirs[0]
            for name in ("config.json", "report.json", "raw.jsonl", "outputs.jsonl",
                         "per_case.csv", "summary.csv"):
                self.assertTrue((run_dir / name).is_file(), name)
            header = (run_dir / "summary.csv").read_text(encoding="utf-8").splitlines()[0]
            for column in ("model", "quantization", "accuracy_all", "ttft_p50_ms",
                           "total_p95_ms", "tokens_per_second_median", "peak_vram_mib"):
                self.assertIn(column, header)
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            self.assertIn("all", report)
            self.assertEqual(report["all"]["n"], 107)  # 108 cases - 1 warmup


if __name__ == "__main__":
    unittest.main()
