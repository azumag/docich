"""Compatibility package for the issue-specified ``docich.comments`` path.

The canonical implementation lives in ``docich.comment``. This thin package
keeps the latter's public surface available under both names.
"""

from docich.comment import *  # noqa: F401,F403
from docich.comment import (  # noqa: F401
    bilingual,
    contracts,
    intake,
    pipeline,
    translation,
)

# Canonical submodules are also reachable as attributes, and ``run_pipeline``
# exists directly on this namespace (not only on ``docich.comments.pipeline``).
run_pipeline = pipeline.run_pipeline
comments_from_text = pipeline.comments_from_text
comments_from_lines = pipeline.comments_from_lines
comments_from_file = pipeline.comments_from_file

__all__ = list(getattr(pipeline, "__all__", [])) + [
    "bilingual",
    "contracts",
    "intake",
    "pipeline",
    "translation",
    "run_pipeline",
    "comments_from_text",
    "comments_from_lines",
    "comments_from_file",
]

def __getattr__(name: str):
    return getattr(pipeline, name)
