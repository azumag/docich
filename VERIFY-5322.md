# VERIFY-5322 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: ころぼぐんだん (ch11 戦闘)

- 対象: azumag/docich issue #5322 / fingerprint `7359e40183196fe5` / occurrence `757a4c1fe6005d055626`
- ラン: `g1190-ch1v9` / generation 1190 / `hanjuku-chart-v1445-early-resource-rescue`（v1445 = `01d44e161`、main の祖先）
- 検証範囲: read-only（本ファイルの作成以外の編集なし）
- 現行 main: `cc2b2d969`（v1449）。v1445→HEAD で `src/docich/hanjuku/flows/battle_menus.py` の差分はゼロ（`git diff 01d44e161..HEAD` で 0 行）— 観測バージョンと現行で発火経路は同一。

## 結論: (a) 実欠陥 — #5259 / #5331 と同一機構クラス。現行 main で再発しうる

「召喚獣戦の切り札行を画面に出す」ゲートの前提条件に穴があり、携行が確定した
切り札を一度も一覧化せず攻撃へ進みうる。#5259（ch9 ブラッキー、VERIFY-5259.md
/ PR #5287 で (a) 確定）・#5331（ch11 イッテツーン）と同一 run g1190-ch1v9 で
3 回目の発火であり、検出器は仕様どおり実事象を報告している。修正は PR #5265 →
#5334 → #5347（Draft, v1451）と継承されているが未マージのため、現行 main には
何のカバーもない。証拠の薄い部分は §4 に (d) 要素として残す。

## 根拠となるコード箇所（ファイル・行、main @ cc2b2d969）

### 1. 切り札を開かず攻撃へ進んだ遷移と判断（検証観点1）

発火関数は `egg_battle_step`（`src/docich/hanjuku/flows/battle_menus.py:2368`）。

- `battle_menus.py:2631-2638` — `cards_left` は `planned_cards` 優先、空なら
  `_confirmed_carried_kit`（`judgment/battle.py:989-1001`: 出撃確定
  `departure_binding` − 確定使用）で埋める。つまり issue の「未使用の携行札:
  ころぼぐんだん」は計画値ではなく実測の携行確定札。監査側も
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
  卵行欠落＋回数未確定の画面ではスクロール 0 回のまま攻撃経路へ直行する。
- `battle_menus.py:2647` + `:2859` — ゲートが立っても
  `SUMMON_CARD_SCROLL_LIMIT = 3` で打ち止め。3 回の down でも `きりふだ` が
  OCR に現れなければ同じ攻撃経路へ落ちる（scroll 枯渇型）。
- `battle_menus.py:2716-2803` — `move == 'here' and label == 'こうげき'`
  （攻撃行を実際に確定した瞬間）に `battle_boss_card_missed` を起票
  （`:2793-2802`）。issue の理由文はこの `reason` と一致。`cards_used` が
  あれば起票しないガード（`:2793`、#5241）もあり、今回の起票は「1 枚も
  使わずに攻撃確定」であることの裏付け。
- 監査投影: `_track_card_miss`（`month_audit.py:3163-3220`）の `battle` dict は
  `row.get('ally'/'enemy'/'castle'/'screen_kind')` を読む（`:3193-3195`）が、
  `_record` 呼び出し（`battle_menus.py:2796`）は `**_battle_labels`（step・
  variant・deviation のみ、`judgment/battle.py:90-93`）＋ card + reason +
  observed_metric しか渡さない。ally/enemy/castle/screen_kind は記録自体に
  存在せず `不明`/`None` になる。`observed_metric` の `screen`/`text`/`scrolls`/
  `cursor`（`battle_menus.py:2797-2801`）は投影されず捨てられる。
  「卵なし・奥の手不可のときに」は固定テンプレート文（`month_audit.py:3635`）
  で実測条件ではない。

### 2. #5259 との異同と v1445 世代での発火点（検証観点2）

- **同一機構クラス、別 occurrence。** fingerprint は
  `_fingerprint(chapter, 'card_miss', [card, castle])`（`month_audit.py:3217`）
  で、章・札・城が違えば別 fingerprint。#5259 = ch9/ブラッキー/v1423、
  #5331 = ch11/イッテツーン/v1447、#5322 = ch11/ころぼぐんだん/v1445。
  いずれも同一 run `g1190-ch1v9` で、v1423 → v1445 → v1447 とバージョンを
  跨いで継続発火している = 欠陥が生存し続けている実測。
- 発火点の同一性: #5259 の検証（VERIFY-5259.md）は同じゲート
  `battle_menus.py:2643-2653` を (a) の発火点と確定。v1445→HEAD で
  `battle_menus.py` は無差分であり、#5322 の観測世代から現行まで発火点の
  コードは同一。
- v1445（early-resource-rescue）世代固有の要素: `_survival_menu`
  （`battle_menus.py:361`〜）系の救済機構は `battle_menu_step`
  （`:1023`/`:1046`/`:1090`）からのみ呼ばれ、`egg_battle_step` 経路には
  関与しない。本 issue の発火点（`egg_battle_step` 内攻撃ブロック）は
  救済機構とは別経路であり、v1445 の新機構が発火点を変えた形跡はない。

### 3. #5282 との異同、owner 修正のカバー範囲、owner 方針との整合（検証観点3）

- **#5282 とは別層。** #5282 は「在庫があるのに非携行で出撃」（計画/出撃層:
  調整チャート採用指示が `_stock_cards` 在庫フォールバックを受けない、
  VERIFY-5282.md §残る欠陥）。#5322 は携行が確定していた（確定キット帳簿に
  載る）のに戦闘メニュー層で未使用。層・機構とも別であり、一方の修正では
  他方は消えない。
- owner 修正の現在地: #5265（closed, 未マージ）→ #5334（closed, 未マージ;
  CI fixture 不整合と指摘）→ **#5347（open Draft, v1451）** が現行の
  引き継ぎ先。#5347 は (i) 卵行 OCR 欠落時の `own_attack_menu` フォールバック
  （ボス城・生存する自将軍HPパネル・攻撃側 `こうげき`・認識済み敵召喚獣
  1 体・確定携行札が全て揃う場合に限り navigation-only の down を許す、
  札選択は実表示までしない）と、(ii) card-miss の `screen`/`text` を
  `observed_metric` から投影する監査側修正を含む。
- カバー範囲の評価: 本 issue は戦闘・城が `不明` なので、発生時に
  「認識済み召喚獣1体・ボス城」が成立していたかを記録から確かめられない。
  仮に敵が未認識召喚獣または非ボス城だった場合、#5347 の fail-closed
  フォールバックでも本件は救われない（#5259 検証と同じ残余ギャップ）。
  scroll 枯渇型だった場合も対象外。
- owner 方針との整合: Owner 2026-11-22「敵召喚で自軍将軍が切り札を持った
  まま こうげき へ直行」（`battle_menus.py:2639-2642`）と Owner 2026-11-23
  「退却は禁止、札が無いときだけ攻撃」（`:2685-2686`）から、未使用の確定
  携行札を残したまま攻撃確定した判断は意図と矛盾する。監査の検出は
  オーナー方針どおりの実欠陥報告であり (b)/(c) ではない。

### 4. 証拠が薄い点 — (d) 要素として残す部分（検証観点4）

結論は (a) だが、issue 単体では以下を一意に決められない。

- **scroll 0 型か scroll 枯渇型か**: `observed_metric.scrolls` は記録されるが
  issue へ投影されない（`month_audit.py:3193-3196` が捨てる）。卵行 OCR 欠落
  ＋回数未確定でゲート不成立（#5259 型）か、ゲート成立後に 3 回尽きたかは
  区別不能。#5259 と同一機構と読むのが最も parsimonious だが、issue 単体の
  証拠では確定できない。
- **戦闘の内訳**: 敵・味方・城が `不明`（`_battle_labels` がこれらを記録
  しない設計の結果）。ボス城か・認識済み召喚獣だったかが不明なため、
  #5347 のフォールバックが本 occurrence を救えたかも判断不能。
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
  （#5217 系は中断 card_flow の別機構、#5241 は重複起票ガード）。#5347 は
  未マージ Draft。
- **(c) ではない**: 監査の投影欠落はあるが、起票条件そのものは実測の確定
  携行・未使用・攻撃確定に基づく。
- **(d) 要素**: §4 の部分（scroll 0 vs 枯渇、敵・城の内訳）は証拠不足で
  判定不能。起票自体の実欠陥性には影響しない。

## 修正の方向（実装はしない）

1. PR #5347 を main 追従・CI 通過させてマージする（卵行 OCR 欠落型の主たる
   部分機構を塞ぐ。`own_attack_menu` は navigation-only・fail-closed で
   設計上安全）。
2. `battle_boss_card_missed` の `ally/enemy/castle` と `observed_metric` の
   `screen`/`text`/`scrolls`/`cursor` を `_record` 引数または監査投影へ載せ、
   issue から scroll 0 / 枯渇・ボス城・敵種別を一意に判定できるようにする
   （`month_audit.py:3193-3196`。#5347 が screen/text のフォールバック投影を
   既に含む）。
3. 非ボス城・未認識召喚獣での同型発火が runtime で確認されたら、
   `own_attack_menu` のボス城・認識召喚獣限定の緩和を検討（現状は意図的な
   fail-closed であり、実害確認後の拡張が安全）。

## 手順上の注記

- read-only 検証。VERIFY-5322.md の作成以外のファイル編集・本番 VM 操作なし。
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
