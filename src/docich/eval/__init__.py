"""Docich offline eval base for the comment hillclimb (#1308).

PR-1 + PR-2 of issue #1308: versioned eval contracts, a public fixture corpus
with a private-corpus projection, a group-aware train/validation/sealed-test
split, a leakage checker, deterministic + classifier graders, a side-effect-free
runner, a report and a keep/revert campaign loop. Everything is offline by
default; there is no main push, no production write and no arbitrary file
mutation anywhere in this package.

``load_suite(directory)`` is the single entry point for a suite directory such
as ``evals/comment/v1``.
"""
from __future__ import annotations

from . import (campaign, candidates, contracts, corpus, leakage, metrics,  # noqa: F401
               report, runner, split)
from . import graders  # noqa: F401

__all__ = [
    "campaign", "candidates", "contracts", "corpus", "graders", "leakage",
    "metrics", "report", "runner", "split", "load_suite",
]


def load_suite(directory, *, seed=None, ratios=None) -> dict:
    """Load, validate and split a suite directory (see :func:`corpus.load_public_cases`).

    Returns the suite manifest (pinned with the split hash), every case, and the
    per-split case lists plus the critical hard-gate bucket. Sealed cases are
    present but only reachable through an explicit ``allow_sealed`` run.
    """
    loaded = corpus.load_public_cases(directory)
    manifest = loaded["manifest"]
    result = split.split_cases(loaded["cases"],
                               seed=manifest["seed"] if seed is None else seed,
                               ratios=manifest["ratios"] if ratios is None else ratios,
                               critical_ids=loaded["critical_ids"])
    by_split = {name: [split.with_split(case, name) for case in cases]
                for name, cases in result["by_split"].items()}
    return {
        "manifest": split.manifest_for_suite(manifest, result),
        "suite_manifest": manifest,
        "cases": loaded["cases"],
        "critical_ids": loaded["critical_ids"],
        "critical": result["critical"],
        "assignments": result["assignments"],
        "by_split": by_split,
    }
