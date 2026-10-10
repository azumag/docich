"""Synthetic regressions for inherited #1933/#1934 findings; no inference."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "bench" / "tools"))
import jev_bench as jb
import build_jev_suite as builder
import smoke_ollama as smoke
import reaggregate_ollama_results as offline


class Response(io.BytesIO):
    def __init__(self, data, content_type="text/event-stream"):
        super().__init__(data)
        self.headers = {"Content-Type": content_type}


def sse(events):
    return Response(b"".join(("data: " + (e if isinstance(e, str) else json.dumps(e)) + "\n\n").encode()
                             for e in events))


ANSWER = '{"choice":"other","confidence":0.8}'
CONTENT = {"choices": [{"delta": {"content": ANSWER}}]}
CASE = {"case_id": "synthetic", "input": {"comment": "synthetic body"},
        "expected": {"category": "other", "intent_family": "other", "screen_need": None}, "tags": []}


class TestOpenAIContract(unittest.TestCase):
    def test_cli_request_config_and_report_agree_for_chat_and_template(self):
        for mode in ("chat", "template"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                template = Path(tmp) / "template.txt"
                template.write_text("{{ system }}\n{{ user }}")
                responses = []
                def respond(request, **kwargs):
                    responses.append(json.loads(request.data))
                    return sse([CONTENT, "[DONE]"])
                with patch.object(jb, "load_suite", return_value={"cases": [CASE], "manifest": {}}), \
                     patch.object(jb.urllib.request, "urlopen", side_effect=respond), \
                     contextlib.redirect_stdout(io.StringIO()):
                    jb.main(["--backend", "openai", "--model", "mock", "--vram-source", "none",
                             "--temperature", "0.25", "--top-p", "0.75", "--max-tokens", "17",
                             "--seed", "123", "--runs", "2", "--out", tmp,
                             "--prompt-format", mode, "--chat-template", str(template)])
                run_dir = next(Path(tmp).glob("*/default"))
                config = json.loads((run_dir / "config.json").read_text())
                report = json.loads((run_dir / "report.json").read_text())
                for body in responses:
                    for key, value in report["generation"].items():
                        self.assertEqual(body[key], value)
                        self.assertEqual(body[key], config[key])
                    self.assertTrue(body["stream"])
                    self.assertNotIn("_expected", body)
                self.assertEqual(len(responses), 2)
                self.assertEqual(report["all"]["n"], 2)
                self.assertEqual(sum(report["all"]["confusion"].values()), 2)
                self.assertEqual(len(report["per_run"]), 2)
                self.assertEqual(len((run_dir / "summary.csv").read_text().splitlines()), 3)
                outputs = [json.loads(x) for x in (run_dir / "outputs.jsonl").read_text().splitlines()]
                self.assertEqual([x["run"] for x in outputs], [1, 2])

    def test_extra_body_cannot_override_reported_generation(self):
        for key in (*jb.GENERATION_DEFAULTS, "stream", "model"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                jb.OpenAIBackend("http://synthetic.invalid", "mock", extra_body={key: 999})

    def test_dummy_cli_seed_is_applied(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(jb, "load_suite", return_value={"cases": [CASE], "manifest": {}}), \
             patch.object(jb, "DummyBackend", wraps=jb.DummyBackend) as factory, \
             contextlib.redirect_stdout(io.StringIO()):
            jb.main(["--seed", "123", "--out", tmp, "--vram-source", "none"])
        factory.assert_called_once_with("dummy-1b", seed=123)

    def test_template_json_response_is_read_and_ttft_is_unknown(self):
        event = {"choices": [{"text": ANSWER, "finish_reason": "stop"}],
                 "usage": {"completion_tokens": 7}}
        with patch.object(jb.urllib.request, "urlopen", return_value=Response(
                json.dumps(event).encode(), "application/json")) as urlopen:
            text, meta = jb.OpenAIBackend("http://synthetic.invalid", "mock",
                                          prompt_format="template").generate({"prompt": "synthetic"})
        self.assertEqual(jb.parse_answer(text)[0], "other")
        self.assertTrue(urlopen.call_args.args[0].full_url.endswith("/completions"))
        self.assertTrue(json.loads(urlopen.call_args.args[0].data)["stream"])
        self.assertEqual(meta["output_tokens"], 7)
        self.assertIsNone(meta["ttft_ms"])

    def test_partial_valid_answer_with_error_or_eof_is_a_miss(self):
        for events in ([], [CONTENT], [CONTENT, {"error": {"message": "failed"}}],
                       [CONTENT, "{malformed"], [CONTENT, {"choices": []}]):
            with self.subTest(events=events), patch.object(jb.urllib.request, "urlopen", return_value=sse(events)):
                row = jb.run_once(jb.OpenAIBackend("http://synthetic.invalid", "mock"), [CASE],
                                  system="synthetic", prompt_format="chat", chat_template=None,
                                  vram=jb.VramSampler("none"), run_index=1)[0]
                self.assertIsNotNone(row["error"])
                self.assertFalse(row["parse_ok"])
                self.assertFalse(row["correct"])
                self.assertEqual(row["response_text"], "")

    def test_usage_missing_or_invalid_never_becomes_measured_tokens(self):
        for count in (None, True, -1, "7", 7, 0):
            with self.subTest(count=count), patch.object(jb.urllib.request, "urlopen", return_value=sse([
                    CONTENT, {"choices": [], "usage": {"completion_tokens": count}}, "[DONE]"])):
                _, meta = jb.OpenAIBackend("http://synthetic.invalid", "mock").generate({"messages": []})
                expected = count if type(count) is int and count >= 0 else None
                self.assertEqual(meta["output_tokens"], expected)
                self.assertEqual(meta["tokens_per_second"] is None, expected is None)


class TestSmokeContract(unittest.TestCase):
    def call(self, events):
        data = b"".join((json.dumps(e) + "\n").encode() for e in events)
        with patch.object(smoke.urllib.request, "urlopen", return_value=io.BytesIO(data)):
            return smoke.stream_chat("http://synthetic.invalid", "mock", 32768)

    def test_rejects_missing_terminal_empty_and_invalid_contract(self):
        valid = {"message": {"content": ANSWER}}
        fixtures = [[], [valid], [valid, {"done": "true"}], [{"done": True}],
                    [valid, {"error": "failed"}]]
        for answer in ({"choice": "unknown", "confidence": 0.5},
                       *({"choice": "other", "confidence": c}
                         for c in (None, True, False, "0.5", -0.1, 1.1, float("nan"), float("inf"))),
                       ["other"], "other"):
            fixtures.append([{"message": {"content": json.dumps(answer)}}, {"done": True}])
        for events in fixtures:
            with self.subTest(events=events), self.assertRaises((RuntimeError, ValueError)):
                self.call(events)

    def test_success_requires_valid_json_and_missing_usage_is_unknown(self):
        result = self.call([{"message": {"content": ANSWER}}, {"done": True}])
        self.assertTrue(result["stream_complete"])
        self.assertEqual(result["choice"], "other")
        self.assertEqual(result["confidence"], 0.8)
        self.assertIsNone(result["eval_count"])
        self.assertIsNone(result["eval_tok_per_s"])

    def test_requested_context_is_distinct_from_loaded_and_missing_is_unknown(self):
        for loaded_ctx in (8192, None):
            with self.subTest(loaded_ctx=loaded_ctx):
                def http(url, *args, **kwargs):
                    if url.endswith("/api/version"): return {"version": "synthetic"}
                    if url.endswith("/api/tags"): return {"models": [{"name": "mock", "size": 1}]}
                    if url.endswith("/api/ps"):
                        return {"models": [{"name": "mock", "size": 1, "size_vram": 1,
                                            "context_length": loaded_ctx}]}
                    return {}
                out = io.StringIO()
                with patch.object(smoke, "http_json", side_effect=http), \
                     patch.object(smoke, "stream_chat", return_value={"response": ANSWER}), \
                     contextlib.redirect_stdout(out):
                    smoke.main(["--model", "mock", "--num-ctx", "32768", "--warmup", "0"])
                row = json.loads(out.getvalue())
                self.assertEqual(row["requested_num_ctx"], 32768)
                self.assertEqual(row["loaded_context_length"], loaded_ctx)

    def test_main_reports_invalid_response_as_error(self):
        with patch.object(smoke, "http_json", return_value={}), \
             patch.object(smoke, "stream_chat", side_effect=ValueError("invalid response")), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            smoke.main(["--model", "mock", "--warmup", "0"])
        self.assertEqual(json.loads(out.getvalue())["status"], "error")


class TestProjectionAndSavedIdentity(unittest.TestCase):
    def test_card_notification_removes_poster_and_recipient(self):
        for title in ("X", "X: Y"):
            row = {"batch_line": f"poster_demo: @recipient_demo が 【{title}】カードを獲得しました",
                   "comment": "mangled", "category": "card_gacha"}
            cases = builder.build_cases([row])
            text = next(c["text"] for c in cases if c["origin"] == "live-log")
            self.assertEqual(text, f"[user] が 【{title}】カードを獲得しました")
            self.assertNotIn("poster_demo", text)
            self.assertNotIn("recipient_demo", text)
            builder.contracts.assert_public_safe(builder.to_case(1, cases[0]))

    def test_saved_identity_rejects_missing_duplicate_reordered_and_wrong_gold(self):
        a = {**CASE, "case_id": "a"}
        b = {**CASE, "case_id": "b"}
        rows = [{"run": 1, "case_id": c["case_id"], "warmup": i == 0,
                 "category_expected": "other", "tags": []} for i, c in enumerate([a, b])]
        metadata = {"runs": 1, "warmup": 1}
        offline.validate_records(rows, [a, b], metadata)
        fixtures = [rows[:1], [rows[0], rows[0]], list(reversed(rows)),
                    [rows[0], {**rows[1], "category_expected": "raid"}],
                    [rows[0], {**rows[1], "warmup": True}],
                    [rows[0], {**rows[1], "tags": ["live-log"]}]]
        for invalid in fixtures:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                offline.validate_records(invalid, [a, b], metadata)


if __name__ == "__main__":
    unittest.main()
