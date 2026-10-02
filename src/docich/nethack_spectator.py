"""Viewer-only NetHack spectator renderer.

This module deliberately does not participate in gameplay observation or input.
It converts an already-observed TTY frame into a browser/OBS-friendly HTML
presentation.  The default visual mode uses Docich's original SVG tile set;
ASCII remains available as an immediate fallback.  Neither mode is an AI
semantic decoder because NetHack TTY glyphs are context-sensitive.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .nethack_tiles import TILESET_NAME, sprite_atlas_html, tile_css, tile_markup

DEFAULT_COLS = 80
DEFAULT_ROWS = 24
VISUAL_MODES = frozenset({"tiles", "ascii"})


@dataclass(frozen=True)
class Cell:
    x: int
    y: int
    char: str
    kind: str


@dataclass(frozen=True)
class Frame:
    cols: int
    rows: int
    cells: tuple[Cell, ...]
    message: str
    status: tuple[str, ...]
    frame_kind: str = "map"
    tty_lines: tuple[str, ...] = ()


def _has_menu_or_text_prompt(lines: tuple[str, ...]) -> bool:
    """Fail closed to full TTY for common NetHack non-map screens."""
    markers = (
        "inventory",
        "what do you want",
        "which do you want",
        "which direction",
        "choose an item",
        "select an item",
        "pick up what",
        "really attack",
        "really quit",
        "enter your name",
        "what is your name",
        "you die",
        "you were",
        "goodbye",
        "game over",
        "--more--",
    )
    text = "\n".join(lines).casefold()
    return any(marker in text for marker in markers)


def _is_confident_map(lines: tuple[str, ...]) -> bool:
    """Recognize only a visibly structured map with one player glyph.

    Menus, prompts and tombstones are displayed as the complete terminal. A
    single ``@`` is not enough evidence to interpret an arbitrary text screen
    as a dungeon map.
    """
    if len(lines) < 4 or _has_menu_or_text_prompt(lines):
        return False
    map_lines = lines[1:-2]
    body = "\n".join(map_lines)
    if body.count("@") != 1:
        return False
    structure = sum(body.count(char) for char in ".#-+<>^")
    structured_rows = sum(
        any(char in line for char in ".#-+<>^") for line in map_lines
    )
    return structure >= 3 and structured_rows >= 2


def classify_char(char: str) -> str:
    """Return a presentation-only coarse cell class.

    NetHack characters are context-sensitive, so this is intentionally not an
    AI semantic decoder. Ambiguous glyphs remain coarse visual classes.
    """
    if char == "@":
        return "player"
    if char in ".#":
        return "floor"
    if char in "-|":
        return "wall"
    if char == "+":
        return "door"
    if char in "<>":
        return "stairs"
    if char in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ&;:'":
        return "creature"
    if char in ")([=/%!?*$\"":
        return "item"
    if char == "^":
        return "trap"
    if char == " ":
        return "void"
    return "other"


def parse_tty(text: str, *, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS) -> Frame:
    if type(cols) is not int or cols < 1:
        raise ValueError("cols must be a positive integer")
    if type(rows) is not int or rows < 3:
        raise ValueError("rows must be an integer >= 3")

    raw_lines = text.splitlines()
    lines = [(line[:cols]).ljust(cols) for line in raw_lines[:rows]]
    while len(lines) < rows:
        lines.append(" " * cols)

    # NetHack's classic TTY layout normally reserves the first row for a
    # message and the final two rows for status. Keeping this extraction
    # conservative makes malformed/partial captures fail soft for presentation.
    tty_lines = tuple(lines)
    message = lines[0].rstrip()
    status = tuple(line.rstrip() for line in lines[-2:] if line.rstrip())
    map_lines = lines[1:-2]

    if not _is_confident_map(tty_lines):
        return Frame(
            cols=cols,
            rows=rows,
            cells=(),
            message=message,
            status=status,
            frame_kind="text",
            tty_lines=tty_lines,
        )

    cells: list[Cell] = []
    for y, line in enumerate(map_lines):
        for x, char in enumerate(line):
            cells.append(Cell(x=x, y=y, char=char, kind=classify_char(char)))

    return Frame(
        cols=cols,
        rows=len(map_lines),
        cells=tuple(cells),
        message=message,
        status=status,
        frame_kind="map",
        tty_lines=tty_lines,
    )


def blank_frame(
    message: str,
    *,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
) -> Frame:
    """Create an empty viewer frame without inventing gameplay state."""
    if type(cols) is not int or cols < 1:
        raise ValueError("cols must be a positive integer")
    if type(rows) is not int or rows < 3:
        raise ValueError("rows must be an integer >= 3")
    map_rows = rows - 3
    cells = tuple(
        Cell(x=x, y=y, char=" ", kind="void")
        for y in range(map_rows)
        for x in range(cols)
    )
    return Frame(
        cols=cols,
        rows=map_rows,
        cells=cells,
        message=message,
        status=(),
        frame_kind="placeholder",
        tty_lines=(),
    )


def _cell_markup(cell: Cell, visual_mode: str) -> str:
    if visual_mode == "tiles":
        body = tile_markup(cell.kind, cell.char)
    else:
        body = html.escape(cell.char) if cell.char != " " else "&nbsp;"
    return (
        f'<span class="cell {cell.kind}" data-x="{cell.x}" data-y="{cell.y}" '
        f'data-glyph="{html.escape(cell.char, quote=True)}">{body}</span>'
    )


def render_html(
    frame: Frame,
    *,
    title: str = "NetHack",
    auto_refresh_ms: int | None = None,
    visual_mode: str = "tiles",
) -> str:
    if visual_mode not in VISUAL_MODES:
        raise ValueError(f"visual_mode must be one of {sorted(VISUAL_MODES)}")
    if auto_refresh_ms is not None and (
        type(auto_refresh_ms) is not int or not 100 <= auto_refresh_ms <= 60_000
    ):
        raise ValueError("auto_refresh_ms must be an integer between 100 and 60000")

    cells = [_cell_markup(cell, visual_mode) for cell in frame.cells]
    status_html = "".join(f"<div>{html.escape(line)}</div>" for line in frame.status)
    refresh_script = ""
    if auto_refresh_ms is not None:
        refresh_script = (
            "<script>window.setTimeout(function(){window.location.reload();},"
            f"{auto_refresh_ms});</script>"
        )
    atlas = sprite_atlas_html() if visual_mode == "tiles" else ""
    tile_styles = tile_css() if visual_mode == "tiles" else ""
    body_class = f"mode-{visual_mode}"
    tileset_meta = (
        f'<meta name="docich-tileset" content="{html.escape(TILESET_NAME, quote=True)}">'
        if visual_mode == "tiles"
        else ""
    )
    if frame.frame_kind == "text":
        screen_html = (
            '<main class="tty-frame"><pre class="tty-screen" '
            f'data-cols="{frame.cols}" data-rows="{frame.rows}">'
            f'{html.escape("\n".join(frame.tty_lines))}</pre></main>'
        )
    else:
        screen_html = f"""<div class="wrap">
  <div class="message">{html.escape(frame.message) or '&nbsp;'}</div>
  <div class="board">{''.join(cells)}</div>
  <div class="status">{status_html or '<div>&nbsp;</div>'}</div>
</div>"""
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{tileset_meta}
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: dark; background:#07090c; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:#07090c; color:#f3f4f6; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
.wrap {{ width:100vw; height:100vh; display:grid; grid-template-rows:auto 1fr auto; gap:12px; padding:18px; }}
.message,.status {{ background:#10151d; border:1px solid #263141; border-radius:10px; padding:10px 14px; box-shadow:0 7px 24px rgba(0,0,0,.24); }}
.message {{ min-height:42px; font-size:18px; }}
.status {{ display:flex; justify-content:space-between; gap:24px; font-size:16px; }}
.board {{ align-self:center; justify-self:center; display:grid; grid-template-columns:repeat({frame.cols}, minmax(0,1fr)); width:min(96vw,1600px); aspect-ratio:{frame.cols}/{max(frame.rows,1)}; background:#020407; border:1px solid #263141; border-radius:8px; overflow:hidden; box-shadow:0 12px 44px rgba(0,0,0,.34); }}
.cell {{ position:relative; display:grid; place-items:center; min-width:0; min-height:0; font-size:clamp(6px,1.05vw,18px); line-height:1; overflow:hidden; }}
.tty-frame {{ width:100vw; height:100vh; display:grid; place-items:center; overflow:hidden; background:#020407; }}
.tty-screen {{ margin:0; max-width:100vw; max-height:100vh; overflow:hidden; white-space:pre; color:#f3f4f6; font-size:min(1.8vw,2.8vh,24px); line-height:1.15; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
.mode-ascii .player {{ background:#253347; font-weight:800; border-radius:20%; }}
.mode-ascii .floor {{ color:#7b8492; }}
.mode-ascii .wall {{ color:#c1c7d0; background:#171c24; }}
.mode-ascii .door {{ color:#d8b36a; background:#211a10; }}
.mode-ascii .stairs {{ color:#7dd3fc; font-weight:800; }}
.mode-ascii .creature {{ color:#fb7185; font-weight:700; }}
.mode-ascii .item {{ color:#fde68a; }}
.mode-ascii .trap {{ color:#c084fc; }}
.mode-ascii .other {{ color:#d1d5db; }}
.mode-ascii .void {{ color:transparent; }}
.mode-tiles .cell {{ background:#070b10; border:1px solid rgba(255,255,255,.015); }}
.mode-tiles .cell.void {{ background:#020407; border-color:transparent; }}
{tile_styles}
</style>
{refresh_script}
</head>
<body class="{body_class}">
{atlas}
{screen_html}
</body>
</html>
"""


def render_live_shell(
    *,
    runtime_id: str,
    generation: int,
    presentation_epoch: str,
    poll_interval_ms: int = 500,
    stale_after_ms: int = 3000,
) -> str:
    """Return one fixed browser shell for the bounded ``/frame`` endpoint.

    The document never reloads for an ordinary frame. TTY strings enter the
    DOM through ``textContent``; SVG nodes are created from the fixed tile
    atlas and the server's bounded, validated snapshot schema.
    """
    if not isinstance(runtime_id, str) or not re.fullmatch(
        r"[A-Za-z0-9._-]{1,96}", runtime_id
    ):
        raise ValueError("runtime_id is invalid")
    if type(generation) is not int or generation < 1:
        raise ValueError("generation must be a positive integer")
    if not isinstance(presentation_epoch, str) or not re.fullmatch(
        r"[A-Za-z0-9._-]{1,96}", presentation_epoch
    ):
        raise ValueError("presentation_epoch is invalid")
    if type(poll_interval_ms) is not int or not 100 <= poll_interval_ms <= 2000:
        raise ValueError("poll_interval_ms must be between 100 and 2000")
    if type(stale_after_ms) is not int or not 1000 <= stale_after_ms <= 10_000:
        raise ValueError("stale_after_ms must be between 1000 and 10000")

    runtime_id_json = json.dumps(runtime_id, ensure_ascii=True)
    generation_json = str(generation)
    epoch_json = json.dumps(presentation_epoch, ensure_ascii=True)
    script = r"""(() => {
  'use strict';
  const expectedRuntimeId = __RUNTIME_ID__;
  const expectedGeneration = __GENERATION__;
  const expectedEpoch = __EPOCH__;
  const staleAfterMs = __STALE__;
  const pollIntervalMs = __POLL__;
  const kinds = new Set(['void','player','floor','wall','door','stairs','creature','item','trap','other']);
  const tileKeys = new Set(['void','player','floor','corridor','wall-horizontal','wall-vertical','door','stairs-up','stairs-down','creature','weapon','armor','tool','ring','wand','food','potion','scroll','gem','gold','amulet','item','trap','other']);
  const shell = document.getElementById('screen');
  const notice = document.getElementById('notice');
  const message = document.getElementById('message');
  const board = document.getElementById('board');
  const tty = document.getElementById('tty');
  const status = document.getElementById('status');
  let lastCaptureSeq = -1;
  let lastContentSeq = -1;
  let lastFrameAt = null;

  function clearFrame(text) {
    board.replaceChildren();
    status.replaceChildren();
    tty.textContent = '';
    tty.hidden = true;
    board.hidden = true;
    message.textContent = '';
    message.hidden = true;
    notice.textContent = text;
    notice.hidden = false;
    lastContentSeq = -1;
    lastFrameAt = null;
  }

  function appendTile(cell) {
    const div = document.createElement('span');
    const kind = kinds.has(cell.kind) ? cell.kind : 'other';
    div.className = 'cell ' + kind;
    div.dataset.x = String(cell.x);
    div.dataset.y = String(cell.y);
    const glyph = typeof cell.glyph === 'string' ? cell.glyph : '?';
    div.dataset.glyph = glyph;
    const key = tileKeys.has(cell.tile_key) ? cell.tile_key : 'other';
    if (key === 'void') {
      div.classList.add('void');
    } else {
      const wrap = document.createElement('span');
      wrap.className = 'tile-wrap';
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('class', 'tile-svg');
      svg.setAttribute('viewBox', '0 0 24 24');
      svg.setAttribute('aria-hidden', 'true');
      const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
      use.setAttribute('href', '#tile-' + key);
      svg.appendChild(use);
      wrap.appendChild(svg);
      if ((kind === 'creature' || kind === 'other') && glyph.trim()) {
        const badge = document.createElement('span');
        badge.className = 'tile-glyph';
        badge.textContent = glyph;
        wrap.appendChild(badge);
      }
      div.appendChild(wrap);
    }
    board.appendChild(div);
  }

  function draw(payload) {
    if (payload.frame_kind === 'map' && Array.isArray(payload.cells)) {
      board.replaceChildren();
      board.style.gridTemplateColumns = 'repeat(' + payload.cols + ', minmax(0, 1fr))';
      board.style.aspectRatio = payload.cols + ' / ' + Math.max(payload.rows, 1);
      for (const cell of payload.cells) appendTile(cell);
      tty.hidden = true;
      tty.textContent = '';
      board.hidden = false;
      message.textContent = typeof payload.message === 'string' ? payload.message : '';
      message.hidden = false;
      status.replaceChildren();
      for (const line of Array.isArray(payload.status_lines) ? payload.status_lines : []) {
        const item = document.createElement('div');
        item.textContent = typeof line === 'string' ? line : '';
        status.appendChild(item);
      }
      return;
    }
    if (payload.frame_kind === 'text' && Array.isArray(payload.tty_lines)) {
      board.replaceChildren();
      board.hidden = true;
      message.hidden = true;
      status.replaceChildren();
      tty.textContent = payload.tty_lines.join('\n');
      tty.hidden = false;
      return;
    }
    clearFrame('NetHack画面を準備しています。');
  }

  function ageOut() {
    if (lastFrameAt !== null && performance.now() - lastFrameAt > staleAfterMs) {
      clearFrame('表示更新が停止しています。');
    }
  }

  async function poll() {
    const started = performance.now();
    try {
      const response = await fetch('/frame', {cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok) throw new Error('frame unavailable');
      const payload = await response.json();
      if (payload.runtime_id !== expectedRuntimeId ||
          payload.generation !== expectedGeneration ||
          payload.presentation_epoch !== expectedEpoch ||
          !Number.isSafeInteger(payload.capture_seq) ||
          payload.capture_seq < lastCaptureSeq ||
          !Number.isSafeInteger(payload.content_seq) ||
          payload.content_seq < lastContentSeq) return;
      const age = Number.isFinite(payload.capture_age_ms) ? Math.max(0, payload.capture_age_ms) : Infinity;
      lastFrameAt = performance.now() - age;
      lastCaptureSeq = payload.capture_seq;
      if (payload.state === 'standby' || payload.state === 'unavailable' || age > staleAfterMs) {
        clearFrame(payload.reason === 'runtime_not_committed' ? 'NetHackコーナー待機中です。' : 'NetHack画面を確認しています。');
      } else {
        if (payload.content_seq !== lastContentSeq) draw(payload);
        lastContentSeq = payload.content_seq;
        if (payload.state === 'stale') {
          notice.textContent = '画面の取得を確認しています。';
          notice.hidden = false;
        } else {
          notice.hidden = true;
        }
      }
    } catch (_) {
      ageOut();
    } finally {
      window.setTimeout(poll, Math.max(0, pollIntervalMs - (performance.now() - started)));
    }
  }

  window.setInterval(ageOut, 100);
  clearFrame('NetHack画面を準備しています。');
  poll();
})();"""
    script = script.replace("__RUNTIME_ID__", runtime_id_json)
    script = script.replace("__GENERATION__", generation_json)
    script = script.replace("__EPOCH__", epoch_json)
    script = script.replace("__STALE__", str(stale_after_ms))
    script = script.replace("__POLL__", str(poll_interval_ms))
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="docich-tileset" content="{html.escape(TILESET_NAME, quote=True)}">
<title>NetHack</title><style>
:root {{ color-scheme:dark; background:#07090c; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:#07090c; color:#f3f4f6; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
.shell {{ position:relative; width:100vw; height:100vh; overflow:hidden; }}
.message,.status,.notice {{ background:#10151d; border:1px solid #263141; padding:10px 14px; }}
.message {{ position:absolute; top:2%; left:2%; right:2%; min-height:42px; font-size:clamp(14px,1.5vw,22px); }}
.board {{ position:absolute; top:12%; bottom:12%; left:2%; right:2%; margin:auto; display:grid; width:min(96vw,1600px); max-height:74vh; background:#020407; overflow:hidden; }}
.cell {{ position:relative; display:grid; place-items:center; min-width:0; min-height:0; overflow:hidden; background:#070b10; border:1px solid rgba(255,255,255,.015); }}
.cell.void {{ background:#020407; border-color:transparent; }}
.status {{ position:absolute; bottom:2%; left:2%; right:2%; min-height:8%; display:flex; justify-content:space-between; gap:24px; font-size:clamp(12px,1.3vw,18px); }}
.notice {{ position:absolute; z-index:5; left:2%; right:2%; top:50%; transform:translateY(-50%); text-align:center; font-size:clamp(14px,1.5vw,22px); }}
.tty {{ position:absolute; inset:0; margin:0; padding:3vh 2vw; display:grid; place-items:center; overflow:hidden; white-space:pre; color:#f3f4f6; font-size:min(1.8vw,2.8vh,24px); line-height:1.15; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
{tile_css()}
</style></head><body>
{sprite_atlas_html()}
<main class="shell" id="screen">
<div class="message" id="message" hidden></div><div class="board" id="board" hidden></div>
<pre class="tty" id="tty" hidden></pre><div class="status" id="status"></div>
<div class="notice" id="notice" aria-live="polite"></div></main>
<script>{script}</script></body></html>"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-nethack-spectator")
    parser.add_argument("--input", required=True, metavar="PATH", help="TTY capture text")
    parser.add_argument("--output", required=True, metavar="PATH", help="HTML output path")
    parser.add_argument("--cols", type=int, default=DEFAULT_COLS)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--visual-mode", choices=sorted(VISUAL_MODES), default="tiles")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = Path(args.input)
    target = Path(args.output)
    frame = parse_tty(
        source.read_text(encoding="utf-8", errors="replace"),
        cols=args.cols,
        rows=args.rows,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(frame, visual_mode=args.visual_mode), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
