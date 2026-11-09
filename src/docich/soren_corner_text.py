"""Corner-owned status text rendering.

Moved from soviet_now `status_dashboard.py` (ADR 0006 stage 2).  The module
turns the corner snapshots produced by :mod:`docich.soren_corner_stats` into
the same text lines the Soren status overlay has always printed.  It is pure:
it reads no files and owns no process state.

The generic ANSI/plot helpers below are duplicated here while the Soren
dashboard still uses its local copy.  They move out of soviet_now in the
follow-up cleanup once the docich path has been verified in production.
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
import unicodedata
from datetime import datetime, timezone

W = 57

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

def fg256(n):
    return f"\033[38;5;{n}m"

RST = "\033[0m"

BOLD = "\033[1m"

DIM = "\033[2m"

C_CYAN = fg256(37)

C_GREY = fg256(245)

SCORE_GRADIENT = [196, 202, 208, 214, 220, 226, 190, 154, 118, 82, 46]

def char_display_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1

def ansi_display_width(text):
    plain = ANSI_RE.sub("", str(text or ""))
    return sum(char_display_width(ch) for ch in plain)

def truncate_ansi_display(text, max_width):
    text = str(text or "")
    if max_width <= 0 or ansi_display_width(text) <= max_width:
        return text
    limit = max(0, max_width - 1)
    out = []
    width = 0
    pos = 0
    saw_ansi = False
    for match in ANSI_RE.finditer(text):
        segment = text[pos:match.start()]
        for ch in segment:
            ch_width = char_display_width(ch)
            if width + ch_width > limit:
                return "".join(out) + "…" + (RST if saw_ansi else "")
            out.append(ch)
            width += ch_width
        out.append(match.group(0))
        saw_ansi = True
        pos = match.end()
    for ch in text[pos:]:
        ch_width = char_display_width(ch)
        if width + ch_width > limit:
            return "".join(out) + "…" + (RST if saw_ansi else "")
        out.append(ch)
        width += ch_width
    return "".join(out)

def fit_dashboard_lines(lines, width=W):
    # Bounded, sanitized projection records are machine-readable data for the
    # dedicated large-type cards, not terminal rows. Never truncate their names.
    prefixes = (
        '  投影余白:', '  余白占領記録:', '  余白駐留:', '  余白行軍:', '  余白出撃:', '  余白出撃意図:', '  余白交戦HP:',
        '  余白交戦兵数:', '  余白戦闘計画:', '  余白切り札:', '  余白戦闘判断:', '  余白卵対策:',
        '  余白進行:', '  余白判断:', '  余白進軍数:', '  余白帰還:',
        '  出撃意図:', '  敵将軍:', '  味方将軍:', '  卵落:', '  切り札:', '  切り札在庫:',
    )
    return [line if line.startswith(prefixes) else truncate_ansi_display(line, width) for line in lines]

def gradient_color(val, lo, hi):
    if hi <= lo:
        return fg256(SCORE_GRADIENT[len(SCORE_GRADIENT) // 2])
    ratio = min(max((val - lo) / (hi - lo), 0), 1)
    idx = int(ratio * (len(SCORE_GRADIENT) - 1))
    return fg256(SCORE_GRADIENT[idx])

BAR_CHARS = " ▏▎▍▌▋▊▉█"

def block_bar(value, max_val, width, color=""):
    if max_val <= 0:
        return " " * width
    ratio = min(value / max_val, 1.0)
    full_eighths = ratio * width * 8
    full = int(full_eighths // 8)
    frac = int(full_eighths % 8)
    bar = "█" * full
    if frac > 0 and full < width:
        bar += BAR_CHARS[frac]
        full += 1
    bar += " " * (width - full)
    if color:
        return color + bar + RST
    return bar

def _bucket_score_window(scores, bucket_count):
    """Reduce a score window to stable, left-to-right sparkline samples."""
    if not scores or bucket_count <= 0:
        return []
    bucket_count = min(bucket_count, len(scores))
    values = []
    for index in range(bucket_count):
        start = index * len(scores) // bucket_count
        end = (index + 1) * len(scores) // bucket_count
        if end <= start:
            end = start + 1
        bucket = scores[start:end]
        values.append(sum(bucket) / len(bucket))
    return values

def _draw_timeline_segment(grid, x0, y0, x1, y1, color=None, color_grid=None):
    """Draw one plain-text line segment into a small chart grid."""
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    if y1 == y0:
        segment_char = "-"
    elif x1 == x0:
        segment_char = "|"
    elif y1 < y0:
        segment_char = "/"
    else:
        segment_char = "\\"

    height = len(grid)
    width = len(grid[0]) if height else 0
    for step in range(steps + 1):
        ratio = step / steps
        x = round(x0 + (x1 - x0) * ratio)
        y = round(y0 + (y1 - y0) * ratio)
        if not (0 <= x < width and 0 <= y < height):
            continue
        existing = grid[y][x]
        if existing != "*":
            grid[y][x] = segment_char
            if color_grid is not None:
                color_grid[y][x] = color

def _colorize_plot_row(chars, colors):
    """Wrap plot cells in per-run ANSI colors; visible text stays identical."""
    out = []
    buf = []
    current = None

    def flush():
        nonlocal buf
        if buf:
            if current:
                out.append(current + "".join(buf) + RST)
            else:
                out.append("".join(buf))
            buf = []

    for ch, col in zip(chars, colors):
        if ch == " ":
            flush()
            current = None
            buf.append(" ")
            continue
        if col != current:
            flush()
            current = col
        buf.append(ch)
    flush()
    return "".join(out)

def _render_timeline_grid(samples, width, height, lo, hi, *, lower_is_better=False):
    """Render ordered samples as a readable polyline colored by score value."""
    grid = [[" "] * width for _ in range(height)]
    color_grid = [[None] * width for _ in range(height)]
    if not samples or width <= 0 or height <= 0:
        return (
            [" " * max(width, 0) for _ in range(max(height, 0))],
            [[None] * max(width, 0) for _ in range(max(height, 0))],
        )

    score_range = max(hi - lo, 1)
    points = []
    for index, value in enumerate(samples):
        x = round(index * (width - 1) / max(len(samples) - 1, 1))
        normalized = min(max((value - lo) / score_range, 0), 1)
        if lower_is_better:
            normalized = 1 - normalized
        y = height - 1 - round(normalized * (height - 1))
        points.append((x, y))

    for index, ((x0, y0), (x1, y1)) in enumerate(zip(points, points[1:])):
        seg_val = (samples[index] + samples[index + 1]) / 2
        if lower_is_better:
            seg_val = hi + lo - seg_val
        seg_color = gradient_color(seg_val, lo, hi)
        _draw_timeline_segment(grid, x0, y0, x1, y1, seg_color, color_grid)
    marker_step = max(len(points) // 8, 1)
    for index, (x, y) in enumerate(points):
        if index % marker_step != 0 and index not in (0, len(points) - 1):
            continue
        if 0 <= y < height and 0 <= x < width:
            grid[y][x] = "*"
            value = hi + lo - samples[index] if lower_is_better else samples[index]
            color_grid[y][x] = gradient_color(value, lo, hi)
    return ["".join(row) for row in grid], color_grid

def render_score_timeline(scores, chart_w=42, chart_h=7, *, min_samples=3, lower_is_better=False):
    """Render the score history as a browser-safe text timeline.

    The graph keeps a y-scale and an explicit left-to-right ``old -> now``
    direction while avoiding Braille glyphs, which become dense blocks in the
    tiny OBS/browser feed.
    """
    label_w = max(5, max((len(str(s)) for s in scores[-100:]), default=0))
    width = max(int(chart_w), 1)
    height = max(int(chart_h), 3)
    sep = "│"
    heading = f"  {BOLD}Score Timeline{RST}"

    width = min(width, max(1, W - label_w - 1))
    if len(scores) < min_samples:
        placeholder = f"{'':>{label_w}}{sep} {'(not enough data)':^{width}}"
        return [heading, placeholder, f"{'':>{label_w}}{sep}{' ' * width}"]

    window = scores[-100:]
    lo = min(window)
    hi = max(window)
    samples = _bucket_score_window(window, width)
    plot, color_rows = _render_timeline_grid(samples, width, height, lo, hi, lower_is_better=lower_is_better)
    n = len(window)
    lines = [f"{heading} {DIM}(last {n} games; old -> now){RST}"]
    for row, (plot_row, color_row) in enumerate(zip(plot, color_rows)):
        if row == 0:
            label = f"{lo if lower_is_better else hi:>{label_w}}"
        elif row == height - 1:
            label = f"{hi if lower_is_better else lo:>{label_w}}"
        else:
            label = " " * label_w
        lines.append(f"{C_GREY}{label}{RST}{sep}{_colorize_plot_row(plot_row, color_row)}")
    lines.append(f"{'':>{label_w}}└{C_CYAN}{'-' * max(width - 1, 0)}>{RST}")
    if width >= 6:
        timeline_labels = "old" + (" " * (width - 6)) + "now"
    else:
        timeline_labels = "old"[:width]
    lines.append(f"{'':>{label_w}}{sep}{DIM}{timeline_labels}{RST}")
    return lines

def _corner_status_label(status):
    return {
        "preparing": "準備中",
        "starting": "切替中",
        "active": "進行中",
        "restoring": "復元中",
        "recovery_required": "回復待ち",
    }.get(str(status or ""), str(status or "不明"))

def _corner_short(value, fallback="--", limit=28):
    text = str(value if value not in (None, "") else fallback)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"

def _corner_int(value, fallback="--"):
    if isinstance(value, bool):
        return fallback
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return fallback

def _corner_fill_line(fill):
    if not isinstance(fill, dict):
        return None
    symbol = _corner_short(fill.get("symbol"), "?", 12)
    side = {"buy": "BUY", "sell": "SELL"}.get(str(fill.get("side") or "").lower(), "TRADE")
    notional = fill.get("quote_notional")
    if notional in (None, ""):
        notional = fill.get("price")
    return f"{symbol} {side} {_corner_short(notional, "?", 12)}"

def _corner_number(value):
    return f"{value:.1f}".removesuffix(".0") if abs(value) < 1_000_000 else f"{value:.3g}"

def _paper_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None

def _paper_amount(value, fallback="?"):
    number = _paper_number(value)
    if number is None:
        return fallback
    if abs(number) >= 1_000_000:
        return f"{number:.3g}"
    if float(number).is_integer():
        return str(int(number))
    return f"{number:.6f}".rstrip("0").rstrip(".")

def _paper_signed_amount(value, fallback="--"):
    number = _paper_number(value)
    if number is None:
        return fallback
    rendered = _paper_amount(number, fallback=fallback)
    return f"+{rendered}" if number > 0 else rendered

def _paper_age(value):
    try:
        seconds = max(0, int(value))
    except (TypeError, ValueError):
        return "--"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"

def _paper_worker_label(value):
    return {
        "paper_worker_running": "running",
        "paper_worker_degraded": "degraded",
        "paper_worker_idle": "idle",
    }.get(str(value or ""), _corner_short(value, "unknown", 14))

def _paper_count(mapping, key):
    if not isinstance(mapping, dict):
        return "--"
    return _corner_int(mapping.get(key))

def render_corner_distribution(scores, *, rank=False):
    """Integer bins covering this game's actual range, including negatives."""
    title = "Rank Distribution" if rank else "Score Distribution"
    lines = [f"  {BOLD}{title}{RST} (last {len(scores)})"]
    if not scores:
        return lines + ["  No completed results yet"]
    lo, hi = min(scores), max(scores)
    step = max(1, (hi - lo + 6) // 6)
    bands = [(start, min(start + step - 1, hi)) for start in range(lo, hi + 1, step)]
    counts = [sum(start <= score <= end for score in scores) for start, end in bands]
    labels = [str(start) if start == end else f"{start}..{end}" for start, end in bands]
    label_w = max(map(len, labels))
    bar_w = max(1, min(30, W - label_w - 9))
    for label, count in zip(labels, counts):
        lines.append(f" {label:>{label_w}}│{block_bar(count, max(counts), bar_w, C_CYAN)} {count:>5}")
    return lines

def _corner_score_panels(values, *, rank=False, reports=False, compact=False):
    scores = [value for value in values if type(value) is int]
    noun = "reports" if reports else "results"
    if not scores:
        return [f"Stats: no completed {noun} yet", "  History will appear after a result is recorded."]

    best = min(scores) if rank else max(scores)
    recent = scores[-30:]
    recent_mean = statistics.mean(recent)

    if compact:
        # The right-hand broadcast rail is narrow.  Corner-owned Soren91/JEV
        # status should answer "how is this run doing?" before spending rows on
        # history.  Keep the timeline and Last8 trace, but omit the redundant
        # distribution panel that made the live view difficult to scan.
        lines = [
            f"Stats: {len(scores)} {noun} / best={_corner_number(best)}"
            f" / Recent30={_corner_number(recent_mean)}",
        ]
        if len(scores) > 30:
            previous = scores[-60:-30]
            delta = recent_mean - statistics.mean(previous)
            if abs(delta) < 0.05:
                trend = "flat"
            else:
                better = delta < 0 if rank else delta > 0
                trend = "better" if better else "worse"
            lines.append(f"  Trend: {delta:+.1f} vs previous {len(previous)} / {trend}")
        if rank:
            lines.append(f"  wins={scores.count(1)} / lower rank is better")
        if reports:
            # Older JEV reports finalize on errors too and do not distinguish
            # them from game-over. Do not present these as completed matches.
            lines.append("  Reported scores; may include interrupted runs")
        lines.append("")
        timeline = render_score_timeline(
            scores,
            chart_h=5,
            min_samples=1,
            lower_is_better=rank,
        )
        timeline[0] = timeline[0].replace(
            "Score Timeline",
            "Rank Timeline" if rank else "Score Timeline",
        )
        if reports:
            timeline[0] = timeline[0].replace("games", "reports")
        lines += timeline
        lines.append("Last8: " + " ".join(_corner_number(s) for s in scores[-8:]))
        return fit_dashboard_lines(lines)

    lines = [
        f"Stats: {len(scores)} {noun} / best={_corner_number(best)}",
        f"  mean={_corner_number(statistics.mean(scores))} median={_corner_number(statistics.median(scores))}",
    ]
    lines.append(f"Recent30: n={len(recent)} mean={_corner_number(recent_mean)}")
    if len(scores) > 30:
        previous = scores[-60:-30]
        delta = recent_mean - statistics.mean(previous)
        lines.append(f"  vs previous {len(previous)}: {delta:+.1f}")
    if rank:
        lines.append(f"  wins={scores.count(1)} / lower rank is better")
    if reports:
        # Older JEV reports finalize on errors too and do not distinguish
        # them from game-over. Do not present these as completed matches.
        lines.append("  Reported scores; may include interrupted runs")
    lines.append("")
    timeline = render_score_timeline(scores, min_samples=1, lower_is_better=rank)
    timeline[0] = timeline[0].replace("Score Timeline", "Rank Timeline" if rank else "Score Timeline")
    if reports:
        timeline[0] = timeline[0].replace("games", "reports")
    lines += timeline
    lines.append("")
    lines += render_corner_distribution(scores[-100:], rank=rank)
    lines.append("Last8: " + " ".join(_corner_number(s) for s in scores[-8:]))
    return fit_dashboard_lines(lines)

_CORNER_CONTROL_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-Z\\-_]"
    r"|\[[0-?]{1,4}[ -/]*[@-~]|[\x00-\x1f\x7f-\x9f]")

def _hanjuku_join(names, fallback="未確認", limit=3):
    """Join cached names, dropping escape runs: the renderer trusts no caller."""
    items = []
    for name in names or []:
        if not isinstance(name, str):
            continue
        clean = _corner_short(_CORNER_CONTROL_RE.sub("", name), "", 10)
        if clean and clean not in items:
            items.append(clean)
        if len(items) >= limit:
            break
    return "/".join(items) if items else fallback

def render_hanjuku_status(value):
    value = value if isinstance(value, dict) else {}
    if value.get("availability") != "fresh":
        reason = "更新待ち（観測が古い）" if value.get("availability") == "stale" else "未確認"
        return ["半熟英雄 / ゲーム状況", f"  状態: {reason}"]
    def num(key):
        v = value.get(key)
        return str(v) if type(v) is int and v >= 0 else "不明"
    def label(key):
        v = value.get(key)
        # No raw ANSI/control characters from cached game metadata.  A
        # per-character isprintable() check would keep the printable "[31m"
        # that follows an ESC, so drop whole escape runs first.
        if not isinstance(v, str):
            return "不明"
        return _corner_short(_CORNER_CONTROL_RE.sub("", v), "不明", 24)
    def row(key):
        v = value.get(key)
        return v if isinstance(v, list) else []
    def text(value, fallback="不明", limit=10):
        # Nested cached names go through the same escape-run removal as
        # label(): the renderer must not trust a hand-built snapshot dict.
        if not isinstance(value, str):
            return fallback
        return _corner_short(_CORNER_CONTROL_RE.sub("", value), fallback, limit)

    def duel_general(side, profile):
        """9 requested fields + max HP, keeping seasonal conditions verbatim."""
        if not isinstance(profile, dict):
            return f"  {side}将軍: 判定不能（将軍または最大HP不明）"
        def integer(key, fallback='不明'):
            value = profile.get(key)
            return str(value) if type(value) is int else fallback
        bonus = text(profile.get('egg_bonus'), '', 8) or '－'
        aptitude = text(profile.get('aptitude'), '', 12) or '－'
        egg_type = text(profile.get('egg_type'), '', 24) or '不明'
        debut = profile.get('debut')
        debut_label = f"{debut}話" if type(debut) is int and debut > 0 else '－'
        return (
            f"  {side}将軍: {text(profile.get('name'), '不明', 24)}"
            f" / 最大HP {integer('max_hp')}"
            f" / 戦闘 {integer('combat')}"
            f" / 対抗 {integer('counter')}%（召喚時）"
            f" / 思考パターン {integer('thought')}"
            f" / エッグ {egg_type}"
            f" / 卵補正 {bonus}"
            f" / 得意 {aptitude}"
            f" / 初登場 {debut_label}"
            f" / 性格 {text(profile.get('personality'), '不明', 24)}"
            f" / 趣味 {text(profile.get('hobby'), '不明', 24)}")

    def duel_lines(duel):
        if not isinstance(duel, dict):
            return []
        out = [duel_general('敵', duel.get('enemy_profile')),
               duel_general('味方', duel.get('ally_profile'))]
        egg_state = duel.get('egg_state')
        excluded = duel.get('excluded')
        verdict = duel.get('verdict')
        if excluded == 'boss':
            out.append("  卵落: 対象外（ボス戦）")
        elif excluded in ('enemy_unknown', 'ally_unknown'):
            side = '敵' if excluded == 'enemy_unknown' else '味方'
            out.append(f"  卵落: 判定不能（{side}将軍の最大HP不明）")
        elif egg_state == 'none':
            out.append("  卵落: 対象外（敵に卵なし）")
        elif egg_state == 'used':
            out.append("  卵落: 対象外（敵卵は使用済み） / 実際の落下 未確認")
        elif egg_state == 'dropped_unconfirmed':
            out.append("  卵落: 落下推定（未確認） / 実際の落下 未確認")
        else:
            enemy_max, ally_max = duel.get('enemy_max_hp'), duel.get('ally_max_hp')
            total, remainder = duel.get('max_hp_sum'), duel.get('remainder')
            if not all(type(v) is int for v in (enemy_max, ally_max, total, remainder)):
                out.append("  卵落: 判定不能（最大HPまたは余り不明）")
            else:
                parts = [f"敵最大HP {enemy_max} + 味方最大HP {ally_max} = {total}",
                         f"余り {remainder}", f"条件 卵落>{remainder}"]
                threat = duel.get('threat')
                if verdict == 'droppable':
                    dropper = text(duel.get('dropper'), '不明', 24)
                    row = next((card for card in duel.get('cards') or []
                                if isinstance(card, dict) and card.get('name') == dropper), {})
                    value = row.get('egg_drop') if isinstance(row, dict) else None
                    parts.append(f"予測 成立（{dropper} 卵落{value if type(value) is int else '不明'}）")
                elif threat is False:
                    parts.append("予測 対象外（卵を使わない思考）")
                elif threat is None or verdict == 'unknown':
                    parts.append("予測 判定不能（思考または入力不明）")
                elif duel.get('egg_drop_free') is False:
                    parts.append("予測 保留（チャート指定札のため開幕は使わない）")
                else:
                    parts.append("予測 不成立（携行札に卵落札なし）")
                parts.append('敵卵 保有（静的能力）' if duel.get('has_egg') is True
                             else '敵卵 不明')
                parts.append('実際の落下 未確認')
                out.append("  卵落: " + " / ".join(parts))
        reason = {'not_carried': '未携行', 'used': '使用済み', 'kit_unknown': '携行不明'}
        for card in duel.get('cards') or []:
            if not isinstance(card, dict):
                continue
            name = text(card.get('name'), '', 24)
            if not name:
                continue
            number = lambda key, fallback='不明': (str(card[key]) if type(card.get(key)) is int
                                                   else fallback)
            usable = card.get('usable')
            if usable is True:
                available = card.get('available')
                use = '使用可' + (f"（残{available}）" if type(available) is int and available > 1 else '')
            elif usable is False:
                use = '使用不可（' + reason.get(card.get('unavailable'), '理由不明') + '）'
            else:
                use = '可否 不明'
            drops = card.get('drops')
            egg_drop, remainder = card.get('egg_drop'), duel.get('remainder')
            if drops is True and type(egg_drop) is int and type(remainder) is int:
                prediction = f"成立（{egg_drop}>{remainder}）"
            elif drops is False and type(egg_drop) is int and type(remainder) is int:
                prediction = f"不成立（{egg_drop}>{remainder}）"
            else:
                prediction = '判定不能'
            out.append(
                f"  切り札: {name} / ID {number('id')} / 卵落 {number('egg_drop')}"
                f" / 在庫 {number('stock')} / 携行 {number('carried')}"
                f" / {use} / 予測 {prediction}")
        total_cards = duel.get('cards_total')
        shown = len(duel.get('cards') or [])
        if type(total_cards) is int and total_cards > shown:
            out.append(f"  切り札在庫: ほか {total_cards - shown}枚（実測分・表示上限）")
        return out

    # The general count comes from the game's own house roster. A partial page
    # is not a count (不明, never the page size). When this month's complete
    # list is not read yet, the brain's last complete list is used and marked
    # 前月 so a month change does not blank a confirmed number
    # (docich_corner_stats._hanjuku_generals).
    generals = value.get("generals")
    if type(generals) is int and generals >= 0:
        base, joins = value.get("generals_roster"), value.get("generals_joins")
        if type(joins) is int and joins > 0 and type(base) is int:
            # A confirmed 加入 is added to the last complete list, so a month
            # command that recruits still shows a number (not 不明).
            basis = f"{'前月' if value.get('generals_previous') is True else '名簿'}{base}人＋加入{joins}名"
        else:
            basis = "前月の完全名簿" if value.get("generals_previous") is True else "完全名簿"
        generals_text = f"{generals}人（{basis}）"
    else:
        generals_text = "不明"
    income = value.get("income")
    income_text = f"{income}G" if type(income) is int and income >= 0 else "不明"
    wages = value.get("wages")
    wages_text = f"{wages}G" if type(wages) is int and wages >= 0 else "不明"
    lines = [
        "半熟英雄 / 最終観測・記録",
        f"  第{num('chapter')}話 / 所持金 {num('gold')}G",
        f"  ゲーム内: {num('year')}年{num('month')}月（最終観測）",
        f"  兵力: {num('soldiers')}名 / 停滞 {num('unchanged_seconds')}秒",
        f"  将軍数: {generals_text}",
        f"  半熟レベル: {num('hanjuku_level')} / 半熟値: {num('hanjuku_value')}",
        f"  現在収入: {income_text}",
        f"  総賃金: {wages_text}",
        f"  占領記録 {num('captured')}城（現在の城数ではない）",
        f"  保有: {_hanjuku_join(value.get('captured_names'))}",
    ]
    gap = value.get('gap')
    if isinstance(gap, dict):
        lines.append(f"  投影余白: side={gap.get('side', 'right')} x={gap['left']} w={gap['width']} until={gap['until']:.3f}")
        march_count = gap.get('march_count')
        if type(march_count) is int and 0 <= march_count < 128:
            lines.append(f"  余白進軍数: {march_count}")
        route = gap.get('recall_route')
        if isinstance(route, dict):
            lines.append("  余白帰還: " + json.dumps(route, ensure_ascii=False, separators=(',', ':')))
        names = gap.get('captured_names') or []
        if names:
            lines.append("  余白占領記録: " + " / ".join(names))
        for g in gap.get('garrison') or []:
            lines.append(f"  余白駐留: until={g['until']:.3f} " + g['castle'] + "=" + "/".join(g['generals']))
        for m in gap.get('marching') or []:
            lines.append("  余白行軍: " + m['general'] + "→" + (m['target'] or '未確認'))
        for move in gap.get('moves') or []:
            if not isinstance(move, dict):
                continue
            general = text(move.get('general'), '', 24)
            if not general:
                continue
            source = text(move.get('source'), '', 24)
            target = text(move.get('target'), '', 24)
            status = {'en_route': '行軍中', 'launched_unconfirmed': '成立未確認',
                      'overdue': '到着未確認',
                      'tracking_unknown': '到着未確認（追跡期限超過）'}.get(move.get('status'), '')
            step = text(move.get('step'), '', 24)
            parts = [general, (source or '未確認') + '→' + (target or '未確認')]
            if status:
                parts.append(status)
            if step:
                parts.append('段階 ' + step)
            purpose = text(move.get('purpose'), '', 16)
            if purpose:
                parts.append('種別 ' + purpose)
            note = text(move.get('note'), '', 96)
            if note:
                parts.append('理由 ' + note)
            lines.append("  余白出撃: " + " / ".join(parts))
        for progress in (gap.get('flows') or [])[:6]:
            if not isinstance(progress, dict):
                continue
            key = text(progress.get('key'), '', 24)
            title = text(progress.get('title'), '', 24)
            # These records are rendered in a wrapping overlay, not a terminal cell.
            # Keep the 512-character sanitised upstream value without an ellipsis.
            stage = text(progress.get('stage'), '', 512)
            detail = text(progress.get('detail'), '', 512)
            if key and title and stage:
                parts = [key, title, stage]
                if detail:
                    parts.append(detail)
                lines.append("  余白進行: " + " / ".join(parts))
        # Action plans and policy reasons share one observation id; expose all
        # recent entries rather than treating page turns or monthly menus as
        # invisible mechanical work. The browser consumes JSON, not a slash
        # delimited or ellipsized terminal string.
        for item in (gap.get('decisions') or [])[:48]:
            if not isinstance(item, dict):
                continue
            if not isinstance(item.get('id'), str) or not isinstance(item.get('actions'), list):
                continue
            lines.append("  余白判断: " + json.dumps(item, ensure_ascii=False,
                                               separators=(',', ':')))
        active_order = gap.get('active_order')
        if isinstance(active_order, dict):
            purpose_labels = {'attack': '攻撃', 'retake': '奪還', 'move': '移動'}
            parts = []
            general = text(active_order.get('general'), '', 24)
            source = text(active_order.get('source'), '', 24)
            target = text(active_order.get('target'), '', 24)
            purpose = text(active_order.get('purpose'), '', 16)
            status = text(active_order.get('status'), '', 20)
            step = text(active_order.get('step'), '', 24)
            if general:
                parts.append(general)
            if source or target:
                parts.append((source or '未確認') + '→' + (target or '未確認'))
            if purpose:
                parts.append(purpose_labels.get(purpose, purpose))
            if status:
                parts.append('状態 ' + status)
            if step:
                parts.append('段階 ' + step)
            note = text(active_order.get('note'), '', 96)
            if note:
                parts.append('理由 ' + note)
            if parts:
                lines.append("  余白出撃意図: " + " / ".join(parts))
        hp = gap.get('hp')
        if hp and hp.get('enemy') and hp.get('ally'):
            lines.append(f"  余白交戦HP: until={hp['until']:.3f} {hp['enemy']} {hp['enemy_hp']} / {hp['ally']} {hp['ally_hp']}")
        detail = gap.get('battle')
        if isinstance(detail, dict):
            deadline = detail.get('until')
            valid_deadline = (type(deadline) in (int, float) and not isinstance(deadline, bool)
                              and math.isfinite(deadline))
            if valid_deadline:
                enemy_soldiers, ally_soldiers = detail.get('enemy_soldiers'), detail.get('ally_soldiers')
                if (type(enemy_soldiers) is int and type(ally_soldiers) is int
                        and 0 <= enemy_soldiers <= 1000000 and 0 <= ally_soldiers <= 1000000):
                    lines.append(
                        f"  余白交戦兵数: until={deadline:.3f} 敵 {enemy_soldiers} / 我 {ally_soldiers}"
                    )

                plan_parts = []
                side = detail.get('side')
                castle = text(detail.get('castle'), '', 24)
                if side in {'attack', 'defense'}:
                    side_label = '攻撃' if side == 'attack' else '防衛'
                    plan_parts.append(side_label + (f" {castle}" if castle else ''))
                elif castle:
                    plan_parts.append(f"城 {castle}")
                step = text(detail.get('step'), '', 24)
                variant = text(detail.get('variant'), '', 24)
                if step:
                    plan_parts.append(f"段階 {step}")
                if variant:
                    plan_parts.append(f"方針 {variant}")
                if plan_parts:
                    lines.append(
                        f"  余白戦闘計画: until={deadline:.3f} " + " / ".join(plan_parts)
                    )

                planned = [text(card, '', 16) for card in detail.get('planned_cards') or []]
                used = [text(card, '', 16) for card in detail.get('used_cards') or []]
                selected = [text(card, '', 16) for card in detail.get('selected_cards') or []]
                planned = [card for card in planned if card]
                used = [card for card in used if card]
                selected = [card for card in selected if card]
                card = text(detail.get('card'), '', 16)
                stage = text(detail.get('card_stage'), '', 16)
                card_parts = []
                if planned:
                    card_parts.append("予定 " + "・".join(planned))
                if used:
                    card_parts.append("使用 " + "・".join(used))
                if selected:
                    card_parts.append("選択 " + "・".join(selected))
                if card:
                    card_parts.append("現在 " + card + (f"({stage})" if stage else ""))
                if card_parts:
                    lines.append(
                        f"  余白切り札: until={deadline:.3f} " + " / ".join(card_parts)
                    )

                deviation = text(detail.get('deviation'), '', 96)
                if deviation:
                    lines.append(f"  余白戦闘判断: until={deadline:.3f} {deviation}")

                egg_parts = []
                if detail.get('egg_battle') is True:
                    egg_parts.append("敵召喚を確認")
                guard_stage = detail.get('egg_guard_stage')
                guard_labels = {
                    'opening': '奥の手を開く',
                    'menu': '奥の手メニュー',
                    'selected': '奥の手選択済み',
                    'completed': '奥の手処理完了',
                }
                if guard_stage in guard_labels:
                    egg_parts.append(guard_labels[guard_stage])
                if detail.get('egg_guard_exhausted') is True:
                    egg_parts.append("奥の手確認上限")
                if egg_parts:
                    lines.append(
                        f"  余白卵対策: until={deadline:.3f} " + " / ".join(egg_parts)
                    )
    if value.get("home_lost") is True:
        lines.append("  本拠: 失陥（記録）")
    lost = row("lost_names")
    if lost:
        lines.append(f"  失った城: {_hanjuku_join(lost)}")
    # 戦闘結果 counts only battles whose final HP panel was decisive, so it can
    # read "0敗" while castles are visibly falling. Always show the judged
    # subset next to the started total, and the separately observed castle
    # losses, instead of letting the subset stand in for the whole run.
    unjudged = value.get("battles_unjudged")
    account = f"（判定 {num('battles_judged')}"
    if unjudged:
        account += f" / 未判定 {num('battles_unjudged')}"
    account += "）"
    lines += [
        f"  戦闘結果: {num('wins')}勝 / {num('losses')}敗 / 未分類 {num('unclassified')}{account}",
        f"  戦闘: 開始 {num('battles_started')} / 終了 {num('battles_finished')}"
        f" / 切り札確定 {num('cards_confirmed')}",
    ]
    castle_losses = value.get("castle_losses")
    if type(castle_losses) is int and castle_losses > 0:
        lines.append(f"  城失陥: {castle_losses}件（全体マップで旗が敵色になった実測）")
    enemy_hp, ally_hp = value.get("enemy_hp"), value.get("ally_hp")
    enemy_name, ally_name = label("enemy"), label("ally")
    if (type(enemy_hp) is int and type(ally_hp) is int
            and enemy_name != "不明" and ally_name != "不明"):
        lines.append(f"  交戦HP: 敵 {enemy_hp}（{enemy_name}） / 我 {ally_hp}（{ally_name}）")
    else:
        lines.append("  交戦HP: 戦闘記録なし")
    lines += duel_lines(value.get("duel"))
    garrison = [g for g in row("garrison") if isinstance(g, dict)]
    if garrison:
        lines.append("  駐留: " + " / ".join(
            f"{text(g.get('castle'), '?')}={_hanjuku_join([text(n, '?') for n in g.get('generals') or []], '?')}"
            for g in garrison))
    marching = [s for s in row("marching") if isinstance(s, dict)]
    if marching:
        lines.append("  行軍中: " + " / ".join(
            f"{text(s.get('general'), '?')}→{text(s.get('target'), '未確定')}"
            for s in marching))
    active_order = value.get("active_order")
    if isinstance(active_order, dict):
        purpose_labels = {'attack': '攻撃', 'retake': '奪還', 'move': '移動'}
        intent = []
        general = text(active_order.get('general'), '', 24)
        source = text(active_order.get('source'), '', 24)
        target = text(active_order.get('target'), '', 24)
        purpose = text(active_order.get('purpose'), '', 16)
        status = text(active_order.get('status'), '', 20)
        step = text(active_order.get('step'), '', 24)
        if general:
            intent.append(general)
        if source or target:
            intent.append((source or '未確認') + '→' + (target or '未確認'))
        if purpose:
            intent.append(purpose_labels.get(purpose, purpose))
        if status:
            intent.append('状態 ' + status)
        if step:
            intent.append('段階 ' + step)
        note = text(active_order.get('note'), '', 96)
        if note:
            intent.append('理由 ' + note)
        if intent:
            lines.append("  出撃意図: " + " / ".join(intent))
    eggs = [e for e in row("eggs") if isinstance(e, dict) and type(e.get("uses")) is int]
    if eggs:
        lines.append("  卵: " + " / ".join(
            f"{text(e.get('general'), '?')} {e['uses']}回" for e in eggs))
    lines += [
        f"  出撃: 成立 {num('orders_launched')} / 失敗 {num('orders_failed')}",
        f"  画面: {label('screen')} / {label('phase')}"
        + (f" / 方針 {label('variant')}" if label("variant") != "不明" else ""),
        f"  計画段階: {label('chart_step')}（完了未確認）",
        f"  保留計画: {label('pending_plan')}",
        f"  実入力: {num('actions')}回 / 観測 {num('age')}秒前（{num('observations')}回）",
    ]
    return lines

def render_console_progress(value):
    """Four short record/plan lines for the terminal and common overlay."""
    if not isinstance(value, dict):
        return []
    n = _corner_int(value.get("session_count"))
    mean, best = value.get("session_mean"), value.get("session_best")
    result = f"Session: n={n} / best={_corner_number(best) if best is not None else '--'}"
    result += f" / mean={_corner_number(mean) if mean is not None else '--'}"
    if value.get("history_status") != "readable":
        result += " (history unavailable)"
    latest = value.get("latest")
    if isinstance(latest, dict):
        at = datetime.fromtimestamp(latest["at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        observed = f"Result: {latest['score']} @ {at} / {latest['age']}s ago"
    else:
        observed = "Result: no timestamped result in this session"
    steps = {"switch-wait": "switch completion pending", "restore-wait": "restoration pending",
             "recovery-wait": "recovery required", "unverified": "runtime unverified",
             "target-recorded": "target recorded; corner completion pending",
             "deadline-passed": "limit passed; completion unconfirmed",
             "collect-results": "collect results"}
    planned = "Plan: " + steps.get(value.get("next"), "unverified")
    left = value.get("remaining")
    secs = value.get("remaining_seconds")
    if value.get("next") == "collect-results":
        if left is not None:
            planned += f" / {left} matches left"
        if secs is not None:
            planned += f" / limit {secs//60}:{secs%60:02}"
    # Records, plans and current observation have deliberately different labels.
    reason = value.get("reason")
    evidence = "Evidence: completed results / live score unobserved"
    if reason:
        reasons = {"game_over": "game over", "screen_stalled": "screen stalled",
                   "manual_saved_stop": "saved stop", "manual_forced_stop": "forced stop",
                   "switch-terminal-before-corner-active": "switch stopped",
                   "recovery_required": "recovery needed", "quiesce_failed": "quiesce failed",
                   "readiness_timeout": "ready timeout", "deadline_exceeded": "deadline passed"}
        evidence = f"Reason: {reasons.get(reason, 'unknown')} (record) / live score unobserved"
    return [result, observed, planned, evidence]

def render_docich_corner_stats(corner):
    """Render a corner-owned stats feed without consulting Soren score history."""
    if not isinstance(corner, dict):
        return []

    kind = str(corner.get("kind") or "")
    if kind == "conflict":
        return [
            "SOREN/CORNER: STATE CONFLICT",
            "Live: multiple active corners; stats locked",
            "Score Timeline",
            "  corner data unavailable until one state is active",
        ]
    if kind == "invalid":
        return [
            "SOREN/CORNER: STATE INVALID",
            "Live: corner state cannot be trusted; stats locked",
            "Score Timeline",
            "  corner data unavailable until state recovery",
        ]

    label = _corner_short(corner.get("label"), "CORNER", 16)
    status = _corner_status_label(corner.get("status"))
    lines = []

    if kind == "retro":
        game = _corner_short(corner.get("game"), "unknown", 22)
        scores = corner.get("scores") if isinstance(corner.get("scores"), list) else []
        score_values = [
            int(item["score"])
            for item in scores
            if isinstance(item, dict) and isinstance(item.get("score"), int)
        ]
        target = _corner_int(corner.get("target_matches"))
        matches = _corner_int(corner.get("session_matches"))
        progress = f"{matches}/{target}" if target != "--" else matches
        lines += [
            f"SOREN/CORNER: {label} / {game} / {status}",
            f"{'Record' if corner.get('console') else 'Live'}: this corner matches {progress}",
        ]
        lines += render_console_progress(corner.get("console"))
        if corner.get("game") == "hanjuku-hero":
            lines += render_hanjuku_status(corner.get("hanjuku"))
        # Hanjuku is a scripted corner: it never appends to scores/<game>.jsonl
        # (docich retro_corner waits for game over, not a scorelog target), so
        # the score timeline/distribution panels stay empty forever.  The card
        # above already carries this run's observed progress, so skip the empty
        # panels instead of printing "no completed results yet" every refresh.
        else:
            lines += _corner_score_panels(score_values)
        ranking = corner.get("strategy_ranking") if isinstance(corner.get("strategy_ranking"), list) else []
        if ranking:
            top = ranking[:3]
            lines.append(
                "Strategy: "
                + " / ".join(
                    f"#{index} {str(row.get('key') or '')[:8]} avg={int(float(row.get('best') or 0))}"
                    for index, row in enumerate(top, 1)
                    if isinstance(row, dict)
                )
            )
        elif corner.get("game") != "hanjuku-hero":
            lines.append("Strategy: no corner ranking data")
        return lines

    if kind == "nethack":
        run = corner.get("run") if isinstance(corner.get("run"), dict) else {}
        scores = corner.get("scores") if isinstance(corner.get("scores"), list) else []
        expedition = _corner_int(run.get("expedition"))
        run_status = _corner_short(run.get("status"), "waiting", 16)
        score = _corner_int(run.get("score"))
        turns = _corner_int(run.get("turns"))
        depth = _corner_int(run.get("max_depth"))
        lines += [
            f"SOREN/CORNER: {label} / expedition {expedition} / {status}",
            f"Live: run={run_status} score={score} turns={turns} depth={depth}",
        ]
        lines += _corner_score_panels(scores)
        return lines

    if kind == "paper":
        paper = corner.get("paper") if isinstance(corner.get("paper"), dict) else {}
        fills = paper.get("fills") if isinstance(paper.get("fills"), list) else []
        positions = paper.get("positions") if isinstance(paper.get("positions"), dict) else {}
        signal = paper.get("signal") if isinstance(paper.get("signal"), dict) else {}
        symbol_states = (
            paper.get("symbol_states") if isinstance(paper.get("symbol_states"), list) else []
        )
        skipped = paper.get("skipped") if isinstance(paper.get("skipped"), list) else []
        worker_summary = paper.get("worker") if isinstance(paper.get("worker"), dict) else {}
        freshness = paper.get("freshness") if isinstance(paper.get("freshness"), dict) else {}
        fresh_counts = freshness.get("counts") if isinstance(freshness.get("counts"), dict) else {}
        fresh_issues = freshness.get("issues") if isinstance(freshness.get("issues"), list) else []
        coverage = paper.get("coverage") if isinstance(paper.get("coverage"), dict) else {}
        performance = paper.get("performance") if isinstance(paper.get("performance"), dict) else {}

        fill_lines = [_corner_fill_line(fill) for fill in reversed(fills[-4:])]
        fill_lines = [line for line in fill_lines if line]
        worker = _paper_worker_label(paper.get("status"))
        capital = _corner_short(paper.get("capital"), "?", 14)
        deployed = _corner_short(paper.get("deployed"), "?", 14)
        capital_number = _paper_number(paper.get("capital"))
        deployed_number = _paper_number(paper.get("deployed"))
        free = (
            _paper_amount(max(0.0, capital_number - deployed_number))
            if capital_number is not None and deployed_number is not None
            else "?"
        )

        lines += [
            f"SOREN/CORNER: {label} / PAPER / {status}",
            "  ASSET / PAPER ONLY",
            "Funds: " + _corner_short(f"capital={capital} free={free}", limit=49),
            "  Deployed: " + _corner_short(deployed, "?", 43),
            f"Positions: {len(positions)} / markets={_corner_int(paper.get('market_count'))}",
        ]
        for symbol, amount in list(sorted(positions.items()))[:3]:
            lines.append("  Pos: " + _corner_short(f"{symbol} {amount}", limit=48))
        if len(positions) > 3:
            lines.append(f"  Pos: +{len(positions) - 3} more")
        if performance:
            lines.append(
                "P/L: "
                + _corner_short(
                    "cum="
                    + _paper_signed_amount(performance.get("cumulative_pnl_jpy"))
                    + " unreal="
                    + _paper_signed_amount(performance.get("unrealized_pnl_jpy")),
                    limit=49,
                )
            )
            lines.append(
                "  Realized: "
                + _corner_short(
                    "today="
                    + _paper_signed_amount(performance.get("today_realized_pnl_jpy"))
                    + " all="
                    + _paper_signed_amount(performance.get("realized_total_jpy")),
                    limit=43,
                )
            )
            valuation = (
                _corner_int(performance.get("valued_positions"))
                + "/"
                + _corner_int(performance.get("position_count"))
            )
            lines.append(
                "  Equity: "
                + _corner_short(
                    _paper_amount(performance.get("equity_jpy"), fallback="--")
                    + f" / valued={valuation} / age={_paper_age(performance.get('age'))}",
                    limit=45,
                )
            )
            if performance.get("complete") is False:
                lines.append("  Valuation: incomplete / P/L may be partial")

        lines += [
            "  BOT DECISION",
            (
                "Signals: candidate="
                + _paper_count(signal, "candidate_count")
                + " selected="
                + _paper_count(signal, "selected_count")
                + " rejected="
                + _paper_count(signal, "rejected_count")
            ),
        ]
        state_labels = {
            "no_signal": "no signal",
            "candidate": "candidate",
            "selected": "SELECTED",
            "rejected_after_signal": "REJECTED",
        }
        for item in symbol_states[:4]:
            if not isinstance(item, dict):
                continue
            symbol = _corner_short(item.get("symbol"), "?", 12)
            state_key = str(item.get("state") or "unknown")
            state_text = state_labels.get(state_key, _corner_short(state_key, "unknown", 12))
            reason = _corner_short(item.get("reason_code"), "", 20)
            detail = f"{state_text}:{reason}" if reason else state_text
            lines.append("  " + _corner_short(f"{symbol} -> {detail}", limit=49))
        if len(symbol_states) > 4:
            lines.append(f"  +{len(symbol_states) - 4} markets")

        strategies = signal.get("strategy_ids") if isinstance(signal.get("strategy_ids"), list) else []
        if strategies:
            lines.append("  Strategy: " + _corner_short(" / ".join(strategies), limit=43))
        reasons = (
            signal.get("candidate_reason_codes")
            if isinstance(signal.get("candidate_reason_codes"), list)
            else []
        )
        if reasons:
            lines.append("  Candidate: " + _corner_short(" / ".join(reasons), limit=42))
        if skipped:
            skip_parts = []
            for item in skipped[:2]:
                if not isinstance(item, dict):
                    continue
                symbol = _corner_short(item.get("symbol"), "", 12)
                reason = _corner_short(item.get("reason_code"), "unknown", 24)
                skip_parts.append(f"{symbol}:{reason}" if symbol else reason)
            if skip_parts:
                lines.append("  Rejected: " + _corner_short(" / ".join(skip_parts), limit=43))

        lines += [
            "  OPS / DATA",
            f"Worker: {worker} cycle={_paper_count(worker_summary, 'cycle_index')}",
            (
                f"Age: data={_paper_age(paper.get('snapshot_age'))} "
                f"hb={_paper_age(paper.get('heartbeat_age'))} "
                f"next={_paper_age(worker_summary.get('next_cycle_in'))}"
            ),
        ]
        freshness_total = sum(
            int(fresh_counts.get(key) or 0)
            for key in ("fresh", "stale", "missing", "invalid", "unknown")
            if not isinstance(fresh_counts.get(key), bool)
        )
        market_total = freshness_total or _corner_int(paper.get("market_count"))
        lines.append(
            "Markets: fresh="
            + _corner_int(fresh_counts.get("fresh"))
            + "/"
            + str(market_total)
            + " frame_err="
            + _paper_count(worker_summary, "frame_error_count")
        )
        cycle_activity = []
        for key, name in (
            ("arbitrage_candidate_count", "arb"),
            ("new_fill_count", "fills"),
            ("new_settlement_count", "settlements"),
        ):
            if key in worker_summary:
                cycle_activity.append(f"{name}={_paper_count(worker_summary, key)}")
        if cycle_activity:
            lines.append("  Cycle: " + _corner_short(" ".join(cycle_activity), limit=43))

        experiment_parts = []
        experiment_status = worker_summary.get("experiment_status")
        if experiment_status:
            experiment_parts.append("status=" + _corner_short(experiment_status, limit=16))
        entries_allowed = worker_summary.get("experiment_entries_allowed")
        if type(entries_allowed) is bool:
            experiment_parts.append("entries=" + ("on" if entries_allowed else "off"))
        experiment_reason = worker_summary.get("experiment_reason_code")
        if experiment_reason:
            experiment_parts.append("reason=" + _corner_short(experiment_reason, limit=18))
        if experiment_parts:
            lines.append("  Experiment: " + _corner_short(" ".join(experiment_parts), limit=40))
        attempted = _corner_int(coverage.get("attempted"))
        total = _corner_int(coverage.get("total"))
        if attempted != "--" or total != "--" or coverage.get("budget_exceeded"):
            lines.append(
                f"  Coverage: {attempted}/{total}"
                + (" / budget exceeded" if coverage.get("budget_exceeded") else "")
            )
        if fresh_issues:
            issue_parts = []
            for item in fresh_issues[:2]:
                if not isinstance(item, dict):
                    continue
                symbol = _corner_short(item.get("symbol"), "?", 12)
                reason = _corner_short(item.get("reason_code"), "unknown", 22)
                issue_parts.append(f"{symbol}:{reason}")
            if issue_parts:
                lines.append("  Market issue: " + _corner_short(" / ".join(issue_parts), limit=38))
        errors = (
            worker_summary.get("error_codes")
            if isinstance(worker_summary.get("error_codes"), list)
            else []
        )
        if errors:
            lines.append("  Errors: " + _corner_short(" / ".join(errors), limit=45))

        lines += [
            f"Recent: fills={len(fills)} positions={len(positions)}",
            "  PAPER FILL HISTORY (newest)",
        ]
        lines += [f"  {line}" for line in fill_lines] or ["  (no fills yet)"]
        return lines

    if kind == "soren91":
        game = _corner_short(corner.get("game"), "soren91", 20)
        lines += [
            f"SOREN/CORNER: {label} / {game} / {status}",
            f"Live: this corner results {_corner_int(corner.get('session_matches'))}",
        ]
        scores = corner.get("scores") or []
        lines += _corner_score_panels([item["score"] for item in scores], rank=True, compact=True)
        return lines

    if kind == "jev":
        game = _corner_short(corner.get("game"), "sorengame", 20)
        policy = _corner_short(corner.get("policy"), "unknown", 12)
        generation = _corner_int(corner.get("player_generation"))
        lines += [
            f"SOREN/CORNER: {label} / {game} / {status}",
            f"Live: player policy={policy} generation={generation}",
        ]
        scores = corner.get("scores") or []
        lines += _corner_score_panels([item["score"] for item in scores], reports=True, compact=True)
        return lines

    return [
        "SOREN/CORNER: UNKNOWN",
        "Live: corner kind is not supported; stats locked",
    ]

