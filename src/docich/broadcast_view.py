"""Repository standard for docich broadcast pages shown in Chromium.

A browser translation tip that pops over the top-right of the frame is a
visible broadcast defect, and every docich broadcast page is single-language by
contract (the page text is authoritative and must not be machine-translated on
air).  The standard has two halves and both must hold for every view:

1. Chromium is launched with :data:`BROADCAST_TRANSLATE_ARGS`.  Chromium has
   honored both the legacy ``--disable-translate`` switch and the newer
   ``TranslateUI`` feature gate at different versions, so both stay off.
2. A Japanese page is launched with :data:`JAPANESE_LANG_ARG` and declares
   :data:`JAPANESE_PAGE_MARKERS` in the served HTML.  The page-side markers are
   the only part the browser sees when it decides to offer translation, so a
   view that only disables the browser UI is still one flag away from a tip.

Regression: ``tests/test_broadcast_view_standard.py``.
"""
from __future__ import annotations

#: Chromium switches that keep browser translation away from every docich
#: broadcast window.
BROADCAST_TRANSLATE_ARGS = (
    "--disable-translate",
    "--disable-features=Translate,TranslateUI,BackForwardCache",
)

#: Pins the browser UI language for a Japanese broadcast page.
JAPANESE_LANG_ARG = "--lang=ja-JP"

#: Declarations every Japanese broadcast HTML document starts with.  The
#: ``translate`` attribute and the ``google`` meta are the two signals
#: Chromium/Chrome read before offering (or auto-running) translation.
JAPANESE_PAGE_MARKERS = (
    '<html lang="ja" translate="no">',
    '<meta name="google" content="notranslate">',
)

#: The markers live in the document head; scanning a bounded prefix keeps the
#: check honest for large single-string templates without reading the page body.
PAGE_MARKER_SCAN_BYTES = 4096


def japanese_page_markers_missing(html: str, *, scan_bytes: int = PAGE_MARKER_SCAN_BYTES) -> list[str]:
    """Return the standard markers an HTML broadcast page is missing.

    An empty list means the page declares itself Japanese and not-translatable.
    The scan is bounded so a template constant is validated cheaply.
    """
    if not isinstance(html, str):
        return list(JAPANESE_PAGE_MARKERS)
    head = html[: max(0, int(scan_bytes))]
    return [marker for marker in JAPANESE_PAGE_MARKERS if marker not in head]
