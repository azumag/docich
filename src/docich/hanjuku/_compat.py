"""Compatibility facades for the pre-layer module names.

``docich.hanjuku_policy`` and ``docich.hanjuku_bot`` used to be single
namespaces. Callers (``p.menu_to``) and tests (``monkeypatch.setattr(policy,
'nav_step', ...)``) rely on one rebinding being seen by every function of the
old module. A facade installed here re-exports every top-level name of the
layer modules and forwards a rebinding to each layer module that still holds
the same object, so the old single-namespace semantics are preserved while
the code lives in separate layers.

New code should import from the owning layer module instead.
"""
from __future__ import annotations

import sys
import types

_MISSING = object()


class _Facade(types.ModuleType):
    _layer_modules: tuple = ()

    def __setattr__(self, name, value):
        old = self.__dict__.get(name, _MISSING)
        super().__setattr__(name, value)
        if name.startswith('__'):
            return
        if old is _MISSING:
            for module in self._deleted_bindings.pop(name, ()):
                if name not in module.__dict__:
                    setattr(module, name, value)
            return
        for module in self._layer_modules:
            if module.__dict__.get(name, _MISSING) is old:
                setattr(module, name, value)

    def __delattr__(self, name):
        old = self.__dict__.get(name, _MISSING)
        super().__delattr__(name)
        if old is _MISSING:
            return
        deleted = []
        for module in self._layer_modules:
            if module.__dict__.get(name, _MISSING) is old:
                delattr(module, name)
                deleted.append(module)
        self._deleted_bindings[name] = tuple(deleted)


def install(facade_name: str, modules) -> None:
    """Re-export ``modules``' top-level names from ``facade_name`` and forward rebinding.

    Names already defined by the facade itself win; otherwise the first
    module that defines a name provides it.
    """
    facade = sys.modules[facade_name]
    namespace = facade.__dict__
    for module in modules:
        for name, value in vars(module).items():
            if name.startswith('__') or name == 'annotations':
                continue
            namespace.setdefault(name, value)
    facade.__class__ = _Facade
    object.__setattr__(facade, '_layer_modules', tuple(modules))
    object.__setattr__(facade, '_deleted_bindings', {})
