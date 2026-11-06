# ADR 0007: 直接配信エンコーダーの単一所有者を docich にする

- 状態: **提案（実装・本番切替は未承認）**。Issue #3553「Stream & service」の設計。この文書のマージは実装や本番切替の承認ではない。
- 関連: ADR 0006（リポジトリ境界）、docich #3621 / #3625 / #3633 / #3635 / #3638 / #3640 / #3642
- 方針: 二重 encoder を絶対に起こさない。稼働中の配信を止める手順は、利用者の明示承認と作業窓なしに実行しない。

## 1. 現行構成（リポジトリのコードで確認した事実）

```
systemd: soren-runtime.service   (User=ubuntu, WorkingDirectory=/home/ubuntu/soren,
         │                        Requires=xvfb.service, Wants=soren-rtmp-relay.service ほか,
         │                        Restart=always, KillMode=control-group)
         └─ start_all.sh --supervisor
              └─ worker "direct_stream"   ← SOREN_STREAM_BACKEND=ffmpeg のときだけ登録
                   └─ ./direct_stream.sh run
                        └─ lib/direct_stream.py run
                             └─ FFmpeg  (TwiCa 有効時は bin/docich-twica-ffmpeg 経由)
                                  └─ rtmp://127.0.0.1/... → soren-rtmp-relay.service (nginx)
                                                              └─ 外部配信先（鍵はここだけが保持）
```

- encoder の起動・再起動・停止の所有者は **`start_all.sh` の worker supervisor**（= `soren-runtime.service`）。`direct_stream.py` は単独で `run`/`stop`/`status` もできる。
- 旧排他: `state_dir/direct_stream.lock`（必須）。共通固定ロック `/home/ubuntu/docich/run-soren-live/locks/direct_stream_encoder.lock` は `DOCICH_STREAM_CANONICAL_LOCK_ENABLED=1` のときだけ追加で取る（既定 0）。
- 共通オーバーレイは別 service（`soren-shared-overlay.service`、独自 Xvfb `:98`）。配信 Xvfb `:99` は `xvfb.service` 所有。
- 配信先の鍵はリレー（`soren-rtmp-relay.service`、ユーザー `soren-relay`）だけが持ち、encoder は loopback URL しか受けない。
- docich に移管済み（純関数・状態ファイル・ロック）: 出力 URL ガード、単一 encoder ロック、固定共通ロック、FFmpeg 引数組立／進捗解析／終了分類、再接続・音声監視の設定と再起動判定、Pulse 一覧分類、状態ファイル操作。Soren は全て「従来版を先に計算し一致時のみ採用」または「実行後に検証、失敗なら従来版」で委譲している。

**VM 未確認**（このリポジトリからは分からず、本文書の前提にしない）: 実際の backend（obs / ffmpeg）、`xvfb.service` の中身、稼働中 encoder の PID／実行 SHA、固定ロックを保持している process の有無、`/home/ubuntu/docich/run-soren-live/locks` の存在と権限。

## 2. 目標構成

```
systemd: docich-stream.service   ← 新設（名称は仮）。encoder の唯一の所有者
         Requires=xvfb.service  Wants=soren-rtmp-relay.service user@1001.service
         └─ python -m docich.stream_supervisor run
              ├─ 固定共通ロック + 旧 state_dir ロックを encoder 終了まで保持
              ├─ FFmpeg 起動／graceful 停止／再接続／音声・Twitch 監視
              └─ status.json（Soren や WebUI が読む。`output_url` を含めない）

systemd: soren-runtime.service   ← worker "direct_stream" を登録しない（ゲーム固有のみ）
```

- Soren はゲーム固有処理だけを持つ。encoder を起動・再起動・停止しない。
- `lib/direct_stream.py` は互換期間のみ薄い委譲アダプターとして残し、docich が不正・未配備なら **起動しない（fail-closed）**。二重 encoder を許すフォールバックは作らない。

## 3. 所有権の切替規則（二重起動を作らない仕組み）

1. **所有者フラグ** `DOCICH_STREAM_OWNER=soren|docich`（既定 `soren`）。`start_all.sh` と `docich-stream.service` の双方が先頭で確認し、自分が所有者でなければ起動せず exit 0 で終わる。
2. **ロックが最終の安全弁**。所有者フラグが誤っていても、固定共通ロックを取れない側は FFmpeg を起動しない。
3. **重要な制約**: `flock` は取得した process が生きている間だけ有効なので、**既に稼働中の encoder は固定ロックを保持していない**。ロックを有効化しても稼働中の encoder は守られない。したがって、固定ロックへの初回参加と所有権移転は、どちらも **稼働中 encoder の再起動を必ず伴う**（数秒〜数十秒の配信中断が起こりうる）。無停止での引き継ぎ手段は現行の設計には存在しない。これは承認事項。
4. 旧ロックは切替完了後も一定期間残し、docich 側でも必ず取る（旧実装へのロールバック時に二重起動しないため）。

## 4. 段階（各段階が独立 PR・独立ゲート）

| 段階 | 内容 | 本番影響 | ゲート |
| --- | --- | --- | --- |
| A（実施済み） | 純関数・状態ファイル・ロック・監視判定の移管、Soren 側の委譲アダプター | なし（docich 未配備なら従来動作） | 各 CI |
| B | `docich.stream_supervisor` に **読み取り専用**サブコマンド（`config`/`command`/`validate`/`status`）を追加。`run` は未実装で終了コード 2 | なし | CI + 引数 golden（Soren の `command` 出力と一致） |
| C | `run`/`stop` を実装。process 操作は注入可能にし、フェイク process で停止順序（q → SIGINT → SIGKILL）・再接続・ロック保持を別プロセス競合込みでテスト。**まだどの service からも起動しない** | なし | CI + 異常系テスト |
| D | `docich-stream.service` の unit と `DOCICH_STREAM_OWNER` 判定を追加。unit は配置のみ・`enable`/`start` しない。`start_all.sh` は `owner=docich` のとき worker を登録しない | なし（owner 既定 `soren`） | CI + unit の静的検証 |
| E | **本番切替**（本書 §5）。 | **あり（配信中断の可能性）** | 利用者の明示承認 + 作業窓 |
| F | 旧 Soren 実装の撤去 | なし | E 後の実配信確認 |

A〜D は本番を触らずに進められる。E は §5 の承認なしに実行しない。

## 5. 本番切替（段階 E）の手順案とロールバック

**事前条件（全て満たすまで開始しない）**
1. 利用者の明示承認と作業窓。他の運用（コーナー切替、時事、デプロイ）が走っていない。
2. docich を VM に配備済みで、`stream_supervisor config|command|validate` が現行 `direct_stream.py` の出力と一致。
3. 固定ロックの親ディレクトリが `ubuntu` 所有・0700 で存在（作成は承認後の手順に含める）。
4. 稼働中 encoder の PID、実行 SHA、`status.json` の状態を記録（ロールバック比較用）。

**手順**
1. `DOCICH_STREAM_OWNER=docich` を設定（`.env`）。まだ反映しない。
2. `soren-runtime.service` の encoder worker を停止（graceful: q → SIGINT）。ゲーム・音声・共通オーバーレイの PID は保持されることを確認。
3. `docich-stream.service` を start。固定ロック取得 → encoder 起動。
4. 確認: 新 encoder が唯一であること（FFmpeg プロセス数 1、固定ロック保持者が `docich-stream.service` 配下）、リレーの受信、実配信フレーム／四面 ready／crop／音声、TwiCa、`status.json`。いずれかが未確認なら完了と言わない。

**ロールバック**
- `DOCICH_STREAM_OWNER=soren` に戻し、`docich-stream.service` を stop、`soren-runtime.service` の worker を再開。旧 state_dir ロックは docich 側も取っていたため二重起動は起こらない。
- ロールバックでも 1 回の再起動（短い中断）が発生する。

## 6. 残る未決事項（承認時に決める）

- unit 名と配置（`/etc/systemd/system/docich-stream.service` の想定）。
- 作業窓の時間帯と、中断を許容する長さ。
- OBS backend が現役かどうか（現役なら本設計は ffmpeg backend のみが対象）。
- `xvfb.service` と PulseAudio を docich 側の unit 依存へどう明記するか。

## 7. 非目標

- 本書のマージだけでの本番変更、配備、service の enable/start、固定ロック有効化フラグの変更。
- encoder 以外（TTS・ラジオ・コメント・監視）の移管。別 ADR/Issue で扱う。
