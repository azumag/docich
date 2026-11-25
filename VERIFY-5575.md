# VERIFY-5575 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: クースカン (ch11 ソーピニヨン)

- 対象: [docich#5575](https://github.com/azumag/docich/issues/5575) / 監査 `card_miss`（confidence `high`）
- fingerprint `497c96b5221276f2` / occurrence `3bba7180ddd6130a6d8d`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1504-ch10-approach`
- ch11 / 月 13-5 / 未使用の携行札: **クースカン**（携行札候補: クースカン）
- 戦闘: デュオニソス vs ソーピニヨン / 城: 不明 / side: 不明 / 時刻 08:59:36Z
- 画面 `egg_battle_menu` / scrolls `0` / cursorY `176` / 表示文言 `こうげきもうこうげき`
- 卵状態: `empty_egg=false` / `summon_egg_unavailable=false` / `enemy_summon_observed=true`
- 検証ベース: `origin/main` = `031d0bed304212fa6b71aaadedb88d3cefef78ff`（`BOT_VERSION` `hanjuku-chart-v1529-owned-target-settle`）。観測版 v1504 は `64d5112a1` / `fc8f8aa62` 系の marker。作業割当時 base `0d3c3e0fc` は #5619/#5615 マージ前なので、カバー判定は現行 main で確認した。
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物 (`hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/`) は VM 側のため直接参照せず、issue 本文・git オブジェクト・観測版/現行コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: **(b) 既存修正でカバー済み — v1504 時点では post-open frozen `card_flow` の実欠陥だったが、現行 main にマージ済み PR #5619（v1520）が同型経路を直接カバーする。追加の修正 PR は不要**

本件は誤検知ではない。v1504 時点の実事象として、携行札クースカンが残っているのに `こうげき` が確定された。ただし原因は新規・未対処の欠陥ではなく、#5574/#5612 と同じ「召喚戦で一度切り札一覧を開いた後、選択済み・消費未確定の `card_flow` が `egg_battle_menu` 上で進行不能になり、残り札の揭示と再オープンを抑止する」亜型である。

現行 main には PR #5619（merge commit `f2409ad08`、main の祖先）が入っている。同修正は凍結 flow の有界観測中に `こうげき` を確定せず入力を保留し、3観測目に `_card_use_unclassified` で flow を解放して残り `cards_left` の揭示スクロールへ戻す。回帰テスト `test_a_frozen_announce_flow_holds_attack_then_releases` は本件と同じ初期形（`summon_card_scroll=0`、表示文言 `こうげきもうこうげき`、選択済み札とは別の残り携行札）を直接モデル化している。したがって本検証の分類は、発生時の実欠陥を否定しないが現行では既存修正でカバー済みという意味で **(b)** とする。

## 1. fingerprint / occurrence の sha256 再計算

現行 `month_audit.py` の card_miss 専用式で再計算し、どちらも一致した。

| 項目 | コード | 入力 | 再計算結果 | issue |
| --- | --- | --- | --- | --- |
| fingerprint | `_fingerprint`（`src/docich/hanjuku/month_audit.py:3367-3369`）は `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`。`_track_card_miss`（同 3572-3573）は `[card, castle]` を渡す | `11|card_miss|クースカン+`（城不明なので末尾は空文字） | `497c96b5221276f2` | `497c96b5221276f2` |
| occurrence | `_track_card_miss`（`src/docich/hanjuku/month_audit.py:3520-3523`）は `sha256(f"{runtime_id}|{chapter}|{int(float(at))}|{card}")[:20]`。時刻 2026-11-25 08:59:36Z は epoch `1795597176` | `g1190-ch1v9|11|1795597176|クースカン` | `3bba7180ddd6130a6d8d` | `3bba7180ddd6130a6d8d` |

両方とも完全一致し、issue データは現行コードの監査形式と一致する。章・札・城・ラン・秒のどれかが違えば一致しないため、本件の fingerprint / occurrence は正しい。

## 2. emit 条件と証拠値の意味

- emit サイトは `egg_battle_step` の `battle_boss_card_missed`（現行 `src/docich/hanjuku/flows/battle_menus.py:3458-3497`、v1504 でも同構造 `3342-3361`）だけである。実測カーソルが `こうげき` 行で `move == 'here'`、`missed` 非空、`cards_left` 非空、`cards_used` 空、1戦闘1件未記録が条件。
- `_carried_card_left`（現行 `battle_menus.py:1269-1284`）は planned/確定携行キットから `{cards_selected, cards_used, cards_unclassified, cards_missing}` を除いた残りを返す。クースカンは `BOSS_ONLY_CARDS` ではなく、通常の使用対象である。
- `cursorY=176` は `screen.menu_cursor` の実測で、`こうげき` 行確定と整合する。
- `empty_egg=false` / `summon_egg_unavailable=false` は卵を使えた肯定証拠ではない。issue 本文の「卵なし・奥の手不可のときに」は静的テンプレート文であり、本検証では measured evidence として扱わない。
- `enemy_summon_observed=true` は敵召喚文脈の補強証拠で、emit 理由「敵召喚獣戦で携行切り札を開けず、こうげきへ進んだ」と一致する。

## 3. `scrolls: 0` が示す post-open frozen flow 型

`scrolls=0` は本件の決定的な手掛かりである。`scrolls: 不明` ではなく整数 0 なので、`metric.get('scrolls')` に実際に int 0 が入っていた（`month_audit.py:302-303` の `_bounded_int`、投影は同 3541）。

v1504 `battle_menus.py` で `summon_card_scroll` を書く経路は2系統だけである。

1. 揭示スクロール実行時に `+1` する（v1504 `3181-3190`）。
2. 召喚戦で `きりふだ` 行を開いたとき `0` へリセットする（v1504 `3199-3215`、特に `3212`）。

したがって本件は未スクロール型でも scroll 枯渇型でもなく、**この戦闘で少なくとも一度切り札一覧を開いた後、箱が先頭ページへ戻った状態**である。

それでも emit 時の表示文言は `こうげきもうこうげき` で `きりふだ` を含まず、`cards_left` にはクースカンが残る。もし `battle['card_flow']` が偽なら、`_summon_card_row_hidden`（v1504 `2822-2851`、現行 `2886-2893`）は次の経路で真になる。

- `cards_left` 非空、`card_flow` なし、`きりふだ` 非含有で先頭ゲートを通過し、
- `screen.kind == 'egg_battle_menu'` かつ `こうげき` / `もうこうげき` を含むため最終行で True。

True なら attack へ進む前に `return [pad('down')]` する。実際には `こうげき` が確定され emit されたので、**`battle['card_flow']` は真であった**と論理強制される。

flow の種類も絞れる。v1504 の interrupted-flow 救済（`3138-3153`）は `stage in ('menu','down','list')` かつ `selection_planned` と `read_kit` を持たない flow を `summon_card_resume` へ退避してクリアする。クリアされれば同じ観測で reveal scroll へ進み emit に届かない。一方 `read_kit` は防衛戦の実携行読み取り専用で、それ単独では `summon_card_scroll=0` を作らない。`scrolls=0` は召喚箱からの開札を必要とするため、本件で残っていた阻害 flow は **`stage == 'announce'` または `selection_planned=True` を持つ、選択済み・消費未確定の flow**と判定できる。

開かれた/選択された札 X はクースカンではない。X=クースカンなら `cards_selected` に入り、`_carried_card_left` の attempted セットでクースカンが除外されて本件 emit が成立しない。したがって、**別札 X が開札・選択されたか少なくとも開札 flow に入り、その消費が確定しないまま召喚画面へ置き換わり、真の flow が残りのクースカンの揭示・再オープンを抑止した**と読める。X の札名と発射有無は issue 本文だけでは確定できないが、root cause の機構分類には不要である。

## 4. 関連 issue / PR との異同

| 対象 | 証拠形・修正 | 本件との関係 |
| --- | --- | --- |
| #5259 family（#5322/#5331/#5352/#5362/#5373/#5381/#5398） | 卵行 OCR 欠落または卵残数不明で `_summon_card_row_hidden` が一度も発火せず、`きりふだ` 行揭示前に攻撃する pre-reveal 型。v1504 には owner 修正済みの拡張ゲートがある | **別型**。本件は `scrolls=0` で開札後。切り札行 OCR/所有判定の前段ゲート不成立ではなく、開札後の `card_flow` 凍結が主因 |
| #5560（ch10 クースカン、v1500） | `scrolls=3`、表示文言に `たまごをつかう` を含む。揭示 down 3回を使い切っても `きりふだ` 行が読めない scroll 枯渇型。PR #5615 の `_summon_card_rearm` が対象 | **別亜型**。本件は `scrolls=0` で開札済み。`_summon_card_rearm` は `scrolls>=3` の再提示だけを扱い、真の `card_flow` が塞ぐ経路を解放しない |
| #5574（ch11 ノリウツール、v1504） | 同ラン・同月、`scrolls=0` / cursorY `176` / 表示文言 `こうげきもうこうげき`。検証済み結論は announce 凍結 `card_flow` 亜型で PR #5619 がカバー | **同一機構**。戦闘と未使用札は異なるが、証拠形は同じ。#5574 では残り候補が `ノリウツール, クースカン`、本件では `クースカン` であり、少なくとも別札 X が `cards_selected` 側へ移っていた点も同じ構造 |
| PR #5615 | `scrolls>=3` の scroll 枯渇を8観測間隔・最大2回で再揭示。main マージ済み | 本件の対象外。scrolls=0 の post-open frozen flow には効かないが、本件はその修正を必要としない |
| PR #5619 | 凍結 `read_kit` を閉じ、`announce` / `selection_planned` flow は有界観測中 `return []` で攻撃を保留し、3観測目に `_card_use_unclassified` で解放。main マージ済み | **本件を直接カバー**。解放後は同じ観測で残り `cards_left` の揭示 scroll / reopen へ戻る。emit の `card_flow` 投影も追加済み |

## 5. PR #5619 が現行 main でカバーすることの確認

- `git merge-base --is-ancestor f2409ad08 origin/main` は成功し、PR #5619 の merge commit は現行 main の祖先である。
- 現行 `battle_menus.py:3213-3260` は、召喚画面へ置き換わった flow を次の3類に分ける。
  1. `read_kit`: `_defense_kit_read_unread` で fail-closed に閉じる（3214-3220）。
  2. 未選択 `menu/down/list`: `summon_card_resume` へ退避してクリア（3221-3235）。
  3. `announce` / `selection_planned`: 3観測まで攻撃を保留し、上限で `_card_use_unclassified` へ解放（3236-3258）。
- 解放後は `cards_left` を再計算し、`_summon_card_row_hidden` と reopen ゲートが通常どおり動く（3270-3344）。切り札を開いたときだけ `summon_card_scroll` が 0 へ戻る（3338）。
- 回帰テスト `test_a_frozen_announce_flow_holds_attack_then_releases` は、`summon_card_scroll=0`・`cards_selected=['ミックミー']`・凍結 announce flow・残り `ノリウツール`・表示文言 `こうげきもうこうげき` で、保留中は `battle_boss_card_missed` が出ず、解放後に揭示 scroll と残り札の reopen へ進むことを確認している。本件はカード名こそ違うが、同じ状態遷移である。
- PR #5619 は emit の `observed_metric` に `card_flow` を追加し、監査 issue へ描画する（現行 `battle_menus.py:3471-3489`、`month_audit.py:313-315` / `3542`）。将来の再発は issue 単体で凍結札 X を識別できる。

## 6. 判定詳細と残存不確実性

- **(a) 新規実装を要する実欠陥ではない**: v1504 当時は実欠陥だったが、現行 main に同型を直接防ぐ PR #5619 がマージ済み。重複実装は不要。
- **(b) 既存修正でカバー済み**: 上記のとおり。PR #5615 は別亜型の修正であり、本件のカバー根拠は PR #5619 である。
- **(c) 判定不能ではない**: sha256 一致、`scrolls=0`、非含有文言、emit 条件、v1504 の flow 救済範囲から、post-open frozen flow 型と論理確定できる。
- **不確実な点（結論に影響なし）**: 凍結 flow の札 X の名前、X が実際に発射したか、城・side の同定は issue 単体では確定できない。確定には run `g1190-ch1v9` の `hanjuku_decisions.jsonl` で当該戦闘前後の `battle_summon_card_open` / `battle_card_selected` / `battle_card_unclassified` / `battle_boss_card_missed` を時系列で読む必要がある。
- **留意（別件）**: v1524 の `_card_usable_here`（自城・防御戦では切り札を使えない）は emit 側 `_carried_card_left` / `_card_usable_in_battle` に直接は反映されていない。本件は side 不明かつ v1504 発生なので遡及適用しないが、将来 `side=defense` の card_miss が出た場合は別途照合が必要である（#5574 検証でも同じ留意が残っている）。

## 検証メモ

- 実行した検証: issue 本文照合、fingerprint / occurrence の Python sha256 再計算、観測版 v1504 (`64d5112a1` 系) と現行 main `031d0bed3` の静的照合、`summon_card_scroll` 全代入箇所の確認、#5259 family / #5560 / #5574 の比較、PR #5615・#5619 の diff・main ancestry・回帰テスト確認。
- ドキュメントのみの追加のためテストは実行しない。
- 依頼どおり verify-only PR は作成しない。結論は issue コメントへ記録する。
- read-only 検証であり、本番操作・コード修正・VM への接続は行っていない。
