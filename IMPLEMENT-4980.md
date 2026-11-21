# docich#4980 「[半熟英雄] 築城可能なまま月一終了: ch10 93-1」 調査・実装

判定: **欠陥** — `month_sub_abandoned` 自体は設計どおりだが、それが生んだ終端状態を
切り札・築城の保留機構が永久化してしまう順序の欠陥。修正済み（v1352）。

## 事象

ラン g1288-b1680690 / generation 1288 / hanjuku-chart-v1348-summon-scroll-batch、
月 ch10 93-1。決定列:

```
month_extras_plan → month_plan → army_count_check → soldier_refill_outcome
→ house_country_*_observed → goal_queued → house_hanjuku_level_observed
→ recruit_batch_planned → recruit_priority_plan
→ month_sub_open（たまごのかいふく）→ egg_recover_select → quantity_confirm_accept
→ month_sub_abort → month_sub_abort_text_advance
→ month_sub_abandoned（B で閉じない月一画面を上限で諦め、今月は実行しない）
→ recruit_deferred_egg → month_done（築城は未試行・所持金 9949 / 必要残し 130）
```

監査（v1346 導入 `chikujou_available_at_end`）が「所有城がそろって築城可能なのに
月一終了」を検出した。

## 原因

`src/docich/hanjuku/flows/month.py` の購入順序機構に 2 つの欠陥が重なった。

1. **終端化した `deferred_egg` が保留を外せない**
   - `month_sub_abandoned`（month.py:1804-1809）は放棄したサブ画面の種別を
     `shop['egg'] = 'skipped'` とする。卵回復は当月終端になる。
   - 卵が `skipped` だと `_recruit_egg_ready`（economy.py:1499）は不成立となり、
     募集は `_defer_recruit_for_egg` で `deferred_egg` へ。
   - `deferred_egg` が当月 `pending` に戻る唯一の経路は economy.py:1176 の
     `egg in ('done','not_needed')` 再計画のみ。卵が `skipped`/`unverified`/
     `None` のままでは二度と復帰しない = **`deferred_egg` は当月終端**。
   - しかし `RECRUIT_BEFORE_CARDS`（month.py:723）に `deferred_egg` が含まれる
     ため `_recruit_precedes_cards` が常に真を返し、`cards_held` の解放条件
     （month.py:535 `not _recruit_precedes_cards`）が永久に満たされなかった。

2. **hold 時の `chikujou` 片道降格が解放時に復元されない**
   - `_hold_month_cards`（旧 month.py:736-737）は `chikujou='check'` を
     `'skipped'` に書き換えるだけで、release（`month_cards_released`）時に
     戻す経路が無かった。deferred_roster の月でも同じ取りこぼしが起き得た。
   - 設計意図は「募集が**未決の間**、切り札・築城より募集を先に確定させる」
     （v511 #3718 のコメント・doc）で、終端化した延期を永久保留する意図ではない。
   - 募集費の保護は別途 `_chikujou_reserve`（economy.py:1546-1555）が担う
     （`deferred_egg` + `recruit_priority` でも RECRUIT_COST を予約する）ため、
     保留を解放して築城を試しても募集資金は侵されない。本事象でも必要残し
     130G はこの予約系で、所持金 9949G は十分に築城可能だった。

結論: 「離脱が築城の機会を奪った」のではなく、**離脱が作った終端状態を
順序制御が終端と認識できず、切り札・築城の保留を永久化した**ことが欠陥。

## 変更

- `src/docich/hanjuku/flows/month.py`
  - `_recruit_precedes_cards`: `recruit == 'deferred_egg'` の場合、当月の卵の
    問いがまだ解決し得る（`egg in ('pending','check','opened')`）ときだけ
    優先を継続する。`done`/`not_needed`/`skipped`/`unverified`/なしの終端状態
    では優先しない → `cards_held` が解放される。
  - `_hold_month_cards`: `chikujou` を `'check'→'skipped'` に降格するとき
    `shop['chikujou_held'] = True` で降格の帰属を記録する。
  - release ブロック（month.py:535-546）: `cards_held` 解放時に
    `chikujou_held` 由来の 'skipped' を `'check'` へ復元し、
    `month_cards_released` の observed_metric に `chikujou` を追加。
- `src/docich/hanjuku/version.py`
  - `BOT_VERSION = 'hanjuku-chart-v1352-chikujou-at-month-end'`
- `ops/vm_actions/collect_diagnostics.py`
  - 許可リストに `"hanjuku-chart-v1352-chikujou-at-month-end",` を追加
    （v1349 エントリ直後、末尾カンマあり・既存エントリ保持）。
- `tests/test_hanjuku_month_status_single_read.py`
  - `test_cards_are_held_while_the_recruit_question_is_unresolved`:
    `deferred_egg` の優先条件を卵の生きた状態（pending/check/opened）に
    限定する新仕様へ更新・拡充（終端状態・egg 未設定は優先しない）。
  - 新規 `test_settled_egg_deferral_releases_the_held_castle`: 本事象を
    再現（卵 skipped 放棄後 → recruit deferred_egg → hold → release →
    `ちくじょう` サブ画面が開く）する回帰テスト。

## 検証

- **ローカル実行**: 本 worktree で `python3 -m pytest tests/test_hanjuku_month_status_single_read.py -x -q`
  と `python3 -m compileall` の実行を複数回試みたが、exec 呼び出しが
  ユーザーによって拒否されたため、ローカルでのテスト実行・構文確認は
  **未実施**。CI または別環境での実行が必要。
- **代替検証（コードトレース）**: 変更経路を手動トレース済み。
  - 再現条件（`recruit='check'` + `egg='skipped'` + `egg_before_recruit`）で
    `month_step` は `_month_extra` → `_defer_recruit_for_egg` →
    `_hold_month_cards` → 直後に release（新条件で `not precedes` が真）→
    `chikujou` 'check' 復元 → `_month_chikujou` が ちくじょう を開く。
  - `egg='check'`（卵未解決）の既存 hold テスト
    `test_deferred_egg_holds_merchant_and_castle_until_recruit` は
    `deferred_egg` が引き続き優先するため挙動不変。
  - 卵が `done`/`not_needed` に解決 → economy.py:1176 で `pending` 復帰 →
    従来どおり優先・hold（回帰なし）。
  - `chikujou_held` は shop スコープのフラグで、shop は月ごとに再作成される
    （economy.py:1368）ため月をまたぐ漏洩なし。hold → release → 再 hold の
    繰り返しも冪等。

## 残課題

- ローカル pytest / compileall 未実行（exec 拒否のため）。CI で
  `test_hanjuku_month_status_single_read.py` および関連
  （`test_hanjuku_multi_chikujou.py`、`test_hanjuku_recovery_before_recruit.py`、
  `test_hanjuku_recruit_roster.py` 等）の回帰確認が必要。
- 卵サブ画面自体が `month_sub_abort` → `month_sub_abandoned` した原因
  （`quantity_confirm_accept` 後の未測定画面が終わらなかった件）は別件。
  本修正はその放棄後の波及欠陥のみを直す。
- 同じ終端保留の罠は `recruit` 種別の `month_sub_abandoned` でも
  `shop['recruit']='skipped'` → 優先外 → release で自然に解消されることを
  コード上確認済み（追加変更なし）。

## 検品ゲートでの修正（2026-11-22、主担当）

関連 4 ファイル実行で `test_hanjuku_recovery_before_recruit.py` の 2 件が
失敗した（`test_lost_or_unverified_recovery_payment_is_not_retried_or_treated_as_done[unverified|opened]`）。
卵が `unverified`（支払い済みかもしれないが完了未確認）の状態で保留を解放すると
`chikujou` の month_sub が開いてしまい、回復前募集の設計（支払いの再試行も
完了扱いもしない、サブ画面を開かない）に反する。

修正: `_recruit_precedes_cards` の解放条件を `done` / `not_needed` /
`skipped` / なしに限定し、`unverified` は引き続き優先（保留）とした。
`house.py:1452` が `unverified` を `done` / `skipped` / `not_needed` と並べて
終端扱いしているが、回復前募集の安全凍結が優先する。`unverified` の卵で
保留が永久化する問題は残るが、支払い状態が不明なまま築城へ進む二重支出の
リスクより小さく、設計判断として DSH・owner への判断依頼に回す。
`test_cards_are_held_while_the_recruit_question_is_unresolved` の
`unverified` 期待値をそれに合わせて更新した（本 PR で新規更新したテストであり、
既存の設計凍結テスト自体は変更していない）。
