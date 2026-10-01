"""Read-only, loopback-only nationwide JMA weather presentation.

No live stream, queue, worker, model, warning generator or scheduler is touched.
Every response validates the captured raw bulletin again. Browser content has a
separate monotonic expiry so a dead server cannot leave a stale forecast on air.
"""
from __future__ import annotations

import argparse
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import time
import uuid

from .weather import (
    MAX_BUNDLE_BYTES, WeatherError, build_bundle, decode, narration, project,
    write_json,
)

DEFAULT_PORT = 8803

HTML = r'''<!doctype html>
<html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>docich 全国の天気</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#0b1628;color:#f6f8fc;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
#stage{width:960px;height:540px;position:absolute;transform-origin:top left;padding:20px 26px;background:linear-gradient(125deg,#0e263d,#101b30)}
header{display:flex;align-items:baseline;justify-content:space-between;border-bottom:2px solid #7fd2eb;padding-bottom:8px}
h1{font-size:30px;margin:0;letter-spacing:.1em}#date{font-size:24px}.subtitle{font-size:12px;color:#c7d5e6;margin:7px 0}
#board{display:grid;grid-template-columns:1fr 1fr;gap:5px 20px;height:386px;align-content:start}
.card{height:120px;display:grid;grid-template-columns:80px 1fr 83px;column-gap:8px;border-bottom:1px solid #34445a;padding:3px 0}
.city{font-size:22px;font-weight:700;align-self:center}.weather{font-size:14px;line-height:18px;max-height:72px;overflow:hidden;overflow-wrap:anywhere}.detail{font-size:11px;line-height:16px;color:#c7d5e6}.temps{font-size:21px;align-self:center;text-align:right}.high{color:#ffbea3}.low{color:#a5daff}
#unavailable{height:386px;display:flex;align-items:center;justify-content:center;font-size:28px;color:#dae3ed}#unavailable[hidden],#board[hidden]{display:none}
footer{font-size:12px;color:#c7d5e6;line-height:18px;border-top:1px solid #7fd2eb;padding-top:5px}a{color:inherit}#sequence{font-size:12px;color:#c7d5e6}
</style>
<div id="stage"><header><h1>全国の天気</h1><div id="date">気象庁発表</div></header>
<div class="subtitle">代表11地点｜天気・降水確率：各地点を含む予報区域 ／ 気温：表示地点 ／ 時刻は日本時間</div>
<div id="unavailable" role="status">予報を確認しています</div><main id="board" hidden></main>
<footer>出典：<a href="https://www.jma.go.jp/bosai/forecast/">気象庁ホームページ</a> ／ 気象庁の発表をもとにdocichが編集<br>降水確率は6時間ごと。— は未発表・対象外・欠測。独自予報・独自警報ではありません。<span id="sequence"></span></footer></div>
<script>
'use strict';
const board=document.getElementById('board'), unavailable=document.getElementById('unavailable');
let until=0, view=null, page=0, pollVersion=0;
function fit(){const s=Math.min(innerWidth/960,innerHeight/540),el=document.getElementById('stage');el.style.transform=`scale(${s})`;el.style.left=`${(innerWidth-960*s)/2}px`;el.style.top=`${(innerHeight-540*s)/2}px`;}
addEventListener('resize',fit);fit();
function hide(){until=0;view=null;board.replaceChildren();board.hidden=true;unavailable.hidden=false;unavailable.textContent='最新の予報を確認できないため休止中';document.getElementById('date').textContent='気象庁発表';document.getElementById('sequence').textContent='';}
function element(tag,cls,txt){const el=document.createElement(tag);el.className=cls;el.textContent=txt;return el;}
function temperature(v){return v===null?'—':`${v}°`;}
function render(){
 if(!view||performance.now()>=until){hide();return;}
 board.replaceChildren();
 const selected=view.cities.slice(page*6,page*6+6);
 for(const c of selected){
  const row=element('article','card',''); row.append(element('div','city',c.city));
  const description=element('div','','');
  // Never inject external forecast strings as HTML, attributes or scripts.
  const w=element('div','weather',c.weather);w.title=c.weather;description.append(w);
  description.append(element('div','detail',c.pops.map(p=>`${String(p.start).padStart(2,'0')}–${String(p.end).padStart(2,'0')}時 ${p.percent===null?'—':p.percent+'%'}`).join(' / ')));
  const issue=c.issued_at.slice(5,10).replace('-','/')+' '+c.issued_at.slice(11,16);
  description.append(element('div','detail',`気象庁 ${issue} 発表`));row.append(description);
  const temps=element('div','temps','');temps.append(element('span','high',temperature(c.high_c)),document.createTextNode(' / '),element('span','low',temperature(c.low_c)));row.append(temps);board.append(row);
 }
 document.getElementById('date').textContent=view.date.replaceAll('-',' / ');
 document.getElementById('sequence').textContent=`　最高 / 最低 °C　${page+1}/2`;
 unavailable.hidden=true;board.hidden=false;
 // Fail closed instead of dropping the end of an unusually long forecast.
 if([...board.querySelectorAll(".weather")].some(el=>el.scrollHeight>el.clientHeight+1))hide();
}
async function poll(){
 const version=++pollVersion, requested=performance.now();
 try{
  const response=await fetch('/api/weather',{cache:'no-store',signal:AbortSignal.timeout(2000)});
  if(!response.ok)throw Error('unavailable');const data=await response.json();
  if(version!==pollVersion)return;
  if(!data.ok||!Array.isArray(data.cities)||data.cities.length!==11)throw Error('incomplete');
  const remaining=(data.expires_at-data.server_now)*1000-(performance.now()-requested);
  if(!Number.isFinite(remaining)||remaining<=0)throw Error('expired');
  view=data;until=performance.now()+Math.min(remaining,5000);render();
 }catch(_){if(version===pollVersion)hide();}
}
// The forecast disappears within five seconds even if HTTP or the process dies.
setInterval(()=>{if(until&&performance.now()>=until)hide();},100);
setInterval(()=>{page=1-page;render();},12000);
addEventListener('pageshow',()=>{hide();poll();});
addEventListener('visibilitychange',()=>{hide();if(!document.hidden)poll();});
setInterval(poll,2000);poll();
</script></html>'''


def read_view(path: Path, *, clock=time.time) -> dict:
    # Bound before decoding; an accidentally huge local file cannot exhaust RAM.
    with path.open('rb') as handle:
        raw = handle.read(MAX_BUNDLE_BYTES + 1)
    return project(decode(raw, limit=MAX_BUNDLE_BYTES), now=clock())


def handler_for(path: Path, runtime_id: str = "preview", *, generation=None,
                lease_id=None, clock=time.time):
    if not isinstance(runtime_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", runtime_id) is None:
        raise WeatherError("invalid-runtime-id")
    if generation is not None and (type(generation) is not int or generation < 1):
        raise WeatherError("invalid-generation")
    if lease_id is not None:
        try:
            if not isinstance(lease_id, str) or str(uuid.UUID(lease_id)) != lease_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise WeatherError("invalid-lease-id") from None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # no request paths, raw data or operational identities in logs

        def reply(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            port = self.server.server_port
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self.reply(403, b"forbidden", "text/plain; charset=utf-8")
                return
            if self.path == "/":
                self.reply(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.path != "/api/weather":
                self.reply(404, b"not found", "text/plain; charset=utf-8")
                return
            try:
                data = read_view(path, clock=clock)
                data.update(ok=True, runtime_id=runtime_id, generation=generation,
                            lease_id=lease_id,
                            narration=narration(data))
                status = 200
            except (OSError, WeatherError):
                data, status = {"ok": False, "reason": "forecast-unavailable"}, 503
            self.reply(status, json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")

    return Handler


def serve(path: Path, *, port=DEFAULT_PORT, runtime_id="preview", generation=None,
          lease_id=None) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise WeatherError("invalid-port")
    # No --host switch: the listener can never bind publicly by configuration.
    with ThreadingHTTPServer(
        ("127.0.0.1", port),
        handler_for(path, runtime_id, generation=generation, lease_id=lease_id),
    ) as server:
        server.serve_forever(poll_interval=0.2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="気象庁全国予報の取得・原稿・読み取り専用画面")
    parser.add_argument("--state-dir", type=Path, required=True, help="専用weather artifact/cacheディレクトリ")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="全11地点を検証し、成功時だけsnapshotを公開")
    fetch.add_argument("--day", choices=("auto", "today", "tomorrow"), default="auto")
    sub.add_parser("status", help="有効なsnapshotの状態のみを表示")
    sub.add_parser("narration", help="検証済みsnapshotの定型原稿をJSON出力（音声queueには送らない）")
    server = sub.add_parser("serve", help="外部通信しない読み取り専用画面")
    server.add_argument("--port", type=int, default=DEFAULT_PORT)
    server.add_argument("--runtime-id", default="preview")
    server.add_argument("--generation", type=int)
    server.add_argument("--lease-id")
    args = parser.parse_args(argv)
    path = args.state_dir / "snapshot.json"
    try:
        if args.command == "fetch":
            # Invalidate publication first. A failed refresh must not replay the
            # previous successful snapshot, including a still-fresh one.
            args.state_dir.mkdir(parents=True, exist_ok=True)
            with (args.state_dir / ".fetch.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                path.unlink(missing_ok=True)
                bundle = build_bundle(args.state_dir / "cache", day=args.day)
                write_json(path, bundle)
                data = read_view(path)
            print(json.dumps({"ok": True, "date": data["date"], "cities": len(data["cities"]), "expires_at": data["expires_at"]}))
        elif args.command == "serve":
            serve(path, port=args.port, runtime_id=args.runtime_id,
                  generation=args.generation, lease_id=args.lease_id)
        else:
            data = read_view(path)
            print(json.dumps({"ok": True, "date": data["date"], "narration": narration(data)} if args.command == "narration" else {"ok": True, "date": data["date"], "expires_at": data["expires_at"]}, ensure_ascii=False))
        return 0
    except (WeatherError, OSError):
        print(json.dumps({"ok": False, "reason": "forecast-unavailable"}))
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
