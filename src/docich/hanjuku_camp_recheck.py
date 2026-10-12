"""Compatibility alias: this module moved to ``docich.hanjuku.flows.camp_recheck``.

Importing ``docich.hanjuku_camp_recheck`` returns that module object itself, so attribute
reads and monkeypatches through either name reach the same code. New code
should import ``docich.hanjuku.flows.camp_recheck``. See docs/hanjuku_layered_architecture.md.
"""
import sys

from .hanjuku.flows import camp_recheck as _module

sys.modules[__name__] = _module
