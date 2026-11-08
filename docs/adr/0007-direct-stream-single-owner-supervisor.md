# ADR 0007: 直接配信エンコーダーの単一所有者を docich にする

- 状態: **方針確定（2026-11-07）。段階 A〜D、D2、unit の配置ツールは実装・マージ済み（本番未適用）。段階 E（本番切替）は、実行の直前に時刻を示して明示承認を得てから行う**。この文書のマージは本番切替の承認ではない。
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
| A（完了） | 純関数・状態ファイル・ロック・監視判定の移管、Soren 側の委譲アダプター | なし（docich 未配備なら従来動作） | 各 CI |
| B（完了） | `docich.stream_supervisor` の読み取り専用 `config`/`command`/`status`（旧 CLI と 50 パターン出力一致） | なし | CI + ローカル差分 |
| C（完了、#3649〜#3653） | `run`/`stop`/`record`/`validate` の実装。固定ロック＋旧ロックを常に必須、`stop` は自分の runner のみ、TwiCa wrapper 差し替え。旧 `_run_ffmpeg` との差分テスト 5 シナリオ一致（ローカル） | なし（どの service からも未起動） | CI + 実プロセステスト + Linux コンテナ |
| D（完了、docich #3654、soviet_now #816） | `docich-stream.service`（配置のみ、`.env` は shell 方式のラッパー経由）、`DOCICH_STREAM_OWNER`（既定 `soren`）、Pulse 前提確認。`start_all.sh` と `direct_stream.py run` が `owner=docich` で何も起動しない | なし（owner 既定 `soren`＝現状維持） | CI + 静的検証 + Linux コンテナ |
| D2（完了、docich #3668/#3670/#3671/#3669、soviet_now #817） | 運用ツールの所有者認識（§8）。`stream_identity`（厳密な cmdline 判別）、docich 用一時停止マーカー、unit を `Restart=always`（sudo なしの停止/開始）、WebUI、`ops/vm_actions`、正午監査 | なし（旧 owner のままなら挙動不変を契約テストで固定） | CI + 実プロセステスト + Linux コンテナ |
| E | **本番切替**（本書 §5）。 | **あり（配信中断の可能性）** | D2 完了 + 利用者の明示承認 + 作業窓 |
| F | 旧 Soren 実装の撤去 | なし | E 後の実配信確認 |
| G | OBS 関連の撤去（`obs_*.sh`、backend `obs` 分岐、`obs.service` drop-in、`obs_capture_watchdog`） | なし（OBS が使われていないことを E で確認済みの場合） | 撤去前に参照箇所の網羅検索 |

A〜D は本番を触らずに進められる。E は §5 の承認なしに実行しない。

## 5. 本番切替（段階 E）の手順案とロールバック

**事前条件（全て満たすまで開始しない）**
1. 利用者の明示承認と作業窓。他の運用（コーナー切替、時事、デプロイ）が走っていない。
2. docich を VM に配備済みで、`stream_supervisor config|command|validate` が現行 `direct_stream.py` の出力と一致。
3. 固定ロックの親ディレクトリが `ubuntu` 所有・0700 で存在（作成は承認後の手順に含める）。
4. 稼働中 encoder の PID、実行 SHA、`status.json` の状態を記録（ロールバック比較用）。
5. 本番 `.env` が ffmpeg backend であること（OBS が現役でないこと）、および §7-4 の Pulse 読み取り確認が済んでいること。
6. 実行直前に時刻を利用者へ提示し、明示承認を得ていること。

**手順（supervisor を再起動しない）**

稼働中の `start_all.sh` supervisor は起動時の worker 一覧を持ち続け、`direct_stream` が止まると指数 backoff で再起動する（最大 10 回、300 秒で回数リセット）。しかし `tmp/state/<worker>.paused` があるとその worker は再起動されない（`_worker_paused`）。これを使えば、ゲーム・音声・オーバーレイを巻き込む `soren-runtime.service` の再起動をせずに切り替えられる。なお supervisor の「既存 worker 採用」は `lib/direct_stream.py run` に一致するプロセスだけを対象にするため、docich の runner（`docich.stream_supervisor ... run`）と混同されない。

1. `touch /home/ubuntu/soren/tmp/state/direct_stream.paused`（旧 worker の自動再起動を止める。配信はまだ続いている）。
2. 旧 runner を graceful に停止（`./direct_stream.sh stop`: q → SIGINT）。**ここから配信が途切れる**。ゲーム・音声・オーバーレイの PID が保持されることを確認。
3. 旧 encoder が居ないことを確認（`status.json` の `running:false`、`lib/direct_stream.py run` のプロセスなし、FFmpeg プロセスなし、旧 state_dir ロックが空）。
4. 固定ロックのディレクトリを整備（`ubuntu` 所有・0700、なければ作成）。`.env` に `DOCICH_STREAM_OWNER=docich` を設定（ラッパーと旧 sh は起動のたびに読み直す）。
5. `systemctl start docich-stream.service`。unit の起動順序は §7 の要求どおり。
6. 確認（実測できたものだけ報告する）: FFmpeg が 1 プロセスであること、固定ロックと旧ロックの保持者が `docich-stream.service` 配下であること、リレーの受信、実配信フレーム／四面 ready／crop、音声（Pulse 取り込み）、TwiCa、`status.json` の更新。

**マーカーの対応（D2 で確定）**: 旧 supervisor のマーカーは `tmp/state/direct_stream.paused`、docich は `tmp/state/docich_stream.paused`（別ファイル）。手順 1 の `direct_stream.paused` は旧 worker の再起動を止めるためのもので、切替後も supervisor が一度再起動されるまで残してよい。切替後の WebUI の停止/開始と正午監査は docich のマーカーだけを操作する。`docich-stream.service` は `Restart=always`（非所有者の終了コード 75 のみ再起動しない）なので、マーカーを作って runner を停止し、後でマーカーを外すだけで、sudo なしに停止/開始できる。

**unit の配置と事前条件の確認（実装済み、`bin/docich-stream-install`）**
- `--status`（読取専用）が段階 E の事前条件を一括で読む: unit の配置状態とハッシュ一致、`is-enabled`/`is-active`、wrapper の実行可否、固定ロックのディレクトリ、`.env` の backend／所有者フラグ／固定ロックのフラグ（**列挙値のみ。他の値は出さない**）、Pulse の応答と `soren_null` の有無、両マーカー。
- `--install --confirm-unit-placement` は unit を `root:root 0644` で置いて `daemon-reload` するだけ。`enable`/`start`/`restart`/`stop`/`disable` は一切しない（テストで固定）。既に別内容の unit があれば上書きしない。
- `--provision-lock-dir --confirm-lock-dir` は固定ロックの 0700 ディレクトリだけを作る（空のディレクトリは固定ロックを有効化しない）。
- どれも段階 E の「切替」ではない。配置と切替は別の承認として扱う。

**段階 E の事前条件（`--status` で読み取り確認する。VM 未確認）**: `env.backend=ffmpeg`、`pulse.server_answers=true` かつ `soren_null_sink=true`、`lock_dir=ready`、`unit.matches_source=true`（配置済み）、`unit.enabled/active` が意図どおり（配置直後は `disabled`/`inactive`）、旧 runner の状態と PID の記録。

**ロールバック**
- `systemctl stop docich-stream.service`（graceful。ここでも短い中断）→ `.env` を `DOCICH_STREAM_OWNER=soren` に戻す → `rm tmp/state/direct_stream.paused`。supervisor が backoff 後に旧 worker を再起動する。旧 state_dir ロックは docich 側も取っていたため二重起動は起こらない。
- 切替後に `soren-runtime.service` が再起動された場合、`owner=docich` なら supervisor は `direct_stream` を登録しない。このときも `paused` マーカーは supervisor が一度再起動されるまで残す（外すと旧 worker が起動のたびに何もせず終了する）。

## 6. 決定事項と残る確認事項

**利用者回答（2026-11-07）で決定**
- 切替時の配信の寸断は許容する。ただし §3 の制約があるため、寸断の長さは「数秒〜数十秒」を目標にし、段階 E は実行直前に時刻を示して承認を得る。
- unit 名は `docich-stream.service` とする（名称にこだわらない）。
- **OBS は廃止**する。対象は ffmpeg backend のみ。OBS 関連（`SOREN_STREAM_BACKEND=obs`、`obs_*.sh`、`obs.service` の drop-in、`obs_capture_watchdog`）は新設計に引き継がず、段階 G（§4）で撤去する。ただし本番の `.env` が `obs` になっていないことは段階 E の事前条件で確認する（**VM 未確認**）。
- PulseAudio の現状は利用者も把握していないため、§7 の事実（リポジトリ由来）を前提に、VM 上の状態は段階 E の事前条件として**読み取り専用で確認**する。

**残る確認事項**
- 作業窓の時間帯（段階 E の直前に提示して決める）。
- `xvfb.service` の実体（リポジトリに unit が無い。VM 未確認）。段階 E の事前条件で読み取り確認する。

## 7. PulseAudio の前提（リポジトリのコードで確認した事実）

- 正規の持ち主は `ubuntu` ユーザーマネージャの `pulseaudio.socket` / `pulseaudio.service`（`install_pulseaudio_user_service.sh`）。固定ソケットは `/run/user/1001/pulse/native`。
- `~/.config/pulse/client.conf` は `autospawn = no` と上記 `default-server` を固定（`deploy/pulse/client.conf`）。クライアントが私的な Pulse サーバーを起動しないための事故対策（docich#2008: 別 service の cgroup 内で自動起動した Pulse が、デプロイによる再起動で巻き添えになり、映像だけが続く無音配信になった）。
- 仮想シンクは `soren_null`、既定ソースは `soren_null.monitor`。現在は `soren-runtime.service` の `ExecStartPre=wait_soren_runtime_prereqs.sh` が、Pulse の到達性確認とシンク作成・既定設定を行う。
- `soren-runtime.service` は `PULSE_SERVER=unix:/run/user/1001/pulse/native`、`XDG_RUNTIME_DIR=/run/user/1001`、`Wants/After=user@1001.service` を持つ。

**`docich-stream.service` への要求**
1. 同じ環境変数と `After=user@1001.service` を持つ。
2. **Pulse を自分の cgroup で起動・所有しない**（`KillMode=control-group` で巻き添え死するため）。Pulse の起動・再起動を unit の動作に含めない。
3. 起動前に Pulse 到達性と `soren_null` シンクの存在を確認する `ExecStartPre` を docich 側に持つ（Soren のスクリプトに依存しない）。シンクが無い場合の作成は既存スクリプトと同じ動作にそろえ、Pulse が落ちている場合は **起動せず失敗する**（無音配信を始めない）。
4. 段階 E の事前条件に、`pactl info`、`soren_null` の存在、`pulseaudio.service` が user session 配下であることの読み取り確認を加える。

## 8. 切替前に直す運用ツール（段階 D2、リポジトリの棚卸し結果と対応状況）

稼働中の encoder を「`direct_stream.py` のプロセス」として識別する、または `lib/direct_stream.py stop` / `./direct_stream.sh run` で操作するコードがある。docich が所有者になると、これらは**誤判定（所有者なしに見える）・停止の拒否・何もしない再起動**を起こす。`status.json` は同じ形式・同じ場所なので、読み取り専用の利用者（`show_status.sh` の `direct_stream.sh status`、診断の status 読取）は変更不要。

| 利用者 | 依存している前提 | 対応（すべてマージ済み） |
| --- | --- | --- |
| soviet_now `workers/stream_noon_audit.sh` | 逸脱時に `lib/direct_stream.py stop` と `./direct_stream.sh run`（`STREAM_NOON_AUDIT_STOP_CMD`/`RUN_CMD`）。`start_all.sh` が常に登録する | `owner=docich` では停止を `python3 -m docich.stream_supervisor stop`、再起動を unit 経由に差し替える。旧コマンドを無改造で呼ぶと停止は拒否され、起動は何もせず終了する。**済（soviet_now #817）**: 所有者を張り直しの直前に `.env` から読み直し、docich 時は `stream_supervisor stop`・docich マーカー・自前起動なし |
| docich `ops/vm_actions/twica_common.py` | `_owned_process(stream.get('pid'), 'direct_stream.py')` と `direct_stream.sh stop` | docich entry も所有者と認識し、stop を owner で振り分ける。**済（#3671）**: `systemctl show`（読取専用）で `ActiveState`/`MainPID`/`Restart=always` を確認してから再起動 |
| docich `ops/vm_actions/probe_twica_runtime.py` | runner の引数に `direct_stream.py` を期待 | **済（#3671）**: 自己完結の厳密 matcher（`stream_identity` と全ケース同値をテストで固定） |
| docich `ops/vm_actions/collect_diagnostics.py`、`runtime_registry.py` | role `shared_stream` の期待プロセスが `lib/direct_stream.py` | 診断が「期待プロセス不在」と誤報しないよう、owner に応じた期待値にする。**済（#3671）**: `shared_stream` に docich の形を追加。ゲートウェイの検証対象一覧（`DIAGNOSTICS_FILES`）にも追加。登録表の `direct_stream` は必須扱いでないため不在でも警報しない |
| docich `src/docich/webui.py`、`soren_recovery_process.py`、`runtime_backend.py`、`webui_resources/index.html` | 停止手順 `python3 lib/direct_stream.py stop`、runner 検出に `direct_stream.py` | **済（#3670）**: 生きている runner の種類を設定より優先して owner を判定。owner 別マーカー、docich 時は `stream_supervisor stop`、runner 識別に docich を追加（未認識のまま「停止済み」と誤報する不具合を防止）|
| docich `ops/vm_actions/restart_direct_stream_overlay.sh`、`reload_shared_overlay.py`、`probe_twica_card_window.py` | encoder の識別・参照 | **済（#3671）**: `restart_direct_stream_overlay.sh` は pid ファイルが古い/無いとき `status.json` の pid を使い cmdline 厳密一致。`reload_shared_overlay.py`・`probe_twica_card_window.py` は status.json の読取のみで変更不要 |
| soviet_now `cutover_direct_stream.sh`、`direct_stream_recovery_test.sh`、`benchmark_direct_stream.sh`、`configure_linux_stream_profile.sh` | 旧 owner 専用の運用スクリプト | 切替後は使用しない（本番では実行しない）。未変更 |

各対応は、旧 owner のままなら挙動が変わらないことを契約テストで固定する（既定 `soren` で完全に従来どおり）。

## 9. 非目標

- 本書のマージだけでの本番変更、配備、service の enable/start、固定ロック有効化フラグの変更。
- encoder 以外（TTS・ラジオ・コメント・監視）の移管。別 ADR/Issue で扱う。
