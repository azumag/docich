# 半熟英雄: 読めない出撃確認は A を2回送って承認する（v611、2026-11-10）

## 症状（実測 04:04）

`card_list_unreadable_depart`（v608）でカード無し出撃へ進んだ後、
`sortie_confirm` が読めないまま v610 の**盲承認 A 1回**では画面が変わらず、
上限到達で結局キャンセル（直近40分で12件）。

## 原因

この画面のテキストが**まったくOCRできない**（`parse` が lines を返すが spans が空、
`kind=unknown`）。カーソルだけは読める。確認パネルは「選択」と「うむッ!」の
2段階のため、A 1回では確定しません。

## 修正（v611 `hanjuku-chart-v611-confirm-double-a`）

盲承認を **A→150ms待ち→A** に変更（`sortie_confirm_blind_approve` の記録は同じ）。
2回目も効かない場合（再度上限到達）は従来どおり取り消し。

## 確認

- `tests/test_hanjuku_sortie_evidence.py` の盲承認テストを2回押しへ更新。
- 出撃・確認・ボス切り札・診断 228件 passed。
- 本番反映後: `order_failed`（出撃確認を読めない）が減り、`order_launched` が増えることを
  実測する。
