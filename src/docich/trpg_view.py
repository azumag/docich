"""Read-only, generation-bound spectator projection of a manual TRPG session."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import uuid

from .trpg_session import load, public

DEFAULT_PORT = 8809
VIEW_NAME = "trpg-view"

HTML = '''<!doctype html>
<html lang="ja" translate="no"><meta charset="utf-8"><meta name="google" content="notranslate">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>参加型 TRPG</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#101317;color:#f4f5f7;font-family:system-ui,sans-serif;overflow:hidden}
#stage{position:absolute;width:960px;height:540px;transform-origin:top left;padding:24px 32px}
h1{font-size:28px;margin:0 0 12px}#scene{font-size:20px;line-height:1.6;max-height:220px;overflow:auto;white-space:pre-wrap}
#choices{display:grid;grid-template-columns:1fr 1fr;gap:12px}.choice{padding:12px;background:#203244;border-radius:8px;font-size:18px}
#status,#phase{color:#b2d8ed;font-size:18px;margin:12px 0}#answer{font-size:22px;color:#ffe294;white-space:pre-wrap}
#unavailable{position:fixed;inset:0;background:#101317;display:grid;place-items:center;font-size:24px}#unavailable[hidden]{display:none}
</style><main id="stage"><h1 id="title">参加型 TRPG / マーダーミステリー</h1>
<div id="phase"></div><p id="scene"></p><div id="choices"></div><p id="status"></p><div id="answer"></div></main>
<div id="unavailable">シーンを確認しています</div><script>
const $=id=>document.getElementById(id);let expires=0;
const phases={SCENE:'シーン紹介',CHOICE:'投票受付',RESOLVE:'投票結果',CLIMAX:'推理受付',REVEAL:'答え合わせ',END:'終了',FAILED:'中断',TIMEOUT:'時間切れ'};
function fit(){const s=Math.min(innerWidth/960,innerHeight/540);$('stage').style.transform=`scale(${s})`;$('stage').style.left=`${(innerWidth-960*s)/2}px`;$('stage').style.top=`${(innerHeight-540*s)/2}px`;}
addEventListener('resize',fit);fit();
async function poll(){try{const r=await fetch('/api/trpg',{cache:'no-store',signal:AbortSignal.timeout(2000)});if(!r.ok)throw Error();const d=await r.json();if(d.ok!==true)throw Error();
$('title').textContent=d.title;$('phase').textContent=(phases[d.phase]||'準備中')+(d.remaining_seconds?' / 残り '+d.remaining_seconds+' 秒':'');$('scene').textContent=d.scene_text;$('choices').replaceChildren();
d.choices.forEach((text,i)=>{const el=document.createElement('div');el.className='choice';el.textContent=`!${i+1} ${text} (${d.votes[i+1]||0}票)`;$('choices').append(el);});
$('status').textContent=d.phase==='CLIMAX'?'!accuse <容疑者> [トリック] / '+d.suspects.join('・')+' / 提出 '+d.accusations+'件':(d.phase==='CHOICE'?'投票 !1〜!'+d.choices.length+' / ':'')+'参加 '+d.participants+'人';
$('answer').textContent=d.answer?'正解: '+d.answer+' / '+d.trick+' / 的中 '+d.correct_count+'人':'';expires=performance.now()+5000;$('unavailable').hidden=true;
}catch(_){$('unavailable').hidden=false;}}
setInterval(()=>{if(performance.now()>expires)$('unavailable').hidden=false;},500);setInterval(poll,1000);poll();
</script></html>'''


class TrpgViewError(ValueError):
    """The view request violates a fixed bound."""


def handler_for(runtime_id: str = "preview", *, generation=None, lease_id=None, session_file=None):
    if not isinstance(runtime_id, str) or len(runtime_id) < 1 or len(runtime_id) > 128:
        raise TrpgViewError("invalid runtime id")
    if generation is not None and (type(generation) is not int or generation < 1):
        raise TrpgViewError("invalid generation")
    if lease_id is not None:
        try:
            if not isinstance(lease_id, str) or str(uuid.UUID(lease_id)) != lease_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise TrpgViewError("invalid lease id") from None

    expected = {"game": VIEW_NAME, "runtime_id": runtime_id, "generation": generation, "lease_id": lease_id}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            port = self.server.server_port
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self.send_response(403)
                self.end_headers()
                return
            if self.path in {"/", "/broadcast"}:
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/trpg":
                try:
                    data = public(load(session_file, expected))
                    status = 200
                except (OSError, ValueError, TypeError, KeyError, StopIteration):
                    data, status = {"ok": False}, 503
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

    return Handler


def serve(*, port: int = DEFAULT_PORT, runtime_id: str = "preview",
          generation=None, lease_id=None, session_file=None) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise TrpgViewError("invalid port")
    with ThreadingHTTPServer(
        ("127.0.0.1", port),
        handler_for(runtime_id, generation=generation, lease_id=lease_id, session_file=session_file),
    ) as server:
        server.serve_forever(poll_interval=0.2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="TRPG synthetic view")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--runtime-id", default="preview")
    parser.add_argument("--generation", type=int)
    parser.add_argument("--lease-id")
    parser.add_argument("--session-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        serve(port=args.port, runtime_id=args.runtime_id,
              generation=args.generation, lease_id=args.lease_id, session_file=args.session_file)
    except (OSError, TrpgViewError):
        print(json.dumps({"ok": False, "reason": "view-unavailable"}))
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
