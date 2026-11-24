# VERIFY-5457 — sortie kit receipt is unobserved, not measured empty

Issue: https://github.com/azumag/docich/issues/5457

## Observed evidence

The incident reports chapter 12, Chiquita, step `A:9a60b4e1:M7fe2cd`, a retake from Heliopolis to Fangol. The plan names クースカン / ミックミー / ノリウツール and the captured field inventory has positive stock for クースカン and ノリウツール. The launch decision is `order_launched`.

The incident payload has `sortie_event.carried=[]`, but the attached 120-second decision summary has no `card_pick`, `sortie_confirm`, `card_list_unreadable_depart`, or `sortie_confirm_blind_approve` receipt. It also has no `order_context` or `departure_binding` in the incident snapshot. Therefore the empty list was synthesized from the absence of a measured receipt; it does not prove a measured empty kit.

A read-only `/hanjuku-evidence list` request to the production evidence control issue returned “not available” and published no artifact. The underlying workflow failed at “List completed Hanjuku runtimes”; its wrapper removed the transport stderr, so the remote cause and raw frames/logs could not be established in this run.

## Change

The audit now distinguishes:

- `sortie_without_cards`: an explicit empty measured receipt; high confidence and eligible for automatic issue filing.
- `sortie_kit_unconfirmed`: no measured receipt; low confidence, `auto_file=false`, and rendered as “携行: 未確認”.

An unbound `launched_orders.cards` snapshot remains non-evidence because it can copy the plan. The cooldown key includes the evidence class so an unconfirmed observation cannot suppress a later measured-empty incident.

## Conclusion

The original #5457 occurrence cannot be classified as a proven empty-kit departure from the attached evidence. The runtime evidence export was unavailable, so the issue remains open for a future run with raw decision/receipt data. The code change prevents future missing receipts from being mislabeled as measured non-carry; it does not change game input or sortie behavior.
