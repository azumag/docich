"""Viewer-only NetHack spectator renderer.

This module deliberately does not participate in gameplay observation or input.
It converts an already-observed TTY frame into a browser/OBS-friendly HTML
presentation.  The boundary is intentionally small so a later NLE/glyph source
or a real tileset can replace the text classifier without changing the corner,
agent, or save/resume lifecycle.
"""
from __future__ import annotations

import argparse
import html
from dataclasses import dataclass
from pathlib import Path

DEFAULT_COLS = 80
DEFAULT_ROWS = 24


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
    AI semantic decoder.  Ambiguous glyphs remain coarse visual classes.
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
    # message and the final two rows for status.  Keeping this extraction
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


def render_html(frame: Frame, *, title: str = "NetHack") -> str:
    cells = []
    for cell in frame.cells:
        char = html.escape(cell.char if cell.char != " " else "&nbsp;")
        if cell.char == " ":
            char = "&nbsp;"
        cells.append(
            f'<span class="cell {cell.kind}" data-x="{cell.x}" data-y="{cell.y}">{char}</span>'
        )

    status_html = "".join(f"<div>{html.escape(line)}</div>" for line in frame.status)
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: dark; background:#07090c; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:#07090c; color:#f3f4f6; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
.wrap {{ width:100vw; height:100vh; display:grid; grid-template-rows:auto 1fr auto; gap:12px; padding:18px; }}
.message,.status {{ background:#10151d; border:1px solid #263141; border-radius:10px; padding:10px 14px; }}
.message {{ min-height:42px; font-size:18px; }}
.status {{ display:flex; justify-content:space-between; gap:24px; font-size:16px; }}
.board {{ align-self:center; justify-self:center; display:grid; grid-template-columns:repeat({frame.cols}, minmax(0,1fr)); width:min(96vw,1440px); aspect-ratio:{frame.cols}/{max(frame.rows,1)}; background:#020407; border:1px solid #263141; overflow:hidden; }}
.cell {{ display:grid; place-items:center; min-width:0; min-height:0; font-size:clamp(6px,1.05vw,18px); line-height:1; }}
.player {{ background:#253347; font-weight:800; border-radius:20%; }}
.floor {{ color:#7b8492; }}
.wall {{ color:#c1c7d0; background:#171c24; }}
.door {{ color:#d8b36a; background:#211a10; }}
.stairs {{ color:#7dd3fc; font-weight:800; }}
.creature {{ color:#fb7185; font-weight:700; }}
.item {{ color:#fde68a; }}
.trap {{ color:#c084fc; }}
.other {{ color:#d1d5db; }}
.void {{ color:transparent; }}
</style>
</head>
<body>
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = Path(args.input)
    target = Path(args.output)
    frame = parse_tty(source.read_text(encoding="utf-8", errors="replace"), cols=args.cols, rows=args.rows)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(frame), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
