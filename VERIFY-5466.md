# VERIFY-5466 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: クースカン (ch4 戦闘)

- 対象: [docich#5466](https://github.com/azumag/docich/issues/5466) / 監査 `card_miss`（confidence `high`）
- fingerprint `3922dfede2b0311f` / occurrence `7521de2696beb559607e`
- ラン: `g1342-1fe0734c` / generation `1342` / `hanjuku-chart-v1484-sortie-kit-evidence-confidence`
- ch4 / 月 9-5 / 未使用の携行札: クースカン / 時刻 17:55:22Z
- 検証ベース: `origin/main` = `c6fd25815b1b307ad3aa640d7444f535c18e3d8c`（`BOT_VERSION` `hanjuku-chart-v1490-fort-recall-picker-owner-bounded`）
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照せず、issue・GitHub の状態・git オブジェクト・現行/観測版コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: **(a) 実欠陥 — #5381 と同一機構の再発。v1484 はこの欠陥をカバーしていない**

本 occurrence は #5381 と同じ fingerprint を持つが、別月・別時刻・別 occurrence の再発である。fingerprint は現行式で再計算して一致した。

- `sha256("4|card_miss|クースカン+")[:16]` = `3922dfede2b0311f`
- 17:55:22Z は epoch `1795542922`。card_miss の occurrence 式 `sha256("g1342-1fe0734c|4|1795542922|クースカン")[:20]` = `7521de2696beb559607e`
- #5381 は ch4 月 6-7 / occurrence `31826554919d39546f61`、本件は ch4 月 9-5 / occurrence `7521de2696beb559607e` であり、重複起票ではなく同一 fingerprint の別 incident として扱うのが正しい。

起票自体は実事象を示す。v1484 の `egg_battle_step` は `move == 'here' and label == 'こうげき'` のときだけ `battle_boss_card_missed` を出し、さらに `missed`・`cards_left` 非空・`cards_used` 空・未起票の条件をすべて要求する（`c73eeaece:src/docich/hanjuku/flows/battle_menus.py` L3256-3275）。したがって「確定携行札を残したまま、カードを1枚も使わず、こうげき行を実際に確定した」ことが emit の前提条件である。誤検知 (c) ではない。

## 1. v1484 に修正が入っていないことの照合

issue の観測版 `hanjuku-chart-v1484-sortie-kit-evidence-confidence` は commit `c73eeaeceaef40e9a896e3336b0a65a4bbe59ce1`（2026-11-25 02:47:09 JST、PR #5460）の `BOT_VERSION` と一致する。本件時刻 17:55:22Z（02:55:22 JST）はこの版の稼働中だった。直後の main には番号を再利用した `hanjuku-chart-v1484-clear-ending-corner-end` もあるが、本件の marker は接尾辞 `sortie-kit-evidence-confidence` であり、そちらとは区別する。

`git merge-base --is-ancestor` で確認したとおり、PR #5387 の最初の修正 commit `2f5b493674df2456e8fe74e87135d5f71af2fe46` も最終 head `8f179092f822424102dc8a97c584efb76088e0bd` も `c73eeaece` の祖先ではない。したがって v1484 が #5387 の修正をカバーすることはない。

実際の v1484 の揭示ゲートも旧条件のままだった。

```python
if (cards_left and 'きりふだ' not in (screen.text or '')
        and ('たまごをつかう' in (screen.text or '') or empty_egg)
        and not battle.get('card_flow')):
```

（`c73eeaece:src/docich/hanjuku/flows/battle_menus.py` L3116-3118）

この条件は「卵行が白 OCR に読める」または「卵残数0を確定的に測定済み」でないと切り札行を出す下スクロールを始めない。一方、画面分類は `こうげき` / `もうこうげき` とメニュー行情報だけで `egg_battle_menu` を返し、コメントにも `たまごをつかう drops out of OCR` とある（`c73eeaece:src/docich/hanjuku/os_layer/screen.py` L1549-1557）。つまり、卵行が灰色・使用済み・未所持で OCR から落ち、かつ卵0の確定記録がない画面では、同じコマンド箱が `egg_battle_menu` に来るのに scroll gate が立たず、携行札を一覧へ出さないまま `こうげき` へ進みうる。これは VERIFY-5381 が確定した機構と同じである。

## 2. #5381・#5385・#5387・#5472 の関係

- #5381 は CLOSED、検証記録 PR #5385 は merged。ただし #5385 は VERIFY 記録のみで、ゲーム動作の修正ではない。
- 実装修正 PR #5387 は現在 closed（draft / superseded）で、main にマージされていない。最新コメントでも「#5472 に統合済み、Draft は superseded としてクローズ」と記録されている。依頼時点の「OPEN」から状態は進んでいるが、「fix commit `2f5b49367` は origin/main に未マージ」という焦点は事実として確認できた。
- 同じ欠陥に対する実効修正は別 PR #5472（squash merge commit `0a99182c0e6cb15f0bf2ec3278ba6ce45c86effc`、2026-11-25 03:50:51 JST）で main に入った。PR 題名は v1485 だが、番号衝突を避けた実際の `BOT_VERSION` は `hanjuku-chart-v1488-summon-card-without-egg`。
- `git merge-base --is-ancestor 0a99182c0 origin/main` は真。現行 main にはこの修正が含まれる。ただし本 occurrence は 02:55:22 JST の v1484 で発火し、修正 merge は約56分後なので、**v1484 での再発確認**という結論は変わらない。

PR #5472 の修正は、旧ゲートを `_summon_card_row_hidden` へ切り出し、`screen.kind == 'egg_battle_menu'` かつ `こうげき` / `もうこうげき` が読める実測コマンド箱では卵行欠落を no-egg 証拠として既存の有界スクロールを行うものである。選択は従来どおり実表示の `きりふだ` 行だけを確定する fail-closed で、scroll 枯渇時の盲選択ではない。あわせて `battle_boss_card_missed` へ `ally` / `enemy` / `castle` / `side` / `screen_kind` を追加し、本件のような「戦闘: 不明 / 城: 不明 / 画面: None」の投影欠落も解消している。

## 3. なぜ同一機構と判断できるか

- emit サイトは v1484 でも `battle_boss_card_missed` の1箇所だけで、理由文は issue 本文と一致する。
- fingerprint は章・kind・札・城から作られる。v1484 の emitter は `castle` を top-level へ渡していないため第3要素は空で、`4|card_miss|クースカン+` になる。章と札が #5381 と同じなので fingerprint が同じになるのは監査式どおりである。
- v1484 の gate は VERIFY-5381 が確認した v1456 と同じ構造で、卵行 OCR 欠落時の穴が残っている。
- ch4 でクースカンを計画携行する注文は VERIFY-5381 のとおり非ボス城 4-A1 が最有力で、自動携行の可能性もある。本件でも城・side は投影欠落で不明だが、#5387 のボス城/攻撃側条件に依存しない最終修正 #5472 は、この非ボス城の可能性を含む形で卵行欠落型を塞ぐ。

## 4. 残る判定不能点（結論を変えない (d) 要素）

v1484 の `battle_boss_card_missed` は `ally` / `enemy` / `castle` / `side` / `screen_kind` を emit せず、監査側はそれらを読むため、issue には `不明` / `None` しか残らない（`c73eeaece:src/docich/hanjuku/month_audit.py` L3266-3269, L3713-3716）。`observed_metric` の `screen` / `text` / `scrolls` / `cursor` も issue へ投影されない。

このため次は本 issue だけでは確定できない。

- 実際の城・敵・味方・攻撃側/防衛側
- scroll 0 回で gate が立たなかったのか、上限3回を使い切ったのか
- 卵残数やおくのて可否（issue の「卵なし・奥の手不可」は固定テンプレート文）
- クースカンが計画携行か自動携行か

ただしこれらは発火の内訳の不明さであり、「v1484 に修正がなく、携行札を残して攻撃確定した実事象が起票された」という結論を不定にするものではない。

## 判定詳細

- **(a) 実欠陥**: v1484 に修正はなく、emit 条件から実際にカード未使用の攻撃確定が起きた。後続 PR #5472 で main には修正済み。
- **(b) ではない**: 観測版 v1484 は修正前で、設計意図「先に切り札を開く」に反する。
- **(c) ではない**: fingerprint と occurrence を再計算一致させ、emit の前提条件も静的に確認した。投影欠落は証拠品質の問題で、起票の実事象性を否定しない。
- **(d) 要素あり**: 城・side・scroll 数・携行経路は issue だけでは不明。ただし再発判定には影響しない。

## 検証メモ

- 実行した検証: fingerprint / occurrence の再計算、`git merge-base --is-ancestor`、v1484・現行 main・PR #5472 diff の静的照合、GitHub issue/PR 状態の照合。
- ドキュメントのみの追加のためテストは実行しない。`git diff --check` と `ops/vm_actions/secret_scan.py VERIFY-5466.md` は実施済みで、どちらも clean。
- read-only 検証であり、本番操作・コード修正・VM への接続は行っていない。
