"""Secret-free effective-config projection for owner-only diagnostics (#882).

This module only projects an already-obtained environment mapping into the
fixed diagnostics shape from #882's own "## diagnostics" section. It never
reads a file, a process or a secret store itself. The owner-only diagnostics
collector supplies a fixed allowlist from the live chat worker and this module
returns only the reviewed secret-free projection; runtime selection and I/O
remain owned by ``ops/vm_actions/collect_diagnostics.py``.

The gate is the one the live classifier actually reads:
``docich.comment_classifier`` calls Jev only when
``COMMENT_CLASSIFIER_BACKEND=jev`` and picks the route from
``DOCICH_JEV_ROUTE`` (default ``direct``). Anything else runs the heuristic
only. (The former ``DOCICH_SEMANTIC_BACKEND`` switch belonged to the retired
soviet_now adapter and is not read by anything any more.)

A route whose endpoint is filled from an account ID (``clef``, #2301) also
reports that account's *shape*: a missing or malformed account makes the route
unusable, and an owner switching providers must be able to observe that from
diagnostics alone. Only the reviewed classification -- not the value -- is ever
returned.
"""
from __future__ import annotations

import re

from .routes import parse_route_chain, resolve_route

_ACCOUNT_RE = re.compile(r"[0-9a-f]{32}\Z")
_NO_FALLBACK = {"fallback_route": None, "fallback_credential": "not_applicable",
                "fallback_account": "not_applicable"}
_HEURISTIC = {"backend": "heuristic", "route": None, "requested_model": None,
              "credential": "not_applicable", "account": "not_applicable", **_NO_FALLBACK}
_UNKNOWN_BACKEND = {"backend": "unknown", "route": None, "requested_model": None,
                    "credential": "unknown", "account": "unknown",
                    "fallback_route": None, "fallback_credential": "unknown",
                    "fallback_account": "unknown"}


def _presence(env, profile) -> str:
    key = env.get(profile.credential_env)
    return "present" if type(key) is str and key else "absent"


def _account(env, profile) -> str:
    """Report whether the route's account ID is usable, never its value.

    ``not_applicable`` for a route that takes no account ID, ``absent`` when
    none is configured, ``invalid`` when one is present but cannot fill the
    fixed endpoint template, and ``valid`` only when it can.
    """
    if profile.account_env is None:
        return "not_applicable"
    value = env.get(profile.account_env)
    if type(value) is not str or not value:
        return "absent"
    return "valid" if _ACCOUNT_RE.fullmatch(value) is not None else "invalid"


def describe(env) -> dict:
    """Return the fixed, secret-free diagnostics shape.

    ``env`` must already be a plain ``dict`` (e.g. one caller-supplied
    environment snapshot); this function performs no I/O and never returns
    a credential or account value, only its presence/shape. An env that is not
    a ``dict`` reports "unknown"; one that does not explicitly enable Jev
    reports the heuristic-only shape -- never a guessed route or model.
    ``route`` is the primary; an owner-configured
    ``DOCICH_JEV_ROUTE=primary,fallback`` chain also reports the fallback route
    and its own credential presence and account shape.
    """
    if type(env) is not dict:
        return dict(_UNKNOWN_BACKEND)
    if env.get("COMMENT_CLASSIFIER_BACKEND") != "jev":
        return dict(_HEURISTIC)
    try:
        chain = [resolve_route(name) for name in parse_route_chain(env.get("DOCICH_JEV_ROUTE", "direct"))]
    except ValueError:
        return {"backend": "jev", "route": "invalid", "requested_model": None, "credential": "unknown",
                "account": "unknown", "fallback_route": None, "fallback_credential": "unknown",
                "fallback_account": "unknown"}
    primary = chain[0]
    result = {"backend": "jev", "route": primary.name,
              "requested_model": primary.requested_model, "credential": _presence(env, primary),
              "account": _account(env, primary), **_NO_FALLBACK}
    if len(chain) > 1:
        result.update(fallback_route=chain[1].name, fallback_credential=_presence(env, chain[1]),
                      fallback_account=_account(env, chain[1]))
    return result
