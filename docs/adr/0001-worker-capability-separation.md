# ADR 0001: soviet_now worker のcapability分離 (Unix identity / systemd hardening / credential broker)

- Status: Proposed (design-only。本ADRは production unit を切替えない)
- Issue: [azumag/docich#40](https://github.com/azumag/docich/issues/40)
- 関連manifest: [`0001-worker-capability-manifest.json`](./0001-worker-capability-manifest.json)
- 関連doctor: [`scripts/worker_capability_doctor.py`](../../scripts/worker_capability_doctor.py)

## 0. 前提と調査方法

本ADRは `azumag/soviet_now` (docichリポジトリの git submodule `games/soviet_now`、
production は Oracle VM `/home/ubuntu/soren` 上で稼働) を対象に、以下の方法で調査した。

- 対象: `/Users/azumag/work/docich/games/soviet_now` の実チェックアウトを **read-only** で
  grep・ファイル読み込み。書き込み・git操作は一切していない。
- `.env` はローカルでは 0 byte (空) であり、値は一切読んでいない。値の収集はこのADR・manifestの
  どこにも行っていない。
- production VM 自体にはこのセッションから到達していない。VM上の実際の `.env` 値・有効な
  機能フラグ (`YOUTUBE_CHAT_ENABLED` 等) は確認できておらず、以下は全て
  「config.sh の既定値」または「`deploy/soren-runtime/soren-runtime.service` の `Environment=`
  行に明示された値」に基づく。両者が異なる場合は本ADRの記述は誤りうる。**この差分は
  doctor スクリプト (§8) が本番VM上で実行された際に検出できるよう設計している。**
- 未調査・未確認の箇所は都度「未確認」「未調査」と明記した。詳細一覧は
  manifest の `unverified` 配列、および本文中の該当箇所を参照。

## 1. 現状 (As-Is)

実測(コード読解)で確認できた現状は以下の通り。

1. **Unix identity は単一。** production の `soren-runtime.service` は
   `User=ubuntu Group=ubuntu` (uid 1001) のみで、`start_all.sh` が起動する11個の常設
   worker + 条件付き worker (soviet_watchdog / obs_capture_watchdog / overlay watchers 等)
   がすべて同一ユーザーの子プロセスとして動く
   (`games/soviet_now/deploy/soren-runtime/soren-runtime.service:9-24`)。
2. **credentialは全worker環境に無条件で渡る。** `start_all.sh` は
   `set -a; . "$ENV_FILE"; set +a` (`games/soviet_now/start_all.sh:22-26`) で `.env` 全体を
   supervisorプロセスの環境変数として export し、各workerは
   `$cmd >>"$log_file" 2>&1 &` という同一シェルのbackground job
   (`games/soviet_now/start_all.sh:605`) として起動される。つまり `.env` に列挙された
   **全てのcredential変数が、現状は全workerプロセスの環境に無条件で渡っている**。多くの
   workerは個別にも `. ./.env` を再読込する (例: `games/soviet_now/workers/chat_worker.sh:107`)。
3. **filesystem分離もない。** 全workerが同一作業ディレクトリ (`$ELOOP_LIB_DIR` =
   soviet_nowチェックアウト直下) で動き、Unixパーミッションによるworker間のファイル
   分離は存在しない。唯一の例外は strategy runner
   (`games/soviet_now/strategy/sandbox.sh`) で、`create_sandbox` が
   `tmp/.soren_sandbox_XXXXXX` 配下に隔離コピー+独立git repoを作り、
   `../` を含むパスやsymlink/hardlinkを拒否する (`strategy/sandbox.sh:333-337, 410-424`)。
   ただしこれはgit/パス単位の論理防御であり、OSレベルのユーザー分離・ファイル
   パーミッションによる強制隔離ではない。
4. **リソース制限は未設定。** `soren-runtime.service` に `CPUQuota` / `MemoryMax` 等は
   一切なく、`games/soviet_now` 配下のシェルスクリプトからも `nice`/`ionice`/`cpulimit`/
   `systemd-run` の使用は見つからなかった。
5. **既に良いパターンが1つ本番にある。** `soren-rtmp-relay.service` /
   `soren-rtmps-bridge.service` (`games/soviet_now/deploy/soren-rtmp/*.service`) は
   専用ユーザー `soren-relay` + `NoNewPrivileges=true` / `ProtectSystem=strict` /
   `ProtectHome=true` / `PrivateTmp=true` / `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`
   / `CapabilityBoundingSet=` を既に適用済みで、配信先ストリームキーはリポジトリ外の
   `/etc/soren-rtmp/push.conf` にのみ置かれる (`install_rtmps_bridge.sh:6-8,40`
   のコメントで「この script はキーを読まない」と明記)。**本ADRが目指す分離の実例が
   既に本番に存在する。** 移行はこのパターンを他workerへ広げる作業と位置づけられる。

worker別の詳細 (read/write paths, network endpoints, credential使用箇所, evidence行番号)
は manifest (`0001-worker-capability-manifest.json`) を参照。ここでは設計判断に必要な
要約のみ記す。

### 1.1 worker inventory 要約

| worker | 役割 | 現在の資格情報 | 備考 |
|---|---|---|---|
| soren_loop | メインAIループ | (間接) ANTHROPIC_API_KEY | game_state.json読み手, データ書き込み多数 |
| improve_daemon | 戦略改善オーケストレータ | MINIMAX_API_KEY, (間接)ANTHROPIC | strategy_runnerを呼ぶ主体 (呼び出しコマンド自体は未確認) |
| strategy_runner (sandbox) | 戦略候補の隔離検証 | MINIMAX_API_KEY | improve_daemon配下、git隔離のみでOS隔離なし |
| chat_worker | Twitchチャット | TWITCH_BOT_TOKEN, TWITCH_CLIENT_ID | kick/youtubeとロック共有 |
| youtube_worker | YouTubeチャット | YOUTUBE_API_KEY, YOUTUBE_OAUTH_* | 既定無効 (config既定、VM実値未確認) |
| kick_worker | Kickチャット | なし (公開Pusher購読) | 既定無効 (同上) |
| audio_worker | 音声再生キュー | なし直接 (VOICEVOXはLAN内認証なし) | Google TTS利用時はgcloud ADC (呼び出し関係未確認) |
| deadline_monitor | デッドライン監視 | なし | |
| radio_worker | ラジオ台本生成 | (間接)ANTHROPIC_API_KEY | 外部ニュースAPI複数 |
| prediction_worker | Twitch Predictions | TWITCH_PREDICTIONS_TOKEN, TWITCH_BOT_GQL_TOKEN | |
| poll_worker | Twitch Polls | TWITCH_POLLS_TOKEN (predictionsへ暗黙fallback) | |
| goal_worker | Twitch Goals | TWITCH_GOALS_TOKEN (predictionsへ暗黙fallback) | |
| obs_capture_watchdog | OBSキャプチャ自己修復 | OBS_WEBSOCKET_PASSWORD | STREAM_BACKEND=obs (既定) 時のみ |
| soviet_watchdog | browserクラッシュ復旧 | なし | production unitで明示的に有効 |
| browser (soviet_local.mjs) | ゲーム描画・配信ソース | OBS_WEBSOCKET_PASSWORD | Playwright/Chromium, port 8080/9222 |
| codex_bug_dispatcher (coding agent) | バグ報告の自動修正 | codex/claude CLI認証 (未調査) | リポジトリ全体書き込みが業務要件 |

補助worker (overlay watcher, stream_noon_audit, youtube_broadcast_guard, direct_stream) は
manifestの `auxiliary_workers` を参照。

## 2. Threat Model

### 2.1 攻撃/事故シナリオと現状の脆弱性

| # | シナリオ | 現状 | 影響範囲 (blast radius) |
|---|---|---|---|
| T1 | ある worker (例: radio_worker が呼ぶニュースRSS取得ロジック) に脆弱性があり任意コード実行を許す | 同一ユーザー・同一環境変数のため、**全credential (Twitch/YouTube/OBS/LLM API key)** が奪われる | 最大。全SNS/配信制御が乗っ取られる |
| T2 | strategy改善ループが実行するAI生成コード (strategy.py候補) が悪意/バグで `../` 以外の経路 (シンボリックリンク作成前のtruncate競合、あるいはsandbox外の共有tmpパスへの書き込み) からホストへ影響する | git-repo境界とパス文字列検証のみ。OSレベルのアクセス制御は無い | strategy_runner + improve_daemonが書ける全パス (現状はworker全体と同じ) |
| T3 | codex_bug_dispatcher が `--permission-mode=bypassPermissions` でclaude/codexを起動し、AIエージェントが意図せず広範な変更 (例: 他workerのcredentialを含むファイルの書き換えやgit push) を行う | リポジトリ全体の読み書きが業務要件なので原理的に防げないが、**credentialにアクセスできる必要はない** のに現状は他workerと同じ環境でcredentialに触れられる | リポジトリ全体 + 全credential |
| T4 | 1つのworkerのクラッシュ・無限ループがホストリソースを食い潰す (例: browserのChromiumプロセスや improve_daemon のLLM呼び出しループ) | CPUQuota/MemoryMax未設定のため他workerを巻き込む | 配信全体 (音声/映像/chat) が停止しうる |
| T5 | TWITCH_PREDICTIONS_TOKEN の漏洩 | poll_worker/goal_worker/update_stream_title_dayが暗黙fallbackで同じトークンを使うため、1つのトークン漏洩で4系統の権限が同時に侵害される (`core/config.sh:117-131`) | Predictions+Polls+Goals+配信タイトル変更 |
| T6 | OBS_WEBSOCKET_PASSWORD の漏洩 | 7箇所以上のスクリプト/workerが生値を参照 (`obs_control.sh`, `soviet_local.mjs` 等) しており、broker化されていない | OBSシーン制御全体 (視聴者向け表示の改竄含む) |

### 2.2 raw credential broker化の対象/非対象

**対象 (broker化すべき)**: manifestの `credentials[].broker_candidate: true` の14件。
主に (a) 複数workerから生値で参照されるもの (OBS_WEBSOCKET_PASSWORD, ANTHROPIC_API_KEY)、
(b) 暗黙fallbackで権限が過剰に共有されているもの (TWITCH_PREDICTIONS_TOKEN系)、
(c) 単一worker専有でも漏洩時の影響が大きいもの (TWITCH_BOT_TOKEN, YOUTUBE_OAUTH_*)。

**非対象 (broker化不要、現状維持または別の対処)**:
- `TWITCH_CLIENT_ID` / `STREAM_NOON_AUDIT_GQL_CLIENT_ID`: クライアントIDは秘匿情報ではなく
  (公開されても単体では悪用できない)、broker化のコストに見合わない。
- RTMP/RTMPSストリームキー: **既にrepo外 (`/etc/soren-rtmp/push.conf`) で運用されており、
  既存のベストプラクティスをそのまま維持する。** 新たなbroker実装は不要。
- gcloud ADC: gcloud CLI自身のcredential storeが既に一種のbrokerとして機能しており
  (`.env` に値を書いていない)、新設のbrokerに移す優先度は低い。
- codex/claude CLI認証: **保存場所・形式が未調査のため、本ADRでは対象/非対象を判定
  できない。** 別Issueで調査してから判断する (未調査のまま broker 化方針だけ決めるのは
  推測になるため避ける)。

## 3. 提案するUnix identity / systemd unit分割

### 3.1 unit分割案

現在の単一 `soren-runtime.service` を、機能クラスタ単位で以下のunitへ分割する
(1 worker = 1 unit にはしない。理由は後述 §3.3)。

| 提案unit | 含まれるworker | 提案unix user |
|---|---|---|
| `soren-game.service` | soren_loop, deadline_monitor | `soren-game` |
| `soren-browser.service` | browser (soviet_local.mjs) + soviet_watchdog相当のRestart | `soren-browser` |
| `soren-strategy-improve.service` | improve_daemon | `soren-strategy` |
| `soren-strategy-sandbox` (transient scope, `systemd-run --uid=... --scope`) | strategy_runner (sandbox実行そのもの) | `soren-strategy-sandbox` |
| `soren-chat-twitch.service` | chat_worker | `soren-chat` |
| `soren-chat-youtube.service` | youtube_worker, youtube_broadcast_guard | `soren-chat` |
| `soren-chat-kick.service` | kick_worker | `soren-chat` |
| `soren-audio.service` | audio_worker | `soren-audio` |
| `soren-radio.service` | radio_worker | `soren-radio` |
| `soren-predictions.service` | prediction_worker | `soren-twitch-engagement` |
| `soren-polls.service` | poll_worker | `soren-twitch-engagement` |
| `soren-goals.service` | goal_worker | `soren-twitch-engagement` |
| `soren-obs-capture-watchdog.service` | obs_capture_watchdog | `soren-obs-control` |
| `soren-overlay.service` | overlay watcher群 | `soren-overlay` |
| `soren-stream-audit.service` | stream_noon_audit | `soren-audit` |
| `soren-codex-bug-dispatcher.service` | codex_bug_dispatcher | `soren-coding-agent` |
| (既存) `soren-rtmp-relay.service` / `soren-rtmps-bridge.service` | - | `soren-relay` (変更なし) |

`chat_worker` / `youtube_worker` / `kick_worker` を同一 `soren-chat` ユーザーに揃えるのは、
`tmp/.twitch_chat/comment_gen.pid` を3workerで共有するロック機構
(`games/soviet_now/workers/chat_worker.sh:147`, `kick_worker.sh:148`, `youtube_worker.sh` 同様の
参照) があるため。同一ユーザー・同一グループにしないとロックディレクトリの
パーミッション設計が破綻する。同様に prediction/poll/goal は
`TWITCH_PREDICTIONS_TOKEN` への暗黙fallback (§2.1 T5) があるため、fallbackを廃止し
各workerに専用トークンを発行してから初めて別ユーザーに分離できる
(migration順 §5 の Phase 2 で扱う)。

### 3.2 hardening matrix (`NoNewPrivileges`/`ProtectSystem`/`ProtectHome`/`PrivateTmp`/`RestrictAddressFamilies`)

既存の `soren-rtmp-relay.service` / `soren-rtmps-bridge.service` を basis とし、
各unitの実際の必要性に応じて緩める。「✓」は適用、「△」は条件付き緩和が必要、
「✗」は適用不可 (理由を明記)。

| unit | NoNewPrivileges | ProtectSystem | ProtectHome | PrivateTmp | RestrictAddressFamilies |
|---|---|---|---|---|---|
| soren-game | ✓ | strict | ✓ (read-only可) | △ (tmp/はrepo内相対パスなのでPrivateTmpと無関係。systemd既定のPrivateTmpのみ適用可) | `AF_UNIX AF_INET AF_INET6` |
| soren-browser | ✓ | full (Xvfb/PulseAudioソケットへのアクセスが要るためstrictは不可) | ✗ (X11/pulseの`$XDG_RUNTIME_DIR`はHOME配下ではないため要件次第で✓化を再検討) | ✗ (Chromiumの`--user-data-dir`をtmp/配下に固定しているため、PrivateTmpにするとキャッシュ効率は変わらない想定。**要検証**) | `AF_UNIX AF_INET AF_INET6` (CDP/HTTPはlocalhostのみ) |
| soren-strategy-improve | ✓ | strict | ✓ | ✓ | `AF_UNIX AF_INET AF_INET6` (MiniMax/Anthropic API向けTCP) |
| soren-strategy-sandbox (transient) | ✓ | strict | ✓ | ✓ (サンドボックス自体がtmp配下なので相性が良い) | `AF_UNIX AF_INET AF_INET6` (AI API呼び出し用) |
| soren-chat-* (twitch/youtube/kick) | ✓ | strict | ✓ | △ (comment_gen.pidの共有ロックがtmp/.twitch_chat/にあるため、3unit共通のbind mountかgroup共有ディレクトリでの緩和が要る) | `AF_UNIX AF_INET AF_INET6` |
| soren-audio | ✓ | strict | ✓ | ✓ | `AF_UNIX AF_INET AF_INET6` (VOICEVOX/Google TTSへのTCP) |
| soren-radio | ✓ | strict | ✓ | ✓ | `AF_UNIX AF_INET AF_INET6` |
| soren-predictions/polls/goals | ✓ | strict | ✓ | ✓ | `AF_UNIX AF_INET AF_INET6` |
| soren-obs-capture-watchdog | ✓ | strict | ✓ | ✓ | `AF_UNIX AF_INET AF_INET6` (OBS WebSocketはlocalhost想定) |
| soren-overlay | ✓ | strict | ✓ | ✓ | `AF_UNIX` のみ (外部通信なし、確認済み) |
| soren-stream-audit | ✓ | strict | ✓ | ✓ | `AF_UNIX AF_INET AF_INET6` |
| soren-codex-bug-dispatcher | ✗ (codexのプラグイン機構が要求する可能性、**未調査。既定はNoNewPrivileges=trueで開始し、実測でcodex/claudeが起動できなければ例外を記録して緩める**) | full (リポジトリ全体書き込みが必要なため`strict`は不可。`ReadWritePaths=`でリポジトリのみ明示許可する形の`ProtectSystem=strict`+例外の方が望ましい) | ✗ (CLIの認証情報が`$HOME`配下にある可能性が高いため。**未調査であり、確認後に✓化を再検討**) | ✗ (codex/claudeの一時ファイル生成場所が未調査のため既定PrivateTmpのみ) | `AF_UNIX AF_INET AF_INET6` |
| soren-relay (既存) | ✓ (適用済み) | strict (適用済み) | ✓ (適用済み) | ✓ (適用済み) | `AF_UNIX AF_INET AF_INET6` (適用済み) |

**注:** browser unit と codex-bug-dispatcher unit は、他unitのように機械的に `strict`
一式を適用できない実務上の制約がある (Xvfb/PulseAudioソケット、リポジトリ全体書き込み)。
これは「妥協」ではなく、**capability分離の目的が『必要最小限にする』ことであり、
『全unitに同じ設定を貼る』ことではない**という原則の帰結として明記する。実装Issueでは
`systemd-analyze security <unit>` の出力を実測してから最終値を決める (未実測、本ADRでは
設計方針のみ)。

## 4. Migration順序 (循環しないことの確認)

以下の順で進める。各Phaseは前Phaseの完了を前提とし、依存が一方向であることを
明示する (逆方向の依存は存在しない = 循環しない)。

```
Phase 0 (本Issue, 完了)
  ADR + manifest + doctor (read-only) の作成のみ。production unit不変。
      │
      ▼
Phase 1: credential fallback の解消
  TWITCH_PREDICTIONS_TOKEN への暗黙fallback (poll/goal/title) を廃止し、
  各workerに専用トークンを発行する (Twitch Developer Console側の作業のみ、
  systemd変更なし)。
  依存: Phase 0のmanifestが対象credentialを確定させている必要がある → Phase 0に依存。
  他Phaseはこれに依存しない側には影響しない (逆方向依存なし)。
      │
      ▼
Phase 2: 専用unix userの作成 (無停止)
  soren-game / soren-browser / soren-chat / soren-audio / soren-radio /
  soren-twitch-engagement / soren-obs-control / soren-overlay / soren-audit /
  soren-coding-agent / soren-strategy / soren-strategy-sandbox を作成するのみ
  (useradd)。既存 soren-runtime.service はubuntuのまま稼働継続。
  依存: なし (Phase 1と並行可能)。
      │
      ▼
Phase 3: canary unit切替 (1系統ずつ)
  影響が最も小さい系統から順にsystemd unit化し、旧prosess (start_all.sh管理下の
  該当worker) と並行稼働させず、1系統ずつ切替える。
  推奨順序 (依存が少ない/観測しやすい順):
    3-1. soren-overlay (外部通信なし、視聴者影響が最小)
    3-2. soren-stream-audit (読み取り専用に近い監査系)
    3-3. soren-obs-capture-watchdog
    3-4. soren-chat-* (comment_gen.pidの共有設計をPhase 2のgroup共有で先に検証)
    3-5. soren-predictions / soren-polls / soren-goals (Phase 1のトークン分離が前提)
    3-6. soren-audio
    3-7. soren-radio
    3-8. soren-game (deadline_monitor込み)
    3-9. soren-browser (最もXvfb/OBS依存が強く、切替失敗時の視聴影響が最大なので最後の常設worker)
    3-10. soren-strategy-improve / soren-strategy-sandbox
    3-11. soren-codex-bug-dispatcher (credential未調査分が残るため最後)
  各ステップの依存は「そのworkerがcredential/共有ロックを分離済みであること」のみで、
  後続ステップへの依存はない (一方向)。
      │
      ▼
Phase 4: raw credential broker導入
  broker_candidate=true の credential から、影響範囲が大きい順
  (OBS_WEBSOCKET_PASSWORD → ANTHROPIC_API_KEY → TWITCH_BOT_TOKEN → ...) にbroker経由へ移行。
  依存: Phase 3で該当workerがunit化済みであること (brokerへのアクセス制御は
  systemd unit / unix user 単位で行うため)。
      │
      ▼
Phase 5: 旧 soren-runtime.service の縮小・廃止
  Phase 3で全workerが個別unit化されたら、soren-runtime.serviceを
  「まだunit化されていないworkerだけを束ねる」役割に縮小し、最終的に空になったら
  `disable` する (削除はせず、rollback用にunit fileは残す)。
```

**循環がないことの根拠:** Phase 1〜5は「credential分離 → user作成 → unit切替 →
broker化 → 旧unit縮小」という一方向の依存グラフであり、各Phase内のstepも
(3-1〜3-11) 影響範囲の小さい順に並べているだけで相互依存はない。ただし
3-4 (chat系) は Phase 2 の group共有設計、3-5 (predictions/polls/goals) は
Phase 1 のトークン分離という**前方のPhaseにのみ**依存しており、後方のPhaseや
他のstepに依存を戻すことはない。

## 5. Canary と rollback

各unit切替 (Phase 3の各step) について、以下を最小セットとして定義する。

### 5.1 Observability (切替前後で見る指標)

- systemd: `systemctl status <unit>`, `journalctl -u <unit> --since -10m`
- 既存ログ: `logs/<worker>.log` (start_all.sh時代と同じパスを踏襲すれば比較しやすい)
- doctor script (`scripts/worker_capability_doctor.py`) の gap report を切替直後に実行し、
  manifestとの乖離が「意図した差分のみ」であることを確認する。

### 5.2 viewer-visible acceptance canary (音声/chat/映像)

systemd unitを切替えた後、**視聴者から見える3経路**それぞれで実際に観測して
初めて「切替成功」とする (グローバル規律「検証してから報告する」に準拠。
プロセス起動の有無だけで判定しない)。

| 経路 | canary手順 | 合格基準 |
|---|---|---|
| 音声 | 切替対象がaudio/chat/radio系のとき: `codex_work_indicator.sh` 経由 or 手動で1件読み上げをenqueueし、配信の実際の音声出力 (OBS音声ミキサー、または視聴者向けストリームのキャプチャ) で再生を確認する | 読み上げが欠落・二重再生・無音化しない |
| chat | chat/kick/youtube worker切替時: テスト用コメントを実際に投稿し、AI応答が対象プラットフォームに反映されるまでを目視確認する (`show_status.sh --once` 等の内部状態だけで判定しない) | 応答が実際にチャット欄に表示される |
| 映像 | browser/obs_capture_watchdog/overlay切替時: OBSプレビューまたは配信出力を目視し、ゲーム画面・オーバーレイが正常に更新され続けることを確認する | フリーズ・黒画面・オーバーレイ欠落がない |

### 5.3 Unit単位rollback

- 各Phase 3 stepは**旧経路 (start_all.sh管理下のworker) を即座に復元できる状態を
  保ったまま**進める。具体的には: 新unitを`systemctl stop`し、`tmp/state/<name>.paused`
  相当の仕組み (既存の `_worker_paused` 機構, `games/soviet_now/start_all.sh:596-599`) を
  一時的に解除して旧経路のworkerを`start_all.sh`のsupervisor配下に戻す。
- rollback判定基準: §5.2 のcanaryが失敗、またはjournalログにcrash loop
  (`StartLimitBurst` 到達) が見えたら即rollback。
- rollbackの実行者は当該作業を行うエージェント/オペレータ自身とし、
  「rollbackした」という報告は実際に旧経路でcanaryが再度合格することを確認してから行う。

## 6. 未調査・残課題

以下は本Issueのスコープでは解決せず、実装Issue分割時に個別に調査/対応する
(推測でこの場に結論を書かない)。

- codex/claude CLIの認証情報の保存場所・パーミッション・ローテーション手順 (§2.2, §3.2)。
- opencode CLI認証 (`_opencode_sync_auth_to_xdg`) の実体パスとローテーション手順。
- `soren-browser` unitのXvfb/PulseAudioソケットに対する`ProtectHome`適用可否の実測。
- `improve_daemon` → `strategy_runner` の実際の呼び出しコマンドライン (opencode/codex/claudeの
  いずれで起動しているか)。
- production VMの実際の `.env` 値・機能フラグ (本ADRはconfig.sh既定値とunitの
  `Environment=` 行のみに基づく)。
- `google_tts.sh` を実際にどのworkerが呼んでいるか。
- manifestの `unverified` 配列に列挙した残り全項目。

## 7. 受入条件との対応

| 受入条件 | 対応 |
|---|---|
| 全worker/coding agent/browser/strategy runnerにownerと最小capabilityが割り当てられる | manifest全20+worker (primary 16 + auxiliary 4) に `proposed_identity` を付与。§3.1 |
| credentialのprovider/consumer/rotation ownerが値なしで追跡できる | manifest `credentials` 配列 (20件、値は一切含まない) |
| migration順と依存関係が循環しない | §4 (Phase 0→5、一方向依存の根拠を明記) |
| 各unitの音声/chat/映像canaryとrollbackが定義されている | §5.2 (3経路の実測canary)、§5.3 (unit単位rollback手順) |
| ADR review後にunit単位の実装Issueへ分割できる | §3.1 のunit表、§4 のPhase/step粒度がそのまま実装Issue単位の候補になる設計 |

## 8. read-only doctor

`scripts/worker_capability_doctor.py` は本ADR/manifestと実環境の乖離を検出する
read-only スクリプト。プロセス起動・kill・ファイル書き込み・systemd操作は一切行わず、
gap reportをstdoutに出すのみ。Linux (systemdあり) / macOS (systemdなし) の両方で
クラッシュせずに動作する (macOSでは「systemd情報を取得できない」ことを明示して
静的manifestの検証のみ行う)。詳細は同スクリプトのdocstringおよび
`tests/test_worker_capability_doctor.py` を参照。
