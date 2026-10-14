# コメント返しの条件付き画面参照（#1233 / #829）

## 現在地：PR-1 は「見る必要性」の判定まで

Jevがコメント本文から画面要否を判断し、必要時だけ画像を返答用LLMへ渡す。
この最初の実装は分類器の拡張のみ。**スクリーンショット取得、画像送信、
返答用モデルの切替、実際のコメント返しへの画像接続はまだない。**
`screen_need=required` は画面を見た証拠ではない。本番の有効化設定も変更しない。

全体の実装計画・取得元・scene/freshness契約・受入条件は
[#1233](https://github.com/azumag/docich/issues/1233) を正本とする。
#829のdocich共通パイプラインへ統合し、soviet_nowに第二の共通基盤を作らない。

## opt-inと互換性

| 設定 | 既定 | 意味 |
|---|---|---|
| `COMMENT_SCREEN_CONTEXT_ENABLED` | `0` | `1`の時だけ画面要否の質問と出力を追加。`0`/`1`以外は設定不正 |
| `COMMENT_SCREEN_MIN_CONFIDENCE` | `0.70` | 画面判定専用の採用閾値。有限0..1。未校正の初期値 |
| `COMMENT_CLASSIFIER_BACKEND` | 従来どおり | `jev`以外なら追加APIを呼ばず画面判定は未取得 |

無効時は以前と同じcategory質問・分類配列。無効なら画面用閾値は読まず、
古い/無効な画面設定で通常のカテゴリ分類を止めない。
Jevのroute、model、credential、timeout、fallback、cooldownは既存設定をそのまま使う。
HTTP実装やAPI予算の追加はない。健康なprimaryでは1バッチ1リクエスト。
既存の明示route failoverだけは従来どおり使用する。

有効時のJSON行は既存の `index/user/comment/category/is_english` に次を加える。

```json
{
  "screen_need": "required",
  "screen_confidence": 0.9,
  "screen_status": "jev"
}
```

`screen_need` は `required / not_required / uncertain`。
confidenceはJevの値で、正解率や画像の有無を示さない。カテゴリのconfidenceとは独立。
例えばカテゴリが低confidenceでheuristicのままでも、画面判定は別に採用できる。
画面が低confidenceなら `screen_need=uncertain` / `screen_status=low_confidence`。

判定未取得時は `screen_confidence=null` / `screen_need=uncertain` とし、
`screen_status` に `missing_key / timeout / cooldown / input_limit / invalid_config /
backend_unavailable / classifier_error` 等の固定状態を返す。
ローカルで保護された通知は `not_required / null / local_notification` とし、Jevへ送らない。
欠損した画面フィールドも後段では「未確認」であり「参照済み」「不要と確定」ではない。

## Jevへの入力

既存の `c1..c8` と同じstateに、独立した `s1..s8` choice質問を加える。
stateは従来どおり `index/text` のみ。投稿者属性、persona、現在のゲーム状態、
画像、パス、過去の会話を渡さない。他のコメントを会話履歴にしない。
質問ID自体には頼らず、各instructionsで `comments[index=N]` を指定する。

画面内の物体・配置・字幕/overlayへの言及は `required` の例。
挨拶や一般ルール、音量、過去のプレイ、動き/凍結の証明は現在の静止画1枚で
根拠を補えない例。曖昧な参照先は創作せず `uncertain`。
これらはJevへ与えるrubricであり、ローカルのキーワード判定器ではない。

32KiBのリクエスト上限、本文4096 bytes、最大8候補は緩めない。
質問追加で収まる候補数が減ることはある。対象外の行も失わず `input_limit` とする。
一度失敗した画面判定だけを別APIで再要求しない。
レスポンスは共通validatorの完全一致契約に従い、画面回答が不正でもコメントを捨てず、
バッチ全体のカテゴリをheuristic・画面をuncertainへ戻す。

TypeSafeの[API](https://docs.typesafe.ai/api)と[Models](https://docs.typesafe.ai/models)
（2026-09-28確認）は複数choiceとtext-onlyを定義している。
実API canaryは未実施。日本語rubricの精度や追加質問の実遅延はmockテストでは証明しない。

## 診断・配布

既存の秘匿化イベントに、追加rubric version、閾値、行ごとの
`screen_need / screen_confidence / screen_status / screen_candidate` を追加する。
画像・base64・パス・本文・投稿者名・生例外は追加しない。
実装fingerprintに `screen.py` も含める。既存categoryのrubric versionは維持する。

追加worker、queue、credential、model、captureプロセスはない。
ゲーム用stateや最新画像を変更せず、共通transport・通知保護・分類出力以降の
speech/caption/outbound/ackのコードは変更しない。
本番registry/diagnosticsの画像取得・添付実績の追加は、実際のproviderを追加する
PR-2/PR-3で行う。このPRだけで既存診断が撮影や添付を確認できるとは扱わない。

## テストと残件

```sh
PYTHONPATH=src python3 -m pytest -q tests/test_comment_classifier_screen.py
```

同一リクエスト、旧request fingerprint、カテゴリと画面の独立性、候補の位置対応、
通知保護、サイズ上限、route failover、timeout/cooldown、設定不正、
共通transport/validator境界、秘匿化をsynthetic responseで検証する。
テストはJevを呼ばない。入力文に対応するmockの選択をモデル精度の測定と呼ばない。
既存Semantic decision contracts workflowにもこのテストを追加する。

次は許可された合成済み配信フレームの取得経路を確認し、
`ScreenContextProvider` と共通LLM画像添付契約を実装する。
その後にコメント生成へ接続する。画像非対応モデルへ画像を黙って落とさず、
scene/時刻/サイズ/予算違反では「画像未確認」のテキスト返答へ縮退させる。
#1233はこれらの接続と実機受入までopenのままにする。
