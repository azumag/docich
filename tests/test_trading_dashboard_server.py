import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading import dashboard_server


def test_server_serves_page_snapshot_and_is_read_only(tmp_path):
    trading_dir = tmp_path / "trading"
    trading_dir.mkdir(parents=True)
    (trading_dir / "status.json").write_text(
        json.dumps({"worker_state": "running", "capital_reference": "10000",
                    "eligible_symbols": ["btc_jpy"]}),
        encoding="utf-8",
    )
    (trading_dir / "market_cache.json").write_text(
        json.dumps({"symbols": {"btc_jpy": {"closes": [1, 2, 3], "fetched_at": 1.0}}}),
        encoding="utf-8",
    )
    handler = dashboard_server.make_handler(trading_dir)
    httpd = dashboard_server._ReusableServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        html = urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8")
        assert "PAPER" in html and "dashboard.js" in html

        snapshot = json.loads(urllib.request.urlopen(base + "/api/trading/dashboard", timeout=5).read())
        assert snapshot["schema_version"] == 1
        assert snapshot["portfolio"]["capital_jpy"] == "10000"
        assert snapshot["chart"]["closes"] == [1.0, 2.0, 3.0]

        js = urllib.request.urlopen(base + "/dashboard.js", timeout=5).read().decode("utf-8")
        assert "api/trading/dashboard" in js

        for method in ("POST", "PUT", "DELETE"):
            req = urllib.request.Request(base + "/api/trading/dashboard", data=b"x", method=method)
            try:
                urllib.request.urlopen(req, timeout=5)
                raise AssertionError(f"{method} must be refused")
            except urllib.error.HTTPError as exc:
                assert exc.code == 405

        try:
            urllib.request.urlopen(base + "/nope", timeout=5)
            raise AssertionError("unknown path must be 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
