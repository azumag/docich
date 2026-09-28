# 半熟英雄・終了済みランの非公開エクスポート

## 現在の範囲

終了済みランを固定ファイル一覧から読み取り、受信者だけが復号できる `.cms` にする
オペレーター用CLI。受信側CLIは復号の認証とZIPの検査が成功するまで平文ファイルを作らない。
Python標準ライブラリと `/usr/bin/openssl` のOpenSSL 3を使う。Linuxでの利用を前提とする。

**この変更はVM gateway / Actionsに新しい権限を付けない。ChatGPTからVMへの自動取得は未接続。**
実行には既存の承認済みVMセッションが必要で、暗号文の転送もオーナーが行う。
公開リポジトリで拒否される汎用 `exec` を迂回してはいけない。
本番配備、gatewayの拡張、実ログの取得・実機受入はこの変更の検証結果に含めない。

## 出力内容と検証

`ops/vm_actions/hanjuku_evidence.py` は `--runtime-id` に明示した1世代だけを対象にする。
`latest`、稼働中の世代、手動停止、終了証拠のないクラッシュ、lease不明の旧形式は拒否する。
正常な `game_over` と `screen_stalled` の証拠は `hanjuku_run.terminal` の条件に合わせて検証する。

収集するものは次に限定する。

- `hanjuku_run.json`、`hanjuku_bot.json`、調整チャート・調整要求・終了後レビューのJSON。
- `hanjuku_events`、`hanjuku_decisions`、`hanjuku_chart_history` の現行・previous JSONL。
- `hanjuku_frames/frame-*.png` と `decision-*.png`（合計240枚まで）。

ROM、セーブ、`.env`、実況文、音声ログ、共有の経験記憶、任意のパスは収集しない。
JSONのcredential/prompt関連キーは入れ子も伏せる。ただし自由文の完全な秘密検出器ではない。
**復号したZIPや画像をpublic Issue、PR、Actionsログ・artifactへ転載しない。**

`manifest.json` はruntime/game/generation/lease、保存されていたbot版、ファイルごとの
source SHA-256とexport SHA-256、欠落ファイル、不正JSONL行、欠落画像SHAを記録する。
不正JSONL行は元の行番号を保ったエラーレコードに置換し、破損したバイトを転載しない。
JSON状態ファイルの破損や、別世代・別leaseを示すレコードがある場合は全体を拒否する。

画像は実装が保存する256×224・RGB・filter 0のPNGだけを受け付ける。
`rgb_sha256` は画素バイト列のSHAであり、PNGファイル自体のSHAではない。
イベントの `frame_sha256` / `decision_frame_sha256` と画像をこのRGB SHAで照合する。
未校正の画像形式、CRC不一致、追加メタデータ、過大な展開結果は拒否する。

`action_plan` と `input_sent` は別レコードのまま残す。前者を実送信・成功と数えない。
ローテーション済みログと画像リングから全履歴の存在は証明できないため、
`history_complete` は常に `false`。欠落画像を推測で別の画像へ割り当てない。

## 読み取り境界

既存の `locks/game-switch.lock` に共有ロックを非ブロッキングで取り、競合時は即拒否する。
ロックは作成しない。canonicalの安定phaseとactive/retiringを読み、対象世代が稼働側にないことを確認する。
データのコピー前後にcanonical・終了証拠・各ファイルを照合する。
ディレクトリfdと `O_NOFOLLOW` を使い、symlink、hardlink、FIFO、ファイルの差し替えを拒否する。

スナップショットの目標上限は3秒、合計32 MiB。5 MiBを超えるログ、過大な状態JSON・画像、
過大なフレーム一覧も拒否する。ディスクの単一read自体にハードリアルタイムの期限を保証するものではない。
PNG解析、JSON加工、ZIP圧縮、暗号化は共有ロックを解放した後に行う。
既存のgame-switch、agent、コーナー、配信、音声の起動・停止・設定変更は行わない。

## 手動での受け渡し

### 1. 受信側で鍵を用意

受信するオーナーのLinux環境で実行する。秘密鍵をVM、GitHub、Actionsへ渡さない。
ChatGPT側で受信する場合も、秘密鍵は当該セッションの作業領域だけに保持する。

```sh
umask 077
mkdir -m 700 hanjuku-receive
openssl req -x509 -newkey rsa:3072 -nodes -days 7 \
  -subj '/CN=hanjuku-evidence-recipient' \
  -keyout hanjuku-receive/private.pem \
  -out hanjuku-receive/recipient.pem
```

VMへ渡すのは公開証明書 `recipient.pem` だけ。鍵紛失・セッション消失時は暗号文を復号できないため、
新しい受信鍵でエクスポートをやり直す。秘密鍵をPRやチャット本文へ貼り付けない。

### 2. 承認済みのVMセッションで書き出す

対象runtime IDは実際に終了したものを確認して指定する。以下のIDは形式例であり、存在を示すものではない。
`recipient.pem` はオーナーが確認した公開証明書を配置済みとする。

```sh
umask 077
mkdir -m 700 "$HOME/hanjuku-export"
python3 ops/vm_actions/hanjuku_evidence.py \
  --state-dir run \
  --runtime-id g7-1234abcd \
  --recipient "$HOME/hanjuku-export/recipient.pem" \
  --output "$HOME/hanjuku-export/evidence.cms"
```

出力先の親はオーナー所有・0700相当が必要。ファイルは0600で排他的に新規作成し、既存ファイルを上書きしない。
標準出力は固定の成功メッセージだけ。例外の生文字列、ファイル内容、秘密鍵は出さない。
暗号化はAES-256-GCM + RSA-OAEP（OAEP/MGF1ともSHA-256）。平文ZIPをVMのディスクへ保存しない。

### 3. 暗号文を受信側へ渡す

オーナーが `.cms` だけを既存の認証済み手段で取得する。
このセッションで公開鍵を生成した場合は、暗号文をこのセッションへ添付する。
**現時点でこの手順をActionsやChatGPTから自動実行できるとは扱わない。**

暗号化は受信者以外から内容を隠し、暗号文の変更を検出するが、送信元の署名ではない。
公開証明書を知る第三者も暗号文を作れるため、取得元・対象runtime・ファイルSHAを確認する。

### 4. 受信側で復号・検査

```sh
python3 ops/vm_actions/receive_hanjuku_evidence.py \
  --input hanjuku-receive/evidence.cms \
  --runtime-id g7-1234abcd \
  --key hanjuku-receive/private.pem \
  --recipient hanjuku-receive/recipient.pem \
  --output hanjuku-receive/verified.zip
```

復号の認証、ZIPのパス・件数・展開サイズ、要求したruntime ID、manifestと各ファイルのSHA、終了証拠を検証してから、
0600のZIPを新規作成する。自動展開やコード実行はしない。認証失敗時の部分平文も保存しない。
これ以降に `manifest.json` の欠落を確認し、判断→実入力→結果→画像を対応させて戦略を分析する。

## 自動取得を完成させるための未実装部分

通常のdiagnosticsやSoren91専用フィールドに暗号文を紛れ込ませず、別レビューで専用の固定operationを追加する。
必要な契約は以下。

1. オーナー限定workflow・production/main・実SHAの一致。入力はruntime IDと公開証明書のみ。
2. 固定collectorと固定state root。任意パス・任意コマンド・任意送信先・秘密鍵は受け取らない。
3. 平文をVMから出さず、サイズ上限付き暗号文のみを返す。通常診断の機密境界は不変。
4. artifactのオーナー限定起動を、artifactそのものの非公開性と混同しない。公開artifactでも中身は暗号文だけ。
5. 当該runのartifactをChatGPTが取得し、セッション内の鍵で復号・検査する実機受入。

受入ではruntime/state/ゲーム/配信の非変更、対象ID・leaseの一致、画像のRGB SHA対応、
鍵不一致・破損・欠落の扱いを個別に記録する。実ログ、画像、ROMはPRに添付しない。

## テスト

```sh
python3 -m unittest discover -s ops/vm_actions/tests -p 'test_hanjuku_evidence.py' -v
```

標準ライブラリのunittestなので既存 `VM operations CI` のdiscover対象になる。
合成ピクセル・合成ログを使用し、ゲーム起動やネットワーク通信は行わない。
ネイティブ終了判定との片方向互換（exportが元判定より緩くならないこと）と、
実OpenSSLによる暗号化→復号・改ざん拒否もテストする。
