# docich#5046 「[半熟英雄] 卵回復サブ画面が数量確認後に終了せず
month_sub_abort→abandoned（支払い結果が未確認のまま）」調査・実装

判定: **欠陥** — 修正済み（v1369）。詳細な切り分けは VERIFY-5046.md。

## 事象

ラン g1288 / ch10 93-1（IMPLEMENT-4980.md §事象と同一観測）:

```
month_sub_open（たまごのかいふく）→ egg_recover_select → quantity_confirm_accept
→ month_sub_abort → month_sub_abort_text_advance
→ month_sub_abandoned → recruit_deferred_egg → month_done
```

全回復の見積もり確認が受理されたのにサブ画面が終了せず放棄され、
支払いの成否が未確認のまま `shop['egg']='skipped'` と記録された。

## 原因

1. `orchestrator._is_quantity_confirm`（v1124, #4573, g1190 63-5 の兵士数量
   確認の停止対策）が、卵回復の全回復見積もり「NこでNGになりまんな!
   よろしでっか?」を `shop_quantity` 分類・同文言・同手で横取りし、
   `_egg_recovery_step` の検証・束縛を通さず A を送信。
   `stage='recovering'`・`quoted_cost`・`confirm_*` が立たない。
2. 儀式の押し切り上限が `EGG_RITUAL_LIMIT=64` でなく `MONTH_SUB_LIMIT=8`
   のまま → 儀式フレーム超過で `month_sub_abort` → B で閉じない儀式へ
   上限離脱 `month_sub_abandoned`。
3. 放棄時の終端が一律 `'skipped'` で、送信済みの支払いAを「未実施」と
   記録し、同月再購入しうる（`skipped`→`_prepare_recruit_egg_check` で
   再 pending 化可能。`unverified` は再試行しない設計）。

## 変更

- `src/docich/hanjuku/orchestrator.py`
  - `_decide_impl` の `quantity_confirm_accept` 早期受理を、追跡中の
    `month_sub` が `kind=='egg'` の間は発火しないようにガード
    （`month_sub` の読み取りを当該ブロックへ移動し、後続の築城プロンプト
    分岐の重複読み取りを除去）。
- `src/docich/hanjuku/flows/month.py`
  - `month_sub_abandoned`: `kind=='egg'` かつ `stage=='recovering'`
    （見積もり受理A送信済み）は `shop['egg']` に `'skipped'` ではなく
    `'unverified'` を記録。`observed_metric.shop_status` を追加。
- `src/docich/hanjuku/version.py`
  - `BOT_VERSION = 'hanjuku-chart-v1369-egg-quote-owned'`。
- `ops/vm_actions/collect_diagnostics.py`
  - 許可リストに `"hanjuku-chart-v1369-egg-quote-owned",` を追加
    （v1366 エントリ直後、末尾カンマあり・既存エントリ保持）。
- `tests/test_hanjuku_quantity_confirm.py`
  - 新規: 生きた卵サブが自身の見積もり画面を所有し費用を束縛して受理、
    不足見積もりは横取りなしに B で拒否、無所有者・他種サブでは従来の
    早期受理が維持されること（decide() 経路、Canvas 合成フレーム）。
- `tests/test_hanjuku_month_sub_abandon.py`
  - 新規: `stage='recovering'` の放棄は `unverified`、未確認の放棄は
    `skipped` 維持の回帰。

## 検証

- **ローカル実行**: exec 経由の python3/pytest 起動が拒否されたため未実施。
- **代替検証（コードトレース）**:
  - ガード後の見積もりフレームは kind='shop_quantity'（≠'month_menu'）で
    `month_sub_step` へ到達。`recruit_input_guard`・`_paid_recruit_candidate`
    は kind='egg' で早期 None、`payment_interrupted` 未設定なら
    `_egg_recovery_step` の見積もり分岐へ。
  - 見積もり受理後は `stage=='recovering'` → EGG_RITUAL_LIMIT=64 で儀式を
    完走し、返却一覧→B→月メニュー復帰で `_finish_month_sub` が
    gold 差分を照合して `done`（g719 系既存テストと同一設計経路）。
  - 兵士数量確認（month_sub 非保持）は従来どおり早期受理（v1124 の
    g1190 63-5 対策は維持）。recruit/chikujou のサブは「よろしいでっか」
    文言を使わないためガード対象外でも影響なし。
- CI で `tests/test_hanjuku_quantity_confirm.py`・
  `tests/test_hanjuku_month_sub_abandon.py` と周辺の回帰確認が必要。

## 残課題

- `unverified` の卵による `recruit_deferred_egg` の当月終端化（#5045）は
  本修正では意図的に維持（支払い未確認での支出回避＝検品ゲートの
  DSH・owner 判断）。#5045 側で恒久対応を要す。
- ローカル pytest 未実行（exec 拒否）。
