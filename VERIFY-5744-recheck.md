# VERIFY-5744-recheck — ch12 クーモン boss_phase_no_battle

- 検証日: 2026-11-27
- 検証種別: 独立した read-only 再検証（Web Devin）
- 対象 issue: docich#5744 `[半熟英雄] ボスフェーズでボス戦闘ゼロ: ch12 クーモン`
- 対象ラン: `g1190-ch1v9` / generation `1190` / 観測版 `hanjuku-chart-v1575-ch9-cliff-camp-recall`
- 検証基準: 現行 main `ba18d254d` / `hanjuku-chart-v1580-depleted-source`
- fingerprint: `b4e810c24409f219`
- incident/occurrence: `fe08858667e071c5add6`

## 結論

**(b) 設計どおり / 既存修正でカバー済み**。実装変更と修正 PR は不要と判定する。

一次検証の結論 (b) と一致する。一次検証の切り分け（主因は v1483 / PR #5451 の ch12 `boss_staging` gathering、副次経路は v1556 / PR #5689 の占有→dispatch 枯渇回復、#5703 と root cause 同型、#5743 と同じラン・ボス・gathering）も、公開 issue の証拠と現行 main のコード照合から支持できる。

## ハッシュ再計算

現行 `src/docich/hanjuku/month_audit.py` の式で独立に再計算した。

- fingerprint raw: `12|boss_phase_no_battle|boss_no_battle`
  - sha256 先頭16 hex: `b4e810c24409f219` — issue コメントヘッダと一致
- boss incident raw: `g1190-ch1v9|1190|boss|12|1795772541.455596`
  - sha256 先頭20 hex: `fe08858667e071c5add6` — issue の `occurrence` / embedded `incident_id` と一致

したがって issue 特定と監査レコードの同一性は確認できる。なおこの kind では `occurrence` 表示値は汎用 `_incident_id(runtime|generation|chapter|month)` ではなく、boss incident 用の `runtime|generation|boss|chapter|started_at` から計算された値である。

## 監査の誤検知ではないこと

(c) 誤検知とは判定しない。issue の embedded JSON は、ボスフェーズ開始 `1795772541.455596` / tick `226185` から終端 `1795773141.495661` / tick `228679` まで、600.040 秒・2495 観測・max gap 0.963 秒という密な観測で、以下を示す。

- `battle_count` 0、`boss_battles` 0
- `boss_entry` null
- `boss_hp_seen` null、`boss_hp_history_count` 0
- `sortie_history` にボス城向けの行がない
- `battle_start` は 8 件あるが、boss battle としては分類されていない

つまり「600 秒のボスフェーズ中にボス戦闘が観測されなかった」事象自体は発生している。監査の高 confidence 発火は契約どおりで、単なる監査の誤読ではない。

## 主因: v1483 の ch12 boss_staging gathering が継続していた

現行 main でも、ch12 の boss staging は v1483 / PR #5451 の設計を保っている。

- `src/docich/hanjuku_chart.py` は ch12 だけ staging を持ち、ボス注文 `12-B1` の出撃元 `ディランダ` を拠点として導出する。主人公 relay は `アルマムーン → ブラックランド → ヘリオポリス → ディランダ`。
- `src/docich/hanjuku/strategy/routes.py` の設計コメントは、主人公が先に relay を進み、他の将軍が追従し、その後にだけボス wave を拠点から出すと明示する。
- `staging_update()` は、拠点 `ディランダ` の将軍一覧へ主人公 `どうし` が実読みで載ったときだけ gathering → ready に進める。
- `staging_blocks()` は、gathering 中のボス出撃を全城的に止め、ready 後も拠点以外からのボス出撃を止める。
- 上限は `STAGING_MAX_TICKS = 6000`（1 hop の実測 560..5000 tick を考慮）と `STAGING_MAX_TOTAL = 24000` で、無進行または長期停滞では abandoned に戻る設計である。

issue #5744 の世界状態は、この gathering 継続と一致する。

- `12-A1` / `12-A2` / `12-A3` は done、`12-B1` は pending。
- 主人公 `どうし` は `ヘリオポリス → ディランダ` の relay 最終 hop を `en_route`（age 266 ticks）で進行中。
- `ディランダ` の在城は `グリッシーニ` / `カシュー` のみで、主人公は実読み roster にいない。
- すべての relay 城（アルマムーン、ブラックランド、ヘリオポリス、ディランダ）は保有済み。
- issue の decision counts に `boss_staging_ready` / `boss_staging_abandoned` はない。
- 行軍中のボス attack はなく、存在する launched/en-route は move または未確定 move である。

#5743（同じラン `g1190-ch1v9`、同じボス クーモン、同じ boss phase start `1795772541.455596`）の証拠は、この解釈を独立に補強する。#5743 の 24-6 レコードには `boss_staging_started: 1` があり、`boss_staging_ready` / `boss_staging_abandoned` はない。24-6 の範囲は first tick `225367` / last tick `226859` なので、staging は遅くともその月内に始まっている。#5744 の終端 tick は `228679` であり、仮に 24-6 先頭 tick `225367` に開始していたとしても経過は最大 3312 tick で、`STAGING_MAX_TICKS = 6000` も `STAGING_MAX_TOTAL = 24000` も超えない。さらに終端で主人公の relay 行軍が進行中であり、現行設計では relay 行軍の launch 自体が進捗として扱われる。したがって期限切れで abandoned になったとは読めず、gathering のままボス出撃が意図的に抑止されていたと判定できる。

このため #5448 / #5455 型の「本国発ボス行軍が吸収・野営化して失控する」実欠陥の再発ではない。v1483 は、その失控を避けるために意図的に拠点集結を優先する変更であり、本件はその変更が前提どおりにボス出撃を保留している状態である。

## 副次経路: v1556 / PR #5689 は発火しており、反証にならない

issue #5744 の counts には `order_dispatch_starved` 9 件と `post_month_boss_dispatch_floor` 2 件がある。現行コードでは、boss-only phase で pending 指示が dispatch へ届かない状態が上限を超えると `order_dispatch_starved` を記録し、月次確認がマップを占有していても `post_month_boss_dispatch_floor` を記録して出撃計画へ譲る。これは v1556 / PR #5689 の回復機構である。

本件ではこの機構が実際に発火している。したがって、月次占有が boss dispatch を永続的に塞いだ #5681 型の未修正欠陥とは判定しない。もっとも、今回開始・発射している指示は move / roster 系であってボス attack ではなく、ボス attack が出ない主因は dispatch 以前の `staging_blocks()` による意図的抑止である。v1556 は占有からの復帰をカバーするが、staging gathering 自体を短縮・解除する変更ではない、という一次検証の整理は正しい。

## #5702 型との切り分け

#5744 の末尾には `launch_interrupted_by_battle` が 1 件あるが、対象は `マフィン` の `アルマムーン → ブラックランド` move であって、ボス注文 `12-B1` ではない。しかもこの記録は incident 終端の約 2 秒前であり、再開可否をこの incident だけで評価できる窓ではない。一方、その時点で主人公はすでに `ヘリオポリス → ディランダ` の最終 relay を行軍中で、ボス不出撃の直接条件は拠点 roster 未確認である。

したがって #5702 で問題になった「探索や前景処理が発射確認を押し出す」経路は、本件の boss zero の主因ではない。観測版 v1575 には #5702 対応の v1570 と picker 再開の v1572 系が含まれているが、防衛戦闘そのものの割り込みは引き続き起こり得る。ここでの interruption は副次的な観測であって、結論を (a) へ変える根拠にはならない。

## 現行 main での確認

現行 main `ba18d254d` / v1580 で、staging 本体と dispatch 回復の回帰テストを実行した。

```text
PYTHONPATH=src:$HOME/py311shim python3 -m pytest -q \
  tests/test_hanjuku_ch12_boss_staging.py \
  tests/test_hanjuku_boss_dispatch_recovery.py
14 passed in 2.58s
```

観測版 v1575 から現行 v1580 までの差分には ch9 関連、築城探索の譲渡、失敗 move leg の再計画抑制、守備を残せない城の出撃元除外などがあるが、ch12 `boss_staging` の基本契約（gathering 中はボス出撃を止め、主人公の拠点 roster 実読みで ready へ進む）は変わっていない。

## 制限

公開 issue には runtime の `hanjuku_decisions.jsonl` 全行と生 frame が埋め込まれず、owner-only runtime artifact へはこの環境からアクセスしていない。この意味では raw frame 未取得は (d) 判定不能の要素である。ただし、本件では公開 embedded JSON、#5743 の同一ラン証拠、現行コード、ハッシュ再計算、回帰テストが同じ結論を支えるため、証拠不足で判定不能とはしなかった。

## 最終判定

- 事象の発生: あり（誤検知ではない）
- 実欠陥か: 現行証拠では否定できる
- 主因: ch12 boss_staging gathering による意図的なボス出撃抑止
- 副次経路: v1556 / PR #5689 の dispatch 回復が発火済みで、未カバーの実欠陥とは読めない
- 一次結論との関係: 一致
- 追加修正: 不要（修正 PR は作成しない）
