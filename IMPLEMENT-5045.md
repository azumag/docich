# docich#5045 「unverified 卵による築城保留の永久化（#4980/#4989 の残余ギャップ）」調査・実装

判定: **欠陥（設計ギャップ）** — 修正実装済み（v1373）。調査と設計根拠は
VERIFY-5045.md。

## 事象

#4989（v1363）で `_recruit_precedes_cards` は `deferred_egg` を卵の終端
（done/not_needed/skipped/なし）でのみ解放するようになったが、`unverified`
（v1369 / PR #5056 で導入: `stage=='recovering'`＝見積もり受理済みの放棄）
は意図的に終端へ含めない fail-closed のままだった。その結果、同月内に
`unverified` から抜ける経路が一切無く、`cards_held='recruit_pending'` で
築城・切り札が当月永久に保留され得た。shop は月単位で再作成されるため
翌月は自然に解放されるが、卵枯渇が残る限り同じ放棄→保留が毎月再発しうる
＝実効的な永久化。

## 採用設計

候補の検討は VERIFY-5045.md §4。採用は **(a) 残金レシートによる同月内の
再照合**:

- `unverified` への記録時点で、追跡していたサブが所有していた支払い期待値
  （`gold_before`・`quoted_cost`）と、マーク以後の出費を分離するための
  帳簿ベースライン（`bought` 件数・兵士補充レシート状態・築城実績件数・
  募集状態）を `shop['egg_receipt']` として保持する。
- 追跡中の `month_sub` は画面を占有するため `gold_before`→マークの間の
  出費は卵費用しかありえない。マーク以後の出費は `others` として帳簿から
  差し引き、`rest = (before - gold) - others` が
  0 なら支払い不成立（`skipped`）、`quoted_cost` と一致すれば支払い成立
  （`done`＝`_finish_month_sub` と同一の照合基準）、それ以外は
  `unverified` 維持。
- `egg_recheck_settled` フラグで、settle 済みの卵が同月に再 `pending` 化
  されるのを塞ぐ（unverified→skipped→repend の再試行ループ防止）。
- 二重支出にならない理由の詳細は VERIFY-5045.md「二重支出にならない理由」。

## 変更

- `src/docich/hanjuku/flows/month.py`
  - `_keep_egg_receipt(shop, sub)` 新設: `kind=='egg'`・`payment_interrupted`
    でない・サブ/shop のキー一致・`gold_before` が int・`left_menu is True`
    のときだけ `egg_receipt` を保持（`before`/`cost`/`bought`/
    `refill_status`/`upgrades`/`recruit`）。月メニューを離れていないサブは
    支払いを試みていないため、その残金差は stale な背景であり証拠にならない
    （CI で `test_payment_delta_on_a_stale_background_is_not_a_recovery_completion_receipt`
    が失敗したため追加。修正後は green）。
  - `_settle_unverified_egg(mem, shop, header)` 新設: `egg=='unverified'`・
    未 settle・`month_sub` 非追跡中のとき、差額から記録済み他出費
    （マーク後に状態が変化した兵士補充の `qty`×1G、マーク後の `bought`
    行の個数×単価）を差し引き `rest==0`→`skipped`、`rest==quoted_cost`→
    `done` に settle。帰属不能な出費候補（支出額未読の補充・無価格列の
    商人行・新規築城実績・マーク後の `recruit` 'done'/'unverified'）や
    `gold>before`・非整数・キー不整合は settle せず
    `unverified` 維持。settle 時に `egg_unverified_settled` を `_record`。
  - `_finish_month_sub`: `egg` が `unverified` に確定した際に
    `_keep_egg_receipt` を呼ぶ。
  - `month_sub_abandoned`: `status=='unverified'` の際に同じく呼ぶ。
  - `month_step`: cards 保留ゲート（`_recruit_precedes_cards`）評価の直前で
    `_settle_unverified_egg` を評価（解放判定が同 tick で効く）。
- `src/docich/hanjuku/strategy/economy.py`
  - `_refresh_egg_priority`: `egg_recheck_settled` 済みの卵を roster 再照合で
    `check`/`pending` へ戻さない。
  - `_prepare_recruit_egg_check`: `egg_recheck_settled` 済みの `skipped` を
    `pending` へ戻さない（`opened`→`unverified` 変換も settled 後は行わない）。
- `src/docich/hanjuku/version.py`
  - `BOT_VERSION = 'hanjuku-chart-v1373-egg-receipt-settle'`。
- `ops/vm_actions/collect_diagnostics.py`
  - diagnostics allowlist に `"hanjuku-chart-v1373-egg-receipt-settle",`
    を追加（v1372 エントリ直後、末尾カンマあり）。
- `ops/vm_actions/tests/test_collect_diagnostics.py`
  - 固定期待バージョンリストに同文字列を追加。
- `tests/test_hanjuku_egg_unverified_settle.py`（新規）
  - 残金不変 → `skipped` settle＋築城保留解放（発火側の非発火＝解放側）。
  - 差額＝提示額 → `done` settle。
  - マーク後の有給補充の差し引き（delta=qty+cost→`done`、delta=qty→`skipped`）。
  - 曖昧差額・支出額未読の補充・無価格商人行・新規築城・マーク後募集確定 →
    `unverified` 維持（発火側: 保留継続・再試行なし）。
  - settle 後の `skipped` が再 pending 化・再支払いされないこと。
  - `_finish_month_sub` の `unverified` 確定時にレシート保持、
    `payment_interrupted` では保持しないこと。
- `tests/test_hanjuku_month_sub_abandon.py`
  - `stage='recovering'` 放棄時の `egg_receipt` 保持を既存テストへ追加。

## 検証

- **ローカル実行**: exec 経由の python3/pytest 起動が拒否されたため未実施。
  CI で `tests/test_hanjuku_egg_unverified_settle.py`・
  `tests/test_hanjuku_month_sub_abandon.py`・
  `tests/test_hanjuku_recruit_wage_headroom.py`・
  `tests/test_hanjuku_month_status_single_read.py`・
  `ops/vm_actions/tests/test_collect_diagnostics.py` の回帰確認が必要。
- **代替検証（コードトレース）**:
  - `month_step` 内の実行順: `_refresh_egg_priority`/`_prioritise_recruit`
    （423-425）→ `_settle_unverified_egg`（531）→ `_recruit_precedes_cards`
    保留ゲート（532）→ 解放（537-548）。settle→`skipped`/`done` は
    deferred_egg の `precedes` を同 tick で偽にし、`cards_held` /
    `chikujou_held` を既存解放経路で剥がす。
  - `month_sub` 追跡中・レシートなし・キー不一致・非整数・`gold>before`・
    帰属不能出費の各早期 return を確認。`bought` 行は `[name, qty, price]`
    （shop.py:516）、兵士補充は 1G/人（shop.py:308）を確認済み。
  - `unverified` のまま `_recruit_precedes_cards` が真を返す仕様（DSH 承認の
    fail-closed freeze）は不変。`_chikujou_reserve` の `unverified` 全額保持も
    不変。
  - `_prioritise_recruit`（1178）は `done`/`not_needed` だけ deferred_egg を
    再 pending 化し、`skipped` ではしない（#4989 仕様）— この挙動は維持。

## 残課題

- `egg_receipt` を持たない `unverified`（メニュー未確認の `unconfirmed`、
  `month_sub` 追跡喪失、`opened` 残留からの `unverified`）は同月内に
  settle せず、月替わりの shop 再作成で解放される（VERIFY-5045.md §2）。
- `egg_receipt`/`egg_recheck_settled` は月スコープの shop キーに紐づくため、
  月またぎの照合は意図的に行わない。
- ローカル pytest 未実行（exec 拒否）。
