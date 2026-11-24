# Hanjuku: record the reserve-eval skip instead of ending the month silently (2026-11-24)

## Incident

Issues #5186 (ch2), #5195 (ch4), #5200 (ch5), #5201 (ch6) and #5163 (ch9)
were all filed as `chikujou_available_at_end` — a month that closed with a
buildable castle list and no recorded reason (判定理由: なし). The audit
recomputes affordability from the month-end snapshot, where the necessity
reserve has already settled; the flow evaluated the same gate earlier while
the reserve still held unpaid or unverified necessities, failed it, and
closed the row as `skipped` with no decision record. #5163 additionally hit
the chapter-9 broken-hero hold in `_plan_extras`, whose
`month_chikujou_skipped` record the audit never consumed.

## Change

- `flows/month.py::_month_chikujou`: an unaffordable eval no longer marks
  the row `skipped`. It stays `check`, so a reserve released later in the
  same menu (an egg/recruit skip, a repair deferral, a completed roster)
  can still fund the build. Each newly failing (gold, reserve) pair is
  written to `shop['chikujou_last_eval']` and recorded once as
  `month_chikujou_skipped` (`reserve_unsettled` when the reserve cannot be
  computed because an unpriced merchant item is still required).
- `strategy/economy.py::_plan_extras`: the chapter-9 broken-hero skip also
  writes the eval marker, so its month end is explained to the audit.
- `month_audit._chikujou_snapshot`: reads the marker for the incident's
  month and reports `reserve_skipped`; `_build_chikujou_incident`
  downgrades such ends to `medium` confidence with reason
  `chikujou_reserve_skipped` — countable in the ledger like the other
  explained holds (`held_for`, `menu_unreadable`), below the issue filer's
  high-confidence threshold. A stale marker from an earlier month is
  ignored.

This does not change the affordability gate, the reserve composition, or
any recorded decline/refusal path.

## Verification

Regression coverage:

- `tests/test_hanjuku_chikujou_reserve_skip.py`: the failing eval stays
  open, records each new (gold, reserve) signature once, and a reserve
  released in the same menu still builds.
- `tests/test_hanjuku_month_audit.py`: a month end carrying the marker is
  kept at medium confidence; a stale marker still files at high.
- `tests/test_hanjuku_multi_chikujou.py`: the unresolved-recovery hold now
  asserts `check` + marker + single record instead of a silent `skipped`.

Targeted suites pass locally (479 chikujou/month tests, 83 merchant
tests). The hot-load marker is
`hanjuku-chart-v1420-chikujou-reserve-check`. Production deployment and a
new live month observation are still required for runtime acceptance.
