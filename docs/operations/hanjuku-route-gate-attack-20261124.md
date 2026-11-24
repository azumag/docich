# Hanjuku: revalidate attack routes at deploy time (2026-11-24)

## Review finding

The follow-up review on PR #5237 identified that an unconditional
`purpose == 'attack'` exemption in `_hold_unrouted_sortie` allowed an
adopted/interim attack order with registered endpoints to bypass the current
chapter's route graph.

## Change

- Keep read-only `source_probe` / `roster_probe` orders exempt: they only
  open a castle list and do not march.
- Re-evaluate `routes.order_allowed(mem, order, source)` for every unsent
  attack order at the deploy continuation, using current chapter, source,
  target, and current ownership. An attack is rejected if its source is no
  longer owned or its target has already become owned.
- Preserve reconciliation for already launched / launched-unconfirmed orders.
- A route-invalid attack is cancelled and replanned through the existing
  `sortie_route_unverified` path; a currently route-valid attack continues.

A fresh receipt is not persisted: the deploy gate revalidates the deterministic
route graph and ownership at the moment the order can commit, which avoids
trusting stale route evidence from an adopted plan.

## Verification

`tests/test_hanjuku_castle_routes.py` covers both a registered-endpoint
diagonal attack that must be held and a measured road-leg attack that must
continue, plus source/target ownership changes after planning. The current
marker is `hanjuku-chart-v1439-attack-route-revalidation`; diagnostics retains the historical v1435 marker.
