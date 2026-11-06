#!/usr/bin/env python3
"""Generate a lightweight OBS toast overlay from overlay_events.jsonl."""

from __future__ import annotations

import html
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def read_events(path: Path, keep: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return events
    for line in lines[-keep:]:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events[-keep:]


def read_work_indicator(path: Path, now: int | None = None) -> dict[str, Any] | None:
    try:
        item = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(item, dict) or not item.get("active"):
        return None
    if now is None:
        now = int(time.time())
    try:
        stale_sec = max(1, int(os.environ.get("CODEX_WORK_OVERLAY_STALE_SEC", "3600")))
        ts = int(item.get("ts", 0) or 0)
    except (TypeError, ValueError):
        stale_sec = 3600
        ts = 0
    if ts <= 0 or now - ts > stale_sec:
        # 明示 stop まで到達できなかった異常終了も、次の定期描画で回収する。
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return item


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_gen_indicators(now: int) -> list[dict[str, Any]]:
    """Collect "still generating" signals (comment / radio) into indicator rows.

    Reads small state files written by broadcast/comment.sh and
    broadcast/radio_state.sh. Each row is shown only while its signal is
    fresh (mtime/epoch within a stale window) so a crashed generator does
    not leave a permanent ghost indicator.
    """
    base = os.environ.get("EVENT_OVERLAY_STATE_BASE", "")
    base_path = Path(base) if base else Path.cwd()

    def resolve(raw: str) -> Path:
        p = Path(raw)
        return p if p.is_absolute() else (base_path / p)

    indicators: list[dict[str, Any]] = []

    # --- コメント生成中: tmp/state/.comment_gen_state = "generating:comment:<epoch>" ---
    comment_raw = os.environ.get("EVENT_OVERLAY_COMMENT_GEN_STATE", "")
    comment_stale = int(os.environ.get("EVENT_OVERLAY_COMMENT_GEN_STALE_SEC", "90") or "90")
    if comment_raw:
        path = resolve(comment_raw)
        try:
            line = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            line = ""
        if line.startswith("generating:"):
            parts = line.split(":")
            ts = 0
            if len(parts) >= 3 and parts[-1].isdigit():
                ts = int(parts[-1])
            if ts <= 0:
                try:
                    ts = int(path.stat().st_mtime)
                except OSError:
                    ts = now
            if 0 <= now - ts <= comment_stale:
                indicators.append({
                    "key": "comment",
                    "icon": "💬",
                    "label": "コメント生成中",
                    "ts": ts,
                })

    # --- ラジオ生成中: tmp/state/.radio_state = "mode:corner:ts:owner_pid" ---
    # 生成 (jiji の Web 検索等) は数分かかることがあり ts は開始時にしか刻まれない。
    # owner_pid が生きている間は project の RADIO_STATE_STALE_SEC (既定600s) まで信頼し、
    # pid が死んでいる/不明な場合のみ短い窓 (dead window) でゴーストを防ぐ。
    radio_raw = os.environ.get("EVENT_OVERLAY_RADIO_STATE", "")
    radio_alive_stale = int(os.environ.get("EVENT_OVERLAY_RADIO_GEN_STALE_SEC", "600") or "600")
    radio_dead_stale = int(os.environ.get("EVENT_OVERLAY_RADIO_GEN_DEAD_STALE_SEC", "20") or "20")
    if radio_raw:
        path = resolve(radio_raw)
        try:
            line = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            line = ""
        if line:
            fields = line.split(":")
            mode = fields[0] if fields else ""
            corner = fields[1] if len(fields) > 1 else ""
            ts = int(fields[2]) if len(fields) > 2 and fields[2].isdigit() else 0
            owner_pid = int(fields[3]) if len(fields) > 3 and fields[3].isdigit() else 0
            if ts <= 0:
                try:
                    ts = int(path.stat().st_mtime)
                except OSError:
                    ts = now
            age = now - ts
            # Only "being produced" phases count as a loading indicator.
            if mode in ("generating", "verifying"):
                alive = bool(owner_pid) and _pid_alive(owner_pid)
                window = radio_alive_stale if alive else radio_dead_stale
                fresh = 0 <= age <= window
                if fresh:
                    label = "ラジオ生成中" if mode == "generating" else "ラジオ検証中"
                    if corner:
                        label = f"{label} ({corner})"
                    indicators.append({
                        "key": "radio",
                        "icon": "📻",
                        "label": label,
                        "ts": ts,
                    })

    # --- Say生成中: tmp/.say_queue/current_source または .voicevox_synth_lock ---
    # VOICEVOX 合成・再生は say_enqueue.sh が tmp/.say_queue 以下で管理する。
    # 合成は数秒〜十数秒、チャンク分割時は最大数十秒かかる。AI生成と同様に
    # LIVE STATUS 枠で AI思考中と同じ扱いで表示する。
    say_queue_raw = os.environ.get("EVENT_OVERLAY_SAY_QUEUE_DIR", "tmp/.say_queue")
    say_alive_stale = int(os.environ.get("EVENT_OVERLAY_SAY_STALE_SEC", "40") or "40")
    say_dead_stale = int(os.environ.get("EVENT_OVERLAY_SAY_DEAD_STALE_SEC", "10") or "10")
    if say_queue_raw:
        qdir = resolve(say_queue_raw)
        say_ts = 0
        say_owner_pid = 0
        say_label_extra = ""
        say_found = False
        say_phase = ""
        # 1) current_source – 再生ロック取得後の再生待ち/再生中/リトライ待ち
        cs_path = qdir / "current_source"
        try:
            line = cs_path.read_text(encoding="utf-8", errors="replace").strip()
            if line:
                parts = line.split("|")
                owner_raw = parts[0] if len(parts) > 0 else ""
                say_phase = parts[1] if len(parts) > 1 else ""
                ts_raw = parts[3] if len(parts) > 3 else ""
                say_label_extra = parts[4] if len(parts) > 4 else ""
                if ts_raw.isdigit():
                    say_ts = int(ts_raw)
                else:
                    try:
                        say_ts = int(cs_path.stat().st_mtime)
                    except OSError:
                        say_ts = now
                owner_str = owner_raw.split(":")[0] if ":" in owner_raw else owner_raw
                if owner_str.isdigit():
                    say_owner_pid = int(owner_str)
                age = now - say_ts
                if say_phase in ("waiting", "playing", "retry_wait"):
                    alive = bool(say_owner_pid) and _pid_alive(say_owner_pid)
                    window = say_alive_stale if alive else say_dead_stale
                    if 0 <= age <= window:
                        say_found = True
        except OSError:
            pass
        except Exception:
            pass
        # 2) voicevox 合成ロック – ロック取得前の事前合成（current_source 未作成）を拾う
        if not say_found:
            lock_dir = qdir / ".voicevox_synth_lock"
            try:
                if lock_dir.is_dir():
                    heartbeat_file = lock_dir / "heartbeat"
                    owner_file = lock_dir / "owner_pid"
                    hb = 0
                    owner_pid2 = 0
                    try:
                        hb_text = heartbeat_file.read_text(encoding="utf-8", errors="replace").strip()
                        if hb_text.isdigit():
                            hb = int(hb_text)
                    except OSError:
                        hb = 0
                    if hb == 0:
                        try:
                            hb = int(lock_dir.stat().st_mtime)
                        except OSError:
                            hb = now
                    try:
                        owner_text = owner_file.read_text(encoding="utf-8", errors="replace").strip()
                        owner_str2 = owner_text.split(":")[0] if ":" in owner_text else owner_text
                        if owner_str2.isdigit():
                            owner_pid2 = int(owner_str2)
                    except OSError:
                        owner_pid2 = 0
                    say_ts = hb
                    say_owner_pid = owner_pid2
                    say_phase = "synthesis"
                    age = now - hb
                    alive = bool(owner_pid2) and _pid_alive(owner_pid2)
                    window = say_alive_stale if alive else say_dead_stale
                    if 0 <= age <= window and (alive or hb != 0):
                        say_found = True
                        say_label_extra = ""
            except OSError:
                pass
            except Exception:
                pass
        # 3) pid ファイル – 直接再生フォールバック
        if not say_found:
            pid_path = qdir / "pid"
            try:
                if pid_path.is_file():
                    pid_text = pid_path.read_text(encoding="utf-8", errors="replace").strip()
                    if pid_text.isdigit():
                        pid = int(pid_text)
                        try:
                            mtime = int(pid_path.stat().st_mtime)
                        except OSError:
                            mtime = now
                        age = now - mtime
                        alive = _pid_alive(pid)
                        window = say_alive_stale if alive else say_dead_stale
                        if 0 <= age <= window and alive:
                            say_ts = mtime
                            say_owner_pid = pid
                            say_found = True
            except OSError:
                pass
            except Exception:
                pass
        if say_found:
            label = "Say生成中"
            hint = say_label_extra.strip() if say_label_extra else ""
            if hint:
                hl = hint.lower()
                if "comment" in hl:
                    hint = "コメント"
                elif "radio" in hl:
                    if ":" in hint:
                        corner = hint.split(":", 1)[1].strip()
                        hint = f"ラジオ {corner}" if corner else "ラジオ"
                    else:
                        hint = "ラジオ"
                elif "soren91" in hl:
                    hint = "soren91"
                else:
                    hint = hint[:20]
                if hint:
                    label = f"Say生成中 ({hint})"
            # owner が死んでいる古い ghost は弾く（上ですでに window で弾いている）
            indicators.append({
                "key": "say",
                "icon": "🔊",
                "label": label,
                "ts": say_ts,
            })

    return indicators


def render_event_overlay(
    events_path: Path | str,
    out_path: Path | str,
    keep: int = 180,
    visible_sec: int = 18,
    work_state_path: Path | str | None = None,
    *,
    now: int | None = None,
) -> None:
    """Render event overlay HTML without requiring a separate interpreter."""
    events_path = Path(events_path)
    out_path = Path(out_path)
    keep = int(keep)
    visible_sec = int(visible_sec)
    work_state_path = Path(work_state_path) if work_state_path else None
    events = read_events(events_path, keep)
    now = int(time.time()) if now is None else int(now)
    work = read_work_indicator(work_state_path, now=now) if work_state_path else None
    recent = [e for e in events if now - int(e.get("ts", 0) or 0) <= max(visible_sec * 4, 60)]
    gen_indicators = read_gen_indicators(now)

    payload = json.dumps(recent[-18:], ensure_ascii=False, separators=(",", ":"))
    work_payload = json.dumps(work or {}, ensure_ascii=False, separators=(",", ":"))
    gen_payload = json.dumps(gen_indicators, ensure_ascii=False, separators=(",", ":"))
    doc = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<style>
* {{ box-sizing: border-box; }}
html, body {{
  margin: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: transparent;
  font-family: "Segoe UI", "Helvetica Neue", sans-serif;
}}
#toasts {{
  position: fixed;
  right: 24px;
  bottom: 28px;
  width: 560px;
  display: flex;
  flex-direction: column-reverse;
  gap: 10px;
}}
#work-indicator {{
  position: fixed;
  left: 50%;
  top: 18px;
  transform: translateX(-50%);
  min-width: 460px;
  max-width: min(860px, calc(100vw - 48px));
  display: none;
  grid-template-columns: 10px minmax(0, 1fr);
  color: #fff7ed;
  background: rgba(25, 14, 4, 0.92);
  border: 2px solid rgba(251, 146, 60, 0.82);
  box-shadow: 0 14px 34px rgba(0, 0, 0, 0.44);
  border-radius: 8px;
  overflow: hidden;
}}
#work-indicator.active {{ display: grid; }}
#work-indicator.comment {{
  background: rgba(32, 18, 78, 0.96);
  border-color: rgba(167, 139, 250, 0.82);
}}
#work-indicator.comment .work-bar {{
  background: linear-gradient(180deg, #7c3aed, #a78bfa);
}}
#work-indicator.comment .work-elapsed {{
  color: #ddd6fe;
}}
#work-indicator.comment .work-body {{
  color: #ede9fe;
}}
.work-bar {{
  background: linear-gradient(180deg, #f97316, #facc15);
}}
.work-content {{
  padding: 13px 18px 14px;
  min-width: 0;
}}
.work-head {{
  display: flex;
  align-items: baseline;
  gap: 14px;
  min-width: 0;
}}
.work-title {{
  font-size: 30px;
  font-weight: 900;
  line-height: 1.08;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.work-elapsed {{
  margin-left: auto;
  font-size: 18px;
  font-weight: 800;
  color: #fed7aa;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}}
.work-body {{
  margin-top: 5px;
  color: #ffedd5;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.26;
  word-break: break-word;
}}
.toast {{
  display: grid;
  grid-template-columns: 7px minmax(0, 1fr);
  min-height: 72px;
  color: #eef5ff;
  background: rgba(7, 12, 22, 0.88);
  border: 1px solid rgba(184, 205, 235, 0.22);
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.38);
  border-radius: 8px;
  overflow: hidden;
}}
.toast.fresh {{
  animation: slideIn 220ms ease-out;
}}
.bar {{ background: #8ab4ff; }}
.toast.game .bar {{ background: #facc15; }}
.toast.worker .bar {{ background: #38bdf8; }}
.toast.chat .bar {{ background: #a78bfa; }}
.toast.radio .bar {{ background: #22c55e; }}
.toast.prediction .bar {{ background: #fb7185; }}
.toast.rollback .bar {{ background: #f97316; }}
.toast.system .bar {{ background: #e5e7eb; }}
.content {{ padding: 10px 13px 11px; min-width: 0; }}
.head {{
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}}
.title {{
  font-size: 20px;
  font-weight: 800;
  line-height: 1.15;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.time {{
  margin-left: auto;
  color: rgba(238, 245, 255, 0.62);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}}
.body {{
  margin-top: 5px;
  color: #d8e4f2;
  font-size: 16px;
  line-height: 1.28;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}}
.toast.chat .title {{
  color: rgba(238, 245, 255, 0.68);
  font-size: 14px;
  font-weight: 700;
}}
.toast.chat .body {{
  margin-top: 6px;
  color: #f8fbff;
  font-size: 22px;
  font-weight: 850;
  line-height: 1.22;
  -webkit-line-clamp: 2;
}}
.empty {{ display: none; }}
#gen-loaders {{
  position: fixed;
  right: 24px;
  top: 24px;
  width: 360px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}}
#gen-loaders:empty {{ display: none; }}
.gen-loader {{
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 9px 15px 9px 13px;
  color: #ecfeff;
  background: rgba(8, 17, 30, 0.90);
  border: 1px solid rgba(56, 189, 248, 0.55);
  border-left: 4px solid #38bdf8;
  border-radius: 8px;
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.40);
  font-size: 17px;
  font-weight: 800;
  line-height: 1.15;
}}
.gen-loader.comment {{ border-left-color: #a78bfa; border-color: rgba(167, 139, 250, 0.55); }}
.gen-loader.radio {{ border-left-color: #22c55e; border-color: rgba(34, 197, 94, 0.55); }}
.gen-loader.say {{ border-left-color: #f97316; border-color: rgba(249, 115, 22, 0.55); }}
.gen-loader .gen-icon {{ font-size: 18px; line-height: 1; }}
.gen-loader .gen-label {{
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.gen-loader .gen-elapsed {{
  flex: 0 0 auto;
  font-size: 14px;
  font-weight: 800;
  color: rgba(236, 254, 255, 0.62);
  font-variant-numeric: tabular-nums;
}}
.gen-loader .gen-dots::after {{
  content: "";
  animation: genDots 1.4s steps(4, end) infinite;
}}
.gen-spinner {{
  width: 16px;
  height: 16px;
  flex: 0 0 auto;
  border: 3px solid rgba(255, 255, 255, 0.22);
  border-top-color: #38bdf8;
  border-radius: 50%;
  animation: genSpin 0.8s linear infinite;
}}
.gen-loader.comment .gen-spinner {{ border-top-color: #a78bfa; }}
.gen-loader.radio .gen-spinner {{ border-top-color: #22c55e; }}
.gen-loader.say .gen-spinner {{ border-top-color: #f97316; }}
@keyframes genSpin {{ to {{ transform: rotate(360deg); }} }}
@keyframes genDots {{
  0% {{ content: ""; }}
  25% {{ content: "."; }}
  50% {{ content: ".."; }}
  75% {{ content: "..."; }}
  100% {{ content: ""; }}
}}
@keyframes slideIn {{
  from {{ transform: translateX(18px); opacity: 0; }}
  to {{ transform: translateX(0); opacity: 1; }}
}}
</style>
</head>
<body>
<section id="work-indicator" aria-live="polite">
  <div class="work-bar"></div>
  <div class="work-content">
    <div class="work-head">
      <div class="work-title"></div>
      <div class="work-elapsed"></div>
    </div>
    <div class="work-body"></div>
  </div>
</section>
<div id="gen-loaders" aria-live="polite"></div>
<div id="toasts"></div>
<script>
const EVENTS = {payload};
const WORK = {work_payload};
const GEN = {gen_payload};
const VISIBLE_SEC = {visible_sec};
const ANIMATE_MAX_AGE = 3;
const now = Math.floor(Date.now() / 1000);
const container = document.getElementById('toasts');
const workIndicator = document.getElementById('work-indicator');
const genLoaders = document.getElementById('gen-loaders');
function pad(n) {{ return String(n).padStart(2, '0'); }}
function timeLabel(ts) {{
  const d = new Date(ts * 1000);
  return `${{pad(d.getHours())}}:${{pad(d.getMinutes())}}:${{pad(d.getSeconds())}}`;
}}
function elapsedLabel(startedAt) {{
  const elapsed = Math.max(0, now - Number(startedAt || now));
  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  return `${{mins}}:${{pad(secs)}}`;
}}
const commentGen = (GEN || []).find(g => String(g.key||'') === 'comment');
if (WORK && WORK.active) {{
  workIndicator.classList.add('active');
  workIndicator.querySelector('.work-title').textContent = WORK.title || 'システム自動分析・修正作業中';
  workIndicator.querySelector('.work-elapsed').textContent = elapsedLabel(WORK.ts);
  let body = WORK.body || '';
  if (commentGen) {{
    body = body ? `${{body}}  |  ${{commentGen.icon||'💬'}} ${{commentGen.label||'コメント生成中'}} ${{elapsedLabel(commentGen.ts)}}` : `${{commentGen.icon||'💬'}} ${{commentGen.label||'コメント生成中'}} ${{elapsedLabel(commentGen.ts)}}`;
  }}
  workIndicator.querySelector('.work-body').textContent = body;
}} else if (commentGen) {{
  workIndicator.classList.add('active');
  workIndicator.classList.add('comment');
  workIndicator.querySelector('.work-title').textContent = `${{commentGen.icon||'💬'}} ${{commentGen.label||'コメント生成中'}}`;
  workIndicator.querySelector('.work-elapsed').textContent = elapsedLabel(commentGen.ts);
  workIndicator.querySelector('.work-body').textContent = 'AIがコメントを生成しています…';
}}
for (const g of (GEN || [])) {{
  if (String(g.key||'') === 'comment') continue;
  const row = document.createElement('div');
  row.className = `gen-loader ${{g.key || ''}}`;
  row.innerHTML = `<span class="gen-spinner"></span><span class="gen-icon"></span><span class="gen-label"></span><span class="gen-elapsed"></span>`;
  row.querySelector('.gen-icon').textContent = g.icon || '⏳';
  const labelEl = row.querySelector('.gen-label');
  labelEl.textContent = (g.label || '生成中');
  const dots = document.createElement('span');
  dots.className = 'gen-dots';
  labelEl.appendChild(dots);
  row.querySelector('.gen-elapsed').textContent = ' ' + elapsedLabel(g.ts);
  genLoaders.appendChild(row);
}}
for (const ev of EVENTS.slice().reverse()) {{
  const age = now - Number(ev.ts || 0);
  if (age > VISIBLE_SEC) continue;
  const item = document.createElement('section');
  const fresh = age <= ANIMATE_MAX_AGE ? ' fresh' : '';
  item.className = `toast ${{ev.category || 'worker'}}${{fresh}}`;
  item.innerHTML = `<div class="bar"></div><div class="content"><div class="head"><div class="title"></div><div class="time"></div></div><div class="body"></div></div>`;
  item.querySelector('.title').textContent = ev.title || ev.category || 'event';
  item.querySelector('.time').textContent = timeLabel(Number(ev.ts || now));
  item.querySelector('.body').textContent = ev.body || '';
  container.appendChild(item);
}}
</script>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".event_overlay.", suffix=".html", dir=str(out_path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, out_path)
    try:
        out_path.chmod(0o644)
    except OSError:
        pass


def main() -> None:
    events_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    keep = int(sys.argv[3]) if len(sys.argv) > 3 else 180
    visible_sec = int(sys.argv[4]) if len(sys.argv) > 4 else 18
    work_state_path = Path(sys.argv[5]) if len(sys.argv) > 5 else None
    render_event_overlay(events_path, out_path, keep, visible_sec, work_state_path)


if __name__ == "__main__":
    main()
