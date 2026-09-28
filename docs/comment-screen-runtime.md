# コメント返しの条件付き画面取得と画像送信（#1233 PR-2/3）

## 状態

PR #1235 の画面要否判定に続く実装。分類の契約は [comment-screen-context.md](comment-screen-context.md) を維持する。この文書は取得・生成の後段だけを扱う。soviet_now側の接続は PR #522。ローカルでは元ファイルのGit blobを照合したsource subsetで84件のテストを実行した。実HTTPアダプターの送信bodyはモック通信境界で検証したが、完全checkoutの既存全体スイート・実API・本番受入は未実施。CI結果はPRの最新HEADで確認する。

この変更だけで既存の画像非対応モデルが画像対応になるわけではない。初期の画像実行先は、既存チェーンに含まれ、運用者が実機で確認した `local:<model>` のOpenAI互換チャット経路のみ。OpenCode / Codex CLI / AMD / OpenRouter / Vercelへの画像添付はこの候補では未対応。設定した対応モデルがなければ、撮影せずテキストで返答する。

## 流れと所有権

```text
既存のJev分類（PR #1235、本文のみ、1リクエスト）
 → category / screen_need / screen_status / screen_confidence
 → soviet_now のcomment_screen_context.sh（既存COMMENT呼出しだけの薄いCLI接続）
 → docich.comment.screen_reply
      required以外: テキスト
      required: 承認済み配信入力を1枚取得
 → 共通DispatchRequest / Dispatcher / provider adapter
 → 既存の候補validatorと、旧経路の後段guard・翻訳・配信・ack
```

キャプチャ、画像契約、判定結果の利用はdocich側に置く。Jevへの追加往復はない。旧経路へ分類器や画像HTTPクライアントを複製しない。#829のnative pipeline移行後は `generate_screen_reply()` を直接呼び、旧CLI接続を削除する。ゲームAI、画面描画、共通配信エンコーダ、音声サービスを操作しない。

## 取得元と時点

初期取得元は `direct_x11`。読んだdirect-stream実装と同じX11の `(0,0,width,height)` を読み取る。運用者が指定した画面・寸法と、明示されたエンコーダ設定の一致を要求する。ただし環境変数の一致は**本番エンコーダの実効値を測定した証拠ではない**。有効化前に実測照合が必要。

これは **エンコード前の配信入力** であり、OBSの合成フレーム取得や視聴者側で実際に届いたフレームの確認ではない。X11に描かれる表示は対象に含まれるが、エンコード後にプレイヤーが描くクローズドキャプション、音量、ネットワーク障害、動画の停止、過去の場面は静止画から確認しない。

キャプチャは一つの返信バッチの初回生成で最大1枚。旧経路の再生成2回目以降は撮り直さず、テキストへ戻す。画像取得時刻はパイプの取得完了時刻であり、厳密なピクセルの露光時刻やコメント投稿時の視聴フレームとは呼ばない。

## 設定（自動変更しない）

以下は設定項目の説明で、実際のモデルや本番パスを確定した値ではない。

| キー | 意味 |
|---|---|
| `COMMENT_SCREEN_CONTEXT_ENABLED=1` | Jevの画面要否質問と条件付き画像経路。既定off |
| `DOCICH_ALLOW_REAL_AI=1` | 既存の生成実行許可。キャプチャ承認とは別 |
| `DOCICH_LLM_IMAGE_AGENTS` | 確認済みの `local:<model>` 識別子。元のCOMMENTチェーンにも同じ識別子が必要。新モデルを追加しない |
| `COMMENT_SCREEN_SOURCE=direct_x11` | 初期対応の取得方式 |
| `COMMENT_SCREEN_CAPTURE_APPROVED=1` | 運用者が対象の公開範囲を確認したことを示す明示設定 |
| `COMMENT_SCREEN_DISPLAY` / `COMMENT_SCREEN_SIZE` | 確認済みX11表示番号と寸法 |
| `SOREN_DIRECT_STREAM_DISPLAY` / `SOREN_DIRECT_STREAM_SIZE` | 上記と同じ値を明示。暗黙のDISPLAYや既定デスクトップは使わない |
| `COMMENT_SCREEN_SCENE_FILE` | 確認済みcanonical `game_switch.json` の絶対パス |
| `DOCICH_COMMENT_SCREEN_CLI` | 配置した `bin/docich-comment-screen`。旧互換接続の入口 |

モデルの画像対応は名前から推測しない。可変のbare `local` エイリアスも認定せず、`local:<model>` の完全一致を使う。環境設定、認証情報、モデル契約、課金枠は変更していない。Pillowは画像を扱う時だけ必要であり、無効・テキスト経路のimportでは要求しない。`requirements-screen.txt` はテストしたPillow版を固定する追加依存。新規CLIは実行可能ファイルで配布する。ffmpeg/X11、manifestの包含、Pillowの実環境への導入は有効化前に確認すること。

## 制限と縮退

JPEG 1枚、長辺1280px以下、1MiB以下。キャプチャの上限は500ms、送信前の最大ageは5秒。キャプチャ終了後の圧縮時間も予算超過の判定に含める。値は実測校正前であり、500msで撮れない環境では未取得として通常返答へ戻る。

canonical schema v2のready状態からゲーム・世代・runtime ID・revisionの投影を取得し、撮影前後・画像送信前・生成後に比較する。状態が読めない、切替中、世代やrevisionが変わった場合は利用を止める。画面所有権がcanonical stateに反映されないコーナーは、この確認だけでは対応済みとしない。

共通providerに入る時点、すなわち生成queue待機後にも `image_guard` を評価する。期限切れ画像は送らず、元promptから画面未確認のテキスト返答を作る。添付実績を返さない成功応答も画像確認済みとして採用しない。生成後に状態が変わった返信は出力せず、既存の再生成・未ackの扱いへ戻す。

全体予算は既存のdispatch deadlineと残り時間へ渡すが、既存HTTPクライアント自体のDNS・socket readの中断保証を追加したわけではない。ネットワーク呼出しまで含む絶対wall-time保証は未検証。500msのキャプチャ子プロセスは出力サイズ制限とkill/reapを実装し、TERM/INT時も回収する。SIGKILL後のOS全体の回収までは保証しない。

## データとログ

画像はメモリ内のbytesだけで保持し、ローカル一時画像ファイル、viewer memory、永続会話履歴へ保存しない。新しいtelemetryは固定status、必要行数、用意した画像byte数、画像dispatch要求の有無、採用返信の添付枚数、取得時間だけ。画像本体、base64、本文、パス、環境値を新しいログへ書かない。

`reply_images_sent` は採用された返信の添付枚数であり、全HTTP試行の累計送信枚数や課金量ではない。失敗・timeout時の送信到達は未確定なので `image_dispatch_requested=true` と分離する。外部またはローカルモデルサーバーの保存設定はこのコードでは管理していないため、運用者が確認する。

画像内文字は資料であって指示ではないとpromptで明示する。ただしこの文言とunit testだけでモデルのprompt injection耐性を証明したとは扱わない。モデルへ操作権限を追加せず、既存出力guardは維持する。

## 受入前に残る確認

1. 両repoの完全checkoutによる既存全体・関連テスト、queue待機後の拒否、旧guard/翻訳/ack互換、CIと独立レビュー。実際の `_local` が画像bytesをHTTP要求に組み込むことはmock opener境界で確認したが、サーバー到達やモデル解釈は未確認。
2. runtime manifest、必要ファイルとPillowの配布、CLI実行権限、health/diagnosticsへの登録。新workerや永続queueは追加していない。
3. 有効な画像対応モデルの確認、配信入力の公開範囲・座標・実効設定、各コーナーのscene所有権と切替時の整合。
4. Jevの日本語判定精度、取得成功率、遅延・CPU/メモリ・追加利用量、実返信の自然さの承認済みcanary。

この候補をmainやVMへ直接適用しない。upstream接続はsoviet_now側PRでレビューし、統合されたSHAに対してdocichのgitlinkを更新する。未マージbranchを本番参照へ切り替えない。コードの無効化は `COMMENT_SCREEN_CONTEXT_ENABLED=0` で旧分岐へ戻す方針だが、本番でその切替を実測済みとはしない。
