"""Duplicate, reprint and similar-title relations between stored versions (N02, #778).

Rules, in priority order:

* ``DUPLICATE`` — same source item or same canonical URL.  Never adds evidence.
* ``REPRINT`` — same content digest from a different source.  The text is
  shared, so it counts once as evidence.
* ``SIMILAR_HELD`` — similar titles with different digests and identities.
  The pair is held and not merged: similarity alone does not prove the same event.

Anything that cannot be related with certainty stays separate.  Nothing here
calls an LLM or decides which source is trustworthy.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Sequence

from .normalization import FIXTURE_SIMILARITY_THRESHOLD
from .observation_store import VersionRecord

KIND_DUPLICATE = "DUPLICATE"
KIND_REPRINT = "REPRINT"
KIND_SIMILAR_HELD = "SIMILAR_HELD"


@dataclass(frozen=True)
class Relation:
    kind: str
    left: str
    right: str


def relate(records: Sequence[VersionRecord]) -> list[Relation]:
    relations: list[Relation] = []
    ordered = sorted(records, key=lambda r: r.version_id)
    for i, left in enumerate(ordered):
        for right in ordered[i + 1:]:
            if left.version_id == right.version_id:
                continue
            same_item = (left.source_id == right.source_id and left.source_item_id == right.source_item_id)
            if same_item or left.canonical_url == right.canonical_url:
                relations.append(Relation(KIND_DUPLICATE, left.version_id, right.version_id))
            elif left.content_digest == right.content_digest:
                relations.append(Relation(KIND_REPRINT, left.version_id, right.version_id))
            elif SequenceMatcher(None, left.title, right.title).ratio() >= FIXTURE_SIMILARITY_THRESHOLD:
                relations.append(Relation(KIND_SIMILAR_HELD, left.version_id, right.version_id))
    return relations


def independent_evidence_count(records: Sequence[VersionRecord]) -> int:
    """Distinct texts. Duplicates and reprints share a digest and count once."""
    return len({record.content_digest for record in records})


def held_ids(relations: Sequence[Relation]) -> set[str]:
    """Versions with an unresolved similar-title relation. They stay out of ready output."""
    return {rel.left for rel in relations if rel.kind == KIND_SIMILAR_HELD} | {
        rel.right for rel in relations if rel.kind == KIND_SIMILAR_HELD}
