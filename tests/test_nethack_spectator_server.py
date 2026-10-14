from __future__ import annotations

import http.client
import json
import threading
import unittest
from urllib.request import urlopen

from docich.nethack_spectator import parse_tty
from docich.nethack_spectator_live import (
    ActiveRuntime,
    NethackFrameSnapshot,
    SnapshotStore,
)
from docich.nethack_spectator_server import NethackSpectatorFrameServer


def active_snapshot(*, captured_at: float = 100.0) -> NethackFrameSnapshot:
    runtime = ActiveRuntime(
        runtime_id="g3-deadbeef",
        generation=3,
        adapter_session="docich-game-g3",
        game_window="game-g3",
    )
    return NethackFrameSnapshot(
        runtime=runtime,
        presentation_epoch="p-test-epoch",
        capture_seq=1,
        content_seq=1,
        state="active",
        frame=parse_tty("msg\n.@!>\n.|d?\nHP:10\nDlvl:2\n", cols=8, rows=5),
        captured_monotonic=captured_at,
    )


class TestNethackSpectatorFrameServer(unittest.TestCase):
    def setUp(self) -> None:
        self.now = [100.5]
        self.server = NethackSpectatorFrameServer(
            snapshots=SnapshotStore(active_snapshot()),
            presentation_epoch="p-test-epoch",
            window_title="docich-present-g3-deadbeef",
            stale_after_ms=1000,
            now_monotonic=lambda: self.now[0],
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.close()
        self.assertFalse(self.thread.is_alive())

    def test_binds_ephemeral_ipv4_loopback_and_serves_a_fixed_shell(self) -> None:
        self.assertEqual(self.server.host, "127.0.0.1")
        self.assertGreater(self.server.port, 0)
        with urlopen(self.server.url, timeout=2) as response:
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
            self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
        shell = body.decode("utf-8")
        self.assertIn("fetch('/frame'", shell)
        self.assertIn("<title>docich-present-g3-deadbeef</title>", shell)
        self.assertIn("p-test-epoch", shell)
        self.assertNotIn("msg", shell)

    def test_frame_endpoint_serves_one_bounded_active_snapshot(self) -> None:
        with urlopen(self.server.url.rstrip("/") + "/frame", timeout=2) as response:
            payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["state"], "active")
            self.assertEqual(payload["capture_seq"], 1)
            self.assertEqual(payload["capture_age_ms"], 500)
            self.assertEqual(payload["presentation_epoch"], "p-test-epoch")
            self.assertEqual(payload["frame_kind"], "map")
            self.assertTrue(any(cell["tile_key"] == "player" for cell in payload["cells"]))

    def test_health_endpoint_omits_terminal_contents(self) -> None:
        url = f"http://127.0.0.1:{self.server.port}/health"
        with urlopen(url, timeout=2) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["state"], "active")
        self.assertNotIn("tty_lines", payload)
        self.assertNotIn("cells", payload)
        self.assertNotIn("message", payload)

    def test_stale_snapshot_expires_without_serving_old_cells(self) -> None:
        self.now[0] = 102.0
        url = f"http://127.0.0.1:{self.server.port}/frame"
        with urlopen(url, timeout=2) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["state"], "unavailable")
        self.assertEqual(payload["frame_kind"], "placeholder")
        self.assertEqual(payload["cells"], [])
        self.assertEqual(payload["reason"], "stale_timeout")

    def test_bad_host_origin_route_and_method_are_rejected(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=2)
        connection.request("GET", "/frame", headers={"Host": "example.com"})
        self.assertEqual(connection.getresponse().status, 403)
        connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=2)
        connection.request(
            "GET",
            "/frame",
            headers={
                "Host": f"127.0.0.1:{self.server.port}",
                "Origin": "https://attacker.example",
            },
        )
        self.assertEqual(connection.getresponse().status, 403)
        connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=2)
        connection.request("GET", "/private", headers={"Host": f"127.0.0.1:{self.server.port}"})
        self.assertEqual(connection.getresponse().status, 404)
        connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=2)
        connection.request("POST", "/frame", headers={"Host": f"127.0.0.1:{self.server.port}"})
        self.assertEqual(connection.getresponse().status, 405)
        connection.close()

    def test_head_does_not_return_a_body(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=2)
        connection.request("HEAD", "/frame", headers={"Host": f"127.0.0.1:{self.server.port}"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), b"")
        connection.close()

    def test_server_rejects_snapshot_store_from_a_different_epoch(self) -> None:
        with self.assertRaises(ValueError):
            NethackSpectatorFrameServer(
                snapshots=SnapshotStore(active_snapshot()),
                presentation_epoch="p-other-epoch",
            )


if __name__ == "__main__":
    unittest.main()
