# VERIFY-5528 — [半熟英雄] 切り札在庫があるのに非携行で出撃: ch6 ピオーネ

- 対象: [docich#5528](https://github.com/azumag/docich/issues/5528) / 監査 `sortie_without_cards`（confidence `high`）
- fingerprint `9c33b4ec49d5cf6e` / occurrence `d1eb5f32fc808d1e7305`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1496-card-open-limit`
- ch6 / 月 6-10 / 将軍 ピオーネ / 出撃元 アルマムーン / 対象 ボス / purpose `attack`
- 在庫: ころぼぐんだん×45, グリンボー×45, ピッグローラー×33, フットバース×30, ミックミー×58 / 携行: なし（実測空）/ 計画cards: []
- 判定: sortie_without_cards / 型「計画が空（自動携行の対象・v1448 以降）」
- 検証ベース: `origin/main` = `70497a203`（`BOT_VERSION` `hanjuku-chart-v1496-card-open-limit`）
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。
- ラン成果物 (`hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/`) は VM 側のため直接参照せず、公開 issue ページ・git オブジェクト・現行コードを照合した。

## 結論: **(a) 実欠陥 — boss wave の再計画が在庫札を空 kit に上書きする**

本件の実測は `want=[]` / `carried=[]` の boss wave 出撃であり、在庫には5種類の札が存在する。`_auto_carry_safe_card` が `egg_drop == 0` の札だけを選ぶこと自体は安全条件どおりで、この在庫から `None` を返すのも正しい。しかし、それだけでは全体挙動を (b) と結論づけられない。

現行 base の `strategy/replan.py` は、boss target の注文について `_stock_cards(...)` で在庫にある札を選んだ直後に `cards = []` で上書きしている。直前の owner コメントは「切り札は持たせたほうが良い」、同じ行の説明は「carry what is there」であり、在庫札を持たせるという方針と実装が矛盾する。追加 wave の注文も `cards: []` 固定である。

さらに `_empty_kit_blocks_departure(want, carried)` は `bool(want) and carried == []` のため、計画が空の `want=[]` は確認側 guard の対象外となる。したがって、在庫札を選ぶ計画が boss wave で消されても、空 kit のまま出撃できる経路が残っている。これは単なる監査誤検知ではなく、計画層と確認側 guard の未カバーによる実欠陥である。

なお、在庫5種がすべて `egg_drop != 0` なので、空計画時の安全自動携行が何も追加しないことは意図どおり。修正対象は安全条件を緩めることではなく、boss wave が既に選んだ在庫札を消さないこと、および空計画の boss sortie をどう fail-closed にするかである。
## 1. fingerprint / occurrence の再計算

- `_fingerprint` (`month_audit.py` L3174-L3176): `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`
  - 本件: `6|sortie_without_cards|sortie_without_cards` → `9c33b4ec49d5cf6e` ✓ (issue と一致)
- `incident_id` (`month_audit.py` L2508-L2511): `sha256(f"{runtime_id}|{generation}|{kind}|{step}|{general}|{int(now)}")[:20]`
  - 本件: `g1190-ch1v9|1190|sortie_without_cards|A:bf8c3dee:M1cfd05|ピオーネ|1795576695` → `d1eb5f32fc808d1e7305` ✓ (issue と一致)

再発コメント (occurrence `e4fd944abea2bb51e2e8`) も同一 fingerprint で、在庫構成がほぼ同一
(ミックミー 57 vs 58、追加にラズベリー) — 同一機構の再観測。

## 2. boss wave で在庫札が消える経路 (検証観点1)

- 計画札は空 (`planned_cards: []`)。出撃は mechanical 調整チャートの wave attack (暫定) で、base chart のボス注文とは別系統。
- `strategy/replan.py` の通常注文生成は、`base = chart_orders.get(target) or card_orders.get(target)` の後に `_stock_cards(mem, ...)` を呼び、在庫にあるカードを計画へ入れる。
- しかし boss target の場合、その直後の `if chart.is_boss_castle(target): cards = []` が選択結果を破棄する。追加 wave の `boss_wave_orders` も `'cards': []` 固定。
- 同じ箇所の owner コメントは「切り札は持たせたほうが良い」、空 kit 上書きのコメントは「carry what is there」。また `_stock_cards` 自体も、在庫札を実際に持たせる方針を docstring に記録している。したがって boss wave の空上書きはこの方針と矛盾する。
- deploy 側の `_empty_kit_blocks_departure(want, carried)` は `bool(want) and carried == []`。plan-empty (`want=[]`) では guard が false になり、空 kit 出撃を止めない。
- 在庫5種はすべて `egg_drop != 0` のため、`_auto_carry_safe_card` が安全札を選ばないことは正しい。安全条件を緩める必要はなく、replan が既に選んだ stock-aware kit を boss 分岐で消さない修正と、plan-empty boss wave の受入条件を明示する修正が必要。

`cards_autocarried ×11` は issue の120秒集計窓全体の件数であり、対象 step に帰属する証拠ではない。今回の step で auto-carry が発火したとは結論しない。
## 3. #5510 (ch3 タルタル) との異同 (検証観点2)

- #5510: 通常城 (メガパタ) への attack、在庫はバルムンク×1 のみ。
  バルムンクは `BOSS_ONLY_CARDS` → 通常戦では使用・追加携行の対象外。
  通常在庫ゼロ → 空 kit は方針どおり → (c) 誤検知 (監査の適格在庫判定漏れ)。
  PR #5512 で `_sortie_cards_event` が non-boss 出撃の stock から `BOSS_ONLY_CARDS` を除外。
- 本件: ボス城への attack (wave)、在庫は通常札 5 種 (BOSS_ONLY_CARDS ではない)。
  boss 出撃なので `BOSS_ONLY_CARDS` フィルタは適用されない (PR #5512 の設計どおり)。
  札は「携行可能」だが、`egg_drop != 0` のため自動携行の安全条件を満たさない。
- 異同: #5510 は hard 禁止札 (通常戦で使用不可) が唯一の在庫 → 監査が誤って適格在庫とみなした。
  本件は携行可能な札が在庫にあるが、自動携行の安全条件 (egg_drop 0) を満たさない →
  監査の起票条件 (stock 正数 + carried 空) は満たすが、ゲーム挙動は方針どおり。
  #5510 の PR #5512 修正は本件の起票を止めない (boss 出撃で stock が残れば引き続き検出)、
  これは PR #5512 の回帰テスト ("Boss sortie to ボス with only バルムンク still creates the incident") と整合。

## 4. #5498 (ch1 クランベリー) / #5457 (ch12 チキータ) との異同

- #5498: ch1、非ボス attack (けっかい)、stock = イッテツーン/ゼンマイン/ダイチスイム/フットバース/ブラッキー
  (いずれも egg_drop > 0)、計画空、carried 実測空。本件と同型 (通常在庫あり・安全札なし)。
- #5457: ch12、retake、計画札あり (クースカン/ミックミー/ノリウツール)、carried 実測空だが
  measured receipt が曖昧 (unconfirmed) → (d) 判定不能。PR #5460 で measured/unc を区別。
- 本件は #5498 と同型 (通常在庫あり・安全札なし・計画空) で、#5457 とは異なる
  (本件は carried_observed=true の実測空、receipt 曖昧ではない)。

## 5. 検証 base と観測ラン version の区別

- 観測ランの version は `hanjuku-chart-v1496-card-open-limit`。
- この検証記録の base は commit `6c1fd176f25a855087fcbd4b487494690605485c` で、`src/docich/hanjuku/version.py` の `BOT_VERSION` は `hanjuku-chart-v1498-recall-battle-mode-lost`。
- v1496 は incident が発生した実行時の version、v1498 はコードを照合した PR base の version。両者を同じ検証基盤として記載しない。
- base の `replan.py` には boss target の `cards=[]` 上書きと追加 wave の空 kit 固定が存在する。よって、v1496 の観測だけでなく、PR base の現行コードにも同じ欠陥経路が残っている。
## 6. 判定詳細

- **(a) 実欠陥**: boss wave の通常注文生成で `_stock_cards` が選んだ在庫札を、boss target 分岐が `cards=[]` で破棄する。追加 wave も空 kit 固定で、owner の「切り札は持たせたほうが良い / carry what is there」というコメントと矛盾する。
- **空 kit の確認側 guard も不足**: `_empty_kit_blocks_departure` は plan-empty (`want=[]`) を対象にしないため、空計画の boss wave は carried が空でも出撃可能。
- **auto-carry の安全条件は変更不要**: 本件の在庫はすべて `egg_drop != 0` なので、`_auto_carry_safe_card` が追加しないのは正しい。安全条件を緩めて解決する問題ではない。
- **#5498 と同型**: 機械 wave / plan-empty / inventory-positive / observed-empty kit という同じ boss-wave override 経路であり、両方を (a) と分類する。#5498 と異なる結論を支持する base 上の差は確認できない。
- **#5510 とは別層**: #5510 は通常戦で BOSS_ONLY_CARDS を適格在庫から除外する監査修正。本件は boss wave の計画が在庫札を空に上書きする問題で、PR #5512 では解消しない。
- **cards_autocarried ×11 は対象 step の証拠ではない**: issue の集計窓全体の件数であり、対象 step の自動携行発火を示さないため、本件の根拠から除外する。
- **後続実装の受入条件**: boss wave の各注文が実測在庫・予約・ID上限を守って携行札を計画すること、同一在庫を複数 wave に過剰予約しないこと、plan-empty で札が読めない場合に誤って空 kit を承認しないことを回帰テストで固定する。
## 検証メモ

- 実行した検証: issue 本文の公開ページ取得、fingerprint/incident_id の sha256 再計算 (一致)、
  `_auto_carry_safe_card` / `_deploy_cards` / `_sortie_cards_event` / `_positive_card_stock` の
  静的照合、`reference.CARDS` の egg_drop 値確認、ch6 チャート注文 (`CHAPTER_6_ORDERS`) 確認、
  PR #5512 の修正内容・回帰テストとの整合確認。
- ドキュメントのみの追加のためテストは実行しない。
- 本ファイルは worktree ルートに未コミットで置いてある。push・PR 作成・issue コメントの投稿は
  検品ゲート (Muse) 側で実施 (本レーンは read-only 検証のみ)。
- read-only 検証であり、本番操作・コード修正・VM への接続は行っていない。
"