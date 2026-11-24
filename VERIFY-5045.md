# VERIFY-5045 — unverified 卵による築城保留の永久化（#4980/#4989 の残余ギャップ）

- Issue: azumag/docich#5045。`unverified` の卵が `deferred_egg` の保留を
  当月内で永久化し、築城機会が失われうる問題の解消。
- 作業 base: main 34fd0c928 時点の checkout（worktree、commit/push なし）。
- handoff: `DOCICH_HANDOFF_PATH` 未設定のため正本未読。コードとリポジトリ内の
  事実を根拠に調査。

## 1. `unverified` になる経路（現行コードでの確認）

`shop['egg'] = 'unverified'` へ至る経路は 4 箇所:

| # | 箇所 | 条件 | 支払い可能性 |
|---|------|------|------------|
| 1 | `flows/month.py:865` `egg_recover_menu_unconfirmed` | `egg_before_recruit` で回復欄・カーソルを有限回（6）待っても読めない | なし（サブ画面未開） |
| 2 | `flows/month.py:1445` `_finish_month_sub` | `egg=='opened'` のまま paid でも unpaid（残金不変の事前確認中止）でもなく終了 | あり |
| 3 | `flows/month.py:1843` `month_sub_abandoned` | `kind=='egg'` かつ `stage=='recovering'`（見積もり受理A送信済み）で B 離脱上限（v1369 / PR #5056） | あり（受理済み） |
| 4 | `strategy/economy.py:576` `_prepare_recruit_egg_check` | `egg=='opened'` だが `month_sub` 不在（追跡喪失） | 不明 |

## 2. `unverified` から他状態への遷移（現行コード）

**同月内の遷移経路は存在しない**（fail-closed の意図的設計）。

- `economy.py:530` と `:544`（`_refresh_egg_priority`）:
  `shop.get('egg') not in ('opened', 'unverified')` で除外し、
  `unverified` を `pending` へ戻さない。
- `economy.py:568-576`（`_prepare_recruit_egg_check`）:
  `(None, 'check', 'skipped')` → `pending` / `'opened'` → `unverified` のみ。
  `unverified` 自体は再 pending 化しない。
- `_month_extra`（month.py:779）のサブ画面オープン対象は
  `('pending', 'check')` のみで `unverified` は開かない。
- `house.py:1452` は `unverified` を `done`/`skipped`/`not_needed` と
  並べて終端扱い。

**月をまたいだ遷移は構造的に存在する**（task 候補 (b) は実質成立済み）:
`mem['shop']` は月キー単位で保持され、`_base_month_plan`（economy.py:1368）が
`shop['key'] != _month_key(header)` で新しい shop を作る。`_plan_extras`
（economy.py:1264）が `egg` を実測ターゲットから再計算するため、翌月は
`pending` → `check` → `opened` と回復が再試行される。翌月の築城は別 shop の
別 `month_sub` であり前月の `cards_held` を引き継がない。

## 3. 保留が永久化する仕組み

- `recruit='deferred_egg'` + `egg='unverified'` → `_recruit_precedes_cards`
  が真（#4989 / v1363 で DSH 承認の fail-closed: 支払い状態不明のまま築城へ
  進む二重支出リスクを避けるため、終端と見なさず優先を継続）。
- その間 `cards_held='recruit_pending'` が latch し、`chikujou` が
  `'check'→'skipped'` に降格。解放（month.py:535）は
  `not _recruit_precedes_cards` が必要だが、`unverified` のままでは
  当月内に二度と満たされない → **当月の切り札・商人・築城は全滅**。
- shop は月単位で再作成されるため翌月は解放されるが、卵の枯渇（回復対象）は
  残ったままなので翌月も回復を試み、同じ原因で `unverified` に落ちれば
  毎月同じ保留が再発する = **実効的な永久化**（g1288 93-1 の事象は
  quantity_confirm 横取りが原因で毎回再現し得た。横取り自体は v1369 で
  修正済みだが、他の放棄原因でも同じ保留が成立する）。

## 4. 採用設計: (a) 残金レシートによる同月内の再照合

採用: **(a) 後続の観測で卵の状態を再確認できたら unverified を解消する**。
(b) は §2 のとおり構造的に既成立（追加実装は月またぎ regression test のみで
担保）。(c)「解決不能のまま一定回数後に解放」は、証拠のない解放で
「支払い状態不明のままサブ画面を開かない」設計凍結を緩めるため不採用。

### 仕組み

1. `_finish_month_sub`（month.py:1522 付近）と `month_sub_abandoned`
   （month.py:1931 付近）で卵が `unverified` になる時点で
   `_keep_egg_receipt` が支払い期待値を `shop['egg_receipt']` へ保持する:
   `before`（サブの `gold_before`）、`cost`（提示額 `quoted_cost`）、
   `bought`（商人購入件数）、`refill_status`（記録時点の兵士補充
   レシート状態）、`upgrades`（当月の築城実績件数）、`recruit`（記録時点の
   募集状態）。`payment_interrupted` のサブ、shop キー不一致、
   `gold_before` 非整数は保持しない（別イベントの残金差を証拠にしない
   既存設計に一致）。
   **月メニューを離れていないサブも保持しない**（CI 指摘で追加）:
   `left_menu` が真でないサブは支払いを試みていないため、その残金差は
   支払いの証拠ではなく stale な背景である。`_keep_egg_receipt` は
   `left_menu is True` のときだけ保持する。既存テスト
   `test_payment_delta_on_a_stale_background_is_not_a_recovery_completion_receipt`
   がこの条件を担保する（修正前は `done` へ誤 settle して失敗、修正後は
   `unverified` 維持で green）。
   - キー設計の根拠: 追跡中の `month_sub` は画面を占有し、他の月一メニュー
     はその間開かない。したがって `gold_before`→記録時点（マーク）の間に
     起きた出費は卵費用しかありえない。マーク「以後」の出費だけを
     上記ベースラインで帳簿から分離すれば、残りは卵費用に限定できる。
2. `month_step` の cards 保留ゲート直前で `_settle_unverified_egg` を評価:
   - まず差額 `delta = before - gold` から、マーク以後の**帳簿に記録された
     他出費** `others` を引く:
     - `soldier_receipt` の `status` が記録時点から変化している:
       レシート行は存在中は置換されないため、変化はマーク後に走った補充を
       意味する。`'paid'` なら `qty` 人 ×1G を加算（`shop.py:308` が
       `before - gold == qty` で確定する正確な単価）、それ以外
       （`'unverified'` 等で支出額が読めない）なら settle しない。
       状態不変のレシートの支出は `before` の内側にあり差額へ影響しない。
     - 記録時点以後に増えた `bought` 行: 各行の `row[1] * row[2]`
       （個数×単価、shop.py:516 と同じ帳簿形式）を加算。
       価格列・個数列のない行・行数減少は帰属不能として settle しない。
     - `chikujou_upgrades` が記録時点から増えている、または `recruit` が
       記録後に `'done'`/`'unverified'` へ進んだ場合: 築城費・募集費が
       差額に混ざりうるため settle しない（保留中は構造上到達しないが
       防衛として残す）。
   - `rest = delta - others` が 0: 残金変動を他出費で説明し尽くせた。
     **卵費用は一度も成立しなかった証明** → `egg='skipped'`
     （見積もり受理前の放棄と同じ「当月未実施」終端）。
   - `rest` が正で `quoted_cost`（int）と一致: 残る差額は卵費用に限られる
     → `egg='done'`（`_finish_month_sub` 自身の paid 照合
     `before - gold == quoted_cost` と同一の証拠基準）。
   - それ以外は `unverified` 維持（再試行しない・完了扱いもしない）。
     `before`・`gold` が int で読めない、`gold > before`（入金・増加）、
     `month_sub` 追跡中も評価しない。
3. `egg='skipped'` への settle は解放後に `_prepare_recruit_egg_check` /
   `_refresh_egg_priority` が `skipped` を再 `pending` 化しうるため、
   `shop['egg_recheck_settled']` を立てて同月の再 pending を塞ぐ
   （economy.py:536, 546, 575。unverified → skipped → repend → unverified
   の再試行ループを防止。翌月の自然な再試行は shop 再作成で無関係に動く）。
   `egg='done'` settle 時も同じフラグを立て、stale な roster 再照合が
   確定済みの卵を reopened 扱いしないようにする。
4. settle 成功は `_record` で `egg_unverified_settled` を記録
   （gold_before/after・quoted_cost・others・settled を保持）。
   `egg='done'` への settle は `_prioritise_recruit`（economy.py:1178）の
   既存経路で `deferred_egg` → `pending` に復帰し、通常の資金条件を満たせば
   当月の募集へ進む。`'skipped'` は deferred_egg を再 pending しない
   （#4989 の既存仕様: 当月の募集は最終 deferral）。

### 二重支出にならない理由

- `skipped` 化は `delta - others == 0`（支払い不成立の厳密証明）に限る。
  `others` には整数の `qty` / 実支払価格が読めた記録済み出費だけを含め、
  一つでも帰属不能な出費候補（無価格列の商人行・新規築城・新規募集確定）
  があれば settle 自体を行わない。支払いが成立していた可能性のある場合に
  `skipped` は絶対に使わない。さらに `egg_recheck_settled` で当月の
  再オープン自体を禁止するため、仮に誤判定でも同月の再支払いは発生しない
  （二重防御）。
- `done` 化は `delta - others == quoted_cost` に限る。`others` で説明
  しきれない残額が提示額と一致する時だけ支払いの成立を認める。
  回復儀式の完走は証明していないが、`_finish_month_sub` の paid 照合と
  同一基準（残金差 = 提示額 → done）に揃えるため既存の判定粒度と矛盾しない。
- どちらにも当てはまらない差額（他出費が混ざりうる・読めない）は
  `unverified` 維持 = 現行の fail-closed をそのまま残す。
- 解放されるのは切り札・商人・築城の購入「機会」だけで、卵サブ画面の
  再オープンや卵予約（`reserve` → `_chikujou_reserve` の else 分岐で
  `unverified` は全額保持、`skipped` は英雄卵の 50G のみ）には触れない。
  築城も残金から卵予約を引いた余裕でしか実行されず、支払い済みの
  可能性がある金を食わない。

### 残る制約（検品ゲートで要確認）

- `egg_receipt` を持たない `unverified`（#1 メニュー未確認、#4 追跡喪失、
  `month_sub_lost` 由来の 'opened' 残留）は settle できず当月は保留のまま。
  月替わりで解放される（§2）。
- `payment_interrupted` のサブはレシートを残さないため settle しない
  （既存の「別イベントの残金差は証拠にしない」に一致）。
- `unverified` のまま `_recruit_precedes_cards` が真を返す仕様自体は
  維持（検品ゲートで確定した freeze）。本変更は「証拠で解決できる時だけ
  終端へ落とす」経路の追加であり、未解決のまま解放しない。
