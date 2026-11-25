# VERIFY-5541 — [半熟英雄] 切り札在庫があるのに非携行で出撃: ch8 カシス

- 対象: [docich#5541](https://github.com/azumag/docich/issues/5541) / 監査 `sortie_without_cards`（confidence `high`）
- fingerprint `518e264735ab9bd7` / occurrence `fe6b5f3e5868997c22b1`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1498-recall-battle-mode-lost`
- ch8 / 月 8-3 / 将軍 カシス / 出撃元 あるまむーん / 対象 ボス / purpose `attack`
- 在庫: ころぼぐんだん×41, カルゲンジー×24, グリンボー×98, ダンスライン×88, ピッグローラー×99, フットバース×99, ブラッキー×24, ミックミー×90 / 携行: なし（実測空）/ 計画cards: []
- 判定: sortie_without_cards / 型「計画が空（自動携行の対象・v1448 以降）」
- 検証ベース: `origin/main` = `b33256a09d8b1227879075144863a4290ea86dc8`（`BOT_VERSION` `hanjuku-chart-v1511-status-panel-close`）
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。
- ラン成果物 (`hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/`) は VM 側のため直接参照せず、公開 issue ページ・git オブジェクト・現行コード・静的再構成を照合した。

## 結論: **(b) 既存の修正でカバー済み — v1498 では #5498/#5528 同型の実欠陥だが、PR #5544 / v1500 が現行 main で本経路をカバーする**

本件は監査誤検知ではない。v1498 時点では、boss target の暫定 attack / boss wave が `cards=[]` に固定され、在庫に `egg_drop=0` の安全札が無い本件では v1448 自動携行も発火せず、plan-empty では empty-kit guard も効かないため、実測空 kit のまま発射できた。これは #5498 と #5528 の修正版結論で判定された `(a) 実欠陥` と同じ機構である。

ただし現行 main では PR #5544（merge commit `493a4666379a1a9ba0653a96fe59e950f71ee58c`、`hanjuku-chart-v1500-boss-wave-stock-cards`）がマージ済みで、boss target の concrete sortie と追加 boss wave の両方が stock-aware kit を保持する。issue と同じ在庫構成を現行 `interim_candidates` / `_stock_cards` に入れた静的再構成では `['ミックミー', 'ミックミー']` が返り、concrete boss order は空 kit にならなかった。したがって本件は新規の未修正欠陥ではなく、**観測版 v1498 に存在した既知欠陥が v1500 で修正済み**という扱いが適切である。

実機 runtime での札選択・戦闘使用は read-only 検証範囲外であり、本結論はコード・マージ済み修正・回帰テスト・静的再構成のカバー判定である。VM 実機確認が必要なら別途の運用検証として扱う。

## 1. fingerprint / occurrence の再計算

issue embedded JSONの値は、v1498 と現行 main で同一の `month_audit.py` の式と一致する。

- `_fingerprint`（v1498 `month_audit.py` L3174-L3176、現行 L3319-L3321）: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`
  - `_build_sortie_cards_incident` は `[signals[0]]` を渡す（現行 L2642）。本件の先頭 signal は `sortie_without_cards`。
  - raw: `8|sortie_without_cards|sortie_without_cards`
  - sha256先頭16: `518e264735ab9bd7` — issue と一致
- `sortie_without_cards` の incident_id は、汎用 `_incident_id(identity, chapter, month)` ではなく `_build_sortie_cards_incident` 内の専用 raw 式で作られる（v1498 L2508-L2511、現行 L2637-L2640）。
  - raw: `g1190-ch1v9|1190|sortie_without_cards|A:da59c2da:M2b037c|カシス|1795582188`
  - sha256先頭20: `fe6b5f3e5868997c22b1` — issue の occurrence / incident_id と一致
  - 参考: 汎用 `_incident_id` に `runtime|generation|chapter|month` を入れた値は本件 occurrence と一致しない。これは式の適用先の違いであり、issue の値の矛盾ではない。

`created_at` は `1795582188.395587` で、整数化すると `1795582188`。issue 本文の決定列でも `order_launched` は 04:49:48Z で、時刻と整合する。

## 2. v1498 で空 kit 出撃した機構

### 2.1 観測事実

embedded JSONの `sortie_event` は次の通りで、未観測ではなく実測空である。

- `carried: []` / `carried_observed: true` / `planned_cards: []`
- `general: カシス` / `source: あるまむーん` / `target: ボス` / `purpose: attack`
- 決定列: `order_start`（暫定: キャンディーでボスを攻撃）→ `hero_priority_selected`（カシス）→ `sortie_confirm` → `y_jump_open` → `y_jump_confirm` → `sortie_departure_requested` → `order_launched`

計画上の将軍キャンディーからカシスへ変わったのは `hero_priority_selected` による先発交代で、カード携行機構とは独立である。監査は実際に発射したカシスの `order_launched` を正しく拾っている。

### 2.2 v1498 は boss target の stock-aware kit を空へ上書きしていた

v1498 commit `6c1fd176f` の `strategy/replan.py` では、通常の暫定 attack では `_stock_cards(...)` を計算する一方、boss target では直後に `cards=[]` へ上書きしていた。

- v1498 `replan.py` L1468-L1471: `cards = _stock_cards(...)` の後、`if chart.is_boss_castle(target): cards = []`
- v1498 `replan.py` L1509-L1514: 追加 boss wave も `'cards': []` 固定

本件の note は「暫定: キャンディーでボスを攻撃」、target は `ボス` であり、この経路に一致する。`planned_cards=[]` は採用層の喪失や監査側の読み違いではなく、v1498 の計画入力どおりである。

### 2.3 v1448 自動携行は本件在庫では候補ゼロ

`_auto_carry_safe_card` は `egg_drop == 0` の札だけを自動携行する（`strategy/kit.py` L117-L146）。本件在庫8種はすべて `egg_drop > 0` なので、`None` を返すのは v1448 の安全条件どおりである。

| 札 | 在庫 | egg_drop |
| --- | ---: | ---: |
| ころぼぐんだん | 41 | 12 |
| カルゲンジー | 24 | 1 |
| グリンボー | 98 | 4 |
| ダンスライン | 88 | 1 |
| ピッグローラー | 99 | 2 |
| フットバース | 99 | 5 |
| ブラッキー | 24 | 3 |
| ミックミー | 90 | 2 |

issue の `cards_autocarried: 5` は直前120秒の集計値であり、対象 step `A:da59c2da:M2b037c` に帰属する証拠ではない。`decisions.before` に残る当該 step の列にも `cards_autocarried` は無く、実測 `carried=[]` と矛盾しない。

### 2.4 plan-empty は empty-kit guard の対象外だった

`flows/deploy.py` の `_empty_kit_blocks_departure(want, carried)` は現行でも `bool(want) and carried == []`（L912-L920）である。v1498 では boss wave が `want=[]` を作るため、この guard は発火せず、`want=[]` / `got=[]` が一致したまま `sortie_confirm` → `order_launched` へ進んだ。PR #5544 後は positive stock がある concrete boss order で `want` が空にならないため、仮に携行選択に失敗すればこの guard が `carried=[]` を止める位置に入る。

## 3. #5498 / #5528 / #5510 との異同

### #5498（ch1 クランベリー、結論 (a)）との関係

同型である。両件とも boss target の暫定 attack / wave で `planned_cards=[]`、在庫には使用可能な通常札があり、実測 `carried=[]` で `order_launched` へ進んだ。#5498 は ch1・target けっかい、本件は ch8・target ボスという差はあるが、どちらも `chart.is_boss_castle(target)` 経路であり、v1498 までの `cards=[]` 強制と plan-empty guard 未カバーが共通原因である。

差異は在庫構成だけである。#5498 はイッテツーン・ゼンマイン等、本件はミックミー×90を含む8種があった。いずれも `egg_drop=0` の安全自動携行対象は無かったが、PR #5544 の stock-aware kit は安全札限定ではなく実在庫から計画するため、両方の在庫構成をカバーする。

### #5528（ch6、結論 (a) 修正版）と PR #5544 のカバー範囲

#5528 と本件はほぼ同型で、どちらも boss wave / plan-empty / positive stock / measured empty / high confidence である。#5528 の修正 PR #5544 は、ちょうど本件と同じ2箇所を直した。

現行 main での該当箇所:

- `strategy/replan.py` L1485-L1490: boss target でも `_stock_cards(...)` の結果を保持し、`MOVE_ANY_GENERAL` の read-only probe だけ `cards=[]` のままにする
- `strategy/replan.py` L1528-L1530: 追加 boss wave も probe 以外では `_stock_cards(mem, None)` を使う
- `strategy/replan.py` L958-L1004: `_stock_cards` は実在庫から strong kit または上位在庫札を返し、全滅在庫では空のままにする

本件と同じ在庫・ch8占有状態を使った現行コードの静的再構成では、`_stock_cards(mem, None)` は `['ミックミー', 'ミックミー']` を返し、concrete boss order はすべて非空 kit になった。`ミックミー` は `STRONG_CARDS` に含まれ、在庫90があるため、本件では少なくとも計画層で空 kit にはならない。

したがって PR #5544 は本件をカバーする。PR #5544 が残した read-only probe の cardless 例外は、本件のように具体将軍（カシス）が割り当てられた committed sortie ではない。#5498 で提案された「plan-empty でも止める第2層guard」は PR #5544 には入っていないが、positive stock がある本経路では第1層で `want` が非空になるため、既存の empty-kit guard が保険として効く状態に戻る。

### #5510（ch3 タルタル、結論 (c)）との違い

#5510 は通常城メガパタへの attack で、唯一の正数在庫が boss-only のバルムンクだったため、通常出撃で使える在庫は実質ゼロだった。PR #5512 は監査側で通常出撃の適格在庫から `BOSS_ONLY_CARDS` を除外した修正であり、結論 `(c) 誤検知` はその範囲に限る。

本件は target がボスで、在庫にバルムンクは無く、通常札が多数ある。`month_audit.py` L2545-L2549 の boss-only filter も boss purpose または boss castle では適用されない。したがって #5510 / PR #5512 は本件を誤検知として消すものではなく、ボス出撃を引き続き検出する positive control 側に本件は該当する。

## 4. v1498 での再発か、現行 main での変更有無

- 本件自体が `hanjuku-chart-v1498-recall-battle-mode-lost` の観測であり、#5498/#5528 と同じ欠陥の v1498 での再発である。
- 同一 issue には 04:53Z に occurrence `8f137ca2edc8ab9cd9b3`（ピオーネ）の compact 再発コメントもある。compact コメントには step と created_at が無いため occurrence hash は独立再計算できないが、同 fingerprint・同様の在庫・実測空の再観測として記録されている。
- PR #5544 の merge commit は 2026-11-25 15:51:28 JST（06:51 UTC）で、本件の初回 04:49:48Z（13:49:48 JST）より後である。したがって本件は修正後バージョンでの再発ではない。
- `493a4666379a1a9ba0653a96fe59e950f71ee58c` は現行 `origin/main` の祖先である。現行 main は `hanjuku-chart-v1511-status-panel-close`。
- PR #5544 以降の `replan.py` 差分は第10話の進入路・ボス出撃元制約（commit `64d5112a1`）であり、boss wave の card 選択行は維持されている。`kit.py` / `flows/deploy.py` に PR #5544 以降の差分はない。
- v1498 から現行 main まで、`_sortie_cards_event`、`_build_sortie_cards_incident`、`_fingerprint`、`_incident_id` の該当関数は同一である。後続の `month_audit.py` 変更は `battle_card_at_hp1` 等の別 incident であり、`sortie_without_cards` の hash・検出機構を変えていない。
- ローカル確認: PR #5544 の回帰テスト2件（`test_boss_waves_carry_stock_cards_but_never_invent_empty_stock` / `test_every_known_spare_joins_the_boss_in_one_plan`）は現行 main で pass（2 passed）。

## 5. 判定不能・未検証の範囲

- 本番 VM の生 `hanjuku_decisions.jsonl` / frames / 実際のカード一覧画面は参照していない。issue embedded JSON の `carried_observed=true` と直前決定列が揃っているため、v1498 での発火機構と hash 照合には足りる。
- v1500 以降の実機 runtime でカード選択・使用が成功したかは未確認である。これは PR #5544 本文でも残件とされている運用 acceptance であり、本 read-only 検証の結論を変えるものではない。
- compact 再発コメント `8f137ca2edc8ab9cd9b3` は、step と created_at が無く occurrence raw を再構成できない。初回 occurrence の hash は完全一致した。

## 手順上の注記

- 本検証は read-only で実施し、コード変更・本番 SSH・ゲーム入力は行わなかった。
- `agent-guards/secret_scan.py` はこの環境に未配置。投稿前に `ops/vm_actions/secret_scan.py` と同等の秘密情報パターン検査を行い、本ファイルは clean を確認した。
