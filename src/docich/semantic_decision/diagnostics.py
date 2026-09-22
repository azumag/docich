"""Secret-free effective-config projection for owner-only diagnostics (#882).

This module only projects an already-obtained environment mapping into the
fixed diagnostics shape from #882's own "## diagnostics" section. It never
reads a file, a process or a secret store itself. The owner-only diagnostics
collector supplies a fixed allowlist from the live chat worker and this module
returns only the reviewed secret-free projection; runtime selection and I/O
remain owned by ``ops/vm_actions/collect_diagnostics.py``.
"""
from __future__ import annotations

from .routes import resolve_route

_LEGACY = {"backend": "legacy", "route": None, "requested_model": None, "credential": "not_applicable"}
_UNKNOWN_BACKEND = {"backend": "legacy", "route": None, "requested_model": None, "credential": "unknown"}


def describe(env) -> dict:
    """Return the fixed, secret-free diagnostics shape.

    ``env`` must already be a plain ``dict`` (e.g. one caller-supplied
    environment snapshot); this function performs no I/O and never returns
    a credential value, only its presence. An env that is not a ``dict``,
    or that does not explicitly request the jev backend, reports the
    unchanged/"legacy" shape -- never a guessed route or model.
    """
    if type(env) is not dict:
        return dict(_UNKNOWN_BACKEND)
    if env.get("DOCICH_SEMANTIC_BACKEND") != "jev":
        return dict(_LEGACY)
    try:
        profile = resolve_route(env.get("DOCICH_JEV_ROUTE", "direct"))
    except ValueError:
        return {"backend": "jev", "route": "invalid", "requested_model": None, "credential": "unknown"}
    key = env.get(profile.credential_env)
    credential = "present" if type(key) is str and key else "absent"
    return {"backend": "jev", "route": profile.name,
            "requested_model": profile.requested_model, "credential": credential}
