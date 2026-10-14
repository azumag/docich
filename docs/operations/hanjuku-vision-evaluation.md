# 半熟英雄：終了済みランによる画像認識のオフライン評価

## 目的と境界

`ops/vm_actions/evaluate_hanjuku_vision.py` は、Issue #1339の取得経路（または既存の暗号化経路）で検証した
終了済みランのZIPを、**受信側だけで**評価する。既存 `verify_archive()` による
終了証拠、固定収集対象、ファイルSHA、RGB SHA、ZIP種別・サイズの検査を再使用する。
画像はメモリ内で読む。ZIPの展開、ゲーム起動、入力送信、VM接続、モデル呼出し、
本番deploy、gateway更新、自動方策変更は行わない。既存bot/parserも変更しない。

このツールは取得経路や権限の代替ではない。取得前のcanonical非稼働確認・共有lockは
#1302のexport側が担当する。GitHub run/attempt/SHA/actor/artifactの照合と
`receive_hanjuku_evidence.verify_archive()` による検証を先に完了する。
既存のCMS経路を使う場合だけ、同ツールによる認証付き復号も必要となる。
**ZIPのハッシュ整合性だけでは本番由来とは証明できない。**

取得workflowが使えない場合に、汎用exec/diagnosticsへfallbackしない。
Issueコメント経路は平文artifactを1日保持し、ユーザーの鍵管理を必要としない。

## 準備

レビュー済みcheckoutのルートで、新規のCLIプロセスとして実行する。
以下の `g12-12345678` は説明用であり、実際に要求・検証したruntime IDに置き換える。
`$PRIVATE` は本人専用の作業ディレクトリ（0700）。ユーザー判断により、ゲーム画像と
scrub済みゲームログの平文Actions artifactを許可する。秘密情報・ROM・セーブは含めず、
本文をActionsログへ流さない。評価の出典としてrun/attempt/SHAと画像ハッシュを残す。
出力は0600で新規作成し、既存ファイルを上書きしない。

```sh
# evidence.zip は固定Issue経路で取得し、manifestとハッシュを検証済みであること。
python3 ops/vm_actions/evaluate_hanjuku_vision.py \
  --archive "$PRIVATE/evidence.zip" --runtime-id g12-12345678 \
  --prepare holdout --output "$PRIVATE/labels.json"
```

テンプレートにはsnapshot名、PNGファイルSHA、RGB画素SHA、空の `expected` が入る。
現在のparserの予測を「正解」として埋めない。各画像を人が確認し、確定できた項目だけを
`expected` に記入する。同じRGBの画像が複数のリング枠にある場合は一枚にまとめる。
生成されたハッシュ・runtime ID・archive SHAは編集しない。

## 正解ラベル

ラベル全体の `split` は `calibration`（調整用）か `holdout`（最終確認用）。
同じランの似た連続画像を二群に分けず、ラン単位で役割を決める。
この第一段階は一つのZIPを評価する。複数ZIP間のsplit重複・類似画像の自動検出や
統計的な改善判定は未実装なので、別のラベルファイルへ同じランを再登録しない。

`expected` の例（値は説明用）：

```json
{
  "kind": "battle",
  "enemy_hp": 32,
  "ally_hp": 40,
  "hand": null
}
```

対応項目は `kind`, `selected`, `hand`, `cursor`, `marker`, `menu_cursor`,
`enemy`, `enemy_hp`, `ally`, `ally_hp`。座標は256×224のゲーム座標系で、
`hand` は `[x0,y0,x1,y1]`、`cursor` / `marker` は `[x,y]`、`menu_cursor` は
選択行の**Y座標**であり行番号ではない。HPは0も実値として扱う。

- **キー省略**：この項目は採点しない。人にも読めない項目は省略する。
- **`null`**：その特徴が画面に存在しないことを人が確認した場合のみ使う。
  「読めない」の意味に使わない。`kind` の `null` / `unknown` は正解として認めない。
- **`expected: {}`**：未ラベル画像。成功数・誤認識数・分母に入れない。

未知キー、bool/stringのHP、範囲外座標、不明字形、別runtime/別ZIPのラベル、
PNG SHAとRGB SHAの取り違え、古いリング枠のラベル、同じ画像の重複採点を拒否する。
全ラベルを先に検証し、一部のラベルが不正なまま採点を続けない。

## 実行と指標

```sh
python3 ops/vm_actions/evaluate_hanjuku_vision.py \
  --archive "$PRIVATE/evidence.zip" --runtime-id g12-12345678 \
  --labels "$PRIVATE/labels.json" --output "$PRIVATE/report-baseline.json"
```

本番botと同じ `classify(frame)` → `parse(frame, phase=...)` の順で認識する。
`decide()`、方策、キー送信は呼ばない。#1302が受理する正規化済みRGB画像だけが対象で、
生の299×224キャプチャや配信映像に対する正規化精度はこの評価の対象外。

各ラベル項目の結果を次に分け、項目別と合計の両方を出力する。

| 結果 | 意味 |
|---|---|
| `correct_present` | 表示されている正解値と一致 |
| `correct_absent` | 人が不在と確認した特徴を検出しなかった |
| `abstained` | 正解値は存在するが、parserが `None` / `unknown` で保留 |
| `wrong_value` | 値を出したが間違い。例：32を2と読む |
| `false_present` | 特徴が不在なのに値を出した |

`accuracy_when_decided = correct / (correct + wrong)`、
`decision_coverage = (correct + wrong) / labeled`、
`present_coverage = (expected_present - abstained) / expected_present`、
`wrong_rate = wrong / labeled`。分母が0の指標は `null` とし、100%にしない。
`correct_absent` が多いデータで成績を良く見せないため、存在する特徴だけのcoverageと
`false_present` も必ず確認する。合計は**画像単位ではなくラベル項目単位**。
全画像一致率や全プレイ中の認識率と混同しない。

未ラベルのみなら `status=no_ground_truth` の私的レポートを作り終了コード2を返す。
正常な採点／テンプレート作成は0、不正入力・環境不足は1。エラー時に本文やラベルを
stdout/stderrへ流さず、不完全な出力を残さない。

結果にはarchive SHA、ラベル内容SHA、認識器5ファイルのSHA、採点した項目と観測値を
記録する。認識器ファイルが評価中に変化した場合は拒否する。変更前後で**同じZIP・
同じ正解ラベル**を使い、別ファイルへ出力して比較する。調整後にholdoutを繰り返し
見てチューニングしない。このツールだけで改善の統計的有意性や本番採用を決めない。

## この段階で分からないこと

保存リングとローテーション済みログは全履歴ではない。`saved_frames` はZIP内の画像数で
あり、全観測数ではない。画像が残っていない停止局面の頻度や保留時間は推測しない。
切り札の実発動、ログの `action_plan` と `input_sent` の対応、検証側の拒否理由、
戦線位置の校正は別の評価対象で、この第一段階では採点しない。

実画像の独立した正解ラベルが揃うまでは、実運用の認識精度・勝率改善は未確認。
現段階で本番の閾値やカーソル検出を緩めず、まず誤読・未読の分布を測る。

## 回帰テスト

```sh
python3 -m unittest discover -s ops/vm_actions/tests -p 'test_hanjuku_vision_eval.py' -v
```

既存のVM operations CIがこのディレクトリを自動収集するため、workflow変更は不要。
合成画像の正常HP読み取りと遮蔽された32を2にしないこと、実archive verifierとの整合、
改ざん・未終了・リンク拒否、私的出力・非上書き・部分出力なし、ラベルと集計契約を確認する。
合成画像での成功を実際のゲーム映像に対する精度改善とは扱わない。
