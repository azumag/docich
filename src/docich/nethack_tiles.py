"""Original viewer-only SVG tiles for the NetHack spectator.

The artwork in this module is made from simple geometric SVG primitives for
Docich.  It intentionally does not copy or embed NetHack's distributed tile
art, so the spectator can keep its presentation layer independent from the
NetHack source/art asset license.  The TTY character mapping is deliberately
coarse and must never be treated as AI semantic observation.
"""
from __future__ import annotations

import html


TILESET_NAME = "docich-minimal-v1"


def tile_key(kind: str, char: str) -> str:
    """Map a presentation cell to one of the original SVG sprite symbols."""
    if kind == "void":
        return "void"
    if kind == "player":
        return "player"
    if kind == "floor":
        return "corridor" if char == "#" else "floor"
    if kind == "wall":
        return "wall-horizontal" if char == "-" else "wall-vertical"
    if kind == "door":
        return "door"
    if kind == "stairs":
        return "stairs-up" if char == "<" else "stairs-down"
    if kind == "creature":
        return "creature"
    if kind == "trap":
        return "trap"
    if kind == "item":
        return {
            ")": "weapon",
            "[": "armor",
            "(": "tool",
            "=": "ring",
            "/": "wand",
            "%": "food",
            "!": "potion",
            "?": "scroll",
            "*": "gem",
            "$": "gold",
            '"': "amulet",
        }.get(char, "item")
    return "other"


# All symbols use a 24x24 coordinate system.  Colors are mostly inherited from
# CSS via currentColor; a few neutral fills use CSS custom properties so the
# same document stays readable in OBS Chromium without external assets.
_SPRITES = r"""
<symbol id="tile-floor" viewBox="0 0 24 24">
  <rect x="0" y="0" width="24" height="24" rx="2" class="tile-bg-floor"/>
  <circle cx="5" cy="7" r="1" class="tile-detail-muted"/><circle cx="17" cy="15" r="1" class="tile-detail-muted"/>
</symbol>
<symbol id="tile-corridor" viewBox="0 0 24 24">
  <rect x="0" y="0" width="24" height="24" rx="2" class="tile-bg-floor"/>
  <path d="M0 9h24v6H0z" class="tile-detail-muted"/><path d="M3 12h18" class="tile-stroke-soft"/>
</symbol>
<symbol id="tile-wall-horizontal" viewBox="0 0 24 24">
  <rect width="24" height="24" class="tile-bg-wall"/>
  <path d="M0 5h24M0 12h24M0 19h24M6 5v7M17 5v7M11 12v7" class="tile-stroke-wall"/>
</symbol>
<symbol id="tile-wall-vertical" viewBox="0 0 24 24">
  <rect width="24" height="24" class="tile-bg-wall"/>
  <path d="M5 0v24M12 0v24M19 0v24M5 7h7M12 16h7" class="tile-stroke-wall"/>
</symbol>
<symbol id="tile-door" viewBox="0 0 24 24">
  <rect width="24" height="24" class="tile-bg-floor"/>
  <rect x="5" y="2" width="14" height="21" rx="1" class="tile-fill-door"/>
  <path d="M8 5h8v15H8z" class="tile-stroke-door"/><circle cx="15.5" cy="13" r="1.2" class="tile-fill-metal"/>
</symbol>
<symbol id="tile-stairs-up" viewBox="0 0 24 24">
  <rect width="24" height="24" class="tile-bg-floor"/>
  <path d="M4 19h5v-4h4v-4h4V7h3" class="tile-stroke-stairs"/><path d="M16 4l4 3-4 3" class="tile-stroke-stairs"/>
</symbol>
<symbol id="tile-stairs-down" viewBox="0 0 24 24">
  <rect width="24" height="24" class="tile-bg-floor"/>
  <path d="M4 5h5v4h4v4h4v4h3" class="tile-stroke-stairs"/><path d="M16 14l4 3-4 3" class="tile-stroke-stairs"/>
</symbol>
<symbol id="tile-player" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <circle cx="12" cy="6.5" r="3.2" class="tile-fill-player"/>
  <path d="M7 20v-5.5c0-3 2.1-5 5-5s5 2 5 5V20M8 13l-3 4M16 13l3 4" class="tile-stroke-player"/>
  <path d="M17.5 8.5l3 1.2v4.4c0 2.2-1.2 3.9-3 5-1.8-1.1-3-2.8-3-5V9.7z" class="tile-fill-shield"/>
</symbol>
<symbol id="tile-creature" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <path d="M5 19c0-5 1.7-10 7-10s7 5 7 10l-3-2-2 2-2-2-2 2-2-2z" class="tile-fill-creature"/>
  <path d="M8 9L6 5l5 3M16 9l2-4-5 3" class="tile-fill-creature"/>
  <circle cx="9.5" cy="12.5" r="1.2" class="tile-eye"/><circle cx="14.5" cy="12.5" r="1.2" class="tile-eye"/>
</symbol>
<symbol id="tile-potion" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <path d="M9 3h6v4l3.2 5.4A5.8 5.8 0 0113.2 21h-2.4a5.8 5.8 0 01-5-8.6L9 7z" class="tile-fill-potion"/>
  <path d="M8 14h8.5" class="tile-stroke-glass"/>
</symbol>
<symbol id="tile-scroll" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <path d="M7 4h10c2 0 2 3 0 3v13H7V7c-2 0-2-3 0-3z" class="tile-fill-paper"/>
  <path d="M9 10h6M9 13h5M9 16h6" class="tile-stroke-ink"/>
</symbol>
<symbol id="tile-weapon" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <path d="M5 19L18.5 5.5 20 4l-1.5 4L8 18.5z" class="tile-fill-metal"/>
  <path d="M5 14l5 5M4 20l3-3" class="tile-stroke-weapon"/>
</symbol>
<symbol id="tile-armor" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <path d="M12 3l7 3v5c0 5-2.6 8.2-7 10-4.4-1.8-7-5-7-10V6z" class="tile-fill-armor"/>
  <path d="M12 6v11M8 9h8" class="tile-stroke-armor"/>
</symbol>
<symbol id="tile-ring" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <ellipse cx="12" cy="13" rx="6" ry="7" class="tile-stroke-ring"/><path d="M8 7l4-4 4 4-4 3z" class="tile-fill-gem"/>
</symbol>
<symbol id="tile-wand" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <path d="M5 19L17 7" class="tile-stroke-wand"/><path d="M18 3v3M16.5 4.5h3M20 7l1.5 1.5M15 2.5L13.5 1" class="tile-stroke-magic"/>
</symbol>
<symbol id="tile-food" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <path d="M12 8c-5-4-8 0-7 5 1 5 4 8 7 8s6-3 7-8c1-5-2-9-7-5z" class="tile-fill-food"/><path d="M12 8c0-3 2-5 5-5" class="tile-stroke-leaf"/>
</symbol>
<symbol id="tile-gem" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <path d="M5 9l4-5h6l4 5-7 11z" class="tile-fill-gem"/><path d="M5 9h14M9 4l3 16 3-16" class="tile-stroke-gem"/>
</symbol>
<symbol id="tile-gold" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <ellipse cx="12" cy="12" rx="7" ry="7" class="tile-fill-gold"/><path d="M12 7v10M15 9.5c-1-2-6-1.5-6 .5 0 3 6 1 6 4 0 2-5 2.5-6 .5" class="tile-stroke-gold"/>
</symbol>
<symbol id="tile-amulet" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <path d="M6 4c0 6 2 9 6 9s6-3 6-9" class="tile-stroke-chain"/><path d="M12 11l4 4-4 6-4-6z" class="tile-fill-amulet"/>
</symbol>
<symbol id="tile-tool" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <rect x="5" y="8" width="14" height="11" rx="2" class="tile-fill-tool"/><path d="M9 8V5h6v3M5 12h14" class="tile-stroke-tool"/>
</symbol>
<symbol id="tile-item" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <path d="M12 4l7 6-7 10-7-10z" class="tile-fill-item"/><circle cx="12" cy="10" r="2" class="tile-eye"/>
</symbol>
<symbol id="tile-trap" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <path d="M3 19l4-9 4 9 4-12 6 12z" class="tile-fill-trap"/><path d="M3 20h18" class="tile-stroke-trap"/>
</symbol>
<symbol id="tile-other" viewBox="0 0 24 24">
  <rect width="24" height="24" rx="2" class="tile-bg-floor"/>
  <circle cx="12" cy="12" r="7" class="tile-stroke-other"/><path d="M12 7v6M12 17h.01" class="tile-stroke-other"/>
</symbol>
"""


def sprite_atlas_html() -> str:
    """Return a hidden in-document SVG sprite atlas."""
    return (
        '<svg class="sprite-atlas" aria-hidden="true" width="0" height="0" '
        'focusable="false"><defs>' + _SPRITES + "</defs></svg>"
    )


def tile_markup(kind: str, char: str) -> str:
    """Return SVG tile markup while preserving an ambiguous-glyph hint."""
    key = tile_key(kind, char)
    if key == "void":
        return '<span class="tile-empty" aria-hidden="true"></span>'

    label = html.escape(char, quote=True)
    # Creature and unknown glyphs stay visibly distinguishable.  TTY glyphs
    # are ambiguous (for example monster letters), so the overlay is only a
    # viewer hint and does not claim a species identity.
    overlay = ""
    if kind in {"creature", "other"} and char.strip():
        overlay = f'<span class="tile-glyph" aria-hidden="true">{html.escape(char)}</span>'
    return (
        f'<span class="tile-wrap" role="img" aria-label="glyph {label}">'
        f'<svg class="tile-svg" viewBox="0 0 24 24" aria-hidden="true">'
        f'<use href="#tile-{key}"></use></svg>{overlay}</span>'
    )


def tile_css() -> str:
    """CSS for the bundled original SVG tiles."""
    return r"""
.sprite-atlas{position:absolute;width:0;height:0;overflow:hidden}
.tile-wrap,.tile-empty{position:relative;display:block;width:100%;height:100%;min-width:0;min-height:0}
.tile-svg{display:block;width:100%;height:100%;shape-rendering:geometricPrecision}
.tile-glyph{position:absolute;right:5%;bottom:3%;display:grid;place-items:center;min-width:38%;height:42%;padding:0 1px;border-radius:4px;background:rgba(3,7,18,.74);color:#fff;font-size:clamp(6px,.62vw,11px);font-weight:800;line-height:1;text-shadow:0 1px 2px #000}
.tile-bg-floor{fill:#10151d}.tile-bg-wall{fill:#273241}.tile-detail-muted{fill:#303b4c}.tile-stroke-soft{fill:none;stroke:#46536a;stroke-width:1.2}
.tile-stroke-wall{fill:none;stroke:#778399;stroke-width:1.4}.tile-fill-door{fill:#8b5e34}.tile-stroke-door{fill:none;stroke:#c38a52;stroke-width:1.2}.tile-fill-metal{fill:#cbd5e1}
.tile-stroke-stairs{fill:none;stroke:#67e8f9;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}.tile-fill-player{fill:#f8fafc}.tile-stroke-player{fill:none;stroke:#e2e8f0;stroke-width:2;stroke-linecap:round}.tile-fill-shield{fill:#38bdf8}
.tile-fill-creature{fill:#f43f5e}.tile-eye{fill:#fef08a}.tile-fill-potion{fill:#a855f7;stroke:#e9d5ff;stroke-width:1}.tile-stroke-glass{fill:none;stroke:#f3e8ff;stroke-width:1.2}
.tile-fill-paper{fill:#fef3c7;stroke:#d6b87a;stroke-width:1}.tile-stroke-ink{fill:none;stroke:#92400e;stroke-width:1.2;stroke-linecap:round}.tile-stroke-weapon{fill:none;stroke:#94a3b8;stroke-width:2;stroke-linecap:round}
.tile-fill-armor{fill:#64748b}.tile-stroke-armor{fill:none;stroke:#cbd5e1;stroke-width:1.4}.tile-stroke-ring{fill:none;stroke:#fbbf24;stroke-width:3}.tile-fill-gem{fill:#22d3ee}.tile-stroke-gem{fill:none;stroke:#cffafe;stroke-width:1}
.tile-stroke-wand{fill:none;stroke:#d97706;stroke-width:3;stroke-linecap:round}.tile-stroke-magic{fill:none;stroke:#c084fc;stroke-width:1.5;stroke-linecap:round}.tile-fill-food{fill:#84cc16}.tile-stroke-leaf{fill:none;stroke:#bef264;stroke-width:1.6;stroke-linecap:round}
.tile-fill-gold{fill:#facc15}.tile-stroke-gold{fill:none;stroke:#854d0e;stroke-width:1.4;stroke-linecap:round}.tile-stroke-chain{fill:none;stroke:#fbbf24;stroke-width:1.5}.tile-fill-amulet{fill:#fb7185;stroke:#fecdd3;stroke-width:1}
.tile-fill-tool{fill:#78716c}.tile-stroke-tool{fill:none;stroke:#d6d3d1;stroke-width:1.5}.tile-fill-item{fill:#f59e0b}.tile-fill-trap{fill:#c084fc}.tile-stroke-trap{fill:none;stroke:#e9d5ff;stroke-width:1.3}.tile-stroke-other{fill:none;stroke:#94a3b8;stroke-width:1.7;stroke-linecap:round}
"""
