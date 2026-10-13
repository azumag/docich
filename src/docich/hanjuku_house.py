"""Compatibility alias: this module moved to ``docich.hanjuku.flows.house``.

Importing ``docich.hanjuku_house`` returns that module object itself, so attribute
reads and monkeypatches through either name reach the same code. New code
should import ``docich.hanjuku.flows.house``. See docs/hanjuku_layered_architecture.md.
"""
import sys

from .hanjuku.flows import house as _module

sys.modules[__name__] = _module
