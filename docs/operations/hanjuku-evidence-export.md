# Issueコメントから終了済み半熟英雄の証拠を取得する

固定Issue [#1339](https://github.com/azumag/docich/issues/1339) にownerが次のいずれかを
**完全一致の1行**でコメントする。証明書・秘密鍵・ローカル復号は不要。

```text
/hanjuku-evidence list
/hanjuku-evidence export g7-1234abcd
```

上記IDは形式例。先に `list` の `candidates.json` artifactを取得し、候補から実在する
runtime IDを明示選択する。候補は正常な終了証拠がありactive/retiringではない世代の最大20件。
世代・終了理由・観測数・入力数のみを返す。`latest` や任意pathは受け付けない。

Actionsは同じIssueへ結果を返信する。返信には元コメントへのリンク、run/attempt、main SHA、
成功時のartifact IDとartifact ZIPのSHA-256だけを含める。画像・ログ・拒否時のstderrは出さない。
失敗時は成功artifactが無いことを返信する。botの返信はowner条件を満たさず再実行を起こさない。
返信先は固定Issue #1339のみで、jobの `issues: write` はこの応答に使う。

ChatGPT connectorでは `fetch_issue_comments` で元コメントに対応する返信を取得し、
`fetch_workflow_run_jobs` / `fetch_workflow_run_artifacts` で実行者・結果・attempt・SHA・
artifact IDを照合して `download_workflow_artifact` を呼ぶ。run一覧CLIは不要。
返却された一時download URLをローカルで読む場合は期限内にHTTP取得する。
本環境では標準ブラウザUser-Agentを指定すると取得でき、Python既定ヘッダーは403だった。
外側ZIPのSHA-256をGitHubのdigestと比較し、内側 `evidence.zip` を既存verifierで検証する。
資格情報や一時URLを文書・ログへ保存しない。

`Hanjuku evidence query` runのevent/actor/SHA/attemptとIssueコメント時刻を照合し、
GitHub connectorのworkflow artifacts一覧とdownloadを使う。export artifactの内側にある
`evidence.zip` はログ・最大240画像・manifestを含む。保持は1日で、ユーザー判断により
ゲーム画面・ゲームログの平文artifactを許可する。秘密鍵やSSH設定はartifactに含めない。

認可はowner ID・comment author ID・固定Issue・protected main・workflow refを固定する。
Actionsは実行前と公開直前に最新mainを照合し、gatewayはproduction configured SHAと
root-owned導入コードの一致を検証する。mainが進んだ場合は失敗し、新しいコメントで再実行する。
診断や汎用execへのfallbackはない。受信したZIPはmanifest・PNG/RGB SHA・runtime IDを
検証してから公開する。画像とログ本文はActionsログへ出さない。

`hanjuku_evidence_query docich production <SHA> list|export <runtime-id>` は固定の
read-only gateway operationである。通常deployに加えて、レビュー済みmainから既存
`install_vm_gateway.sh` を管理者経路で一度更新する必要がある。通常deploy成功だけで
root gateway更新済みとはみなさない。以後はIssueコメントだけで取得できる。

取得後は [画像認識評価](hanjuku-vision-evaluation.md) に従い、実画像を目視したラベルで
baselineを測る。parserの出力を正解ラベルにせず、ゲーム入力・配信・音声は変更しない。

以下は既存の暗号化exportの後方互換手順。Issueコメント経路では不要。

# 半熟英雄・終了済みランの暗号化エクスポート

## 実装範囲と導入状態

保存済みの1ランから、判断・実入力・結果・対応画像を収集する。CLIに加え、
owner-only Actions → 専用SSH operation → 暗号化artifact → 受信側検証の経路を実装した。
**コードと合成テストの完了は、本番への導入・実ログ取得の完了ではない。**
管理者によるgateway更新と、protected mainの正規デプロイ後に初めて利用できる。
実ログ・画像の取得、実機での性能・互換性検証、戦略変更は本PRでは実施していない。

Python標準ライブラリと `/usr/bin/openssl` のOpenSSL 3、Linuxを使用する。
受信者の秘密鍵は受信側だけに保持し、VM・GitHub・Actionsに送らない。

## 取得経路

1. 受信側（このChatGPTセッション等）で使い捨てRSA鍵と公開証明書を生成する。
2. `hanjuku-evidence-export.yml` をmainから明示dispatchする。入力は対象runtime ID、
   公開証明書PEM、`confirm=production`。`latest` や任意パスは指定できない。
3. workflowはowner名・不変ID、repository名・不変ID、triggering actor、protected main、
   workflow ref、イベント種別を照合する。実行SHAと現在のmainも取得前後に確認する。
4. pinned known_hostsでSSH接続し、固定 `hanjuku_evidence docich production <SHA>` だけを呼ぶ。
   stdinは `{schema: 1, runtime_id, recipient}` の固定JSON。余分なキー、秘密鍵は拒否する。
5. VMは既存のdeployment lockを共有・非ブロッキングで取得する。本番statusがconfigured、
   実SHA一致、root-ownedなインストール済み6ファイルが指定commitと一致する場合だけ収集する。
6. 固定 `/home/ubuntu/docich/run-soren-live` 配下の終了済みランを読み取り、
   平文ZIPをディスク保存せず暗号化する。stdoutは暗号文だけで、拒否時は固定エラーと空stdout。
7. ActionsはサイズとDER AuthEnvelopedDataの外側の形式を検査し、`evidence.cms` **だけ**を
   `hanjuku-evidence-<run_id>-<run_attempt>` artifactへ保存する。保存期間は1日。
8. 受信側は当該workflow run・attempt・head SHA・実行者・結論・artifact名/IDを照合して取得する。
   CMSの認証とZIP・対象runtime ID・ファイルSHAの検証後にだけ、0600のZIPを新規保存する。

artifact ZIPの取得にはGitHub Actionsのartifact download APIまたは接続済みGitHubの
`download_workflow_artifact`を使う。外側ZIPには `evidence.cms` 1件だけがあることを確認し、
リンクや重複、過大サイズを拒否して取り出す。内容の自動実行・任意パスへの展開は行わない。
鍵紛失やセッション消失時は新しい公開証明書で再取得する。秘密鍵をチャット本文に貼らない。

**owner限定の起動はartifact自体を非公開にはしない。公開artifactにも平文を置かない。**
暗号化は送信元署名ではない。公開証明書を知る第三者も暗号文を作れるので、
復号成功だけで本番由来とせず、取得元のGitHub run/artifactの照合を必須とする。
Actions側の外形検査はMAC検証ではなく、MAC検証は秘密鍵を持つ受信側で行う。

## gatewayの導入

`gateway_entry.py` は新operationだけを専用helperへ渡し、その他は既存 `gateway.main()` へ
そのまま渡す。既存 `gateway.py` のoperation一覧・exec制限・diagnostics予算は変更しない。
新operationが拒否された際のexec/diagnosticsへのフォールバックはない。

管理者がレビュー済みprotected mainから既存 `ops/vm_actions/install_vm_gateway.sh` を更新実行する。
既存のActions SSH公開鍵を用い、秘密鍵の再生成・公開は不要。installerは3つの新しいhelperを
root-ownedで配置し、既存のforced-commandをPython `-I`で起動するentryへ切り替える。
既存のroot-owned設定・他のauthorized_keysは従来どおり保持する。
この管理者操作を、通常deployや公開リポジトリの汎用execへ紛れ込ませない。

未更新gatewayは新operationを拒否する。helperの欠落、変更、指定commitとの不一致でも拒否する。
root-owned設定のproduction rootとoperations stateが既定の固定パスと違う場合も、
勝手に別のrootへ広げず拒否する。`VMOPS_TESTING`による本番チェックの無効化はない。
後でこれらのインストール済み6ファイルを変更した場合は、再び管理者の更新が必要になる。

新operationだけにCPU 60秒・アドレス空間512 MiB・実時間120秒・core dump無効を適用する。
Actions側も実時間と一時ファイル容量を制限する。ゲームや配信のプロセス制限は変更しない。

## 収集内容と検証

`--runtime-id` に明示した1世代だけを対象にする。稼働中の世代、手動停止、
終了証拠のないクラッシュ、lease不明の旧形式は拒否する。
`game_over` と `screen_stalled` は `hanjuku_run.terminal` より緩くならない条件で検証する。

対象は `hanjuku_run.json`、`hanjuku_bot.json`、調整チャート・調整要求・終了後レビューのJSON、
`hanjuku_events` / `hanjuku_decisions` / `hanjuku_chart_history` の現行・previous JSONL、
`hanjuku_frames/frame-*.png` / `decision-*.png`（合計240枚まで）に限定する。
ROM・セーブ・`.env`・実況文・音声ログ・共有の経験記憶・任意パスは含めない。
JSONのcredential/prompt関連キーは入れ子も伏せるが、自由文の完全な秘密検出器ではない。
ゲーム画面・scrub済みゲームログの平文Actions artifactはownerの明示判断で許可されている。
Actionsログへ本文を出さず、認証情報・ROM・セーブは引き続き収集しない。

manifestにはruntime/game/generation/lease、bot版、source/exportファイルSHA-256、
欠落ファイル、不正JSONL行、欠落画像SHAを記録する。不正JSONLは元の行番号を保つ
エラーレコードへ置換する。状態JSONの破損や別世代・別leaseのレコードは全体を拒否する。

画像は256×224・RGB・filter 0のPNGに限定し、CRC・展開サイズ・追加メタデータを検査する。
`rgb_sha256` は画素バイト列のSHAで、PNGファイルSHAとは別。
イベントの `frame_sha256` / `decision_frame_sha256` とこのRGB SHAで対応付ける。
`action_plan` は実送信ではなく、`input_sent`とは別のまま残す。
画像リングとローテーションログは全履歴ではないため、`history_complete` は常にfalse。
欠落画像を推測で別画像へ割り当てない。

## 読み取り境界

既存 `locks/game-switch.lock` を共有・非ブロッキングで取得し、競合時は即拒否する。
ロックは作らず、canonicalの安定phaseとactive/retiringを確認する。
コピー前後のcanonical・終了証拠・各ファイルを照合し、差し替え・書込み中のsnapshotを拒否する。
dirfd/O_NOFOLLOWで全パスをたどり、symlink・hardlink・FIFOを拒否する。
目標3秒、合計32 MiB、ログ単体5 MiB等の上限を適用する。
単一のディスクreadそのものにハードリアルタイム期限を保証するものではない。
PNG解析・JSON加工・圧縮・暗号化はgame-switch lockの解放後に行う。
ゲーム入力、起動停止、配信・音声の変更、runtime状態書換えは行わない。

## 受信側の準備と復号

```sh
umask 077
mkdir -m 700 hanjuku-receive
openssl req -x509 -newkey rsa:3072 -nodes -days 7 \
  -subj '/CN=hanjuku-evidence-recipient' \
  -keyout hanjuku-receive/private.pem \
  -out hanjuku-receive/recipient.pem
```

dispatchに渡すのは `recipient.pem` の内容だけ。IDは実際の終了済み記録を確認して指定する。
受信した暗号文を外側artifact ZIPから検査して取り出した後、次を実行する。
以下のruntime IDは形式例であり、実データの存在を意味しない。

```sh
python3 ops/vm_actions/receive_hanjuku_evidence.py \
  --input hanjuku-receive/evidence.cms --runtime-id g7-1234abcd \
  --key hanjuku-receive/private.pem --recipient hanjuku-receive/recipient.pem \
  --output hanjuku-receive/verified.zip
```

AES-256-GCM + RSA-OAEP（OAEP/MGF1ともSHA-256）で暗号化する。
受信側は認証なしCMS形式を復号前に拒否する。OpenSSLが認証エラー前に出す部分平文も保存しない。
認証・ZIPのパス/件数/展開サイズ/種別・manifest/各SHA・要求IDの検証後だけ新規保存し、
既存ファイルの上書きや自動展開はしない。CLI単体の手動exportも引き続き使用できる。

## テストと実機受入

```sh
python3 -m unittest discover -s ops/vm_actions/tests -p 'test_hanjuku_evidence*.py' -v
```

当初未登録だった25テストを登録済み。専用経路を含め56件の合成テストを実装した。
既存 `VM operations CI` のunittest discover対象となる。
合成ログ・画像、ローカル一時Git repository、使い捨て鍵による実OpenSSLを使用する。
SSH・ゲーム起動・本番接続は行わない。native終了判定との片方向互換はCI上の実ファイルで照合する。

実機受入では、終了済みの明示IDについてworkflowからartifactを取得し、run/attempt/SHAと
鍵・ID・lease・画像RGB SHA・欠落を確認する。runtimeファイルの非変更と共有プロセスの継続も
確認し、成功/未確認を区別して記録する。合成テスト成功だけで実ログ互換性を保証しない。
