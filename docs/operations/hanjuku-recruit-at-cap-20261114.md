# 半熟英雄: 国の人数が上限なら募集を見送る（v797、2026-11-14）

## 症状（実測 g1163 2026-11-14 00:0x）

国の将軍数は **32 = MAX_GENERALS**（上限）でしたが、月次処理で
`recruit_roster_unknown`（「当月の人数または必要人数が不明のため募集を
保留する」）が記録され、募集の保留が続いていました。

- `_recruit_sufficient` は「人数 >= 目標」かつ「完全な名簿または名前」を
  要求するため、目標を導けない月（`_recruit_target` が None）や
  可視の在城名が空のときに十分と判定できなかった。
- 上限に達している以上、募集しても配置できないため保留は無意味。

## 修正（v797 `hanjuku-chart-v797-recruit-at-cap`）

`_recruit_sufficient` は、人数が `MAX_GENERALS` 以上なら True を返します
（募集不要）。目標不明・在城名空でも同じです。

## 検証

- 新規 `test_a_full_country_at_the_cap_needs_no_recruit_even_with_blank_garrisons`:
  目標 None・在城名空・人数32で sufficient。
- 募集・level scan・chart bot 関連 **464件 passed**。
- 修正前の `economy.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- 国の人数が上限の月に `recruit_roster_unknown` が出ないこと。
- `recruit_hold` の不要な保留が残らないこと。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
