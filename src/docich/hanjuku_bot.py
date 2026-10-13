"""Compatibility facade for the Hanjuku bot entry points.

``decide`` lives in ``docich.hanjuku.orchestrator``, frame classification in
``docich.hanjuku.os_layer.phase`` and ``BOT_VERSION`` in
``docich.hanjuku.version``. Every old name stays importable from here, and
rebinding one (``monkeypatch.setattr(hanjuku_bot, 'classify', ...)``) reaches
the layer modules that use it. New code should import the owning module.
"""
from __future__ import annotations

from .hanjuku_pixels import Frame
from .hanjuku import _compat
from .hanjuku import orchestrator as _orchestrator, version as _version
from .hanjuku.os_layer import phase as _phase

_compat.install(__name__, (_version, _phase, _orchestrator))
