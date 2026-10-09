# 現行 Jev の公開保存予測の再集計 (#1263 / PR #1974)

正本は `report.json` と `score_baseline.py`。公開 raw の bytes は変更せず、
case ID・件数・suite SHA-256 を検証して再集計する。provider/API/GPU/VM への
再計測は行っていない。accuracy_all は miss を分母に含み、available-only は
別 field とする。macro F1 も abstention を miss として扱う共通 grader
`docich.eval.graders.classifier.evaluate` を使用する。

## 公開106件での ungated 単一パス

| 保存予測 | accuracy_all | available-only | macro F1 | live-log accuracy_all | coverage | miss |
| --- | --- | --- | --- | --- | --- | --- |
| 現行 Jev ungated (106) | 71/106 = 0.6698 | 0.7100 | 0.7899 | 46/78 = 0.5897 | 0.9434 | 6 |

live-log available-only は 46/72 =
0.6389。旧主表の 0.7100 / 0.6389 は
available-only の値だった。miss込みの精度として扱わない。

## 同じ public 103ケースの比較

baseline は public106件。Llama は各 run で public106件の先頭3件
`jev-0001/0002/0003` を warmup とし、critical2件 `jev-0093/0094` を含む105件を採点。
主比較は critical を除き、warmup3件を除いた public103件で固定する。
対応ID・ID順序の SHA-256・各 run の採点値は report に保存している。

| 保存予測 | accuracy_all | available-only | macro F1 | live-log accuracy_all | coverage | miss |
| --- | --- | --- | --- | --- | --- | --- |
| 現行 Jev ungated (103) | 69/103 = 0.6699 | 0.7041 | 0.7922 | 44/75 = 0.5867 | 0.9515 | 5 |
| Llama (103, 各run同値) | 66/103 = 0.6408 | 0.6408 | 0.7389 | 44/75 = 0.5867 | 1.0000 | 0 |

Llama は保存された3 run 全てを別々に採点し、中央値と pooled 母数を報告する。
この raw では3 run が同値。Jev ungated − Llama の観測差は accuracy_all
+0.0291、macro F1 +0.0533、live-log 0.0000。missing prediction は分母から除かない。

| 同一103件の通知分類 | precision | recall | FP / ケース | FP/100 |
| --- | --- | --- | --- | --- |
| 現行 Jev ungated | 0.8421 | 0.9412 | 3 / 103 | 2.9126 |
| Llama 各run | 0.8824 | 0.8824 | 2 / 103 | 1.9417 |

Llama の pooled FP は **6/309 × 100 = 1.9417**（共通103件 × 3 run）。
元の105件 × 3 runでは **6/315 × 100 = 1.9048**。旧 0.6/100 は
1 runの分子を3 runの分母で割った誤値であり、参照値には使用しない。

## pipeline 証拠の限界

`pipeline_metrics.jsonl` は公開済み106イベントをそのまま保持するが、case ID を持たず、
ランダム `batch_id` からケースを復元できない。元 batches と latency/case対応記録も
公開されていない。順序から case ID を後付けしない。
`pipeline_reproduction.state = unavailable` として、新しい report には
pipeline/heuristic の case別スコアと直接比較 delta を生成しない。

旧 report と verify の pipeline 精度0.6698、共通103件0.6602、通知FP 1件という
数値は、公開case順との positional join による**対応関係未検証の歴史的集計**。
旧出力は Git の `d6bab623` に残るが、新しい集計の正本ではない。
検証可能な実測時 case対応の公開投影が得られるまで保留する。

## metadata と再現契約

- public suite: 106件、SHA-256 `d750a4361f7b7acffd46a192b1f7f5e873e8a2692cf45bbae78ce97240a46c37`。
- critical suite: 2件、SHA-256 `3dc691d401b1bbb7d2f6cdc23f686ee8c0521bb24b6fa0b250996b0c903a7d3a`。
- それぞれの label/intent分布の合計は対応する n と一致する。public の other は17件。
- 実装は上記期待SHAと照合する。hashを記録するだけではない。
- ungated は public IDと件数が完全一致し、保存goldも照合する。
- Llama は3 run全ての public+critical ID、件数、warmup集合、保存goldを照合する。
  欠落・重複・未知ID・run不一致は失敗する。
- 読取pathは内部で解決し、出力へは repo相対参照と内容hashを保存する。
  repo外の入力は内容識別子へ投影する。作業場所・時刻は serialize しない。

## オフライン再現

repository の任意の clean checkout から実行できる。キー・非公開 workspace・
batches は不要。入力は公開 suite2ファイル、ungated raw、pipeline raw、Llama raw。

```sh
python3 bench/results/2026-10-09_jev_baseline/score_baseline.py
python3 bench/results/2026-10-09_jev_baseline/verify/score_independent.py
python3 -m unittest discover -s bench/tests -p test_jev_baseline.py -v
```

`verify/score_independent.py` は入力identity検証と出力schemaを共有するが、
metric算術は共通graderを呼ばず別実装する。二つの checkout場所からのbyte一致、
両 scorerの一致、分母・metadata・不一致入力・retryを回帰テストする。

## 今後の収集コード（今回未実行）

`build_batches.py <public_cases.jsonl> <out_dir>` は case IDと相対batch名を manifestへ保存。
`run_baseline.sh` が正しい収集script名。実行には別途 provider呼出の許可が必要。
準備済みrepoで既存の安全な環境設定を使用し、`BATCH_DIR` と新しい `RUN_DIR` を指定する。
今回これらのlive収集scriptは実行していない。

runner は各 case呼出の専用metrics directoryを使い、イベント1件/row1件を検証して
case ID・pass・exit code・latencyを保存する。passファイルを残し、前passの成功ケースは
再試行対象に含めず、全caseの latest eventへmergeする。passのID・件数・順序が
期待したpending集合と異なる場合は失敗する。後続pass開始時も前passの記録を消さない。

合成classifierによる「成功1件＋retry1件」のshell実行で、pass1成功event/latency保持、
retryだけの更新、元pass保持を検証する。新収集の証拠を既存rawへ補完した扱いにはしない。

## 解釈と保留

観測値の差は統計的有意性・採否確定・本番受入を証明しない。live-log は同値。
pipeline は通知保護/cooldownを含み、Llama はungatedで条件が異なる。
実TTS発火は測定していないので、分類FPから実害の大小を結論しない。
19/150の別provider実験には公開rawがなく、このcheckoutから再現できない。
本番hash一致もここでは独立確認していない。#1933/#1934 の残条件は別件として保持する。

今回の作業はoffline修正のみ。VM・本番・配信バナー/音声は未操作。
handoff正本参照は未設定で未読/未更新。PRはReadyのまま維持し、mergeしない。
