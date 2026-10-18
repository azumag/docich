"""Read-only, loopback-only TRPG/murder-mystery presentation view.

No live stream, queue, worker, model, warning generator or scheduler is touched.
The view renders the current scene from a validated payload written by the
corner manager. Browser content has a separate monotonic expiry so a dead
server cannot leave a stale scene on air.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import time
import uuid

VIEW_NAME = "trpg-view"
PAYLOAD_FILENAME = "trpg_view.json"
DEFAULT_PORT = 8812

MAX_PAYLOAD_BYTES = 256 * 1024

HTML = r'''<!doctype html>
<html lang="ja" translate="no"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="google" content="notranslate">
<title>docich TRPG / マーダーミステリー</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#0b1220;color:#edf5fa;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
#stage{position:absolute;width:960px;height:540px;transform-origin:top left;padding:18px 22px;background:radial-gradient(ellipse at 40% 35%,#1a2a44 0,#0b1526 60%,#070d18 100%);overflow:hidden}
h1{font-size:26px;letter-spacing:.06em;margin:0 0 6px;font-weight:750}
.phase{font-size:11px;color:#92d8de;letter-spacing:.18em;margin-bottom:10px}
.scene{font-size:16px;line-height:1.6;margin:0 0 12px;min-height:60px}
.choices{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
.choice{border:1px solid #315166;border-radius:8px;background:#10283a;padding:10px 12px;font-size:13px}
.choice .n{color:#ffd178;font-weight:700;margin-right:6px}
.votes{font-size:11px;color:#a5c3ce;margin-left:8px}
.status{font-size:11px;color:#9eb5c4;letter-spacing:.04em}
#unavailable{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:#07111de8;color:#f0d9ad;text-align:center;font-size:20px;line-height:1.7;letter-spacing:.06em;padding:40px}
#unavailable[hidden]{display:none}
</style>
<div id="stage">
<h1>参加型 TRPG / マーダーミステリー</h1>
<div class="phase" id="phase">準備中</div>
<p class="scene" id="scene">シーンを読み込み中...</p>
<div class="choices" id="choices"></div>
<div class="status" id="status">投票: !1〜!N / 推理: !accuse &lt;容疑者&gt; &lt;トリック&gt;</div>
</div>
<div id="unavailable" role="status">TRPGコーナーを準備しています</div>
<script>
'use strict';
let view=null,pollVersion=0,until=0;
function fit(){const el=document.getElementById('stage');const s=Math.min(innerWidth/960,innerHeight/540);el.style.transform=`scale(${s})`;el.style.left=`${(innerWidth-960*s)/2}px`;el.style.top=`${(innerHeight-540*s)/2}px`;}
addEventListener('resize',fit);fit();
function hide(m){document.getElementById('unavailable').hidden=false;document.getElementById('unavailable').textContent=m;}
function render(){
 if(!view){hide('TRPGコーナーを準備しています');return;}
 document.getElementById('unavailable').hidden=true;
 document.getElementById('phase').textContent=view.phase||'—';
 document.getElementById('scene').textContent=view.scene_text||'';
 const c=document.getElementById('choices');c.replaceChildren();
 (view.choices||[]).forEach((ch,i)=>{
   const d=document.createElement('div');d.className='choice';
   d.innerHTML=`<span class="n">${i+1}.</span>${ch}<span class="votes">(${(view.votes||{})[i+1]||0}票)</span>`;
   c.append(d);
 });
 document.getElementById('status').textContent=view.status||'';
}
async function poll(){
 const v=++pollVersion;
 try{const r=await fetch('/api/trpg',{cache:'no-store',signal:AbortSignal.timeout(2000)});if(!r.ok)throw Error();const data=await r.json();if(v!==pollVersion)return;if(!data||data.ok!==true)throw Error();view=data;render();}catch(_){if(v===pollVersion)hide('TRPGコーナーを準備しています');}
}
setInterval(poll,1000);poll();
</script></html>
'''


class TrpgViewError(ValueError):
    pass


def _check_payload(payload) -> None:
    if not isinstance(payload, dict):
        raise TrpgViewError("payload must be an object")
    if payload.get("schema") != "trpg-view":
        raise TrpgViewError("invalid schema")
    if type(payload.get("version")) is not int or payload["version"] < 1:
        raise TrpgViewError("invalid version")
    if not isinstance(payload.get("phase"), str):
        raise TrpgViewError("invalid phase")
    if not isinstance(payload.get("scene_text"), str):
        raise TrpgViewError("invalid scene text")
    if not isinstance(payload.get("choices"), list):
        raise TrpgViewError("invalid choices")
    if not isinstance(payload.get("votes"), dict):
        raise TrpgViewError("invalid votes")


def payload_path(state_dir) -> Path:
    return Path(state_dir) / PAYLOAD_FILENAME


def read_payload(path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PAYLOAD_BYTES:
            raise TrpgViewError("payload is not a bounded regular file")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TrpgViewError("payload is unreadable") from exc
    _check_payload(data)
    return data


def write_payload(path, payload) -> None:
    _check_payload(payload)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def handler_for(path, *, clock=time.time):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path not in {"/", "/api/trpg"}:
                self.send_response(404)
                self.end_headers()
                return
            try:
                data = read_payload(path)
            except TrpgViewError:
                data = None
            if data is None:
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(path, *, port=DEFAULT_PORT) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise TrpgViewError("invalid port")
    with ThreadingHTTPServer(("127.0.0.1", port), handler_for(path)) as server:
        server.serve_forever(poll_interval=0.2)


def main(argv=None) -> int:
    import argparse as _ap

    parser = _ap.ArgumentParser(description="TRPG/murder-mystery view")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    path = Path(args.state_dir) / PAYLOAD_FILENAME
    serve(path, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
