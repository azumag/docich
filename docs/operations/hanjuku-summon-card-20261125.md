# 召喚獣戦・ボス戦で卵が使えないときの切り札使用（2026-11-25）

## オーナー指示

> ボス戦や敵が召喚獣をだしたとき、卵を持っていない、かつきりふだを持っている場合、切り札が使われること。

召喚獣戦・ボス戦のコマンド箱（`こうげき` / `もうこうげき` / `たまごをつかう` /
`きりふだ` / `たいきゃく`）で、卵が使えず携行切り札が残っているときは、`こうげき`
ではなく `きりふだ` を開いて札を使う。

## 旧挙動の欠陥

`egg_battle_step` は、召喚獣戦で切り札行を画面に出すために箱を下へ有界スクロール
していたが、その発火条件は

- `たまごをつかう` が画面テキストに読めている、または
- その将軍の卵残数0を実測済み（`empty_egg`）

のどちらかだった。灰色・使用済みの卵行は白OCRから落ちる（`os_layer/screen.py` 実測）
ため、**卵行が読めず、かつ0の実測記録も無い**画面ではスクロールが一度も走らず、
携行札を残したまま `こうげき` を確定していた。監査はこれを `card_miss`
（「敵召喚獣戦/ボス戦で切り札未使用」）として起票しており、#5259/#5322/#5331/
#5352/#5362/#5373/#5381/#5398 が同一機構の再発だった。

## 変更

- `flows/battle_menus.py` の `_summon_card_row_hidden` を追加し、揭示ゲートを
  この判定に置き換えた。卵行が読めない場合でも、**召喚獣戦のコマンド箱と実測できた
  画面**（`screen.kind == 'egg_battle_menu'` かつ `こうげき`/`もうこうげき` が読める）
  なら、卵が使えない証拠として同じ有界スクロール（`SUMMON_CARD_SCROLL_LIMIT = 3`）を行う。
  防衛の召喚箱が既に `summon_egg_unavailable` を立てている場合も同じ扱いにする。
- `screen.kind` の条件は、メッセージ画面が `egg_battle` ラッチ中に `こうげき` の語を
  含みうるため。そこでスクロール予算を使うと本物のコマンド箱が出た時に尽きる。
- 切り札行を開いた時点で `summon_card_scroll` を0へ戻す。一覧を閉じた箱は先頭ページへ
  戻るため、同じ戦闘の2枚目以降（2026-11-24 オーナー指示）も同じ有界回数で揭示できる。
- `battle_boss_card_missed` の記録へ `ally` / `enemy` / `castle` / `side` /
  `screen_kind` を追加した。監査のissue投影がこれらを読むため、従来の
  「戦闘: 不明 / 城: 不明 / 画面: None」では summon か boss か・攻撃側か防衛側かを
  判別できなかった。`observed_metric` に `screen_kind`・`empty_egg`・
  `summon_egg_unavailable`・`enemy_summon_observed` も残す。

## fail-closed の維持

- スクロールは移動入力だけで、確定は行わない。
- 札の選択は従来どおり**実表示の `きりふだ` 行**を必要とする（`_battle_menu_to` が
  行を読めないときはAを送らない）。
- 箱が読めない・行が灰色・一覧が空の場合は、従来の卵・おくのて・こうげきの判断へ戻る。
- スクロール回数は戦闘ごと・札を開くたびに有界。

## テスト

`tests/test_hanjuku_boss_egg_row_log.py`

- `test_a_missing_egg_row_reveals_then_opens_the_carried_card`:
  卵残数未読・卵行OCR欠落で下スクロールし、`きりふだ` 表示後に札を開く。開いた後
  `summon_card_scroll` が0へ戻る。
- `test_a_message_panel_does_not_spend_the_card_scroll_budget`:
  `kind='text'` のメッセージではスクロールしない。
- `test_the_missed_card_record_carries_the_battle_identity`:
  `battle_boss_card_missed` に戦闘の同一性が載る。

既存の `test_the_summon_card_row_is_revealed_by_scrolling_down`（卵0実測）と
`test_a_carried_card_is_opened_before_attacking_a_summon`（きりふだ既表示）は維持。

## 未確認・残件

- 自然ランでの `card_miss` 消滅は未確認。次回以降の監査issueで同じ fingerprint が
  出ないこと、`battle_boss_card_missed` の `castle`/`side` が埋まることを確認する。
- スクロール3回で `きりふだ` が読めないまま枯渇する型、および防衛の `mixed_item_box`
  がコマンド行を `menu_rows` にだけ持つ型は、本変更の対象外。実ランで再発したら
  `_command_rows_to` 経由の揭示を検討する（PR #5347 の `own_attack_menu` は未マージ）。
- 卵行がOCRで落ちただけで実際は卵が使える場合もスクロールするが、移動のみで
  選択は実表示の `きりふだ` 行に限るため、札を消費しない。


## 2026-11-25 追補: 開札上限の適用順序と監査記録（v1494）

v1493 の再オープン上限は、隠れた `きりふだ` 行を探すスクロールより後に評価されていたため、
上限到達後も最大3回の down を追加で送る余地があった。v1494 では上限を先に評価し、
上限到達後はスクロールも再オープンもしない。

残っている札は使用済みと見なさず `cards_unclassified` に移し、
`battle_summon_card_abandoned` に `opens` / `open_limit` / `abandoned` を記録する。
既に進行中の `card_flow` は中断しない。これにより、上限による安全な打ち切りと
実際の札使用・使用未確認を区別できる。

回帰テスト:
- `test_summon_card_open_limit_is_checked_before_scrolling`: 上限到達後に down/open を送らず、
  未確認札を一度だけ記録する。
- `test_summon_card_open_limit_does_not_interrupt_an_active_card_flow`: 開札後の進行中フローを
  上限処理が中断しない。
- hot-load marker v1494 を diagnostics allowlist と固定値テストへ同期。

#5506 は同じ章・同じ実行世代で発生した未使用札の観測として関連付けるが、
この上限修正だけで札の実使用を保証したとは扱わない。自然ランでの成功確認までIssueはOpenのままにする。
