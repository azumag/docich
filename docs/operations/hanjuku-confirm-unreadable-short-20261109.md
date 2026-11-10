# 半熟英雄: 通常出撃は読めない出撃確認から早く出発する（v626、2026-11-10）

## 症状（実測 09:00〜09:20）

`situation_held` 165件/40分のうち **63件**が
「携行切り札の読取が不確実または計画と不一致のため出撃承認を保留」。
v622 の内訳記録で、これらは**すべて `kit_unreadable`**（読めていない）と判明済みです。
読めないまま `SORTIE_CONFIRM_LIMIT = 8` 回待ってから盲承認していました。

## 修正（v626 `hanjuku-chart-v626-confirm-unreadable-short`）

- **通常の出撃**（ボス以外）で確認画面が読めない場合は
  `SORTIE_CONFIRM_UNREADABLE_LIMIT = 3` 回で盲承認（A→150ms→A）。
- **ボス出撃は従来どおり 8回**（#3201 のキャンセル予算を維持）。

待機が 8→3 回になり、1出撃あたり約7秒短縮されます。

## 確認

- `tests/test_hanjuku_sortie_evidence.py` の該当テストを新定数へ更新。
- 出撃・確認・ボス切り札・切り札・診断 270件 passed（ボス予算のテストは不変）。
- 本番反映後: `situation_held` の減少と `order_launched` の増加を実測する。
