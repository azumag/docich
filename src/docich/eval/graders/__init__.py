"""Graders for the offline eval base (#1308).

Each grader exposes a ``GRADER_VERSION`` pinned in
:data:`docich.eval.contracts.GRADER_VERSIONS`. A campaign records the version
next to every score; the deterministic and classifier graders are offline, the
semantic reply grader needs an injected judge (no network of its own).
"""
from . import classifier, deterministic, reply_semantic  # noqa: F401

__all__ = ["classifier", "deterministic", "reply_semantic"]
