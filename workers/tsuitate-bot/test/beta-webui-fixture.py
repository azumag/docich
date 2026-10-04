"""Local-only transport fixture; never imported by production code."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.request

from docich import config, webui, tsuitate_beta_control as control

origin = sys.argv[1]
if not origin.startswith("http://127.0.0.1:"):
    raise ValueError("local_fixture_origin_required")
original_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class LocalOpener:
    def open(self, request, timeout):
        # Replace only fixture transport. Actual signing, response projection,
        # operator gate, CSRF and Worker verification remain production code.
        local = urllib.request.Request(origin + "/beta-control", data=request.data,
                                       method=request.method, headers=dict(request.header_items()))
        return original_opener.open(local, timeout=timeout)


control.urllib.request.build_opener = lambda *_args: LocalOpener()
os.environ.update(DOCICH_BETA_CONTROL_ENABLED="true",
                  DOCICH_BETA_CONTROL_SECRET="fixture-only-control-capability-not-credential",
                  DOCICH_BETA_CONTROL_URL="https://docich-tsuitate-bot.fixture-only.workers.dev")
with tempfile.TemporaryDirectory() as directory:
    g = config.load_global(Path(directory))
    g.webui.token = "fixture-only-operator-not-credential"
    g.webui.read_only_token = "fixture-only-viewer-not-credential"
    class Handler(webui._Handler):
        pass
    Handler.g, Handler.soren_root, Handler.read_only = g, Path(directory), False
    Handler.csrf_secret = b"fixture-csrf-only-not-a-credential"
    Handler.start_time = time.time()
    server = webui.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(json.dumps({"port": server.server_address[1]}), flush=True)
    server.serve_forever()
