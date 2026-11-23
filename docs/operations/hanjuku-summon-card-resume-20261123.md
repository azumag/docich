# Hanjuku: resume a card interrupted by an enemy summon menu (2026-11-23)

## Incident

Issue #5217 records a carried `ノリウツール` that was planned after a previous
card's effect was confirmed. Before the next card list opened, the enemy's
summon dialogue replaced the normal battle menu with `egg_battle_menu`.
The old `card_flow` still described navigation in the previous menu, so the
summon handler treated the non-empty flow as already active and fell through
to ordinary attack.

## Change

- Release only an unselected `card_flow` at `menu`, `down`, or `list` when
  the summon menu takes ownership. Keep `announce`, selected, and read-only
  kit flows intact.
- Recompute remaining cards from planned cards or the confirmed carried kit,
  excluding selected, used, unclassified, and confirmed-missing cards.
- If the egg is confirmed empty, allow the bounded down-scroll even when the
  grey egg row is omitted from OCR.
- Reopen the exact interrupted card before any other remaining card.
- The normal card-list handler still requires the card to be visibly present
  and applies the existing damage/safety gate before selection.

This does not infer possession from castle stock, bypass the damage gate, alter
retreat policy, or change combat inputs outside the existing card-menu flow.

## Verification

Regression coverage is in `tests/test_hanjuku_boss_egg_row_log.py`:

- interrupted `menu` flow survives the summon transition and resumes the same
  card after a bounded scroll;
- an already selected/announcing card is not selected a second time.

The hot-load marker is `hanjuku-chart-v1412-summon-card-resume`. Production deployment and a new live
battle observation are still required for runtime acceptance.


## Follow-up: the grey egg row may be absent from OCR

Issue #5259 showed the remaining-card case where the own egg row is grey and omitted, and the text contains only the own general's `こうげき` command. When a live own-general HP row, an attack-side battle context, and a confirmed carried card all agree, the summon handler now makes a bounded navigation-only down press to reveal the lower card row.

This does not infer that the egg count is zero, does not confirm a menu row, and does not bypass the card-list visibility or damage/safety gates. If the actor panel is not readable, the fallback is not used.

The hot-load marker is `hanjuku-chart-v1441-summon-card-hidden-row`.
