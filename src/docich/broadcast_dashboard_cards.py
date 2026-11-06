"""Shared, read-only HTML cards for multi-game broadcast status overlays.

The terminal text remains the source-of-truth contract. These helpers only
project selected stable lines into broadcast-facing cards; callers keep the raw
<pre> alongside the cards for legacy/direct consumers.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
from pathlib import Path


DASHBOARD_CARDS_CONTRACT_VERSION = 1


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_LEAD_RE = re.compile(r"^[\s│┃┌└─━●○✓!◌▸◆◇♪♫📻⟳💬]+")
_ALERT_RE = re.compile(
    r"\b(?:STOPPED|DOWN|FAILED|INVALID|DEGRADED|CHECK|UNKNOWN|PAUSED)\b"
    r"|Duplicates\s+DETECTED|^Unexpected\b|^Stopped\b|AI 429",
    re.IGNORECASE,
)
_GIB = 1024 ** 3
_DAY_SEC = 86400
_AV_SYNC_LINE_RE = re.compile(r"\bAVSync\b")
_AV_SYNC_STATE_ENV = "SOREN_DIRECT_AV_SYNC_STATE_FILE"
_AV_SYNC_STATE_DEFAULT = "tmp/state/direct_av_sync_probe.json"


def plain(raw: str) -> str:
    return _ANSI_RE.sub("", str(raw or "")).replace("\r\n", "\n").replace("\r", "\n")


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _clean(line: str) -> str:
    return _LEAD_RE.sub("", line).strip()


def _tone(value: str) -> str:
    upper = str(value or "").upper()
    if re.search(r"STOPPED|DOWN|FAILED|INVALID|DISCONNECTED|ALERT", upper):
        return "down"
    if re.search(r"DEGRADED|CHECK|UNKNOWN|PAUSED|RETRY|WAIT", upper):
        return "warn"
    return "ok"


def _root_disk_metric(path: str = "/") -> tuple[str, str, str]:
    """Return df-compatible root filesystem usage for the status overlay.

    Linux filesystems may reserve blocks that are counted as used but are not
    available to the runtime user. Match ``df`` by calculating the percentage
    over used + user-available blocks while still showing the filesystem's
    total size. The read is local/read-only and failure degrades to UNKNOWN.
    """
    try:
        info = os.statvfs(path)
    except (OSError, ValueError):
        return "—", "warn", "UNAVAILABLE"

    block = int(info.f_frsize or info.f_bsize or 0)
    total = block * int(info.f_blocks)
    free = block * int(info.f_bfree)
    available = block * int(info.f_bavail)
    used = max(0, total - free)
    denominator = used + max(0, available)
    if block <= 0 or total <= 0 or denominator <= 0:
        return "—", "warn", "UNAVAILABLE"

    # df(1) rounds usage upward so a nearly-full filesystem cannot look safer
    # than it is. Clamp malformed/platform-specific statvfs values defensively.
    percent = max(0, min(100, (100 * used + denominator - 1) // denominator))
    tone = "down" if percent >= 95 else "warn" if percent >= 85 else "ok"
    sub = f"{used / _GIB:.1f}/{total / _GIB:.1f} GiB · FREE {available / _GIB:.1f} GiB"
    return f"{percent}%", tone, sub


def _sections(raw: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    section = ""
    for raw_line in plain(raw).splitlines():
        stripped = raw_line.strip()
        if stripped in {"HEALTH", "CORE", "ACTIVITY", "AUDIO", "TWITCH", "YOUTUBE", "ROLLBACKS", "AI IMPROVE"}:
            section = "HEALTH" if stripped == "CORE" else stripped
            continue
        if stripped:
            result.setdefault(section, []).append(stripped)
    return result


def _find_status(lines: list[str], pattern: str) -> str:
    rx = re.compile(pattern)
    for line in lines:
        match = rx.search(_clean(line))
        if match:
            return match.group(1).strip()
    return ""


def _audio_status(lines: list[str]) -> str:
    """Pick the broadcast audio channel without claiming whole-path silence.

    The AUDIO section starts with the speech (Say) channel, which is legitimately
    idle between lines even while radio or the game/BGM path is playing. Showing
    that idle line verbatim as "AUDIO SILENT" reads as a dead audio path, so fall
    through to radio and use a neutral IDLE label when neither channel is active.
    """
    say = _find_status(lines, r"^Say\s+(.+)$")
    if say and not re.match(r"^SILENT\b", say, re.IGNORECASE):
        return say
    radio = _find_status(lines, r"^Radio\s+(.+)$")
    if radio and not re.match(r"^IDLE\b", radio, re.IGNORECASE):
        return radio
    return "IDLE"


def _metric(label: str, value: str, *, tone: str = "", sub: str = "") -> str:
    cls = f" metric-value {tone}".rstrip()
    sub_html = f'<div class="metric-sub">{_esc(sub)}</div>' if sub else ""
    return (
        '<div class="metric">'
        f'<div class="metric-label">{_esc(label)}</div>'
        f'<div class="{cls.strip()}">{_esc(value or "—")}</div>'
        f"{sub_html}</div>"
    )


def _service(label: str, value: str) -> str:
    value = value or "—"
    tone = _tone(value)
    return (
        f'<div class="service {tone}">'
        f'<span class="service-label">{_esc(label)}</span>'
        f'<span class="service-value">{_esc(value)}</span>'
        "</div>"
    )


def render_game_dashboard(raw: str) -> str:
    text = plain(raw)
    corner = re.search(
        r"^SOREN/CORNER:\s*(SOREN91|JEV)\s*/\s*([^/\n]+)\s*/\s*(.+)$",
        text,
        re.MULTILINE,
    )
    stats = re.search(
        r"Stats:\s*(\d+)\s+(results|reports)\s*/\s*best=([^/\n]+)"
        r"(?:\s*/\s*Recent30=([^\n]+))?",
        text,
    )
    if not corner or not stats:
        return ""

    kind, game, status = [part.strip() for part in corner.groups()]
    recent = stats.group(4)
    if not recent:
        recent_match = re.search(r"^Recent30:[^\n]*mean=([^\s]+)", text, re.MULTILINE)
        recent = recent_match.group(1) if recent_match else "—"

    live = re.search(r"^Live:\s*(.+)$", text, re.MULTILINE)
    trend = re.search(r"^\s*Trend:\s*(.+)$", text, re.MULTILINE)
    wins = re.search(r"^\s*wins=(\d+)\s*/\s*lower rank is better$", text, re.MULTILINE)
    last8_match = re.search(r"^Last8:\s+(.+)$", text, re.MULTILINE)
    values: list[float] = []
    labels: list[str] = []
    if last8_match:
        for token in last8_match.group(1).split()[-8:]:
            try:
                values.append(float(token.replace(",", "")))
                labels.append(token)
            except ValueError:
                pass

    status_tone = _tone(status)
    if re.search(r"ACTIVE|LIVE|進行中", status, re.IGNORECASE):
        status_tone = "ok"

    metrics = [
        _metric("BEST", stats.group(3).strip(), tone="accent"),
        _metric("RECENT 30", recent.strip(), tone="accent2"),
        _metric("SAMPLES", stats.group(1), sub=stats.group(2).upper()),
        _metric("WINS", wins.group(1) if wins else "—", sub="#1 FINISH" if wins else ""),
    ]

    history = ""
    if values:
        lo, hi = min(values), max(values)
        span = max(1.0, hi - lo)
        lower_better = kind == "SOREN91" or "Rank Timeline" in text
        bars = []
        for index, (value, label) in enumerate(zip(values, labels)):
            quality = (hi - value) / span if lower_better else (value - lo) / span
            height = 18 + round(quality * 60)
            current = " current" if index == len(values) - 1 else ""
            bars.append(
                '<div class="bar-wrap">'
                f'<div class="bar{current}" style="height:{height}px"></div>'
                f'<div class="bar-label">{_esc(label)}</div>'
                "</div>"
            )
        history = (
            '<div class="section-heading"><span>LAST 8</span>'
            f'<span>{"LOWER IS BETTER" if lower_better else "RECENT RESULTS"}</span></div>'
            f'<div class="history-bars">{"".join(bars)}</div>'
        )

    live_html = f'<div class="live-strip">{_esc(live.group(1))}</div>' if live else ""
    trend_html = ""
    if trend:
        trend_text = trend.group(1).strip()
        trend_tone = "better" if re.search(r"/\s*better\s*$", trend_text, re.I) else (
            "worse" if re.search(r"/\s*worse\s*$", trend_text, re.I) else "flat"
        )
        trend_html = f'<div class="trend {trend_tone}"><b>TREND</b><span>{_esc(trend_text)}</span></div>'

    return (
        '<section class="broadcast-card game-card">'
        '<div class="card-head">'
        '<div><div class="eyebrow">GAME PERFORMANCE</div>'
        f'<div class="card-title">{_esc(game)}</div></div>'
        f'<span class="state-pill {status_tone}">{_esc(status)}</span>'
        "</div>"
        f'<div class="metric-grid">{"".join(metrics)}</div>'
        f"{live_html}{trend_html}{history}"
        "</section>"
    )


def _av_sync_alert(now: float | None = None) -> str:
    """Return an ATTENTION row for a fresh failed A/V probe.

    The probe's JSON state is the display source, so drift_ms comes from
    lib/direct_av_sync.py rather than the terminal AVSync line. The freshness
    window mirrors show_status.sh: older results are HISTORICAL evidence.
    """
    path = Path(os.environ.get(_AV_SYNC_STATE_ENV) or _AV_SYNC_STATE_DEFAULT)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(state, dict) or state.get("state") != "failed":
        return ""
    result = state.get("result") if isinstance(state.get("result"), dict) else {}
    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    try:
        tested_at = float(state.get("tested_at") or path.stat().st_mtime)
        duration_sec = max(0, int(config.get("capture_duration_sec") or 0))
    except (OSError, TypeError, ValueError):
        return ""
    age_sec = (time.time() if now is None else now) - tested_at
    if tested_at <= 0 or age_sec > max(_DAY_SEC, duration_sec):
        return ""
    return (
        f"AVSync FAILED drift={result.get('drift_ms', '?')}ms "
        f"max={result.get('max_abs_offset_ms', '?')}ms pairs={result.get('pair_count', '?')}"
    )


def render_ops_dashboard(raw: str) -> str:
    text = plain(raw)
    workers = re.search(r"Workers\s+(\d+)/(\d+)\s+(ONLINE|DEGRADED|CHECK)", text)
    backend_line = next((line.strip() for line in text.splitlines() if re.search(r"\bBackend\s+", line)), "")
    if not workers and not backend_line:
        return ""

    sections = _sections(raw)
    health = sections.get("HEALTH", [])
    audio = sections.get("AUDIO", [])
    twitch = sections.get("TWITCH", [])
    youtube = sections.get("YOUTUBE", [])
    activity = sections.get("ACTIVITY", [])

    overall = workers.group(3) if workers else ("ALERT" if _ALERT_RE.search(text) else "LIVE")
    worker_value = f"{workers.group(1)} / {workers.group(2)}" if workers else "—"
    disk_value, disk_tone, disk_sub = _root_disk_metric()

    backend_clean = _clean(backend_line)
    backend = re.sub(r"^Backend\s+", "", backend_clean).strip() if backend_clean else "—"
    backend_state = "LIVE" if re.search(r"\bLIVE\b|\bOK\b", backend) else (
        "DOWN" if re.search(r"\bDOWN\b|\bFAILED\b", backend) else ""
    )

    services = [
        _service("STREAM", backend_state or "—"),
        _service("LOOP", _find_status(health, r"^Loop\s+(.+)$")),
        _service("AUDIO", _audio_status(audio)),
        _service("TWITCH", _find_status(twitch, r"^Chat\s+(.+)$")),
        _service("YOUTUBE", _find_status(youtube, r"^Chat\s+(.+)$")),
    ]

    game_line = next((_clean(line) for line in activity if re.search(r"\bGame\s+", line)), "")
    queue_line = next((_clean(line) for line in activity if "QueueMeter" in line), "")
    drop_line = next((_clean(line) for line in activity if "LastDrop" in line), "")

    game_match = re.search(r"Game\s+(\d+)試合目", game_line)
    game_value = game_match.group(1) if game_match else (re.sub(r"^Game\s+", "", game_line)[:20] or "—")
    queue_match = re.search(r"A=(\d+)\s+C=(\d+)\s+T=(\d+)", queue_line)
    queue_value = str(sum(map(int, queue_match.groups()))) if queue_match else "—"
    drop_value = re.sub(r"^LastDrop\s+", "", drop_line).split()[0] if drop_line else "—"

    av_alert = _av_sync_alert()
    alerts = [av_alert] if av_alert else []
    for line in text.splitlines():
        clean = _clean(line)
        # AVSync reaches ATTENTION only via _av_sync_alert (the probe JSON). Its
        # diagnostics line is excluded here so the display source stays separate.
        # Completed acceptance tests are demoted to HISTORICAL by show_status.
        # They are evidence, not a current fault, so they must not paint the
        # live ATTENTION list red (docich#2687: a weeks-old AVSync FAILED).
        if (
            clean
            and not _AV_SYNC_LINE_RE.search(clean)
            and "HISTORICAL" not in clean.upper()
            and _ALERT_RE.search(clean)
        ):
            alerts.append(clean)
        if len(alerts) >= 3:
            break

    alert_html = ""
    if alerts:
        rows = "".join(
            f'<div class="attention { _tone(item) }">{_esc(item)}</div>'
            for item in alerts
        )
        alert_html = (
            '<div class="section-heading"><span>ATTENTION</span>'
            f'<span>{len(alerts)} ITEM{"S" if len(alerts) != 1 else ""}</span></div>'
            f'<div class="attention-list">{rows}</div>'
        )
    else:
        alert_html = '<div class="all-clear">ALL SYSTEMS NOMINAL · BACKGROUND LOGGING ACTIVE</div>'

    activity_html = "".join(
        '<div class="activity-box">'
        f'<span>{_esc(label)}</span><b>{_esc(value)}</b></div>'
        for label, value in (("GAME", game_value), ("QUEUE", queue_value), ("DROP", drop_value))
    )

    return (
        '<section class="broadcast-card ops-card">'
        '<div class="card-head">'
        '<div><div class="eyebrow">OPERATIONS</div><div class="card-title">SYSTEM HEALTH</div></div>'
        f'<span class="state-pill {_tone(overall)}">{_esc(overall)}</span>'
        "</div>"
        '<div class="metric-grid two">'
        f'{_metric("WORKERS", worker_value, tone=_tone(overall), sub=workers.group(3) if workers else "")}'
        f'{_metric("DISK", disk_value, tone=disk_tone, sub=disk_sub)}'
        "</div>"
        '<div class="section-heading"><span>SERVICES</span><span>LIVE STATE</span></div>'
        f'<div class="service-grid">{"".join(services)}</div>'
        f'<div class="activity-grid">{activity_html}</div>'
        f"{alert_html}"
        "</section>"
    )


def dashboard_css() -> str:
    return r"""
.dashboard-shell { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:14px; min-height:0; flex:1; }
.broadcast-card { box-sizing:border-box; min-height:0; overflow:hidden; border:0; border-radius:0; background:transparent; padding:10px 8px; box-shadow:none; font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
.card-head { display:flex; align-items:center; gap:12px; margin-bottom:16px; }
.eyebrow { color:#6f91a5; font-size:11px; font-weight:900; letter-spacing:.14em; }
.card-title { margin-top:3px; color:#effbff; font-size:26px; line-height:30px; font-weight:900; letter-spacing:.01em; }
.state-pill { margin-left:auto; flex:0 0 auto; padding:6px 10px; border:1px solid #31576a; border-radius:999px; color:#abc2d0; background:#07131c; font-size:11px; font-weight:900; letter-spacing:.05em; }
.state-pill.ok { color:#a7f3d0; border-color:#257057; background:rgba(16,185,129,.10); }
.state-pill.warn { color:#fde68a; border-color:#81621d; background:rgba(245,158,11,.11); }
.state-pill.down { color:#fecaca; border-color:#7d3434; background:rgba(239,68,68,.12); }
.metric-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:9px; margin-bottom:12px; }
.metric-grid.two { grid-template-columns:repeat(2,minmax(0,1fr)); }
.metric-grid.one { grid-template-columns:minmax(0,1fr); }
.metric { min-width:0; padding:12px 13px; border:0; border-radius:7px; background:#081b27; }
.metric-label { color:#7896a8; font-size:10px; font-weight:900; letter-spacing:.13em; }
.metric-value { margin-top:3px; color:#f2fbff; font-size:30px; line-height:33px; font-weight:950; font-variant-numeric:tabular-nums; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.metric-value.accent { color:#a5f3fc; }
.metric-value.accent2 { color:#c7d2fe; }
.metric-value.ok { color:#a7f3d0; }
.metric-value.warn { color:#fde68a; }
.metric-value.down { color:#fecaca; }
.metric-sub { margin-top:3px; color:#6f8ca0; font-size:9px; font-weight:900; letter-spacing:.10em; }
.live-strip { margin-bottom:8px; padding:9px 11px; border-radius:7px; background:#071720; color:#b9d1dc; font-size:13px; font-weight:750; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.trend { margin-bottom:12px; padding:9px 11px; border-radius:7px; display:flex; gap:12px; align-items:center; font-size:12px; }
.trend b { letter-spacing:.11em; font-size:10px; }
.trend.better { color:#a7f3d0; background:rgba(16,185,129,.08); }
.trend.worse { color:#fecaca; background:rgba(239,68,68,.09); }
.trend.flat { color:#cbd5e1; background:rgba(148,163,184,.07); }
.section-heading { display:flex; justify-content:space-between; align-items:center; margin:9px 2px 6px; color:#718fa2; font-size:10px; font-weight:900; letter-spacing:.12em; }
.history-bars { height:118px; display:flex; align-items:flex-end; gap:9px; padding:10px 12px 6px; border-radius:8px; background:#06141d; }
.bar-wrap { flex:1 1 0; min-width:0; height:102px; display:flex; flex-direction:column; justify-content:flex-end; align-items:stretch; }
.bar { min-height:8px; border-radius:5px 5px 2px 2px; background:linear-gradient(180deg,#67e8f9,#0e7490); opacity:.86; }
.bar.current { background:linear-gradient(180deg,#fde68a,#d97706); opacity:1; }
.bar-label { margin-top:4px; color:#7894a6; font-size:9px; line-height:11px; text-align:center; font-weight:800; font-variant-numeric:tabular-nums; }
.service-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px; }
.service { min-width:0; padding:9px 11px; border:0; border-radius:6px; background:#071720; display:flex; align-items:center; gap:8px; }
.service-label { color:#7894a6; font-size:10px; font-weight:900; letter-spacing:.09em; }
.service-value { margin-left:auto; min-width:0; color:#c0d3dd; font-size:11px; font-weight:900; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.service.ok .service-value{color:#a7f3d0;}
.service.warn .service-value{color:#fde68a;}
.service.down .service-value{color:#fecaca;}
.activity-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:7px; margin-top:10px; }
.activity-box { padding:8px 10px; border:0; border-radius:6px; background:#071720; }
.activity-box span { display:block; color:#708da0; font-size:9px; font-weight:900; letter-spacing:.10em; }
.activity-box b { display:block; margin-top:3px; color:#c4d7e1; font-size:14px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.attention-list { display:flex; flex-direction:column; gap:5px; }
.attention { padding:7px 9px; border-radius:6px; background:rgba(245,158,11,.10); color:#fde68a; font-size:11px; font-weight:850; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.attention.down { background:rgba(239,68,68,.12); color:#fecaca; }
.all-clear { margin-top:10px; padding:9px 10px; border:0; border-radius:6px; background:rgba(16,185,129,.06); color:#9fe4c5; font-size:10px; font-weight:900; text-align:center; letter-spacing:.06em; }
.source-pre { display:none !important; }
.fallback-pre { margin:0; white-space:pre-wrap; color:#dbeafe; font:13px/1.32 "SF Mono",Menlo,Consolas,monospace; overflow:hidden; }
"""