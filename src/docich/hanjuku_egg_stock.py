"""Validate directly observed SFC egg counts, not inferred recovery results.

GCGX egg.html and RRPG hanjuku_hero_2/egg.html: Angelin can restore
ordinary eggs to five uses. Monthly recovery still targets four, and
one-use eggs are not promoted by merely planning or selecting the card.
"""

MAX_OBSERVED_USES = 5


def observed_uses(value: object) -> int | None:
    """Accept only an observed integer in 0..5; unknown is never zero."""
    return value if type(value) is int and 0 <= value <= MAX_OBSERVED_USES else None
