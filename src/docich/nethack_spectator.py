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
    message = lines[0].rstrip()
    status = tuple(line.rstrip() for line in lines[-2:] if line.rstrip())
    map_lines = lines[1:-2]

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
    return Frame(cols=cols, rows=map_rows, cells=cells, message=message, status=())


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
<div class="wrap">
  <div class="message">{html.escape(frame.message) or '&nbsp;'}</div>
  <div class="board">{''.join(cells)}</div>
  <div class="status">{status_html or '<div>&nbsp;</div>'}</div>
</div>
</body>
</html>
"""


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
