# VERIFY-5328 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: ノリウツール (ch11 戦闘)

- 対象: azumag/docich issue #5328 / fingerprint `468e54826f86d538` / occurrence
  `3b9da8ae329169794d87`（17-11）＋再発 `0756e954951a0411f744`（17-12, 03:22:46Z）
- ラン: `g1190-ch1v9` / generation 1190 / `hanjuku-chart-v1447-password-measured-order`
- 検証範囲: read-only（本ファイルの作成以外の編集なし）
- 現行 main: `2cbe3542f`（v1451）。v1447 世代のコミット `682d1b0a`（version.py が
  `v1447-password-measured-order`）との比較で `battle_menus.py` / `month_audit.py` /
  `judgment/battle.py` は完全一致、`os_layer/screen.py` の差分は password_entry の
  リスト読み取り（#5325/#5345）のみ — 観測バージョンと現行で発火経路は同一。

## 結論: (a) 実欠陥 — #5259 / #5322 / #5331 と同一機構クラス。現行 main で再発しうる

「召喚獣戦の切り札行を画面に出す」ゲートの前提条件に穴があり、携行が確定した
切り札を一度も一覧化せず攻撃へ進みうる。同一 run `g1190-ch1v9` で #5259
（ch9 ブラッキー）、#5322（ch11 ころぼぐんだん）、#5331（ch11 イッテツーン）
に続く 4 件目の発火であり、本 issue も同一機構の実事象を検出している。
#5347（Ready for review, v1452）に修正が継承されているが未マージのため、現行 main
には何のカバーもない（再発 occurrence は #5347 作成直後の 03:22 に記録）。
証拠の薄い部分は §4 に (d) 要素として残す。

## 根拠となるコード箇所（ファイル・行、main @ 2cbe3542f）

### 1. 切り札を開かず攻撃へ進んだ遷移と判断（検証観点1）

発火関数は `egg_battle_step`（`src/docich/hanjuku/flows/battle_menus.py:2368`）。

- `battle_menus.py:2629-2638` — `cards_left` は `planned_cards` 優先、空なら
  `_confirmed_carried_kit`（`judgment/battle.py:989-1001`: 出撃確定
  `departure_binding` − 確定使用）で埋める。issue の「未使用の携行札:
  ノリウツール」は計画値ではなく実測の携行確定札。監査側も
  `_carried_card_left`（`battle_menus.py:895-908`）で同じ帳簿を見る。
- `battle_menus.py:2639-2642` — Owner 2026-11-22 の意図どおり、召喚獣戦では
  `きりふだ` 行は `たまごをつかう` の下にあり画面スクロールしないと OCR に
  出ない。コード自身が「先に切り札を開く」ことを設計意図としている。
- `battle_menus.py:2643-2653` — 揭示ゲート。発火には
  `cards_left and 'きりふだ' not in text and ('たまごをつかう' in text or empty_egg) and not card_flow`
  が必要。灰色の卵行は白 OCR（`screen.text`）に載らず、灰色専用 predicate でしか
  読まれない（`os_layer/screen.py:1089-1096` の grey 読み取り例）。一方
  `_own_egg_empty`（`judgment/battle.py:2815-2833`）は fail-closed で、観測済み
  整数 0 または `egg_zero_owners` 登録がない限り `empty_egg` は立たない。
  卵行 OCR 欠落＋回数未確定の画面ではスクロール 0 回のまま攻撃経路へ直行する。
- `battle_menus.py:2646-2647` + `:2859` — ゲートが立っても
  `SUMMON_CARD_SCROLL_LIMIT = 3` で打ち止め。3 回の down でも `きりふだ` が
  OCR に現れなければ同じ攻撃経路へ落ちる（scroll 枯渇型）。
- `battle_menus.py:2783-2803` — `move == 'here' and label == 'こうげき'`
  （攻撃行を実際に確定した瞬間）に `battle_boss_card_missed` を起票
  （`:2793-2802`）。issue の理由文はこの `reason` と一致。`cards_used` が
  あれば起票しないガード（`:2793`、#5241）もあり、今回の起票は「1 枚も
  使わずに攻撃確定」であることの裏付け。
- 監査投影: `_track_card_miss`（`month_audit.py:3163-3222`）の `battle` dict は
  `row.get('ally'/'enemy'/'castle'/'screen_kind')` を読む（`:3193-3196`）が、
  `_record` 呼び出し（`battle_menus.py:2796`）は `**_battle_labels`（step・
  variant・deviation のみ）＋ card + reason + observed_metric しか渡さない。
  ally/enemy/castle/screen_kind は記録自体に存在せず `不明`/`None` になる。
  `observed_metric` の `screen`/`text`/`scrolls`/`cursor`（`battle_menus.py:2797-2801`）
  は投影されず捨てられる。「卵なし・奥の手不可のときに」は固定テンプレート文
  （`month_audit.py:3635`）で実測条件ではない。

### 2. #5322 / #5259 との異同（検証観点2）

- **同一機構クラス、別 occurrence。** fingerprint は
  `_fingerprint(chapter, 'card_miss', [card, castle])`（`month_audit.py:3217`）
  で、章・札・城が違えば別 fingerprint。#5259 = ch9/ブラッキー/v1423、
  #5322 = ch11/ころぼぐんだん/v1445、#5331 = ch11/イッテツーン/v1447、
  #5328 = ch11/ノリウツール/v1447。いずれも同一 run `g1190-ch1v9` で、
  v1423 → v1445 → v1447 とバージョンを跨いで継続発火 = 欠陥の生存の実測。
- 発火点の同一性: #5259 / #5322 の検証（VERIFY-5259.md / VERIFY-5322.md）は
  同じゲート `battle_menus.py:2643-2653` を (a) の発火点と確定。v1447 世代と
  現行 main で `battle_menus.py` は無差分であり、発火点のコードは同一。
- 卵行 OCR 欠落機構との切り分け: issue 単体では「scroll 0 でゲート不成立」
  （卵行欠落型）と「ゲート成立後に scroll 3 回枯渇」を区別できない
  （`observed_metric.scrolls` が投影されない）。いずれも同一箇所に帰着する
  実欠陥であり、#5217 系（中断 `card_flow` の `summon_card_resume` 再開、
  `battle_menus.py:2613-2628`）とは別経路 — card_flow 存在時のみ動く再開機構は
  本件をカバーしない。

### 3. owner 修正 PR のカバー範囲（検証観点3）

- owner 修正の現在地: #5265（closed, 未マージ、main と conflict）→ #5334
  （closed Draft、CI 未通過で中断）→ **#5347（Ready for review, v1452,
  base `2cbe3542f` = 現行 main）** が現行の引き継ぎ先。
- #5265（v1441）のカバー範囲: ボス城・生存する自将軍HPパネル・攻撃側
  `こうげき`・認識済み敵召喚獣 1 体・確定携行札が全て揃う場合に限り
  navigation-only の下スクロールを許可。本 issue は `戦闘: 不明 / 城: 不明`
  で、発生時にボス城・認識召喚獣が成立していたか記録から確かめられず、
  仮にマージされていたとしても本 occurrence を救えたかは判定不能。
  いずれにせよ closed・未マージで現行 main へのカバーはゼロ。
- #5347 のカバー範囲（実測の diff 確認済み）:
  - `battle_menus.py` に `own_attack_menu` フォールバック追加 —
    `chart.is_boss_castle(battle.get('castle'))` かつ
    `len(enemy_rows) == len(enemy_monsters) == 1` かつ `general_reading` あり
    かつ `side == 'attack'` かつ `'こうげき' in screen.text` のときだけ、
    卵行 OCR 欠落でも下方向の navigation-only scroll を許可。札選択は
    `きりふだ` 行の実表示まで行わない fail-closed 設計。
  - `month_audit.py` の `_track_card_miss` が `observed_metric` の
    `screen`/`text`/`cards_left` を bounded projection し、`_render_card_miss`
    が「画面 / 文言」へ出す — §4 の (d) 投影欠落を解消する側面修正も含む。
- カバー範囲の評価: 本 issue は敵・城・side が `不明` のため、発生時に
  #5347 のフォールバック条件（ボス城・認識済み召喚獣 1 体・攻撃側）が
  成立していたか確かめられない。非ボス城・未認識召喚獣・防衛側戦闘・
  scroll 枯渇型だった場合は #5347 でも救われない（#5259/#5322 検証と同じ
  残余ギャップ）。未マージのため現行 main では再発しうる — 実際、
  17-12 の再発 occurrence が記録されている。

### 4. 証拠が薄い点 — (d) 要素として残す部分（検証観点4）

結論は (a) だが、issue 単体では以下を一意に決められない。

- **scroll 0 型か scroll 枯渇型か**: `observed_metric.scrolls` は記録されるが
  issue へ投影されない。卵行 OCR 欠落＋回数未確定でゲート不成立（#5259 型）
  か、ゲート成立後に 3 回尽きたかは区別不能。同一 run で同機構が連続発火して
  いるため最も parsimonious な読みは卵行欠落型だが、issue 単体の証拠では
  確定できない。
- **戦闘の内訳**: 敵・味方・城が `不明`（`_battle_labels` がこれらを記録
  しない設計の結果）。ボス城か・認識済み召喚獣だったか・攻撃側/防衛側かが
  不明なため、#5347 のフォールバックが本 occurrence を救えたかも判断不能。
- **「敵召喚獣戦」の分類自体**: reason 文（`battle_menus.py:2802`）は固定で、
  この攻撃ブロックは敵が将軍でも発火しうる。記録上は「召喚獣戦だった」ことの
  独立した裏付けはない。
- **「卵なし・奥の手不可」**: 静的テンプレート文（`month_audit.py:3635`）で
  あり、実際の卵残数・おくのて可否は記録されていない。

## 判定詳細

- **(a) 実欠陥**: 確定携行札を開かず `こうげき` を確定したのは記録上の事実
  （`battle_boss_card_missed` は `cards_left` 非空・`cards_used` 空のときのみ
  起票）。原因機構は現行 main に残る。
- **(b) ではない**: 既存マージ済みの修正はどれもこのゲートを塞いでいない
  （#5217 系は中断 card_flow の別機構、#5241 は重複起票ガード、v1448〜v1451 の
  マージ済み PR は携行補充・パスワード・築城・防衛待機で本経路無変更）。
  #5347 は Ready for review だが未マージ。
- **(c) ではない**: 監査の投影欠落はあるが、起票条件そのものは実測の確定
  携行・未使用・攻撃確定に基づく。
- **(d) 要素**: §4 の部分（scroll 0 vs 枯渇、敵・城・side の内訳）は証拠不足で
  判定不能。起票自体の実欠陥性には影響しない。

## 修正の方向（実装はしない）

1. PR #5347（Ready for review）をレビュー承認・マージする（卵行 OCR 欠落型の主たる
   部分機構を塞ぐ。`own_attack_menu` は navigation-only・fail-closed で
   設計上安全）。#5265・#5334 は closed で再使用しない。
2. `battle_boss_card_missed` の `ally/enemy/castle` と `observed_metric` の
   `scrolls`/`cursor` を `_record` 引数または監査投影へ載せ、issue から
   scroll 0 / 枯渇・ボス城・敵種別を一意に判定できるようにする
   （#5347 は `screen`/`screen_text`/`cards_left` の投影まで含む;
   `scrolls` と `ally/enemy/castle` は依然落ちる）。
3. 非ボス城・未認識召喚獣・防衛側での同型発火が runtime で確認されたら、
   `own_attack_menu` のボス城・認識召喚獣・攻撃側限定の緩和を検討
   （現状は意図的な fail-closed であり、実害確認後の拡張が安全）。

## 手順上の注記

- read-only 検証。VERIFY-5328.md の作成以外のファイル編集・本番 VM 操作なし。
- テスト実行: `PYTHONPATH=src:$HOME/py311shim` で
  `tests/test_hanjuku_boss_egg_row_log.py` と `tests/test_hanjuku_month_audit.py`
  を実行し **142 passed**（card_miss 起票・scroll 揭示・中断 card_flow 再開の
  回帰を含む）。本 box の pytest/tomli は今回インストール（py311shim で
  tomllib を shim）。
- `gh` 関連: issue/PR の参照は Devin git ツール経由で取得（`gh pr view` 系は
  本環境では使用していない）。
- `agent-guards/secret_scan.py` は本 box 未配置のため、投稿物（PR 本文・issue
  コメント）は同等のローカル秘密パターンスキャンで clean を確認してから
  投稿する（AGENTS.md §3.1 フォールバック）。
