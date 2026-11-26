"""Read-only, loopback-only presentation for ``new_corner_runtime`` corners.

One generic view serves every corner registered in ``CORNER_SPECS``; the
payload file written by the program adapter selects which corner is on air.
The view never computes content on its own: it re-derives overlay, frames and
the status entry through the deterministic runtime contract in
:mod:`docich.new_corner_runtime` on every load, so a missing, tampered or
contract-breaking payload keeps this process from serving (fail-closed, no
stale board). The page carries a persistent marker distinguishing a synthetic
fixture payload from a verified-material production record.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import uuid

from .new_corner_runtime import (
    CORNER_SPECS,
    CornerRuntimeError,
    citation_urls,
    progress_frames,
    render_overlay,
    status_overlay_entry,
    validate_status,
)

#: One loopback port per registered corner view. New corners take the next
#: free port explicitly; the mapping is part of the reviewed broadcast surface.
PORTS = {
    "geography-world": 8811,
}
DEFAULT_PORT = 8811
PAGE_SECONDS = 12
MAX_RUNTIME_ID_CHARS = 128
MAX_PAYLOAD_BYTES = 256 * 1024


def view_name_for(corner_id: str) -> str:
    if corner_id not in CORNER_SPECS:
        raise CornerRuntimeError(f"unknown corner runtime spec: {corner_id}")
    return f"{corner_id}-view"


def port_for(corner_id: str) -> int:
    port = PORTS.get(corner_id)
    if port is None:
        raise CornerRuntimeError(f"no broadcast view port registered for {corner_id}")
    return port


HTML = r'''<!doctype html>
<html lang="ja" translate="no"><meta charset="utf-8"><meta name="google" content="notranslate"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>docich corner</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#0a0d12;color:#f2f5f8;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
#stage{position:absolute;width:960px;height:540px;transform-origin:top left;padding:14px 24px 14px;background:radial-gradient(ellipse at 25% 15%,#16222e 0,#10161f 55%,#0a0d12 100%);overflow:hidden}
header{height:56px;display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid #2c4256;padding-bottom:6px}
h1{font-size:24px;line-height:1.1;letter-spacing:.03em;margin:0;font-weight:800;color:#cfe3f4}.eyebrow{font-size:8px;letter-spacing:.3em;color:#7fa8c9;display:block;margin-bottom:2px}
.sub{font-size:11px;color:#9db4c6;margin-top:2px;max-width:640px;white-space:nowrap;overflow:hidden}
.tags{display:flex;gap:6px;align-items:center;flex:none}
.tag{border:1px solid #3a5a72;background:#14202c;color:#a9cbe4;padding:4px 8px;border-radius:99px;font-size:10px;letter-spacing:.05em;white-space:nowrap}
.tag.src{color:#f4d9a0;border-color:#8a6b3a}
#status-line{height:24px;display:flex;align-items:center;justify-content:space-between;color:#9db4c6;font-size:11px;white-space:nowrap;overflow:hidden}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#7fc9d4;margin-right:6px}
#board{height:calc(100% - 128px);border:1px solid #243747;border-radius:10px;background:linear-gradient(150deg,#12202b99,#0d141ecc);padding:14px 20px 10px;overflow:hidden}
#label-tag{display:inline-block;border:1px solid #3a5a72;background:#14202c;color:#a9cbe4;padding:2px 10px;border-radius:99px;font-size:11px;font-weight:700;letter-spacing:.08em;margin-bottom:6px}
#label-tag.fact{color:#8fd4a8;border-color:#3a6b4a}
#label-tag.analysis{color:#e8c96a;border-color:#6b5a2a}
#label-tag.forecast{color:#c99fe8;border-color:#5a3a6b}
#label-tag.caveat{color:#e8a88f;border-color:#6b4a3a}
#heading{font-size:21px;font-weight:800;line-height:1.25;color:#eaf3fb;margin-bottom:8px}
#text{font-size:15px;line-height:1.65;color:#d5e4f0;white-space:pre-line}
#footer{position:absolute;left:24px;right:24px;bottom:6px;display:flex;justify-content:space-between;color:#6f8aa0;font-size:9px;line-height:12px;white-space:nowrap;overflow:hidden}
#page-dots{display:flex;gap:6px;align-items:center}.page-dot{width:7px;height:7px;border-radius:50%;background:#243747}.page-dot.on{background:#7fa8c9}
#unavailable{position:absolute;inset:0;z-index:5;display:flex;align-items:center;justify-content:center;background:#0a0d12ee;color:#cfe3f4;text-align:center;font-size:20px;line-height:1.7;padding:40px}
#unavailable[hidden]{display:none}
</style>
<div id="stage">
<header>
<div><span class="eyebrow" id="eyebrow">DOCICH CORNER</span><h1 id="title"></h1><div class="sub" id="question"></div></div>
<div class="tags"><span class="tag" id="segment-tag" hidden></span><span class="tag src" id="source-tag"></span></div>
</header>
<div id="status-line"><span><i class="dot"></i><span id="frame-kind">読み込み中</span></span><span id="citations"></span></div>
<section id="board">
<span id="label-tag" hidden></span>
<div id="heading"></div>
<div id="text"></div>
</section>
<div id="page-dots"></div>
<div id="footer"><span id="caveat"></span><span id="counter"></span></div>
</div>
<div id="unavailable" hidden>コーナー内容を読み込めないため休止中</div>
<script>
'use strict';
function fit(){const el=document.getElementById('stage');const s=Math.max(innerWidth/960,innerHeight/540);el.style.transform=`scale(${s})`;el.style.left=`${(innerWidth-960*s)/2}px`;el.style.top=`${(innerHeight-540*s)/2}px`;}
addEventListener('resize',fit);fit();
const KIND={start:'開始',section:'セクション',end:'終了'};
function showUnavailable(on){document.getElementById('unavailable').hidden=!on;}
function lines(v){return Array.isArray(v)?v.map(s=>'・'+s).join('\n'):String(v||'');}
function render(data,index){
 const frame=data.frames[index];
 document.getElementById('frame-kind').textContent=(KIND[frame.kind]||frame.kind)+`（${index+1}/${data.frames.length}）`;
 const tag=document.getElementById('label-tag');
 if(frame.kind==='section'&&frame.label){tag.hidden=false;tag.textContent=frame.label;tag.className=frame.label;}
 else if(frame.kind==='end'){tag.hidden=false;tag.textContent='caveat';tag.className='caveat';}
 else{tag.hidden=true;}
 document.getElementById('heading').textContent=frame.heading||'';
 document.getElementById('text').textContent=lines(frame.text);
 const dots=document.getElementById('page-dots');dots.replaceChildren();
 for(let i=0;i<data.frames.length;i++){const d=document.createElement('i');d.className='page-dot'+(i===index?' on':'');dots.append(d);}
 document.getElementById('counter').textContent=`${index+1} / ${data.frames.length}`;
 showUnavailable(false);
}
let data=null;let page=0;let timer=null;
async function load(){
 try{
  const response=await fetch('/api/new-corner',{cache:'no-store',signal:AbortSignal.timeout(2500)});
  if(!response.ok)throw new Error('unavailable');
  const payload=await response.json();
  if(payload.ok!==true||!Array.isArray(payload.frames)||payload.frames.length<3)throw new Error('invalid');
  data=payload;
  document.getElementById('eyebrow').textContent=(data.corner||'').toUpperCase();
  document.getElementById('title').textContent=data.title;
  document.getElementById('question').textContent=data.question||'';
  document.getElementById('source-tag').textContent=data.isSynthetic===true?'合成fixture（非本番）':'実材料ベース・出典URL保持';
  const seg=document.getElementById('segment-tag');
  if(Array.isArray(data.segmentLabels)&&data.segmentLabels.length){seg.textContent='labels: '+data.segmentLabels.join('/');seg.hidden=false;}
  document.getElementById('citations').textContent=`出典 ${data.citationUrls.length} 件を保持`;
  document.getElementById('caveat').textContent=data.caveatFooter||'';
  showUnavailable(false);render(data,page);
 }catch(_){showUnavailable(true);document.getElementById('frame-kind').textContent='内容を再確認中';}
}
function schedule(){if(timer)clearInterval(timer);timer=setInterval(()=>{if(!data)return;page=(page+1)%data.frames.length;render(data,page);},__PAGE_SECONDS__*1000);}
load().then(schedule);
</script></html>'''.replace("__PAGE_SECONDS__", str(PAGE_SECONDS))


class NewCornerViewError(ValueError):
    """The view request or the stored payload violates a fixed bound."""


def load_payload(payload_file) -> dict:
    """Read the adapter-written record and re-derive everything (fail-closed).

    The payload file is transient runtime state: regenerated on every start
    and removed with the runtime directory. The stored record is validated
    through the same runtime contract the production runner applied, so a
    tampered file fails instead of reaching the broadcast frame.
    """
    try:
        raw = Path(payload_file).read_text(encoding="utf-8")
    except OSError:
        raise NewCornerViewError("new-corner payload missing") from None
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise NewCornerViewError("new-corner payload too large")
    try:
        stored = json.loads(raw)
    except ValueError:
        raise NewCornerViewError("new-corner payload unreadable") from None
    if not isinstance(stored, dict) or stored.get("corner") not in CORNER_SPECS:
        raise NewCornerViewError("new-corner payload invalid")
    record = stored.get("record")
    if not isinstance(record, dict) or record.get("corner") != stored["corner"]:
        raise NewCornerViewError("new-corner record invalid")
    corner_id = stored["corner"]
    try:
        overlay = render_overlay(record, corner_id)
        frames = progress_frames(record, corner_id)
        entry = validate_status(record, frames, corner_id)
        status_overlay_entry(record, corner_id)
        urls = citation_urls(record, corner_id)
    except (CornerRuntimeError, TypeError, ValueError, KeyError) as exc:
        raise NewCornerViewError("new-corner record failed the runtime contract") from exc
    synthetic = record.get("isSyntheticFixture") is True
    caveat_values = overlay.get("caveats") or {}
    return {
        "ok": True,
        "corner": corner_id,
        "view": view_name_for(corner_id),
        "title": overlay["title"],
        "question": overlay.get("question"),
        "summary": overlay.get("summary"),
        "isSynthetic": synthetic,
        "segmentLabels": list(overlay.get("segmentLabels") or []),
        "citationUrls": list(urls),
        "frames": [
            {
                "index": frame["index"],
                "kind": frame["kind"],
                "label": frame.get("label"),
                "heading": frame.get("heading"),
                "text": frame.get("text"),
            }
            for frame in frames
        ],
        "statusEntry": entry,
        "caveatFooter": " / ".join(str(v) for v in caveat_values.values())[:300],
        "pageSeconds": PAGE_SECONDS,
    }


def handler_for(payload: dict, runtime_id: str = "preview", *, generation=None,
                lease_id=None) -> type:
    if not isinstance(runtime_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", runtime_id) is None:
        raise NewCornerViewError("invalid runtime id")
    if generation is not None and (type(generation) is not int or generation < 1):
        raise NewCornerViewError("invalid generation")
    if lease_id is not None:
        try:
            if not isinstance(lease_id, str) or str(uuid.UUID(lease_id)) != lease_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise NewCornerViewError("invalid lease id") from None

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
            if self.path != "/api/new-corner":
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
        raise NewCornerViewError("invalid port")
    # No --host switch: the listener can never bind publicly by configuration.
    with ThreadingHTTPServer(
        ("127.0.0.1", port),
        handler_for(payload, runtime_id, generation=generation, lease_id=lease_id),
    ) as server:
        server.serve_forever(poll_interval=0.2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="new_corner_runtime コーナーの読み取り専用画面")
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
    except (OSError, NewCornerViewError, CornerRuntimeError):
        # Fixed, non-sensitive line only.
        print(json.dumps({"ok": False, "reason": "view-unavailable"}))
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
