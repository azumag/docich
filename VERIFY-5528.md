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

## 結論: **(b) 設計どおり — 自動携行の安全札が在庫に存在しなかったため、空 kit 出撃は方針どおり**

出撃は計画札が空 (wave attack / 暫定 mechanical order) で、自動携行 (`_auto_carry_safe_card`) が発火する条件
(在庫に `egg_drop == 0` の札がある) を満たす札が在庫になかったため、携行ゼロで出撃した。
これは自動携行機能 v1448 以降の設計 (安全札のみ自動携行・卵落とし回避) と整合する。
監査が `sortie_without_cards` (high) を起票したのは、監査側の適格在庫判定が `egg_drop` を考慮せず
「在庫が正数で存在する」だけで起票条件を満たすためであり、#5510 とは異なる層の起票
(本件は hard 禁止札ではなく、自動携行の安全条件を満たさない札)。ゲーム挙動の欠陥ではない。

## 1. fingerprint / occurrence の再計算

- `_fingerprint` (`month_audit.py` L3124-3127): `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`
  - 本件: `6|sortie_without_cards|sortie_without_cards` → `9c33b4ec49d5cf6e` ✓ (issue と一致)
- `incident_id` (`month_audit.py` L2499-2504): `sha256(f"{runtime_id}|{generation}|{kind}|{step}|{general}|{int(now)}")[:20]`
  - 本件: `g1190-ch1v9|1190|sortie_without_cards|A:bf8c3dee:M1cfd05|ピオーネ|1795576695` → `d1eb5f32fc808d1e7305` ✓ (issue と一致)

再発コメント (occurrence `e4fd944abea2bb51e2e8`) も同一 fingerprint で、在庫構成がほぼ同一
(ミックミー 57 vs 58、追加にラズベリー) — 同一機構の再観測。

## 2. 自動携行が働かなかった理由 (検証観点1)

- 計画札は空 (`planned_cards: []`)。出撃は mechanical 調整チャートの wave attack (暫定) で、
  base chart のボス注文 `6-B1` (ヴィーナス / エンジェリン・マグネガキン) とは別系統。
- 空計画時の自動携行は `_deploy_cards` (`strategy/kit.py`) の non-strict 経路で実行される:
  `carried` が空なら `_with_extra_card(carried, _auto_carry_safe_card(mem, order), ...)`.
- `_auto_carry_safe_card` (`strategy/kit.py` L117-146) の条件:
  - `mem.card_stock` から spent/dropped を除いた札を列挙
  - `count > 0` かつ `reference.CARDS[name].egg_drop == 0` のみ対象
  - 安全札 (卵落 0) が無ければ `None` を返し、何も携行しない
- 本件の在庫札と egg_drop:
  - ころぼぐんだん: 12
  - グリンボー: 4
  - ピッグローラー: 2
  - フットバース: 5
  - ミックミー: 2
  - いずれも `egg_drop != 0`。したがって `_auto_carry_safe_card` は `None` を返し、
    自動携行は発火しても札を追加しない。空 kit 出撃は方針どおり。

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

## 5. v1496 での再発か (検証観点3)

- `BOT_VERSION` v1496 (`hanjuku-chart-v1496-card-open-limit`) に自動携行機能 v1448 以降は含まれる
  (`_auto_carry_safe_card` は現行 main に存在)。
- 本件は v1496 のコードで発火しており、コード欠落や回帰ではない。
  空 kit は「安全札が在庫にない」という在庫構成に起因する方針どおりの結果。
- 同一 fingerprint の 2 件目も同一機構 (在庫構成がほぼ同一)。

## 6. 判定詳細

- **(a) ではない**: ゲーム挙動 (空 plan → 自動携行 → 安全札なし → 携行ゼロ) は
  `_auto_carry_safe_card` の設計どおり。auto-carry は発火している (records の `cards_autocarried` ×11)。
- **(b) 設計どおり**: 空 plan の wave attack で、在庫に egg_drop==0 の札がない場合、
  自動携行は札を追加しない。これは owner 方針「資源を使い切る・卵落としは避ける」
  (`strategy/kit.py` L108-113 コメント) と整合。監査の起票は、監査側が egg_drop を
  考慮しない広い stock 定義を使っていることに起因するが、#5510 のような hard 禁止札の
  誤計上ではなく、監査が「携行可能な札が在庫にある」事実を正しく surface している。
- **(c) ではない**: #5510 の (c) は BOSS_ONLY_CARDS (hard 禁止) の誤計上。
  本件の stock は boss 出撃で携行可能な通常札であり、監査の stock 定義は本件では
  事実と整合している。誤検知ではない。
- **(d) 要素あり**: wave attack にカードを計画すべきか (非安全札を携行すべきか) は
  戦略判断であり、read-only では判定不能。auto-carry の安全条件を緩める
  (`_auto_carry_safe_card` の egg_drop==0 制限変更) は owner 方針の変更であり、
  本検証のスコープ外。ラン成果物 (`dropper_card_kit` / `card_stock` / departure binding)
  が入手できれば spent/dropped の内訳を確認できるが、結論 (b) は変わらない。

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