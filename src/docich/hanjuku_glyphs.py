"""Compatibility alias: this module moved to ``docich.hanjuku.os_layer.glyphs``.

Importing ``docich.hanjuku_glyphs`` returns that module object itself, so attribute
reads and monkeypatches through either name reach the same code. New code
should import ``docich.hanjuku.os_layer.glyphs``. See docs/hanjuku_layered_architecture.md.
"""
import sys

from .hanjuku.os_layer import glyphs as _module

sys.modules[__name__] = _module
