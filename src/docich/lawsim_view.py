"""Read-only, loopback-only 共産主義法律シミュレーター presentation (#697).

The view is the display half of the law-simulator corner. It renders one
already-computed session payload to rotating pages and never computes state on
its own: the program adapter writes the payload before the switch commits and
a missing or invalid payload keeps this process from serving at all
(fail-closed, no stale board).

The payload stores only the authored *session*; the view re-derives the whole
trace through the deterministic engine in
:mod:`docich.communist_law_simulator` on every load. A tampered or truncated
file therefore cannot reach the broadcast frame: the same fail-closed
re-validation contract the doushi view applies to its ranking.

Every displayed name, law, metric and event is fictional synthetic content and
the page keeps the 合成シミュレーション marker visible at all times.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import uuid

from .communist_law_simulator import (
    CORNER_ID,
    LawSimError,
    parse_session,
    progress_frames,
    render_overlay,
    validate_status,
)

DEFAULT_PORT = 8808
VIEW_NAME = "lawsim-view"
#: design: 1 frame 12 秒。最長バンク（1 start + 11 turns + 1 end）で
#: 168 秒 ≈ 既定 duration 3 分に収まる。
PAGE_SECONDS = 12
MAX_RUNTIME_ID_CHARS = 128
MAX_PAYLOAD_BYTES = 256 * 1024

HTML = r'''<!doctype html>
<html lang="ja" translate="no"><meta charset="utf-8"><meta name="google" content="notranslate"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>共産主義法律シミュレーター</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#0a0d12;color:#f2f5f8;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
#stage{position:absolute;width:960px;height:540px;transform-origin:top left;padding:12px 22px 14px;background:radial-gradient(ellipse at 25% 15%,#2a1c1c 0,#151019 55%,#0a0d12 100%);overflow:hidden}
header{height:54px;display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid #4a3040;padding-bottom:5px}
h1{font-size:25px;line-height:1.05;letter-spacing:.04em;margin:0;font-weight:800;color:#f4d9a0}.eyebrow{font-size:8px;letter-spacing:.3em;color:#c98f8f;display:block;margin-bottom:1px}
.sub{font-size:11px;color:#c4b3a4;margin-top:1px}.syn{border:1px solid #6b4a3a;background:#241823;color:#e8b7a0;padding:4px 8px;border-radius:99px;font-size:10px;letter-spacing:.05em;white-space:nowrap}
#status-line{height:24px;display:flex;align-items:center;justify-content:space-between;color:#b7a99b;font-size:11px;white-space:nowrap;overflow:hidden}.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#e8b7a0;margin-right:6px}
#lawline{font-weight:700;color:#f4d9a0;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#board{height:calc(100% - 128px);border:1px solid #3d2c38;border-radius:10px;background:linear-gradient(150deg,#1d141d99,#120d16cc);padding:10px 14px;overflow:hidden}
#deltas{font-size:14px;line-height:1.5;color:#e6d8c8;white-space:pre-line;margin:2px 0 8px;min-height:44px}
.bar-row{display:flex;align-items:center;gap:8px;margin:6px 0}
.bar-label{width:64px;font-size:12px;color:#b7a99b;text-align:right;white-space:nowrap}
.bar-track{flex:1;height:16px;border-radius:8px;background:#1a1420;border:1px solid #322533;overflow:hidden}
.bar-fill{height:100%;border-radius:7px;transition:width .5s}
.bar-value{width:36px;font-size:14px;font-weight:750;text-align:right}
#events{margin-top:8px;font-size:13px;line-height:1.55;color:#f0c7a8;white-space:pre-line}
#statusline2{margin-top:8px;font-size:12px;color:#c9b8a5}
#footer{position:absolute;left:22px;right:22px;bottom:5px;display:flex;justify-content:space-between;color:#8a7468;font-size:9px;line-height:12px;white-space:nowrap;overflow:hidden}
#page-dots{display:flex;gap:6px;align-items:center}.page-dot{width:7px;height:7px;border-radius:50%;background:#3d2c38}.page-dot.on{background:#c98f8f}
#unavailable{position:absolute;inset:0;z-index:5;display:flex;align-items:center;justify-content:center;background:#0a0d12ee;color:#f0d9ad;text-align:center;font-size:20px;line-height:1.7;padding:40px}
#unavailable[hidden]{display:none}
canvas#scene{position:absolute;inset:0;width:100%;height:100%;z-index:-1;pointer-events:none}
</style>
<div id="stage">
<canvas id="scene"></canvas>
<header>
<div><span class="eyebrow">COMMUNIST LAW SIMULATOR</span><h1 id="title">共産主義法律シミュレーター</h1><div class="sub" id="republic"></div></div>
<span class="syn">合成シミュレーション・非本番</span>
</header>
<div id="status-line"><span><i class="dot"></i><span id="frame-kind">開会を準備中</span></span><span id="status-text"></span></div>
<div id="lawline"></div>
<section id="board">
<div id="deltas"></div>
<div id="bars"></div>
<div id="events"></div>
<div id="statusline2"></div>
</section>
<div id="page-dots"></div>
<div id="footer"><span id="caveat"></span><span id="counter"></span></div>
</div>
<div id="unavailable" hidden>会議記録を読み込めないため休止中</div>
<script>
'use strict';
const COLORS={production:'#7fd48a',support:'#7fc9d4',discipline:'#e8c96a',reserves:'#8fa8e8',blackMarket:'#e87f7f',planProgress:'#c99fe8'};
const METRIC_KEYS=['production','support','discipline','reserves','blackMarket','planProgress'];
function fit(){const el=document.getElementById('stage');const s=Math.max(innerWidth/960,innerHeight/540);el.style.transform=`scale(${s})`;el.style.left=`${(innerWidth-960*s)/2}px`;el.style.top=`${(innerHeight-540*s)/2}px`;}
addEventListener('resize',fit);fit();
const SCENE=document.getElementById('scene');
const SCTX=SCENE.getContext('2d');
SCENE.width=960;SCENE.height=540;
let sceneFrame=0;let sceneTimer=null;
function drawScene(metrics){
 if(!SCTX)return;
 const w=SCENE.width,h=SCENE.height;
 SCTX.clearRect(0,0,w,h);
 const g=SCTX.createLinearGradient(0,0,0,h);
 g.addColorStop(0,'#2a1c1c');g.addColorStop(1,'#0a0d12');
 SCTX.fillStyle=g;SCTX.fillRect(0,0,w,h);
 const prod=(metrics&&metrics.production)||0;
 const plan=(metrics&&metrics.planProgress)||0;
 const bars=6;const bw=w/bars;
 for(let i=0;i<bars;i++){
  const heightFactor=(prod/100)*(0.4+0.6*Math.sin((sceneFrame+i)/8));
  const bh=h*0.35*heightFactor;
  SCTX.fillStyle='rgba(127,212,138,0.35)';
  SCTX.fillRect(i*bw+4,h-bh-60,bw-8,bh);
 }
 SCTX.strokeStyle='#c99fe8';SCTX.lineWidth=6;SCTX.beginPath();
 SCTX.arc(w/2,h/2,60,-Math.PI/2,-Math.PI/2+Math.PI*2*(plan/100));
 SCTX.stroke();
 const bm=(metrics&&metrics.blackMarket)||0;
 if(bm>50){SCTX.fillStyle='rgba(232,127,127,0.15)';SCTX.fillRect(0,0,w,h);}
}
function sceneMetrics(){
 if(!data)return null;
 const f=data.frames&&data.frames[page];
 if(!f)return data.initialMetrics;
 if(f.kind==='turn')return data.turns[f.index-1].metricsAfter;
 if(f.kind==='end')return data.finalMetrics;
 return data.initialMetrics;
}
function sceneTick(){
 if(document.hidden)return;
 sceneFrame++;
 drawScene(sceneMetrics()||{});
}
function startScene(){if(sceneTimer)clearInterval(sceneTimer);sceneTimer=setInterval(sceneTick,250);}
function stopScene(){if(sceneTimer){clearInterval(sceneTimer);sceneTimer=null;}}
document.addEventListener('visibilitychange',function(){if(document.hidden)stopScene();else startScene();});
startScene();

function showUnavailable(on){document.getElementById('unavailable').hidden=!on;}
function bars(metrics,labels){
 const host=document.getElementById('bars');host.replaceChildren();
 for(const key of METRIC_KEYS){
  const row=document.createElement('div');row.className='bar-row';
  const label=document.createElement('span');label.className='bar-label';label.textContent=labels[key]||key;
  const track=document.createElement('span');track.className='bar-track';
  const fill=document.createElement('span');fill.className='bar-fill';fill.style.width=`${metrics[key]}%`;fill.style.background=COLORS[key];
  const value=document.createElement('span');value.className='bar-value';value.textContent=metrics[key];
  track.append(fill);row.append(label,track,value);host.append(row);
 }
}
function render(data,index){
 const frame=data.frames[index];
 document.getElementById('frame-kind').textContent=frame.kind==='start'?'開会':frame.kind==='end'?'閉会':`第${frame.index}回会議`;
 document.getElementById('status-text').textContent=data.finalStatusText;
 document.getElementById('lawline').textContent=frame.heading;
 const deltas=document.getElementById('deltas');
 const turn=frame.kind==='turn'?data.turns[frame.index-1]:null;
 if(turn){
  const parts=[];
  for(const key of METRIC_KEYS){if(turn.deltas[key])parts.push(`${data.metricLabels[key]} ${turn.deltas[key]>0?'+':''}${turn.deltas[key]}`);}
  deltas.textContent=`法案の効果: ${parts.length?parts.join('　'):'効果なし'}　／　計画の前倒し ${turn.planGain>=0?'+':''}${turn.planGain}`;
 }else{deltas.textContent=frame.text;}
 bars(frame.kind==='start'?data.initialMetrics:frame.kind==='end'?data.finalMetrics:(turn?turn.metricsAfter:data.finalMetrics),data.metricLabels);
 const events=document.getElementById('events');
 events.textContent=turn&&turn.events.length?turn.events.map(e=>`⚠ ${e.text}`).join('\n'):'';
 document.getElementById('statusline2').textContent=frame.kind==='end'?data.finalStatusText:(turn?`状態: ${turn.status==='in_session'?'審議続行中':data.finalStatusText}`:'');
 document.getElementById('caveat').textContent=data.caveatFooter;
 const dots=document.getElementById('page-dots');dots.replaceChildren();
 for(let i=0;i<data.frames.length;i++){const d=document.createElement('i');d.className='page-dot'+(i===index?' on':'');dots.append(d);}
 document.getElementById('counter').textContent=`${index+1} / ${data.frames.length}`;
 showUnavailable(false);
}
let data=null;let page=0;let timer=null;
async function load(){
 try{
  const response=await fetch('/api/lawsim',{cache:'no-store',signal:AbortSignal.timeout(2500)});
  if(!response.ok)throw new Error('unavailable');
  const payload=await response.json();
  if(payload.ok!==true||!Array.isArray(payload.frames)||payload.frames.length<3)throw new Error('invalid');
  data=payload;
  document.getElementById('title').textContent=data.title;
  document.getElementById('republic').textContent=`${data.republic} ／ ${data.finalStatus}`;
  document.getElementById('caveat').textContent=data.caveatFooter;
  showUnavailable(false);render(data,page);
 }catch(_){showUnavailable(true);document.getElementById('frame-kind').textContent='会議記録を再確認中';}
}
function schedule(){if(timer)clearInterval(timer);timer=setInterval(()=>{if(!data)return;page=(page+1)%data.frames.length;render(data,page);},__PAGE_SECONDS__*1000);}
load().then(schedule);
</script></html>'''.replace("__PAGE_SECONDS__", str(PAGE_SECONDS))

#: The persistent footer repeats the model caveat from the engine's caveats.
CAVEAT_FOOTER = (
    "国家指標の効果は固定の近似則であり、実在する経済・政治の法則や"
    "特定の国の歴史をモデル化したものではない"
)


class LawSimViewError(ValueError):
    """The view request or the stored payload violates a fixed bound."""


def load_payload(payload_file) -> dict:
    """Read the adapter-written session and re-derive everything (fail-closed).

    The payload file is transient runtime state: it exists only while the
    corner is starting and is regenerated on every start. Nothing here writes
    it back out. The stored *session* is the only content; the whole trace is
    rebuilt through the deterministic engine, so a tampered file fails the
    same validation the tests exercise instead of reaching the broadcast.
    """

    try:
        raw = Path(payload_file).read_text(encoding="utf-8")
    except OSError:
        raise LawSimError("lawsim session payload missing") from None
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise LawSimError("lawsim session payload too large")
    try:
        stored = json.loads(raw)
    except ValueError:
        raise LawSimError("lawsim session payload unreadable") from None
    if not isinstance(stored, dict) or type(stored.get("session_index")) is not int:
        raise LawSimError("lawsim session payload invalid")
    data = parse_session(stored)
    overlay = render_overlay(stored)
    frames = progress_frames(stored)
    entry = validate_status(stored, frames)
    return {
        "ok": True,
        "corner": CORNER_ID,
        "sessionIndex": stored["session_index"],
        "title": overlay["title"],
        "republic": overlay["republic"],
        "finalStatus": overlay["finalStatus"],
        "finalStatusText": overlay["finalStatusText"],
        "metricLabels": {
            "production": "生産",
            "support": "支持",
            "discipline": "党規律",
            "reserves": "外貨",
            "blackMarket": "闇市場",
            "planProgress": "計画",
        },
        "initialMetrics": overlay["initialMetrics"],
        "finalMetrics": overlay["finalMetrics"],
        "frames": [
            {
                "index": frame["index"],
                "kind": frame["kind"],
                "heading": frame["heading"],
                "text": frame["text"],
            }
            for frame in frames
        ],
        "turns": [
            {
                "turn": turn["turn"],
                "lawName": turn["lawName"],
                "category": turn["category"],
                "deltas": turn["deltas"],
                "planGain": turn["planGain"],
                "events": turn["events"],
                "metricsAfter": turn["metricsAfter"],
                "status": turn["status"],
            }
            for turn in overlay["turns"]
        ],
        "statusEntry": entry,
        "caveatFooter": CAVEAT_FOOTER,
        "pageSeconds": PAGE_SECONDS,
    }


def handler_for(payload: dict, runtime_id: str = "preview", *, generation=None,
                lease_id=None) -> type:
    if not isinstance(runtime_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", runtime_id) is None:
        raise LawSimViewError("invalid runtime id")
    if generation is not None and (type(generation) is not int or generation < 1):
        raise LawSimViewError("invalid generation")
    if lease_id is not None:
        try:
            if not isinstance(lease_id, str) or str(uuid.UUID(lease_id)) != lease_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise LawSimViewError("invalid lease id") from None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # never log request paths or viewer identities

        def reply(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
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
            if self.path in {"/", "/broadcast"}:
                self.reply(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.path != "/api/lawsim":
                self.reply(404, b"not found", "text/plain; charset=utf-8")
                return
            data = dict(payload)
            data.update(runtime_id=runtime_id, generation=generation, lease_id=lease_id)
            self.reply(200, json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                       "application/json; charset=utf-8")

    return Handler


def serve(payload: dict, *, port: int = DEFAULT_PORT, runtime_id: str = "preview",
          generation=None, lease_id=None) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise LawSimViewError("invalid port")
    # No --host switch: the listener can never bind publicly by configuration.
    with ThreadingHTTPServer(
        ("127.0.0.1", port),
        handler_for(payload, runtime_id, generation=generation, lease_id=lease_id),
    ) as server:
        server.serve_forever(poll_interval=0.2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="共産主義法律シミュレーターの読み取り専用画面")
    parser.add_argument("--payload-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--runtime-id", default="preview")
    parser.add_argument("--generation", type=int)
    parser.add_argument("--lease-id")
    args = parser.parse_args(argv)
    try:
        payload = load_payload(args.payload_file)
        serve(payload, port=args.port, runtime_id=args.runtime_id,
              generation=args.generation, lease_id=args.lease_id)
    except (OSError, LawSimViewError, LawSimError):
        # Fixed, non-sensitive line only.
        print(json.dumps({"ok": False, "reason": "view-unavailable"}))
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
