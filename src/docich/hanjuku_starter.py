"""Compatibility alias: this module moved to ``docich.hanjuku.flows.starter``.

Importing ``docich.hanjuku_starter`` returns that module object itself, so attribute
reads and monkeypatches through either name reach the same code. New code
should import ``docich.hanjuku.flows.starter``. See docs/hanjuku_layered_architecture.md.
"""
import sys

from .hanjuku.flows import starter as _module

sys.modules[__name__] = _module
