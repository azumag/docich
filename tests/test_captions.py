#!/usr/bin/env python3

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from docich import captions as closed_captions  # noqa: E402


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int = -1) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class ClosedCaptionPlanTests(unittest.TestCase):
    def test_normalizes_unicode_and_wraps_to_32_by_2(self) -> None:
        pages = closed_captions.wrap_caption_pages(
            "Smart “quotes” — and café captions for everybody.",
            max_pages=1,
        )
        self.assertEqual(len(pages), 1)
        self.assertLessEqual(len(pages[0].lines), 2)
        self.assertTrue(all(len(line) <= 32 for line in pages[0].lines))
        self.assertEqual(
            " ".join(pages[0].lines),
            'Smart "quotes" - and cafe captions for everybody.',
        )

    def test_long_translation_is_rejected_in_single_page_mvp(self) -> None:
        with self.assertRaisesRegex(closed_captions.CaptionPlanError, "requires"):
            closed_captions.wrap_caption_pages("word " * 30, max_pages=1)

    def test_plan_never_exceeds_two_lines_per_page(self) -> None:
        with self.assertRaisesRegex(closed_captions.CaptionPlanError, "between 1 and 2"):
            closed_captions.wrap_caption_pages("short caption", max_lines=3)

    def test_plan_preserves_chunk_alignment(self) -> None:
        plan = closed_captions.build_caption_plan(
            ["一つ目。", "二つ目。"],
            ["The first one.", "The second one."],
            execution_id="speech-42",
        )
        self.assertEqual(plan["executionId"], "speech-42")
        self.assertEqual([chunk["index"] for chunk in plan["chunks"]], [0, 1])
        self.assertEqual(plan["chunks"][1]["pages"][0]["lines"], ("The second one.",))

    def test_invalid_execution_id_is_rejected(self) -> None:
        with self.assertRaises(closed_captions.CaptionPlanError):
            closed_captions.build_caption_plan(
                ["テスト"], ["test"], execution_id="bad id/with spaces"
            )

    def test_protocol_page_is_bounded(self) -> None:
        self.assertEqual(closed_captions.validate_page(31), 31)
        for value in (-1, 32, True):
            with self.subTest(value=value):
                with self.assertRaises(closed_captions.CaptionProtocolError):
                    closed_captions.validate_page(value)

    def test_translation_client_accepts_only_exact_json(self) -> None:
        response = {
            "choices": [{"message": {"content": '{"translations":["Hello."]}'}}]
        }
        requests = []

        def opener(request, **_kwargs):
            requests.append(json.loads(request.data.decode("utf-8")))
            return _Response(response)

        client = closed_captions.TranslationRuntimeClient(
            opener=opener
        )
        self.assertEqual(client.translate(["こんにちは。"]), ["Hello."])
        self.assertEqual(requests[0]["response_format"], {"type": "json_object"})
        self.assertEqual(requests[0]["reasoning_effort"], "none")
        self.assertEqual(
            requests[0]["allowed_openai_params"], ["reasoning_effort"]
        )

    def test_translation_client_disables_reasoning_only_for_minimax_m3(self) -> None:
        response = {
            "choices": [{"message": {"content": '{"translations":["Hello."]}'}}]
        }
        requests = []

        def opener(request, **_kwargs):
            requests.append(json.loads(request.data.decode("utf-8")))
            return _Response(response)

        client = closed_captions.TranslationRuntimeClient(
            models=("deepseek-v4-flash",), opener=opener
        )
        self.assertEqual(client.translate(["こんにちは。"]), ["Hello."])
        self.assertNotIn("reasoning_effort", requests[0])
        self.assertNotIn("allowed_openai_params", requests[0])

    def test_translation_client_retries_overlong_translation(self) -> None:
        overlong = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"translations": ["x" * 65]})
                    }
                }
            ]
        }
        valid = {
            "choices": [{"message": {"content": '{"translations":["Short."]}'}}]
        }
        responses = iter((_Response(overlong), _Response(valid)))
        calls = []

        def opener(*_args, **_kwargs):
            calls.append(1)
            return next(responses)

        client = closed_captions.TranslationRuntimeClient(
            attempts_per_model=2, opener=opener
        )
        self.assertEqual(client.translate(["長い字幕です。"]), ["Short."])
        self.assertEqual(len(calls), 2)

    def test_translation_client_rejects_thinking_around_json(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": 'I should translate this.\n{"translations":["Hello."]}'
                    }
                }
            ]
        }
        client = closed_captions.TranslationRuntimeClient(
            opener=lambda *_args, **_kwargs: _Response(response)
        )
        with self.assertRaisesRegex(closed_captions.CaptionPlanError, "all translation models failed"):
            client.translate(["こんにちは。"])

    def test_translation_client_retries_without_accepting_malformed_output(self) -> None:
        malformed = {
            "choices": [
                {
                    "message": {
                        "content": 'thinking\n{"translations":["Hidden."]}'
                    }
                }
            ]
        }
        valid = {
            "choices": [{"message": {"content": '{"translations":["Hello."]}'}}]
        }
        responses = iter((_Response(malformed), _Response(valid)))
        calls = []

        def opener(*_args, **_kwargs):
            calls.append(1)
            return next(responses)

        client = closed_captions.TranslationRuntimeClient(
            attempts_per_model=2,
            opener=opener,
        )
        self.assertEqual(client.translate(["こんにちは。"]), ["Hello."])
        self.assertEqual(len(calls), 2)

    def test_translation_client_does_not_retry_transport_failure(self) -> None:
        calls = []

        def opener(*_args, **_kwargs):
            calls.append(1)
            raise TimeoutError("translation endpoint timed out")

        client = closed_captions.TranslationRuntimeClient(
            attempts_per_model=3,
            opener=opener,
        )
        with self.assertRaisesRegex(
            closed_captions.CaptionPlanError, "all translation models failed"
        ):
            client.translate(["こんにちは。"])
        self.assertEqual(len(calls), 1)

    def test_translation_client_rejects_extra_schema_keys(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": '{"translations":["Hello."],"thinking":"hidden"}'
                    }
                }
            ]
        }
        client = closed_captions.TranslationRuntimeClient(
            opener=lambda *_args, **_kwargs: _Response(response)
        )
        with self.assertRaisesRegex(closed_captions.CaptionPlanError, "all translation models failed"):
            client.translate(["こんにちは。"])

    def test_translation_client_rejects_duplicate_schema_keys(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": '{"translations":["Hidden."],"translations":["Hello."]}'
                    }
                }
            ]
        }
        client = closed_captions.TranslationRuntimeClient(
            opener=lambda *_args, **_kwargs: _Response(response)
        )
        with self.assertRaisesRegex(
            closed_captions.CaptionPlanError, "all translation models failed"
        ):
            client.translate(["こんにちは。"])

    def test_translation_endpoint_cannot_escape_loopback_with_userinfo(self) -> None:
        for endpoint in (
            "https://127.0.0.1:4100/v1/chat/completions",
            "http://127.0.0.1:4100@evil.example/v1/chat/completions",
            "http://evil.example:4100/v1/chat/completions",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(closed_captions.CaptionPlanError):
                    closed_captions.TranslationRuntimeClient(endpoint=endpoint)

    def test_load_plan_rejects_extra_keys_and_page_tampering(self) -> None:
        plan = closed_captions.build_caption_plan(
            ["テスト。"], ["Safe caption."], execution_id="speech-guard"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "plan.json"

            extra = dict(plan)
            extra["thinking"] = "do not display"
            path.write_text(json.dumps(extra), encoding="utf-8")
            with self.assertRaisesRegex(
                closed_captions.CaptionPlanError, "exact schema"
            ):
                closed_captions.load_plan(path)

            tampered = json.loads(json.dumps(plan))
            tampered["chunks"][0]["pages"][0]["lines"] = ["x" * 33]
            path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(
                closed_captions.CaptionPlanError, "exceeds its bounds"
            ):
                closed_captions.load_plan(path)

    def test_invalid_translation_runtime_env_is_caption_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chunks = root / "chunks.txt"
            output = root / "plan.json"
            chunks.write_text("テスト。\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {"DOCICH_CC_TRANSLATION_TIMEOUT_SEC": "not-a-number"},
            ):
                with self.assertRaisesRegex(
                    closed_captions.CaptionPlanError, "must be numeric"
                ):
                    closed_captions.main(
                        [
                            "plan",
                            "--chunks-file",
                            str(chunks),
                            "--execution-id",
                            "speech-env",
                            "--output",
                            str(output),
                        ]
                    )


class CaptionSocketClientTests(unittest.TestCase):
    def _serve_once(self, socket_path: Path, requests: list[dict[str, object]]) -> threading.Thread:
        ready = threading.Event()

        def server() -> None:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(socket_path))
                listener.listen(1)
                ready.set()
                connection, _ = listener.accept()
                with connection:
                    raw = b""
                    while b"\n" not in raw:
                        raw += connection.recv(4096)
                    request = json.loads(raw.split(b"\n", 1)[0])
                    requests.append(request)
                    connection.sendall(b'{"v":1,"event":"accepted"}\n')
                    event = {
                        "prepare": "prepared",
                        "commit": "committed",
                        "clear": "cleared",
                        "reset": "reset",
                    }[str(request["op"])]
                    connection.sendall(
                        json.dumps({"v": 1, "event": event}).encode("utf-8") + b"\n"
                    )

        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(2))
        return thread

    def test_prepare_uses_base64_and_waits_for_prepared(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            path = Path(temp_dir) / "cc.sock"
            requests: list[dict[str, object]] = []
            thread = self._serve_once(path, requests)
            client = closed_captions.CaptionSocketClient(str(path), timeout=2)
            response = client.prepare("speech-42", 0, "Hello\nworld")
            thread.join(2)
            self.assertEqual(response["event"], "prepared")
            self.assertEqual(requests[0]["executionId"], "speech-42")
            decoded = base64.b64decode(str(requests[0]["textBase64"])).decode("ascii")
            self.assertEqual(decoded, "Hello\nworld")

    def test_prepare_rejects_text_outside_32_by_2_before_connecting(self) -> None:
        client = closed_captions.CaptionSocketClient("/tmp/not-used.sock", timeout=2)
        for text in ("x" * 33, "one\ntwo\nthree"):
            with self.subTest(text=text):
                with self.assertRaises(closed_captions.CaptionProtocolError):
                    client.prepare("speech-bounds", 0, text)

    def test_server_error_is_fail_open_exception(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            path = Path(temp_dir) / "cc.sock"
            ready = threading.Event()

            def server() -> None:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    listener.bind(str(path))
                    listener.listen(1)
                    ready.set()
                    connection, _ = listener.accept()
                    with connection:
                        connection.recv(4096)
                        connection.sendall(
                            b'{"v":1,"event":"error","code":"STALE_EXECUTION","message":"old"}\n'
                        )

            thread = threading.Thread(target=server, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(2))
            client = closed_captions.CaptionSocketClient(str(path), timeout=2)
            with self.assertRaisesRegex(closed_captions.CaptionProtocolError, "STALE_EXECUTION"):
                client.clear("speech-old")
            thread.join(2)

    def test_response_protocol_version_must_match(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            path = Path(temp_dir) / "cc.sock"
            ready = threading.Event()

            def server() -> None:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    listener.bind(str(path))
                    listener.listen(1)
                    ready.set()
                    connection, _ = listener.accept()
                    with connection:
                        connection.recv(4096)
                        connection.sendall(b'{"v":2,"event":"reset"}\n')

            thread = threading.Thread(target=server, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(2))
            client = closed_captions.CaptionSocketClient(str(path), timeout=2)
            with self.assertRaisesRegex(
                closed_captions.CaptionProtocolError, "protocol version"
            ):
                client.reset()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
