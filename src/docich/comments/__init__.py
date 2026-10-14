"""Compatibility package for the issue-specified ``docich.comments`` path.

The canonical implementation lives in ``docich.comment``. This thin package
keeps the latter's public surface available under both names.
"""

from docich.comment import *  # noqa: F401,F403
from docich.comment import (  # noqa: F401
    contracts,
    intake,
    pipeline,
    translation,
)
