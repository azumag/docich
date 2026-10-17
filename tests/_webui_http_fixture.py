"""実 HTTP fixture を「応答後の書込み」と競合させない共有ヘルパー (issue #2346)。

``http.server.ThreadingHTTPServer`` は ``daemon_threads = True`` で、
``socketserver.ThreadingMixIn._Threads`` は daemon thread を追跡対象から外す。
そのため ``server_close()`` は handler thread を join せず、``shutdown()`` は
accept loop しか止めない。受付けた request の handler が応答送信後も書込みを
続けている (例: ``_guard_mutation`` の authorization audit event、webui.log)
場合、``TemporaryDirectory.cleanup()`` の rmtree と競合して
``OSError [Errno 39] Directory not empty`` になる (PR #2340 の full-suite flake)。

このモジュールは PR #2340 で ``tests/test_webui.py`` に入れた追跡つき server と
後片付けヘルパーを、同じ構造が残る実 HTTP fixture でも使えるように共有化した
もの。対象は ``tests/conftest.py`` が poll interval を短縮する 3 ファイル:

- ``tests/test_webui.py``
- ``tests/test_webui_resources.py``
- ``tests/test_tsuitate_beta_control.py``

production コード (``src/docich/webui.py`` の ``daemon_threads`` /
``block_on_close``) は変更しない。fixture 用 server にだけ追跡を入れ、待ちを
挟んでから tmp を消す。
"""

from __future__ import annotations

import threading
import time

from docich import webui

__all__ = (
    "tracking_server_class",
    "shutdown_test_server",
    "assert_no_stuck_handlers",
)


def tracking_server_class():
    """handler thread を追跡して明示的に待てる ThreadingHTTPServer を作る。

    ``http.server.ThreadingHTTPServer`` は ``daemon_threads = True`` で、
    ``ThreadingMixIn`` の ``_Threads`` は daemon thread を join 対象にしない。
    そのため ``server_close()`` は「応答送信後も書込みを続けている handler」
    (例: ``_guard_mutation`` の audit event、webui.log) を待たない。受け付けた
    request をここで追跡し、tmp を消す前に完了を待てるようにする。

    conftest が ``webui.ThreadingHTTPServer`` を poll interval 短縮版へ
    monkeypatch するため、基底クラスは import 時ではなく呼び出し時に取得する
    (追跡は fixture 用サーバーにだけ入れ、production の設定は変えない)。
    """

    base = webui.ThreadingHTTPServer

    class _TrackingHTTPServer(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._handler_threads = []
            self._handler_threads_lock = threading.Lock()

        def process_request(self, request, client_address):
            thread = threading.Thread(
                target=self.process_request_thread,
                args=(request, client_address),
                daemon=True,
                name="test-webui-handler",
            )
            with self._handler_threads_lock:
                self._handler_threads.append(thread)
            thread.start()

        def wait_for_handlers(self, timeout=5.0):
            """accept 済み request の handler が終わるまで待つ。

            期限内に終わらなかった thread を返す (空 list なら全て完了)。
            """
            deadline = time.monotonic() + timeout
            while True:
                with self._handler_threads_lock:
                    pending = [t for t in self._handler_threads if t.is_alive()]
                    self._handler_threads = pending
                if not pending:
                    return []
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return pending
                for thread in pending:
                    thread.join(max(0.0, deadline - time.monotonic()))

    return _TrackingHTTPServer


def shutdown_test_server(server, serve_thread, *, timeout=5.0):
    """実 HTTP fixture の server を止め、in-flight handler の完了まで待つ。

    戻り値は期限内に終わらなかった handler thread (空 list なら全て完了)。
    ``shutdown()`` は accept loop しか止めず、daemon handler は ``server_close()``
    でも join されないため、この待ちを挟まずに ``TemporaryDirectory.cleanup()``
    すると rmtree と応答後の書込みが競合する (``OSError [Errno 39]``)。
    """
    server.shutdown()
    server.server_close()
    serve_thread.join(timeout=timeout)
    return server.wait_for_handlers(timeout=timeout)


def assert_no_stuck_handlers(case, stuck, *, timeout=5.0):
    """期限内に終わらなかった handler を黙って消さずテスト失敗として報告する。

    ``shutdown_test_server`` の戻り値をそのまま渡す。``case`` は
    ``assertEqual`` を持つ unittest 互換オブジェクト。
    """
    case.assertEqual(
        [], stuck,
        f"in-flight handler did not finish within {timeout}s before cleanup "
        f"(stuck: {[t.name for t in stuck]})",
    )
