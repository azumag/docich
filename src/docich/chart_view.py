"""Read-only, loopback-only チャート解説コーナー broadcast view (issue #659).

This is the display half of the chart corner.  It serves content that
:mod:`docich.corner_659` already generated deterministically; it never fetches
chart data, never calls an LLM and never touches the stream encoder.  The
program adapter writes and validates the payload before GameSwitch commits the
switch, so a missing or malformed payload keeps this process from serving
(fail-closed, no stale board on air).

Every price, order and signal is synthetic fixture content; the page keeps a
合成データ marker visible at all times.  The 960x540 stage is scaled with
``contain`` and shown inside the standard broadcast game viewport.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import uuid

from .corner_659 import (
    MAX_ANIMATION_FRAMES,
    MAX_AUDIO_LINES,
    MAX_COMMENTARY_LINES,
    SCHEMA_VERSION,
)

DEFAULT_PORT = 8810
VIEW_NAME = "chart-659"
CORNER_ID = "chart-659"
READY_PATH = "/api/chart-659"
MAX_PAYLOAD_BYTES = 512 * 1024
MAX_CANDLES = 400
MAX_RUNTIME_ID_CHARS = 128
CAVEAT_FOOTER = "価格・注文・シグナルはすべて合成データです。実際の取引判断には使えません。"

HTML = r'''<!doctype html>
<html lang="ja" translate="no"><meta charset="utf-8"><meta name="google" content="notranslate"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>チャート解説コーナー</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#080c12;color:#eef4fb;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
#stage{position:absolute;width:960px;height:540px;transform-origin:top left;padding:10px 18px 12px;background:radial-gradient(ellipse at 20% 0%,#14263a 0,#0b1420 58%,#080c12 100%);overflow:hidden}
header{height:48px;display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid #29435e;padding-bottom:4px}
h1{font-size:24px;line-height:1.05;letter-spacing:.05em;margin:0;font-weight:800;color:#bfe3ff}.eyebrow{font-size:8px;letter-spacing:.3em;color:#6ea8d8;display:block;margin-bottom:1px}
.sub{font-size:11px;color:#9db6cc;margin-top:1px}.syn{border:1px solid #3a6b52;background:#122619;color:#8fe0ae;padding:4px 8px;border-radius:99px;font-size:10px;white-space:nowrap}
#main{display:flex;gap:12px;height:calc(100% - 104px);padding-top:8px}
#chartwrap{position:relative;flex:1 1 64%;border:1px solid #24405c;border-radius:10px;background:#0a121d;overflow:hidden}
canvas#scene{position:absolute;inset:0;width:100%;height:100%}
#badge{position:absolute;left:10px;top:8px;font-size:11px;color:#cfe6ff;background:#13263ccc;border:1px solid #2d4e6e;border-radius:6px;padding:3px 8px}
#side{flex:0 0 300px;display:flex;flex-direction:column;gap:8px;min-height:0}
.panel{border:1px solid #24405c;border-radius:10px;background:#0c1724;padding:8px 10px;min-height:0;overflow:hidden}
.panel h2{font-size:11px;letter-spacing:.14em;color:#7fb6e6;margin:0 0 5px;font-weight:700}
#audio{font-size:12px;line-height:1.55;color:#dbe9f7;white-space:pre-line}
#commentary{flex:1;font-size:12px;line-height:1.5;color:#cfe0f0;overflow:hidden}
#commentary .row{border-left:3px solid #2d4e6e;padding:2px 0 2px 7px;margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#commentary .row.on{border-left-color:#ffcf6b;color:#fff2cf;background:#2a241633}
#footer{position:absolute;left:18px;right:18px;bottom:4px;display:flex;justify-content:space-between;color:#7690a8;font-size:9px;line-height:12px;white-space:nowrap;overflow:hidden}
#unavailable{position:absolute;inset:0;z-index:5;display:flex;align-items:center;justify-content:center;background:#080c12ee;color:#ffd98a;text-align:center;font-size:19px;line-height:1.7;padding:40px}
#unavailable[hidden]{display:none}
</style>
<div id="stage">
<header>
<div><span class="eyebrow">CHART CORNER / 659</span><h1>チャート解説コーナー</h1><div class="sub" id="subtitle"></div></div>
<span class="syn">合成データ・非本番</span>
</header>
<div id="main">
<div id="chartwrap"><canvas id="scene"></canvas><div id="badge">約定時判断シグナル</div></div>
<div id="side">
<div class="panel"><h2>音声解説原稿</h2><div id="audio"></div></div>
<div class="panel"><h2>注文チャート実況</h2><div id="commentary"></div></div>
</div>
</div>
<div id="footer"><span id="caveat"></span><span id="counter"></span></div>
<div id="unavailable" hidden>チャート解説を読み込めないため休止中</div>
</div>
<script>
'use strict';
const COLORS={buy:'#5ce08a',sell:'#ff7d7d',hold:'#8ea8c4'};
function fit(){const el=document.getElementById('stage');const s=Math.min(innerWidth/960,innerHeight/540);el.style.transform=`scale(${s})`;el.style.left=`${(innerWidth-960*s)/2}px`;el.style.top=`${(innerHeight-540*s)/2}px`;}
addEventListener('resize',fit);fit();
const SCENE=document.getElementById('scene');const CTX=SCENE.getContext('2d');SCENE.width=900;SCENE.height=440;
let data=null;let page=0;let timer=null;
function showUnavailable(on){document.getElementById('unavailable').hidden=!on;}
function priceRange(){
 let lo=Infinity,hi=-Infinity;
 for(const c of (data.candles||[])){lo=Math.min(lo,c.low,c.high);hi=Math.max(hi,c.low,c.high);}
 for(const s of (data.signals||[])){lo=Math.min(lo,s.price);hi=Math.max(hi,s.price);}
 for(const f of (data.frames||[])){lo=Math.min(lo,f.price);hi=Math.max(hi,f.price);}
 if(!isFinite(lo)||!isFinite(hi)||hi<=lo){lo=0;hi=1;}
 const pad=(hi-lo)*0.08;return [lo-pad,hi+pad];
}
function draw(){
 if(!CTX)return;
 const w=SCENE.width,h=SCENE.height;CTX.clearRect(0,0,w,h);
 const [lo,hi]=priceRange();const y=v=>h-18-(v-lo)/(hi-lo)*(h-36);
 CTX.strokeStyle='#1c3149';CTX.lineWidth=1;
 for(let i=1;i<5;i++){const gy=12+i*(h-30)/5;CTX.beginPath();CTX.moveTo(0,gy);CTX.lineTo(w,gy);CTX.stroke();}
 const candles=data.candles||[];
 if(candles.length){
  const step=w/candles.length;CTX.strokeStyle='#5f92bd';CTX.lineWidth=1.4;CTX.beginPath();
  candles.forEach((c,i)=>{const x=i*step+step/2;i?CTX.lineTo(x,y(c.close)):CTX.moveTo(x,y(c.close));});CTX.stroke();
 }
 // Future-signal filter: only signals that have appeared by the current frame.
 const shownSignalTs=new Set();
 for(let i=0;i<=page && i<(data.frames||[]).length;i++){
  const f=(data.frames||[])[i];
  if(f && f.signal && typeof f.signal.timestamp==='number') shownSignalTs.add(f.signal.timestamp);
 }
 const signals=(data.signals||[]).filter(s=>shownSignalTs.has(s.timestamp));
 for(const s of signals){
  const candles=data.candles||[];
  let idx=0;
  for(let i=0;i<candles.length;i++){
   if(typeof candles[i].timestamp==='number' && candles[i].timestamp<=s.timestamp) idx=i; else break;
  }
  idx=Math.min(candles.length-1,Math.max(0,idx));
  const step=candles.length?w/candles.length:w;
  const x=candles.length?idx*step+step/2:w/2;
  const cy=y(s.price);const col=COLORS[s.side]||'#8ea8c4';
  CTX.fillStyle=col;CTX.beginPath();CTX.arc(x,cy,5,0,Math.PI*2);CTX.fill();
  CTX.fillStyle='#08121e';CTX.font='bold 11px sans-serif';CTX.textAlign='center';CTX.fillText(s.marker,x,cy+3.5);
 }
 const frame=(data.frames||[])[page];
 if(frame){
  CTX.fillStyle='#ffcf6b';CTX.font='bold 13px sans-serif';CTX.textAlign='right';
  CTX.fillText(`${frame.symbol}  ${frame.price.toFixed(3)}`,w-8,22);
 }
}
function render(){
 if(!data)return;
 const frame=(data.frames||[])[page];if(!frame)return;
 draw();
 const rows=document.querySelectorAll('#commentary .row');
 rows.forEach((el,i)=>el.classList.toggle('on',i===page%rows.length));
 document.getElementById('counter').textContent=`アニメーション ${page+1} / ${data.frames.length} ／ fps ${data.fps}`;
}
function build(){
 document.getElementById('subtitle').textContent=`${data.symbol} ／ 足 ${data.candles.length} 本 ／ シグナル ${data.signals.length} 件`;
 document.getElementById('caveat').textContent=data.caveatFooter;
 document.getElementById('audio').textContent=(data.audio.lines||[]).join('\n');
 const host=document.getElementById('commentary');host.replaceChildren();
 for(const c of data.commentary){const row=document.createElement('div');row.className='row';row.textContent=c.text;host.appendChild(row);}
 showUnavailable(false);render();
}
async function load(){
 try{
  const response=await fetch('/api/chart-659',{cache:'no-store',signal:AbortSignal.timeout(2500)});
  if(!response.ok)throw new Error('unavailable');
  const payload=await response.json();
  if(payload.ok!==true||!Array.isArray(payload.frames)||payload.frames.length<1)throw new Error('invalid');
  data=payload;page=0;build();
 }catch(_){showUnavailable(true);}
}
function schedule(){if(timer)clearInterval(timer);timer=setInterval(()=>{if(!data)return;page=(page+1)%data.frames.length;render();},data.frameMs);}
load().then(schedule);
</script></html>'''


class ChartViewError(ValueError):
    """The view request or the stored payload violates a fixed bound."""


def _finite_float(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChartViewError(f"invalid {label}")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ChartViewError(f"invalid {label}")
    return number


def _text(value, label, limit=200):
    if not isinstance(value, str) or len(value) > limit:
        raise ChartViewError(f"invalid {label}")
    return value


def build_view_payload(stored: dict) -> dict:
    """Validate already-generated content and shape it for the view.

    The view only presents deterministic content that was already produced by
    ``corner_659.ChartCornerManager``.  Keeping validation in a pure function
    lets the adapter fail closed before GameSwitch commits a switch, and lets
    the view re-validate the same bytes when it starts.
    """
    if (not isinstance(stored, dict) or stored.get("schema_version") != SCHEMA_VERSION
            or not isinstance(stored.get("symbol"), str)):
        raise ChartViewError("chart payload schema invalid")

    audio = stored.get("audio")
    if not isinstance(audio, dict) or not isinstance(audio.get("lines"), list):
        raise ChartViewError("chart audio invalid")
    audio_lines = [_text(line, "audio line", 120) for line in audio["lines"]]
    if len(audio_lines) > MAX_AUDIO_LINES:
        raise ChartViewError("chart audio too long")

    commentary = stored.get("commentary")
    if not isinstance(commentary, list) or len(commentary) > MAX_COMMENTARY_LINES:
        raise ChartViewError("chart commentary invalid")
    commentary_rows = []
    for item in commentary:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ChartViewError("chart commentary row invalid")
        commentary_rows.append({
            "text": _text(item["text"], "commentary text", 120),
            "eventType": _text(item.get("event_type", ""), "event type", 32),
        })

    signals = stored.get("signals")
    if not isinstance(signals, list):
        raise ChartViewError("chart signals invalid")

    signal_rows = []
    for item in signals[:MAX_ANIMATION_FRAMES]:
        if (not isinstance(item, dict) or item.get("side") not in {"buy", "sell", "hold"}
                or not isinstance(item.get("marker"), str)):
            raise ChartViewError("chart signal row invalid")
        signal_rows.append({
            "timestamp": _finite_float(item.get("timestamp"), "signal timestamp"),
            "price": _finite_float(item.get("price"), "signal price"),
            "side": item["side"],
            "marker": _text(item["marker"], "signal marker", 8),
            "reason": _text(item.get("reason", ""), "signal reason", 120),
        })

    frames = stored.get("animation_frames")
    if not isinstance(frames, list) or not frames or len(frames) > MAX_ANIMATION_FRAMES:
        raise ChartViewError("chart animation frames invalid")
    frame_rows = []
    for item in frames:
        if not isinstance(item, dict) or not isinstance(item.get("frame_index"), int):
            raise ChartViewError("chart animation frame invalid")
        frame_rows.append({
            "frameIndex": item["frame_index"],
            "price": _finite_float(item.get("price"), "frame price"),
            "symbol": _text(item.get("symbol", stored["symbol"]), "frame symbol", 32),
            "commentary": _text(item.get("commentary", ""), "frame commentary", 120),
        })

    candles = stored.get("candles", [])
    if not isinstance(candles, list) or len(candles) > MAX_CANDLES:
        raise ChartViewError("chart candles invalid")
    candle_rows = []
    for item in candles:
        if not isinstance(item, dict):
            raise ChartViewError("chart candle invalid")
        candle_rows.append({
            "timestamp": _finite_float(item.get("timestamp"), "candle timestamp"),
            "low": _finite_float(item.get("low"), "candle low"),
            "high": _finite_float(item.get("high"), "candle high"),
            "close": _finite_float(item.get("close"), "candle close"),
        })

    # Derive the playback interval from the generated schedule (1/fps), bounded
    # to the same 1..60 fps window corner_659 enforces.
    intervals = []
    for left, right in zip(frames, frames[1:]):
        if isinstance(left, dict) and isinstance(right, dict):
            delta = _finite_float(right.get("scheduled_at"), "frame time") - \
                _finite_float(left.get("scheduled_at"), "frame time")
            if delta > 0:
                intervals.append(delta)
    interval = sum(intervals) / len(intervals) if intervals else 1.0 / 15.0
    fps = max(1, min(60, round(1.0 / max(1e-6, interval))))
    return {
        "ok": True,
        "corner": CORNER_ID,
        "symbol": stored["symbol"],
        "generatedAt": _finite_float(stored.get("generated_at", 0.0), "generated at"),
        "audio": {"title": _text(audio.get("title", ""), "audio title", 120), "lines": audio_lines},
        "commentary": commentary_rows,
        "signals": signal_rows,
        "frames": frame_rows,
        "candles": candle_rows,
        "fps": fps,
        "frameMs": max(16, round(1000.0 / fps)),
        "caveatFooter": CAVEAT_FOOTER,
    }


def load_payload(payload_file) -> dict:
    """Read the adapter-written transient payload and validate it (fail-closed).

    The file is regenerated on every start and removed with the runtime
    directory; nothing here writes it back out.
    """
    try:
        raw = Path(payload_file).read_text(encoding="utf-8")
    except OSError:
        raise ChartViewError("chart payload missing") from None
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ChartViewError("chart payload too large")
    try:
        stored = json.loads(raw)
    except ValueError:
        raise ChartViewError("chart payload unreadable") from None
    return build_view_payload(stored)


def handler_for(payload: dict, runtime_id: str = "preview", *, generation=None,
                lease_id=None) -> type:
    if (not isinstance(runtime_id, str) or len(runtime_id) > MAX_RUNTIME_ID_CHARS
            or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", runtime_id) is None):
        raise ChartViewError("invalid runtime id")
    if generation is not None and (type(generation) is not int or generation < 1):
        raise ChartViewError("invalid generation")
    if lease_id is not None:
        try:
            if not isinstance(lease_id, str) or str(uuid.UUID(lease_id)) != lease_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ChartViewError("invalid lease id") from None

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
            if self.path != READY_PATH:
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
        raise ChartViewError("invalid port")
    # No --host switch: the listener can never bind publicly by configuration.
    with ThreadingHTTPServer(
        ("127.0.0.1", port),
        handler_for(payload, runtime_id, generation=generation, lease_id=lease_id),
    ) as server:
        server.serve_forever(poll_interval=0.2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="チャート解説コーナーの読み取り専用画面")
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
    except (OSError, ChartViewError):
        # Fixed, non-sensitive line only.
        print(json.dumps({"ok": False, "reason": "view-unavailable"}))
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
