import json
import threading
import unittest
import urllib.error
import urllib.request

from docich import captions
from docich.cc_translation_adapter import make_server, parse_upstream


def _post(url, payload, *, raw=None):
    data = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class CcTranslationAdapterTest(unittest.TestCase):
    def setUp(self):
        self.prompts = []
        self.result = ("", "")

        def translate(prompt, timeout):
            self.prompts.append((prompt, timeout))
            return self.result

        self.server = make_server(translate, port=0, timeout=7.0, upstream="vercel-api:m")
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.port}/v1/chat/completions"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _payload(self, content="hello"):
        return {
            "model": "ignored-by-adapter",
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
        }

    def test_success_returns_openai_shaped_content_and_forwards_timeout(self):
        self.result = ('{"translations":["hi"]}', "")
        status, body = _post(self.url, self._payload("ja"))
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], '{"translations":["hi"]}')
        self.assertEqual(self.prompts, [("ja", 7.0)])

    def test_failure_returns_502_with_kind_only(self):
        self.result = ("", "rate_limit")
        status, body = _post(self.url, self._payload("secret prompt text"))
        self.assertEqual(status, 502)
        self.assertEqual(body, {"error": {"type": "rate_limit"}})
        self.assertNotIn("secret prompt text", json.dumps(body))

    def test_rejects_unknown_path_and_bad_bodies_without_calling_upstream(self):
        url = f"http://127.0.0.1:{self.port}/other"
        status, body = _post(url, self._payload())
        self.assertEqual((status, body["error"]["type"]), (404, "not_found"))
        for payload in (
            {"messages": []},
            {"messages": [{"role": "system", "content": "x"}]},
            {"messages": [{"role": "user", "content": 1}]},
        ):
            status, body = _post(self.url, payload)
            self.assertEqual((status, body["error"]["type"]), (400, "invalid_request"))
        status, body = _post(self.url, None, raw=b"not json")
        self.assertEqual((status, body["error"]["type"]), (400, "invalid_request"))
        self.assertEqual(self.prompts, [])

    def test_healthz_reports_upstream_name_only(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/healthz", timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(body, {"status": "ok", "upstream": "vercel-api:m"})

    def test_bind_is_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_upstream_must_be_direct_chat_provider(self):
        spec = parse_upstream("vercel-api:some-model")
        self.assertEqual((spec.provider, spec.model), ("vercel-api", "some-model"))
        for raw in ("opencode:x", "vercel-api:", "vercel-api", ""):
            with self.assertRaises(ValueError):
                parse_upstream(raw)

    def test_timeout_bounds(self):
        with self.assertRaises(ValueError):
            make_server(lambda p, t: ("", ""), port=0, timeout=60)

    def test_existing_caption_client_works_against_adapter(self):
        self.result = ('{"translations":["Hello there"]}', "")
        client = captions.TranslationRuntimeClient(
            endpoint=self.url, models=("upstream-model",), timeout=5.0, attempts_per_model=1
        )
        self.assertEqual(client.translate(["こんにちは"], max_chars=64), ["Hello there"])


if __name__ == "__main__":
    unittest.main()
